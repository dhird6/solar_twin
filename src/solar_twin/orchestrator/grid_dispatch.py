"""Suspicion-first dispatch: order panels by cell rank, then hand them to the FSM.

Pure-python, Isaac-free. This is the **prioritisation layer** from the research
doc's Slice 4b: it sits strictly UPSTREAM of `orchestrator/mission.py` and decides
only **which panels, in what order**. It hands the escalation FSM the same
`list[InspectionTarget]` shape it takes today, so `ADVANCE -> SCREEN -> CONFIRM ->
WRITEBACK`, `Perception`, `Transport`, `RobotControl` and `FaultReport` are all
untouched.

**The acceptance test is that turning it off reproduces current behaviour exactly**
(`tests/test_grid_dispatch.py::test_disabled_reproduces_layout_order_exactly`).
`order_targets` with `enabled=False` returns *the very same list object contents in
the same order* — not an equivalent ordering, the identical sequence — so a
disabled ranker cannot perturb a recorded KPI.

⚠⚠ **The ranking it consumes is SIMULATED** (`kpi/simulated_scada.py`): the prior is
derived from the twin's own injected faults, so it is circular by construction and
proves nothing about a real plant. See that module's header.

**cuOpt is NOT installed on this box** (checked: `import cuopt` -> ModuleNotFoundError),
so `_greedy_route` is a documented stub, not a solver. It is deliberately a
*nearest-neighbour walk over ranked cells*, which is the simplest thing that
respects the objective ("retire the most suspicion per unit travel") while being
obviously not optimal. Do not mistake its output for a cuOpt result — `RouteRPlan.solver`
says which one produced it.

⚠ **Ground-bot-first vs drone-first is an ASSUMPTION, not a conclusion.** The
research doc is explicit: ground-first is right when travel dominates and the bot
can rule a cell out; it is wrong when the fault is only visible from above
(soiling gradients, string-dropout patterns) and the bot's trip is pure overhead.
So `escalation_arm` is a config choice with both arms available and neither
hard-coded, the way `--route serpentine` was — and until both arms are measured,
neither is the default answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from solar_twin.kpi.simulated_scada import SIMULATED_CAVEAT, SimulatedCellScore

#: Which robot enters a suspect cell first. Both arms exist so the ordering can be
#: MEASURED rather than asserted; see the module header.
ESCALATION_ARMS = ("ground_first", "drone_first")


@dataclass(frozen=True)
class RoutePlan:
    """An ordered dispatch plan over cells."""

    cell_order: tuple[str, ...]
    #: Which solver produced this. "greedy-stub" is NOT cuOpt.
    solver: str
    #: Straight-line travel along the planned cell order, metres.
    travel_m: float
    #: Sum of the (simulated) posterior of every cell in the plan — the numerator
    #: of KPI-09.
    suspicion: float
    escalation_arm: str = "ground_first"
    #: Cells dropped because the budget ran out. Named, never silently truncated.
    dropped: tuple[str, ...] = ()

    @property
    def suspicion_per_m(self) -> float:
        """KPI-09, in per-metre units.

        ⚠ NOT per-battery-hour. There is no battery model in `world/fleet_specs.py`
        (geometry only — no endurance, capacity or power draw), and `wall_seconds`
        on a VLM run is dominated by ~7-12 s/panel of blocking inference, i.e. a
        perception cost rather than a flight cost. Converting to battery-hours needs
        a per-platform cruise speed and energy model; until then do not rename this.
        See `docs/specs/06-scenario-suite-and-kpis.md`.
        """
        return self.suspicion / self.travel_m if self.travel_m > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "solver": self.solver,
            "escalation_arm": self.escalation_arm,
            "cell_order": list(self.cell_order),
            "travel_m": round(self.travel_m, 3),
            "suspicion": round(self.suspicion, 6),
            "suspicion_per_m": round(self.suspicion_per_m, 9),
            "dropped": list(self.dropped),
        }


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _greedy_route(
    cells: Sequence[SimulatedCellScore],
    centroids: dict[str, tuple[float, float]],
    start: tuple[float, float],
    max_cells: int = 0,
) -> tuple[list[str], float, list[str]]:
    """⚠ STUB, NOT cuOpt. Nearest-neighbour walk biased by suspicion.

    Picks, at each step, the unvisited cell maximising `posterior / (1 + distance)`
    — a direct greedy reading of "retire the most suspicion per unit travel". This
    is the classic greedy VRP heuristic and is **not optimal**; cuOpt exists
    precisely because this leaves value on the table under real battery and
    time-window constraints. It is here so the dispatch *plumbing* can be built and
    tested before the solver is available.
    """
    remaining = {c.cell_id: c for c in cells if c.cell_id in centroids}
    order: list[str] = []
    pos, travel = start, 0.0
    limit = max_cells if max_cells > 0 else len(remaining)
    while remaining and len(order) < limit:
        best, best_score, best_d = None, -1.0, 0.0
        for cid, c in remaining.items():
            d = _dist(pos, centroids[cid])
            score = c.posterior / (1.0 + d)
            # Tie-break on cell_id so a seeded run is reproducible.
            if score > best_score or (score == best_score and (best is None or cid < best)):
                best, best_score, best_d = cid, score, d
        order.append(best)
        travel += best_d
        pos = centroids[best]
        del remaining[best]
    return order, travel, sorted(remaining)


def plan_route(
    cells: Sequence[SimulatedCellScore],
    centroids: dict[str, tuple[float, float]],
    start: tuple[float, float] = (0.0, 0.0),
    max_cells: int = 0,
    escalation_arm: str = "ground_first",
    solver: str = "auto",
) -> RoutePlan:
    """Plan a dispatch order over ranked cells.

    `solver="auto"` uses cuOpt when importable and the greedy stub otherwise. The
    chosen solver is recorded on the plan so a result can never be misattributed.
    """
    if escalation_arm not in ESCALATION_ARMS:
        raise ValueError(
            f"escalation_arm={escalation_arm!r} not in {ESCALATION_ARMS}. Both arms "
            "exist because the ordering is an assumption to measure, not a default."
        )
    used = "greedy-stub"
    if solver in ("auto", "cuopt"):
        try:  # pragma: no cover — cuOpt is not installed on this box
            import cuopt  # noqa: F401

            used = "cuopt"
        except ImportError:
            if solver == "cuopt":
                raise RuntimeError(
                    "solver='cuopt' requested but cuOpt is not installed. Refusing "
                    "to silently fall back — a greedy result labelled cuopt would "
                    "be a false provenance."
                ) from None
    order, travel, dropped = _greedy_route(cells, centroids, start, max_cells)
    by_id = {c.cell_id: c for c in cells}
    return RoutePlan(
        cell_order=tuple(order),
        solver=used,
        travel_m=travel,
        suspicion=sum(by_id[c].posterior for c in order),
        escalation_arm=escalation_arm,
        dropped=tuple(dropped),
    )


@dataclass
class DispatchConfig:
    """Config for the prioritisation layer. `enabled=False` is the identity."""

    enabled: bool = False
    max_cells: int = 0
    min_anomaly: float = 0.0
    escalation_arm: str = "ground_first"
    solver: str = "auto"
    irradiance: float = 1.0
    #: 0 = one cell per table (see `schema.pv_module.cell_for_panel`).
    modules_per_cell: int = 0

    @classmethod
    def from_mission_cfg(cls, mission_cfg: dict) -> "DispatchConfig":
        d = (mission_cfg or {}).get("grid_dispatch", {}) or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            max_cells=int(d.get("max_cells", 0)),
            min_anomaly=float(d.get("min_anomaly", 0.0)),
            escalation_arm=str(d.get("escalation_arm", "ground_first")),
            solver=str(d.get("solver", "auto")),
            irradiance=float(d.get("irradiance", 1.0)),
            modules_per_cell=int(d.get("modules_per_cell", 0)),
        )


@dataclass
class DispatchResult:
    """What the layer did, for the run record."""

    plan: RoutePlan | None = None
    n_targets_in: int = 0
    n_targets_out: int = 0
    enabled: bool = False
    reason: str = ""
    cells: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "enabled": self.enabled,
            "reason": self.reason,
            # Stamped even when disabled, so no run record is ambiguous about
            # whether its ordering came from a simulated prior.
            "scada_source": "simulated" if self.enabled else "none",
            "n_targets_in": self.n_targets_in,
            "n_targets_out": self.n_targets_out,
            "plan": self.plan.to_dict() if self.plan else None,
            "cells": self.cells,
        }
        if self.enabled:
            # A label alone ("simulated") is too easy to read as a detail of the
            # plumbing. Whenever a ranking actually influenced the run, the record
            # states in words that the prior is circular by construction.
            out["caveat"] = SIMULATED_CAVEAT
        return out


def order_targets(
    targets: list,
    records_by_panel: dict,
    cfg: DispatchConfig,
    centroids_by_cell: dict | None = None,
) -> tuple[list, DispatchResult]:
    """Reorder `targets` suspicion-first, or return them UNTOUCHED when disabled.

    ⚠ **The disabled path is the identity and must stay that way.** It returns
    `targets` itself — same object, same order — and constructs no plan. That is
    what makes "turn the ranker off and the mission behaves exactly as it does
    today" a testable property rather than a claim.

    Targets whose panel has no `cell_id`, or whose cell did not make the plan, are
    appended in their ORIGINAL relative order after the ranked ones, so enabling
    the layer re-prioritises but never silently drops a panel from the sweep.
    """
    if not cfg.enabled:
        return targets, DispatchResult(
            enabled=False,
            reason="grid_dispatch disabled — layout order preserved byte-for-byte",
            n_targets_in=len(targets),
            n_targets_out=len(targets),
        )

    from solar_twin.kpi import simulated_scada as scada

    records = [records_by_panel[t.panel_id] for t in targets if t.panel_id in records_by_panel]
    scores = scada.rank_cells_simulated(
        records, irradiance=cfg.irradiance, min_anomaly=cfg.min_anomaly
    )
    if not scores:
        return targets, DispatchResult(
            enabled=True,
            reason=(
                "no cells scored — stage has no grid:id (built before the grid "
                "namespace) or every cell was below min_anomaly; fell back to "
                "layout order"
            ),
            n_targets_in=len(targets),
            n_targets_out=len(targets),
        )

    centroids = centroids_by_cell or _centroids_from_targets(targets, records_by_panel)
    plan = plan_route(
        scores,
        centroids,
        max_cells=cfg.max_cells,
        escalation_arm=cfg.escalation_arm,
        solver=cfg.solver,
    )

    rank = {cid: i for i, cid in enumerate(plan.cell_order)}
    ranked, leftover = [], []
    for t in targets:
        rec = records_by_panel.get(t.panel_id)
        cid = getattr(rec, "cell_id", "") if rec else ""
        (ranked if cid in rank else leftover).append(t)
    # Stable within a cell: sort ONLY by cell rank, so panel order inside a cell is
    # the layout's, not an arbitrary re-shuffle.
    ranked.sort(key=lambda t: rank[records_by_panel[t.panel_id].cell_id])
    out = ranked + leftover
    return out, DispatchResult(
        plan=plan,
        enabled=True,
        reason=f"ranked {len(rank)} cells by SIMULATED PR anomaly ({plan.solver})",
        n_targets_in=len(targets),
        n_targets_out=len(out),
        cells=[s.to_dict() for s in scores],
    )


def _centroids_from_targets(targets: list, records_by_panel: dict) -> dict:
    """Cell centroid from the mean of its panels' approach waypoints.

    Reads `control.base.Waypoint`'s real shape — flat `.x` / `.y` floats, NOT a
    `.position` sequence. Found by running the layer against the real 560-panel
    Khavda block: the unit tests used a hand-rolled stand-in that invented
    `.position`, so they passed while this raised `AttributeError` on real
    targets. `_wp_xy` is now the single place that knows the waypoint's shape.
    """
    acc: dict[str, list[float]] = {}
    for t in targets:
        rec = records_by_panel.get(t.panel_id)
        cid = getattr(rec, "cell_id", "") if rec else ""
        if not cid:
            continue
        xy = _wp_xy(getattr(t, "approach", None))
        if xy is None:
            continue
        a = acc.setdefault(cid, [0.0, 0.0, 0.0])
        a[0] += xy[0]
        a[1] += xy[1]
        a[2] += 1
    return {c: (a[0] / a[2], a[1] / a[2]) for c, a in acc.items() if a[2]}


def _wp_xy(wp) -> tuple[float, float] | None:
    """Planar coordinates of a waypoint, tolerant of both shapes.

    `Waypoint` is `x/y/z/yaw` floats. The `.position` fallback exists because
    other waypoint-ish objects in the codebase (and test doubles) carry a
    sequence instead, and a centroid helper should not be the thing that breaks
    when handed one.
    """
    if wp is None:
        return None
    if hasattr(wp, "x") and hasattr(wp, "y"):
        return float(wp.x), float(wp.y)
    pos = getattr(wp, "position", None)
    if pos is not None and len(pos) >= 2:
        return float(pos[0]), float(pos[1])
    return None
