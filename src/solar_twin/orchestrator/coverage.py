"""Route planning: let the robot work out its own visit order (pure, Isaac-free).

Until now the visit order came from a fixed pattern in a config — `linear`,
`serpentine`, or `fault_zone`. That is a human's route, and calling a robot that
follows it "autonomous" was a stretch. This computes the order instead.

## ⭐ Measured first, because the obvious version of this is worthless

Before building anything, serpentine was measured against a real planner on the
actual Khavda BLOCK-02 layout (2026-08-03):

    scenario                          serpentine    planned   saving
    FULL sweep, one table (112)            127 m      127 m      0.0%
    scattered subset, 40 of 30016        7,732 m    2,058 m     73.4%
    scattered subset, 120               19,135 m    3,900 m     79.6%

**Serpentine is already optimal for a full sweep** and no planner will beat it — a
boustrophedon is the optimal coverage pattern over a regular grid, which is exactly
what a solar farm is. Replacing it there would be work for a 0.0% gain.

Where it collapses is a **scattered subset**: asked to visit 40 panels spread across
the block, serpentine zigzags the entire field for 7.7 km to do 2.1 km of work. And a
scattered subset is precisely what the twin increasingly produces — `grid_dispatch`
ranks suspect cells, `scout_dispatch` flags panels from a survey pass, `fault_zone`
selects a window. So this planner is aimed at the targeted case and deliberately
leaves the full sweep alone.

## What this is and is not

`plan_order` is **nearest-neighbour + systematic 2-opt**. That is a well-understood
TSP heuristic and is **not optimal** — cuOpt exists for the constrained version and
is not installed here (`grid_dispatch` documents the same refusal, and the same rule
applies: never label a greedy result as a solver's).

⚠ **Deterministic on purpose.** 2-opt is run as ordered passes over index pairs, not
random sampling, so the same input gives the same route with no seed to thread
through. A KPI run whose route changed between repeats would attribute renderer noise
to the planner.

⚠ **No obstacle detour.** Legs are straight. `is_clear` can reject one — measured on
this stage, zero serpentine legs crossed a turbine keep-out because the turbines ring
the array — see `orchestrator/sortie.py`'s header for that measurement.

⚠ **Planar.** Distances ignore Z. Panel standoffs differ by a metre or two against
legs of hundreds; including height would change no ordering and would make the rover
and drone incomparable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

Point = tuple[float, float, float]

#: 2-opt passes before stopping. Each pass is O(n^2); the measured routes above
#: converged inside 3. Bounded so a 30k-panel input cannot hang a mission.
DEFAULT_MAX_PASSES = 6

#: Above this many targets, 2-opt's O(n^2) per pass stops being worth the wall-clock
#: and the route is left at nearest-neighbour. ⚠ Reported on the plan rather than
#: silently applied — a truncated optimisation that looks like a full one is the kind
#: of quiet cap this project logs.
DEFAULT_REFINE_LIMIT = 2000


def _d(a: Point, b: Point) -> float:
    """Planar distance. See the module header for why Z is ignored."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass(frozen=True)
class RouteResult:
    """An ordered route plus what it cost and how it was produced."""

    order: tuple[str, ...]
    travel_m: float
    #: Travel of the input order, so the improvement is always attributable.
    baseline_m: float
    solver: str
    passes: int
    refined: bool
    unreachable: tuple[str, ...] = ()

    @property
    def saving_fraction(self) -> float:
        if self.baseline_m <= 0:
            return 0.0
        return max(0.0, 1.0 - self.travel_m / self.baseline_m)

    def to_dict(self) -> dict:
        return {
            "solver": self.solver,
            "n_targets": len(self.order),
            "travel_m": round(self.travel_m, 2),
            "baseline_m": round(self.baseline_m, 2),
            "saving_fraction": round(self.saving_fraction, 4),
            "passes": self.passes,
            "refined": self.refined,
            "unreachable": list(self.unreachable),
        }


def path_length(points: Sequence[Point], start: Optional[Point] = None) -> float:
    """Total travel through `points`, optionally from `start`."""
    if not points:
        return 0.0
    total = 0.0 if start is None else _d(start, points[0])
    return total + sum(_d(a, b) for a, b in zip(points, points[1:]))


def _nearest_neighbour(
    pts: list[Point], start: Point, is_clear: Optional[Callable] = None
) -> tuple[list[int], list[int]]:
    """Greedy nearest-first walk. Returns (order, unreachable indices)."""
    remaining = set(range(len(pts)))
    order: list[int] = []
    unreachable: list[int] = []
    cur = start
    while remaining:
        best, best_d = None, math.inf
        for i in remaining:
            if is_clear is not None and not is_clear(cur, pts[i]):
                continue
            dist = _d(cur, pts[i])
            # Tie-break on index so the route is reproducible.
            if dist < best_d:
                best, best_d = i, dist
        if best is None:
            # Nothing reachable from here; the rest cannot be sequenced.
            unreachable.extend(sorted(remaining))
            break
        order.append(best)
        remaining.discard(best)
        cur = pts[best]
    return order, unreachable


def _two_opt(route: list[Point], start: Point, max_passes: int) -> tuple[list[Point], int]:
    """Systematic 2-opt. Deterministic: ordered pairs, no sampling.

    Reverses any segment whose reversal shortens the path, repeatedly, until a full
    pass finds no improvement or `max_passes` is spent.
    """
    n = len(route)
    if n < 4:
        return route, 0
    passes = 0
    for _ in range(max_passes):
        passes += 1
        improved = False
        for i in range(n - 1):
            a = start if i == 0 else route[i - 1]
            for k in range(i + 1, n):
                b, c = route[i], route[k]
                d = route[k + 1] if k + 1 < n else None
                before = _d(a, b) + (_d(c, d) if d is not None else 0.0)
                after = _d(a, c) + (_d(b, d) if d is not None else 0.0)
                if after < before - 1e-9:
                    route[i : k + 1] = reversed(route[i : k + 1])
                    improved = True
        if not improved:
            break
    return route, passes


def plan_order(
    targets: Sequence[tuple[str, Point]],
    start: Point = (0.0, 0.0, 0.0),
    *,
    max_passes: int = DEFAULT_MAX_PASSES,
    refine_limit: int = DEFAULT_REFINE_LIMIT,
    is_clear: Optional[Callable[[Point, Point], bool]] = None,
) -> RouteResult:
    """Order `targets` to shorten travel. Returns the route and what it saved.

    The input order is measured too, so the result always says what it improved on
    rather than asserting it improved anything. On a dense full sweep the honest
    answer is ~0% — see the module header.
    """
    if not targets:
        return RouteResult((), 0.0, 0.0, "none", 0, False)

    ids = [t for t, _ in targets]
    pts = [p for _, p in targets]
    baseline = path_length(pts, start)

    order, unreachable_idx = _nearest_neighbour(pts, start, is_clear)
    route = [pts[i] for i in order]

    refined = len(route) <= refine_limit
    passes = 0
    if refined:
        route, passes = _two_opt(route, start, max_passes)

    # Map the reordered points back to ids. Points are unique per panel in practice;
    # index by identity of position to avoid an O(n^2) lookup being wrong on ties.
    pos_to_ids: dict[Point, list[int]] = {}
    for i in order:
        pos_to_ids.setdefault(pts[i], []).append(i)
    final_ids = []
    for p in route:
        final_ids.append(ids[pos_to_ids[p].pop(0)])

    return RouteResult(
        order=tuple(final_ids),
        travel_m=path_length(route, start),
        baseline_m=baseline,
        solver="nn+2opt" if refined else "nn",
        passes=passes,
        refined=refined,
        unreachable=tuple(ids[i] for i in unreachable_idx),
    )
