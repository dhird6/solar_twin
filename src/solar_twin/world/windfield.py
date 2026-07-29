"""Wind, gust and turbine-wake velocity field — parametric, seeded, no Isaac.

`FR-12` (wind/gust) and `FR-13` (wake as a velocity-deficit field). Answers one
question: **what is the air velocity at this point, at this time?** Everything
that decides the physics lives here, in pure python, so it is unit-testable with
no GPU and — critically for this project — *seeded and reproducible*, which a
KPI measured under gust has to be (`RISK-23` is about exactly this class of
problem).

## Why this is not `omni.physx.forcefields`

`FR-12` names that extension. **It does not exist on this build.** Measured
2026-07-29 against Isaac Sim 6.0.1-rc.7 / PhysX 110.1.13: of 473 extensions in
`extscache`, 18 are `omni.physx.*` and none is `forcefields`, and the string
`ForceField` appears in **zero** `.py`/`.toml` files across the whole install —
so the USD schema (`PhysxSchemaPhysxForceField*`) is gone too, not just the UI
extension. See `RISK-27`.

That turns out to matter less than it sounds, because `FR-13` was never
satisfiable by a generic Wind field anyway: it asks for a **Jensen/Gaussian
velocity-deficit profile localised to the downstream volume**, which is a specific
model, not a uniform breeze. So the deficit had to be authored either way. Doing
the same for the ambient wind keeps one field model instead of two mechanisms that
have to agree.

## Layering

This module is the **model**. It returns velocities; it applies no forces and
imports no Isaac. A thin Isaac-bound applier converts velocity into a drag force
per rigid body each step — that half needs a live sim to verify its API names
against 6.0.1 (`CLAUDE.md`: do not invent Isaac APIs), and is deliberately not
guessed at here.

## Conventions

Stage-local metres, Z-up (`CLAUDE.md`). `wind_dir_deg` is the **met convention**
bearing the wind blows *from* — 270 is a westerly, travelling east — matching
`world/siting.py` so siting and wake cannot disagree about which way the wind
goes. Velocities are returned as `(vx, vy, vz)` in stage axes (x=east, y=north).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, Sequence

#: Jensen wake decay constant. 0.075 is the textbook onshore value (0.04-0.05
#: offshore); it sets how fast the wake widens with downstream distance.
DEFAULT_WAKE_DECAY = 0.075

#: Thrust coefficient at rated operation. 8/9 is the Betz limit and the usual
#: modelling default; the induction factor follows from it.
DEFAULT_THRUST_COEFF = 8.0 / 9.0


def downwind_unit(wind_dir_deg: float) -> tuple[float, float]:
    """Unit vector the wind TRAVELS along, from a met-convention bearing.

    Kept identical to `siting.min_spacing_ellipse`'s derivation on purpose: a
    bearing `b` points at `(sin b, cos b)`, and the wind travels the reciprocal of
    where it comes from. If these two ever disagree, turbines get sited by one
    wind direction and waked by another.
    """
    theta = math.radians(wind_dir_deg)
    return -math.sin(theta), -math.cos(theta)


@dataclass(frozen=True)
class WakeSource:
    """A turbine casting a wake. Mirrors `siting.TurbineSite`'s geometry."""

    x: float
    y: float
    hub_height_m: float
    rotor_diameter_m: float
    thrust_coeff: float = DEFAULT_THRUST_COEFF

    @property
    def rotor_radius_m(self) -> float:
        return self.rotor_diameter_m / 2.0

    @classmethod
    def from_cfg(cls, spec: dict) -> "WakeSource":
        """Read the same `turbines:` entry shape `farm_builder`/`keepout` consume.

        `blade_len` is the authored field (see `siting.TurbineSite.to_cfg`), so the
        diameter is derived rather than expected — a config that carried both could
        disagree with itself.
        """
        pos = spec["pos"]
        blade = float(spec.get("blade_len", 8.0))
        return cls(
            x=float(pos[0]),
            y=float(pos[1]),
            hub_height_m=float(spec.get("hub_height", 18.0)),
            rotor_diameter_m=2.0 * blade,
            thrust_coeff=float(spec.get("thrust_coeff", DEFAULT_THRUST_COEFF)),
        )


@dataclass
class WindField:
    """Ambient wind + gusts + the combined wake deficit of every turbine.

    Parameterised exactly as `FR-12` asks — average speed, speed variation,
    direction variation — plus the wake sources `FR-13` needs. Seeded, so a
    scenario that declares a gust gets the *same* gust every run.

    ⚠ **Not CFD.** `FR-13` requires this be said plainly: the deficit is an
    analytical top-hat (Jensen) profile, the gusts are band-limited noise, and
    neither resolves turbulence, shear, wake meandering, or ground effect. It is
    a repeatable disturbance of about the right shape and magnitude — enough to
    ask "does station-keeping hold?" and not enough to publish a loads analysis.
    """

    mean_speed_ms: float = 8.0
    wind_dir_deg: float = 270.0
    #: Gust magnitude as a fraction of `mean_speed_ms` (FR-12 "speed variation").
    speed_variation: float = 0.0
    #: Direction wander in degrees (FR-12 "direction variation").
    direction_variation_deg: float = 0.0
    #: Gust correlation time. Longer = slower, larger-scale gusts.
    gust_period_s: float = 4.0
    seed: int = 0
    wakes: Sequence[WakeSource] = field(default_factory=tuple)
    wake_decay: float = DEFAULT_WAKE_DECAY
    #: Vertical wind is not modelled; kept explicit so callers do not assume it.
    vertical_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.mean_speed_ms < 0.0:
            raise ValueError("mean_speed_ms must be >= 0")
        if not 0.0 <= self.speed_variation <= 1.0:
            raise ValueError("speed_variation is a fraction of mean speed, 0..1")
        if self.gust_period_s <= 0.0:
            raise ValueError("gust_period_s must be > 0")
        if self.wake_decay <= 0.0:
            raise ValueError("wake_decay must be > 0")
        # Fixed phase offsets drawn once from the seed. This is what makes the
        # gust reproducible: the time series is a deterministic function of
        # (seed, t), with no hidden state advanced by how often it is sampled.
        rng = random.Random(self.seed)
        object.__setattr__(self, "_phase_s", rng.uniform(0.0, 1000.0))
        object.__setattr__(self, "_phase_d", rng.uniform(0.0, 1000.0))

    # -- gusts ------------------------------------------------------------- #

    def _gust_factors(self, t: float) -> tuple[float, float]:
        """(speed multiplier, direction offset in degrees) at time `t`.

        Two incommensurable sinusoids per channel rather than one, so the series
        does not repeat every `gust_period_s` and read as a rotating fan. Sampling
        a closed form of `t` (not stepping an RNG) means `velocity_at` is
        order-independent — two robots queried in either order see the same air.
        """
        w = 2.0 * math.pi / self.gust_period_s
        ph_s: float = getattr(self, "_phase_s")
        ph_d: float = getattr(self, "_phase_d")
        s = 0.6 * math.sin(w * (t + ph_s)) + 0.4 * math.sin(w * 0.37 * (t + ph_s))
        d = 0.6 * math.sin(w * 0.83 * (t + ph_d)) + 0.4 * math.sin(w * 0.19 * (t + ph_d))
        return 1.0 + self.speed_variation * s, self.direction_variation_deg * d

    # -- wake -------------------------------------------------------------- #

    def wake_deficit_at(self, x: float, y: float, z: float) -> float:
        """Fractional speed deficit at a point: 0 = free stream, 1 = still air.

        Jensen top-hat: the wake grows linearly with downstream distance, the
        deficit is uniform inside its radius and zero outside, and overlapping
        wakes combine by **sum of squares** (Katic) rather than adding, which
        would let three turbines produce a negative wind speed.
        """
        total_sq = 0.0
        dwx, dwy = downwind_unit(self.wind_dir_deg)
        for w in self.wakes:
            dx, dy = x - w.x, y - w.y
            along = dx * dwx + dy * dwy
            if along <= 0.0:
                continue  # upwind of the rotor: no wake there
            across = -dx * dwy + dy * dwx
            dz = z - w.hub_height_m
            radial = math.hypot(across, dz)
            wake_radius = w.rotor_radius_m + self.wake_decay * along
            if radial > wake_radius:
                continue  # outside the cone
            # Jensen: dU/U = (1 - sqrt(1-Ct)) / (1 + 2k x / D)^2
            induction = 1.0 - math.sqrt(max(0.0, 1.0 - w.thrust_coeff))
            growth = (wake_radius / w.rotor_radius_m) ** 2
            total_sq += (induction / growth) ** 2
        return min(1.0, math.sqrt(total_sq))

    # -- the field --------------------------------------------------------- #

    def velocity_at(
        self, x: float, y: float, z: float, t: float = 0.0
    ) -> tuple[float, float, float]:
        """Air velocity `(vx, vy, vz)` in stage axes at a point and time."""
        gust_mul, dir_offset = self._gust_factors(t)
        speed = self.mean_speed_ms * gust_mul
        speed *= 1.0 - self.wake_deficit_at(x, y, z)
        dwx, dwy = downwind_unit(self.wind_dir_deg + dir_offset)
        return speed * dwx, speed * dwy, self.vertical_ms

    def speed_at(self, x: float, y: float, z: float, t: float = 0.0) -> float:
        vx, vy, vz = self.velocity_at(x, y, z, t)
        return math.sqrt(vx * vx + vy * vy + vz * vz)

    # -- what an applier needs --------------------------------------------- #

    def drag_force(
        self,
        x: float,
        y: float,
        z: float,
        *,
        body_velocity: tuple[float, float, float] = (0.0, 0.0, 0.0),
        drag_area_m2: float,
        drag_coeff: float = 1.0,
        air_density: float = 1.225,
        t: float = 0.0,
    ) -> tuple[float, float, float]:
        """Aerodynamic drag on a body at this point, in newtons.

        The force depends on **relative** air speed, not wind speed: a drone
        translating downwind at the wind speed feels nothing. Getting that wrong
        produces a field that shoves a moving robot harder the faster it flees,
        which looks like wind and behaves like a spring.

        ``F = 0.5 * rho * Cd * A * |v_rel| * v_rel`` — the vector form, so the
        force follows the relative flow instead of always pointing downwind.
        """
        if drag_area_m2 < 0.0:
            raise ValueError("drag_area_m2 must be >= 0")
        ax, ay, az = self.velocity_at(x, y, z, t)
        rx, ry, rz = ax - body_velocity[0], ay - body_velocity[1], az - body_velocity[2]
        mag = math.sqrt(rx * rx + ry * ry + rz * rz)
        if mag == 0.0:
            return 0.0, 0.0, 0.0
        k = 0.5 * air_density * drag_coeff * drag_area_m2 * mag
        return k * rx, k * ry, k * rz


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def from_cfg(farm_cfg: dict, turbines: Iterable[dict] | None = None) -> WindField:
    """Build a `WindField` from a farm/scenario config's `wind:` block (`IF-03`).

    Wake sources default to the config's resolved `turbines:` list, so a scenario
    that scatters turbines wakes them where they actually stand — the same trap
    `build_keepouts` had to be fixed for.

        wind:
          mean_speed: 12.0
          direction_deg: 270.0
          speed_variation: 0.35
          direction_variation_deg: 15.0
          gust_period_s: 4.0
    """
    wind = dict(farm_cfg.get("wind") or {})
    specs = list(turbines if turbines is not None else (farm_cfg.get("turbines") or []))
    return WindField(
        mean_speed_ms=float(wind.get("mean_speed", 0.0)),
        wind_dir_deg=float(wind.get("direction_deg", 270.0)),
        speed_variation=float(wind.get("speed_variation", 0.0)),
        direction_variation_deg=float(wind.get("direction_variation_deg", 0.0)),
        gust_period_s=float(wind.get("gust_period_s", 4.0)),
        # Seeded off the farm seed so a scenario's gust is part of its identity.
        seed=int(wind.get("seed", farm_cfg.get("seed", 0))),
        wakes=tuple(WakeSource.from_cfg(s) for s in specs),
        wake_decay=float(wind.get("wake_decay", DEFAULT_WAKE_DECAY)),
    )
