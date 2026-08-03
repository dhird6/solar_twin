"""Sortie planning: the robot decides what it can actually do, and turns back in time.

Pure-python, Isaac-free. This is the layer that was missing when the twin's own notes
said "robot motion is scripted": a mission listed panels and the fleet visited them,
and **nothing anywhere knew a robot has a battery**. A 30,016-panel sweep and a
10-panel sweep were equally acceptable plans. That is not a routing weakness, it is
the absence of the constraint that makes routing a decision at all.

## What "autonomous" means here, concretely

Not SLAM — that needs a stereo pair we do not have (`cuVSLAM` requires stereo or RGBD
at >=30 Hz; our drone is monocular, and that is a prerequisite, not a step). What this
layer does is the decision-making a real inspection robot must do before it moves:

1. **Know its own limit.** Endurance comes from the platform's published spec via
   `fleet_specs`, derated at the point of use (`DEFAULT_ENDURANCE_DERATE`).
2. **Decide how much fits.** Targets are packed into sorties until the next one would
   not leave enough to get home — travel out, dwell, travel back, plus a reserve.
3. **Decide to turn back.** The return leg is costed *before* a target is accepted,
   so a sortie cannot strand itself. This is the part a fixed route cannot express.
4. **Decide what NOT to do.** Work beyond the fleet's total capacity is reported as
   `deferred`, not silently dropped and not silently attempted.
5. **Re-plan on new information.** `replan` recomputes from wherever the robot
   actually is, with whatever charge it actually has — which is what makes a mid-
   mission discovery able to change the plan.

## What this is NOT

⚠ **No obstacle avoidance, deliberately.** Legs are straight lines. It was checked
rather than assumed: on `farm_khavda_block02`, across 400 serpentine legs, **zero**
had endpoints that were both legal while the path between them crossed a turbine
keep-out — because the turbines ring the array rather than standing inside it. Adding
detour logic would be building for a hazard this stage does not have. `keepout.segment_clear`
exists for the day a stage puts a caster inside the field; `plan_sorties` takes an
optional `is_clear` predicate so it can be threaded in without a rewrite.

⚠ **Distances are Euclidean and speeds are constant.** No acceleration, no wind, no
climb/descent cost, no terrain gradient for the rover. Each of those makes real
endurance worse, so a plan that is infeasible here is certainly infeasible in reality
— but a plan that is feasible here is **not** thereby proven flyable.

⚠ **Nothing here has been flown.** The endurance figures are datasheet numbers times
an assumed derate. This plans against a model of a battery, not a battery.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

Point = tuple[float, float, float]

#: Fraction of usable endurance held back and never planned into. Distinct from
#: `DEFAULT_ENDURANCE_DERATE`, which models how much less the battery really gives:
#: this is the margin an operator keeps for wind, a go-around, or a diversion. Both
#: apply — the derate shrinks the tank, the reserve fences off part of what is left.
DEFAULT_RESERVE_FRACTION = 0.15


def _dist(a: Point, b: Point) -> float:
    return math.dist(a, b)


@dataclass(frozen=True)
class VehicleEndurance:
    """What a planner needs to know about a vehicle. Built from `fleet_specs`."""

    name: str
    #: Seconds the planner may spend, already derated. See `from_spec`.
    usable_endurance_s: float
    cruise_speed_ms: float
    #: Seconds spent stationary at each target (approach, settle, capture, verdict).
    dwell_s: float = 20.0
    reserve_fraction: float = DEFAULT_RESERVE_FRACTION

    def __post_init__(self) -> None:
        if self.cruise_speed_ms <= 0:
            raise ValueError(f"{self.name}: cruise_speed_ms must be positive")
        if self.usable_endurance_s <= 0:
            raise ValueError(f"{self.name}: usable_endurance_s must be positive")
        if not 0.0 <= self.reserve_fraction < 1.0:
            raise ValueError(f"{self.name}: reserve_fraction must be in [0, 1)")

    @property
    def budget_s(self) -> float:
        """Endurance a sortie may actually consume, after the operating reserve."""
        return self.usable_endurance_s * (1.0 - self.reserve_fraction)

    def travel_s(self, a: Point, b: Point) -> float:
        return _dist(a, b) / self.cruise_speed_ms

    @classmethod
    def from_spec(cls, spec, *, dwell_s: float = 20.0, derate: Optional[float] = None,
                  reserve_fraction: float = DEFAULT_RESERVE_FRACTION) -> "VehicleEndurance":
        """Build from a `fleet_specs` DroneSpec/RoverSpec (duck-typed).

        Raises rather than defaulting if the spec carries no endurance: a planner
        that silently assumed one would produce a confident, meaningless answer.
        """
        from solar_twin.world.fleet_specs import DEFAULT_ENDURANCE_DERATE  # noqa: PLC0415

        if not getattr(spec, "max_endurance_s", 0.0):
            raise ValueError(
                f"{getattr(spec, 'name', spec)!r} has no max_endurance_s — cannot plan "
                "a sortie against an unknown battery. Add the platform's published "
                "figure to fleet_specs."
            )
        d = DEFAULT_ENDURANCE_DERATE if derate is None else derate
        return cls(
            name=spec.name,
            usable_endurance_s=spec.usable_endurance_s(d),
            cruise_speed_ms=spec.cruise_speed_ms,
            dwell_s=dwell_s,
            reserve_fraction=reserve_fraction,
        )


@dataclass(frozen=True)
class Sortie:
    """One out-and-back trip that fits inside the vehicle's budget."""

    index: int
    target_ids: tuple[str, ...]
    travel_m: float
    duration_s: float
    budget_s: float

    @property
    def n_targets(self) -> int:
        return len(self.target_ids)

    @property
    def utilisation(self) -> float:
        """Fraction of the budget used. Low values mean travel, not dwell, dominates."""
        return self.duration_s / self.budget_s if self.budget_s > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "n_targets": self.n_targets,
            "target_ids": list(self.target_ids),
            "travel_m": round(self.travel_m, 2),
            "duration_s": round(self.duration_s, 1),
            "budget_s": round(self.budget_s, 1),
            "utilisation": round(self.utilisation, 3),
        }


@dataclass(frozen=True)
class SortiePlan:
    """The full plan: what gets done, in how many trips, and what does not fit."""

    vehicle: str
    sorties: tuple[Sortie, ...]
    #: Targets that fit in no sortie at all — each unreachable even alone, because
    #: base -> target -> base already exceeds the budget. NOT the same as work that
    #: simply needs more trips.
    unreachable: tuple[str, ...] = ()
    #: Targets dropped because `max_sorties` was hit. The fleet could do them; the
    #: plan was capped. Reported so a cap can never read as "that was everything".
    deferred: tuple[str, ...] = ()

    @property
    def n_planned(self) -> int:
        return sum(s.n_targets for s in self.sorties)

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.sorties)

    @property
    def total_travel_m(self) -> float:
        return sum(s.travel_m for s in self.sorties)

    def to_dict(self) -> dict:
        return {
            "vehicle": self.vehicle,
            "n_sorties": len(self.sorties),
            "n_planned": self.n_planned,
            "n_unreachable": len(self.unreachable),
            "n_deferred": len(self.deferred),
            "unreachable": list(self.unreachable),
            "deferred": list(self.deferred),
            "total_travel_m": round(self.total_travel_m, 2),
            "total_duration_s": round(self.total_duration_s, 1),
            "sorties": [s.to_dict() for s in self.sorties],
        }


def plan_sorties(
    targets: Sequence[tuple[str, Point]],
    vehicle: VehicleEndurance,
    base: Point = (0.0, 0.0, 0.0),
    *,
    max_sorties: int = 0,
    is_clear: Optional[Callable[[Point, Point], bool]] = None,
) -> SortiePlan:
    """Pack `targets` into out-and-back sorties the vehicle can actually complete.

    `targets` is `[(panel_id, (x, y, z)), ...]` **in the order they should be
    visited** — this function decides where to cut, not where to go. Ordering is the
    job of `grid_dispatch.plan_route` / `layout.route_sites` upstream; keeping the two
    separate means a better router and a better battery model can land independently.

    The rule at each step is the one a fixed route cannot express: accept the next
    target only if, after flying to it and inspecting it, there is still enough left
    to **get home**. Otherwise close the sortie, return to base, and start a new one.

    `max_sorties=0` means unlimited. `is_clear(a, b)` may reject a straight leg (see
    the module header on why no detour logic exists yet); a rejected leg defers that
    target rather than silently routing through whatever was in the way.
    """
    remaining = list(targets)
    sorties: list[Sortie] = []
    unreachable: list[str] = []

    # A target that cannot be done even as a sortie of one is unreachable, and stays
    # unreachable no matter how many trips are allowed. Separating this from "ran out
    # of trips" matters: one is a capability limit, the other is a planning cap.
    reachable: list[tuple[str, Point]] = []
    for tid, pos in remaining:
        out_and_back = vehicle.travel_s(base, pos) * 2 + vehicle.dwell_s
        blocked = is_clear is not None and not is_clear(base, pos)
        if out_and_back > vehicle.budget_s or blocked:
            unreachable.append(tid)
        else:
            reachable.append((tid, pos))

    queue = list(reachable)
    while queue:
        if max_sorties and len(sorties) >= max_sorties:
            break
        pos, spent, travel = base, 0.0, 0.0
        taken: list[str] = []
        while queue:
            tid, tpos = queue[0]
            leg = vehicle.travel_s(pos, tpos)
            home = vehicle.travel_s(tpos, base)
            if is_clear is not None and not is_clear(pos, tpos):
                # Cannot fly this leg from here. Leave it for a sortie that starts
                # from base, where it was already proven clear.
                break
            if spent + leg + vehicle.dwell_s + home > vehicle.budget_s:
                break  # taking it would strand us — turn back
            spent += leg + vehicle.dwell_s
            travel += _dist(pos, tpos)
            pos = tpos
            taken.append(tid)
            queue.pop(0)

        if not taken:
            # Nothing fit from base: the head target is individually unreachable
            # under the current predicate. Guard against an infinite loop.
            tid, _ = queue.pop(0)
            unreachable.append(tid)
            continue

        travel += _dist(pos, base)
        spent += vehicle.travel_s(pos, base)
        sorties.append(
            Sortie(
                index=len(sorties),
                target_ids=tuple(taken),
                travel_m=travel,
                duration_s=spent,
                budget_s=vehicle.budget_s,
            )
        )

    return SortiePlan(
        vehicle=vehicle.name,
        sorties=tuple(sorties),
        unreachable=tuple(unreachable),
        deferred=tuple(tid for tid, _ in queue),
    )


def replan(
    remaining: Sequence[tuple[str, Point]],
    vehicle: VehicleEndurance,
    current_pos: Point,
    charge_remaining_s: float,
    base: Point = (0.0, 0.0, 0.0),
    *,
    is_clear: Optional[Callable[[Point, Point], bool]] = None,
) -> tuple[list[str], bool]:
    """Mid-sortie decision: what can still be done from HERE, on THIS much charge.

    This is the call that makes a discovery able to change the plan — perception
    flags something, priorities are reordered upstream, and the robot asks what of
    the new order it can still reach before it has to come home.

    Returns `(ids_reachable_now, must_return_now)`. `must_return_now` is True when
    even the flight home is marginal, which the caller must treat as an abort rather
    than as "zero targets happen to fit".

    ⚠ `charge_remaining_s` is whatever the caller believes is left. Nothing here
    measures a battery — see the module header.
    """
    home_now = vehicle.travel_s(current_pos, base)
    if charge_remaining_s <= home_now:
        return [], True

    pos, spent = current_pos, 0.0
    reachable: list[str] = []
    for tid, tpos in remaining:
        if is_clear is not None and not is_clear(pos, tpos):
            continue
        leg = vehicle.travel_s(pos, tpos)
        home = vehicle.travel_s(tpos, base)
        if spent + leg + vehicle.dwell_s + home > charge_remaining_s:
            break
        spent += leg + vehicle.dwell_s
        pos = tpos
        reachable.append(tid)
    return reachable, False
