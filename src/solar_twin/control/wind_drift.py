"""Wind blowing the camera off its mark — a pose disturbance, not flight dynamics.

## Why this exists when `tools/px4_hover.py` already applies wind

`khavda_windy_hover.yaml` says the important thing plainly: *"Wind is a FORCE. The
inspection mission drives its robots kinematically — a commanded Xform pose — and a
force applied to a kinematically-driven body does nothing at all."* That is correct,
and it is why wind has never appeared in an inspection run. The answer there was to
build a separate PX4 hover scenario where the drone is a dynamic body.

That covers one of the two questions the twin has to answer, and not the other. The
project's own thesis (`DIGITAL_TWIN_VISION_AND_RESEARCH.md`) is that **physics tests
the body; world models test the eyes** — can the drone hold station, *and* does
perception still work while it is being pushed. `px4_hover` tests the body. Nothing
tested the eyes, because the inspection loop is where perception lives and the
inspection loop had no wind in it at all.

Testing the eyes needs far less than flight dynamics. It needs the **camera to be
somewhere other than where the mission asked**, by a realistic amount, reproducibly.
That is a pose offset, and a pose offset is something a kinematic controller can
honestly carry.

## What is modelled, and what is therefore not claimed

Quasi-static equilibrium of a position-holding vehicle:

    drift = drag_force(wind at the commanded pose) / hold_stiffness

`⚠ NFR-07`, stated so nobody has to infer it:

* **This is not flight dynamics.** No mass, no thrust limit, no rotor authority, no
  attitude loop, no lag. The vehicle is assumed to reach its offset equilibrium
  instantly. It is legitimate for asking *"what does the camera see when the drone is
  0.3 m off station?"* and illegitimate for anything about the drone's stability.
* **`KPI-05` (altitude hold) does not come from here.** That is a flight-control
  measurement and it belongs to `tools/px4_hover.py`, which has real dynamics and a
  measured calm-air baseline (43 mm over 35 s). A number produced here would be an
  artefact of `hold_stiffness_n_per_m`, which is an assumption, not a measurement.
* **Quasi-static is an approximation with a stated basis:** the gust correlation time
  is `gust_period_s` (4 s by default) and a multirotor's position-hold response is
  order 1 s, so the vehicle tracks the gust rather than filtering it. At gust periods
  approaching the response time this stops being true and the model overstates the
  excursion.
* **Attitude is not modelled**, so the camera translates but never tilts. A real
  drone holding station in wind sits nose-down by `atan(drag/weight)` — about 14 deg
  for the M350 class at 12 m/s — which rotates the view as well as moving it. What is
  here is the translation only, i.e. a *lower* bound on how much the frame changes.

Pure-python, no Isaac, and a pure function of (position, time): `velocity_at` is
sampled in closed form so two robots queried in either order see the same air, and
that order-independence is preserved here deliberately — it is what makes a gust
reproducible, which `RISK-23` says a KPI measured under gust has to be.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.world.windfield import WindField

#: Position-hold stiffness, newtons per metre of excursion.
#:
#: ⚠ **INFERRED**, and it is the one number here that sets the answer. The arithmetic
#: it comes from, so it can be argued with: an M350-class machine (6.47 kg, ~0.18 m2
#: frontal area — `fleet_specs.DroneSpec.frontal_area_m2`) in a 12 m/s wind feels
#: 0.5 * 1.225 * 1.0 * 0.18 * 144 = 15.9 N of drag, about 25% of its 63.5 N weight,
#: which it holds against by tilting ~14 deg. 80 N/m turns that into 0.20 m of
#: steady-state offset, which is the right order for a GPS/vision-held multirotor.
#:
#: **How to replace this with a measurement:** run `tools/px4_hover.py
#: --wind-scenario` (real dynamics) and the same wind, record the lateral excursion,
#: and set `k = drag / excursion`. Until that is done, treat every drift figure as
#: proportional to an assumption.
DEFAULT_HOLD_STIFFNESS_N_PER_M = 80.0

#: Refuse to displace a robot further than this, in metres. Not physics — a guard.
#: Reaching it means the wind config is outside the model's range of validity (the
#: quasi-static assumption and the linear stiffness both fail long before a drone is
#: 2 m off station), so it is better to clamp and say so than to fly the camera into
#: a panel and call it a gust.
DEFAULT_MAX_DRIFT_M = 2.0


@dataclass(frozen=True)
class HoldModel:
    """How hard the wind pushes one vehicle, and how hard it pushes back.

    `drag_area_m2` is the vehicle's frontal area. `fleet_specs` can supply it from
    published dimensions; note that a body-box figure ignores arms, rotors and
    payload, so it is a lower bound on drag area and therefore on drift.
    """

    drag_area_m2: float
    drag_coeff: float = 1.0
    hold_stiffness_n_per_m: float = DEFAULT_HOLD_STIFFNESS_N_PER_M
    max_drift_m: float = DEFAULT_MAX_DRIFT_M
    #: Whether wind may push the vehicle vertically. False for a ground bot, which
    #: is held on the terrain by something considerably stiffer than a rotor.
    vertical: bool = True

    def __post_init__(self) -> None:
        if self.drag_area_m2 < 0.0:
            raise ValueError("drag_area_m2 must be >= 0")
        if self.hold_stiffness_n_per_m <= 0.0:
            raise ValueError("hold_stiffness_n_per_m must be > 0")
        if self.max_drift_m < 0.0:
            raise ValueError("max_drift_m must be >= 0")

    @classmethod
    def for_drone(cls, spec, **over) -> "HoldModel":
        """Build from a `fleet_specs.DroneSpec`, so the drag area traces to published
        dimensions rather than being typed in beside it."""
        return cls(drag_area_m2=spec.frontal_area_m2, **over)


def drift_at(
    field: WindField,
    x: float,
    y: float,
    z: float,
    model: HoldModel,
    t: float = 0.0,
) -> tuple[float, float, float]:
    """Steady-state offset `(dx, dy, dz)` of a vehicle trying to hold `(x, y, z)`.

    `body_velocity` is zero because this models *station-keeping*: the vehicle is
    trying to stay put, so the relative air speed is the full wind speed. (A vehicle
    translating downwind would feel less, which is what `WindField.drag_force`'s
    vector form is for — it is simply not the case being modelled here.)

    The clamp is applied to the magnitude, not per-axis, so a diagonal gust is not
    silently allowed to displace the vehicle 1.7x further than a head-on one.
    """
    fx, fy, fz = field.drag_force(
        x,
        y,
        z,
        body_velocity=(0.0, 0.0, 0.0),
        drag_area_m2=model.drag_area_m2,
        drag_coeff=model.drag_coeff,
        t=t,
    )
    if not model.vertical:
        fz = 0.0
    k = model.hold_stiffness_n_per_m
    dx, dy, dz = fx / k, fy / k, fz / k
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag > model.max_drift_m > 0.0:
        s = model.max_drift_m / mag
        dx, dy, dz = dx * s, dy * s, dz * s
    return dx, dy, dz


class WindDisturbedControl(RobotControl):
    """Decorator that lands a robot slightly off its commanded waypoint, by wind.

    Wraps any `RobotControl` — the Slice-0 kinematic one today, a PX4 one later (at
    which point this wrapper should be *removed* for that vehicle rather than
    stacked, because real dynamics already produce the excursion this approximates).

    ## Where this belongs in the wrapper stack

    **Outside** `SafeControl`, i.e. `WindDisturbedControl(SafeControl(inner))`. The
    keep-out layer must vet the pose the vehicle will actually hold, not the one the
    mission wished for; wrapped the other way round a gust could displace an
    already-cleared waypoint back into the rotor-swept volume with nothing checking
    it. A consequence worth having: a wind-induced keep-out clamp shows up in
    `SafeControl.events`, which is exactly the hazard `HAZ-*` cares about.

    ## The mission clock

    Gusts are a function of time, so this needs one. It does NOT use wall-clock time
    — that would make every run different and unquotable. By default `t` advances by
    `seconds_per_move` on each `move_to`, i.e. it is a deterministic function of the
    mission's own call sequence, which is itself seeded. Reproducible, and honest
    about being nominal rather than measured: pass `time_source` to supply a real
    sim clock once one exists.
    """

    def __init__(
        self,
        inner: RobotControl,
        field: WindField,
        models: dict[str, HoldModel],
        *,
        seconds_per_move: float = 4.0,
        time_source: Optional[Callable[[], float]] = None,
    ):
        """`models` maps robot_id -> HoldModel. A robot with no model is passed
        through undisturbed, which is how a ground bot opts out."""
        self._inner = inner
        self._field = field
        self._models = dict(models)
        self._seconds_per_move = float(seconds_per_move)
        self._time_source = time_source
        self._moves = 0
        #: Last drift applied per robot, metres. Observable on purpose: a
        #: disturbance nobody can read is indistinguishable from one that never
        #: happened, which is the `NFR-07` failure this module is trying to avoid.
        self.last_drift: dict[str, tuple[float, float, float]] = {}
        self.max_drift_seen_m: float = 0.0
        self._targets: dict[str, tuple[tuple[float, float, float], Waypoint]] = {}

    # -- decorator plumbing ------------------------------------------------- #

    @property
    def inner(self) -> RobotControl:
        """The wrapped controller, matching `SafeControl.inner` so a caller can reach
        past either wrapper without knowing which wraps which."""
        return self._inner

    def reset(self) -> None:
        """Clear the per-run tally and the mission clock (`run.py --repeat`).

        The clock resets too, and that is the point: repeat 2 must see the same gust
        sequence as repeat 1, or `variance.json` would attribute a decoding spread to
        the weather. Delegates to the inner controller's `reset` if it has one, so
        stacking wrappers does not lose anyone's tally."""
        self._moves = 0
        self.last_drift = {}
        self.max_drift_seen_m = 0.0
        self._targets = {}
        inner_reset = getattr(self._inner, "reset", None)
        if callable(inner_reset):
            inner_reset()

    # -- the disturbance ---------------------------------------------------- #

    def _now(self) -> float:
        if self._time_source is not None:
            return float(self._time_source())
        return self._moves * self._seconds_per_move

    def _disturbed(self, robot_id: str, waypoint: Waypoint, t: float) -> Waypoint:
        model = self._models.get(robot_id)
        if model is None:
            return waypoint
        dx, dy, dz = drift_at(
            self._field, waypoint.x, waypoint.y, waypoint.z, model, t=t
        )
        self.last_drift[robot_id] = (dx, dy, dz)
        self.max_drift_seen_m = max(
            self.max_drift_seen_m, math.sqrt(dx * dx + dy * dy + dz * dz)
        )
        return Waypoint(
            waypoint.x + dx, waypoint.y + dy, waypoint.z + dz, waypoint.yaw
        )

    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        self._moves += 1
        target = self._disturbed(robot_id, waypoint, self._now())
        self._targets[robot_id] = ((waypoint.x, waypoint.y, waypoint.z), target)
        self._inner.move_to(robot_id, target)

    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        """True once the robot is within `tol` of where the wind actually left it.

        Asking the inner controller about the *commanded* waypoint instead would be a
        mission hang, not a stricter test: the drift is routinely larger than `tol`
        (0.2 m vs 0.05 m at 12 m/s), so the goal could never be reached and the
        escalation FSM would wait forever for a drone that had already arrived
        wherever the wind allowed. The disturbance is the environment's, not a
        tracking error the controller is expected to null out.
        """
        cached = self._targets.get(robot_id)
        if cached is not None and cached[0] == (waypoint.x, waypoint.y, waypoint.z):
            target = cached[1]
        else:
            target = self._disturbed(robot_id, waypoint, self._now())
        return self._inner.at_goal(robot_id, target, tol)

    # -- reporting ---------------------------------------------------------- #

    def summary(self) -> dict:
        """Drift figures for the run record, so a KPI measured under wind can state
        how much wind actually reached the camera."""
        return {
            "max_drift_m": round(self.max_drift_seen_m, 4),
            "last_drift_m": {
                rid: [round(v, 4) for v in d] for rid, d in sorted(self.last_drift.items())
            },
            "mean_wind_ms": self._field.mean_speed_ms,
            "speed_variation": self._field.speed_variation,
            "model": "quasi-static drag / hold-stiffness; NOT flight dynamics (NFR-07)",
        }
