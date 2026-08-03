"""Closed the loop: what the robot SEES changes where it goes next (pure, Isaac-free).

The mission FSM ran a fixed list. Every panel was decided before the robot moved, and
a fault found at panel 3 changed nothing about panels 4..N — so the "inspection" was
a playback of a plan, not a response to evidence. This makes findings feed back into
the queue.

## The physical reason this is not an invented heuristic

**PV faults cluster, and they cluster differently by type.** That is the domain fact
the expansion rules encode:

* **soiling** is spatially correlated — dust drifts, and edge/downwind rows soil
  together. Finding one soiled module is real evidence about its neighbours, in every
  direction.
* **string_dropout** follows the ELECTRICAL topology, not a disc: a series string runs
  along the torque tube, so the informative neighbours are along the row.
* **hotspot / crack / diode_fault** are module-local defects. A hotspot next door is
  mostly coincidence, so expansion is deliberately weak — over-expanding here would
  burn endurance chasing noise.

⚠ These radii are **reasoned, not measured on this plant** — we hold no fault-location
data for Khavda, so nobody has verified that its soiling clusters at 2 panels rather
than 5. They are a stated prior, and `EXPANSION` is one table to argue with rather
than a rule buried in a branch.

## Design notes that matter later

**Position is an INPUT, never assumed.** `revise` takes where the robot actually is.
Today that is the commanded pose; when SLAM lands it becomes an estimated pose and
nothing here changes. This module must never derive position from "the last target we
sent it to".

**Off is the identity.** `Mission.run` without a planner keeps its `for target in
targets` behaviour exactly, so every recorded KPI stays reproducible. With one, the
queue becomes dynamic — and because that changes how many panels get inspected, it
changes every denominator in the run record. Hence opt-in and loud.

**Provenance travels with each target.** A panel added by expansion records WHICH
finding triggered it (`AdaptiveTarget.reason`), so a run record can be audited for
why the robot went where it went instead of that being unrecoverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from solar_twin.schema.pv_module import PanelState, parse_panel_id

Point = tuple[float, float, float]


@dataclass(frozen=True)
class ExpansionRule:
    """How far to look around a confirmed fault, and along which axis."""

    #: Panels either side, in grid steps. 0 disables expansion for that state.
    radius: int
    #: True = only along the row (the torque tube / electrical string). False = a
    #: square neighbourhood.
    along_row_only: bool = False
    why: str = ""


#: ⚠ A stated prior, not a measurement — see the module header.
EXPANSION: dict[PanelState, ExpansionRule] = {
    PanelState.SOILED: ExpansionRule(2, False, "dust drifts; soiling is spatially correlated"),
    PanelState.STRING_DROPOUT: ExpansionRule(6, True, "a series string runs along the tube"),
    PanelState.SHADING: ExpansionRule(3, True, "a shadow falls across a row, not a disc"),
    PanelState.DIODE_FAULT: ExpansionRule(1, True, "a bypass diode covers a sub-string"),
    PanelState.HOTSPOT: ExpansionRule(1, False, "module-local; neighbours are weak evidence"),
    PanelState.CRACK: ExpansionRule(0, False, "module-local; hail is the exception, not the rule"),
    PanelState.HEALTHY: ExpansionRule(0, False, "nothing to chase"),
    PanelState.UNKNOWN: ExpansionRule(0, False, "never expand on an answer we did not get"),
}


@dataclass(frozen=True)
class AdaptiveTarget:
    """A queued panel and why it is queued."""

    panel_id: str
    #: "scheduled" for the original sweep, or the panel_id whose finding added it.
    reason: str

    @property
    def is_triggered(self) -> bool:
        return self.reason != "scheduled"


@dataclass
class AdaptiveStats:
    """What the feedback loop actually did, for the run record."""

    triggered: dict[str, str] = field(default_factory=dict)
    reorders: int = 0
    expansions: int = 0
    skipped_no_budget: int = 0
    dropped_unknown: int = 0

    def to_dict(self) -> dict:
        return {
            "n_triggered": len(self.triggered),
            "triggered_by": dict(self.triggered),
            "reorders": self.reorders,
            "expansions": self.expansions,
            "skipped_no_budget": self.skipped_no_budget,
            "dropped_unknown": self.dropped_unknown,
        }


class AdaptivePlanner:
    """Revises the remaining queue from findings, position and remaining endurance.

    `catalog` maps panel_id -> position for every panel that COULD be inspected (the
    whole stage), which is what makes expansion possible: the original sweep is a
    subset, and a neighbour of a faulted panel is usually not in it.

    `max_extra` bounds the total number of panels expansion may add. Without it a
    soiled block could enqueue the whole farm — and an autonomous robot that cannot
    stop is worse than one that never started.
    """

    def __init__(
        self,
        catalog: dict[str, Point],
        *,
        max_extra: int = 24,
        reorder: bool = True,
        expansion: Optional[dict[PanelState, ExpansionRule]] = None,
        budget_check: Optional[Callable[[Sequence[str], Point], list[str]]] = None,
    ) -> None:
        self.catalog = dict(catalog)
        self.max_extra = int(max_extra)
        self.reorder = bool(reorder)
        self.expansion = dict(expansion or EXPANSION)
        self.budget_check = budget_check
        self.stats = AdaptiveStats()
        self._visited: set[str] = set()
        self._queued: set[str] = set()

    # -- expansion ---------------------------------------------------------- #

    def neighbours_of(self, panel_id: str, rule: ExpansionRule) -> list[str]:
        """Panel ids around `panel_id` that exist in the catalog.

        Returns [] for a malformed id rather than raising: a perception layer that
        hands back a junk id should cost us an expansion, not the mission.
        """
        if rule.radius <= 0:
            return []
        try:
            row, col = parse_panel_id(panel_id)
        except ValueError:
            return []
        out = []
        r = rule.radius
        rows = [row] if rule.along_row_only else range(row - r, row + r + 1)
        for rr in rows:
            for cc in range(col - r, col + r + 1):
                if rr == row and cc == col:
                    continue
                from solar_twin.schema.pv_module import panel_id as make_id  # noqa: PLC0415

                pid = make_id(rr, cc)
                if pid in self.catalog:
                    out.append(pid)
        return sorted(out)

    # -- the loop hook ------------------------------------------------------ #

    def revise(
        self,
        remaining: Sequence[str],
        last_panel_id: str,
        last_state: PanelState,
        position: Point,
        charge_remaining_s: Optional[float] = None,
    ) -> list[str]:
        """Return the new remaining queue after seeing `last_state` at `last_panel_id`.

        ⚠ `position` is where the robot IS — passed in, never inferred from the last
        target. That is what lets a SLAM pose replace a commanded one without touching
        this module.
        """
        self._visited.add(last_panel_id)
        queue = [p for p in remaining if p not in self._visited]
        self._queued = set(queue)

        rule = self.expansion.get(last_state, ExpansionRule(0))
        if last_state is PanelState.UNKNOWN:
            # An abstention is not evidence of a fault. Counting it as one would let
            # a model that fails to answer drive the robot around the farm.
            self.stats.dropped_unknown += 1
        elif rule.radius > 0 and len(self.stats.triggered) < self.max_extra:
            added = 0
            for pid in self.neighbours_of(last_panel_id, rule):
                if len(self.stats.triggered) >= self.max_extra:
                    break
                if pid in self._visited or pid in self._queued:
                    continue
                queue.append(pid)
                self._queued.add(pid)
                self.stats.triggered[pid] = last_panel_id
                added += 1
            if added:
                self.stats.expansions += 1

        if charge_remaining_s is not None and self.budget_check is not None:
            affordable = self.budget_check(queue, position)
            if len(affordable) < len(queue):
                self.stats.skipped_no_budget += len(queue) - len(affordable)
            queue = list(affordable)

        if self.reorder and len(queue) > 1:
            from solar_twin.orchestrator.coverage import plan_order  # noqa: PLC0415

            res = plan_order(
                [(p, self.catalog[p]) for p in queue if p in self.catalog],
                position,
            )
            if res.order:
                # Anything absent from the catalog keeps its place at the end rather
                # than being silently dropped.
                missing = [p for p in queue if p not in self.catalog]
                queue = list(res.order) + missing
                self.stats.reorders += 1

        return queue
