"""Route planning — the robot computing its own visit order (Isaac-free)."""

from __future__ import annotations

import math

import pytest

from solar_twin.orchestrator.coverage import (
    RouteResult,
    _two_opt,
    path_length,
    plan_order,
)

BASE = (0.0, 0.0, 0.0)


def _grid(rows: int, cols: int, pitch: float = 5.0):
    return [
        (f"R{r:02d}-C{c:03d}", (c * pitch, r * pitch, 0.0))
        for r in range(rows)
        for c in range(cols)
    ]


# --------------------------------------------------------------------------- #
# ⭐ The measurement that justifies the module — and its honest half.
# --------------------------------------------------------------------------- #


def test_a_dense_row_sweep_cannot_be_improved():
    """Serpentine is ALREADY optimal over a regular grid (boustrophedon).

    If this ever starts reporting a big saving, the baseline is being computed
    wrong — not the planner getting cleverer.
    """
    row = [(f"P{i:03d}", (i * 1.15, 0.0, 0.0)) for i in range(60)]
    r = plan_order(row, BASE)
    assert r.saving_fraction < 0.02, (
        f"claimed {r.saving_fraction:.1%} on an already-optimal line — check the baseline"
    )


def test_a_scattered_subset_is_improved_a_lot():
    """The case this exists for: visiting 40 panels spread over a block."""
    import random

    rng = random.Random(7)
    cells = _grid(30, 30, pitch=6.0)
    subset = [cells[i] for i in sorted(rng.sample(range(len(cells)), 40))]
    r = plan_order(subset, BASE)
    assert r.saving_fraction > 0.3, f"only saved {r.saving_fraction:.1%} on a scatter"


def test_the_baseline_is_reported_so_a_saving_is_attributable():
    """A planner that only reports its own number cannot be checked."""
    r = plan_order(_grid(6, 6), BASE)
    assert r.baseline_m > 0
    assert r.travel_m <= r.baseline_m + 1e-6


# --------------------------------------------------------------------------- #
# Determinism — a route that changed between repeats would let renderer noise be
# attributed to the planner.
# --------------------------------------------------------------------------- #


def test_the_same_input_gives_the_same_route():
    targets = _grid(8, 8)
    assert plan_order(targets, BASE).order == plan_order(targets, BASE).order


def test_two_opt_uses_ordered_passes_not_sampling():
    """Called twice on the same route, it must produce the same result."""
    import copy

    route = [(float(i % 5) * 3, float(i // 5) * 3, 0.0) for i in range(20)]
    a, pa = _two_opt(copy.deepcopy(route), BASE, 6)
    b, pb = _two_opt(copy.deepcopy(route), BASE, 6)
    assert a == b and pa == pb


# --------------------------------------------------------------------------- #
# Correctness of the route itself.
# --------------------------------------------------------------------------- #


def test_every_target_is_visited_exactly_once():
    targets = _grid(7, 7)
    r = plan_order(targets, BASE)
    assert sorted(r.order) == sorted(t for t, _ in targets)
    assert len(r.order) == len(set(r.order))


def test_duplicate_positions_do_not_lose_a_target():
    """Two panels at the same point must both survive the id round-trip."""
    targets = [("A", (5.0, 5.0, 0.0)), ("B", (5.0, 5.0, 0.0)), ("C", (9.0, 1.0, 0.0))]
    r = plan_order(targets, BASE)
    assert sorted(r.order) == ["A", "B", "C"]


def test_reported_travel_matches_the_route_it_returns():
    """The headline number must be recomputable from the order — not asserted."""
    targets = _grid(5, 5)
    lookup = dict(targets)
    r = plan_order(targets, BASE)
    assert path_length([lookup[t] for t in r.order], BASE) == pytest.approx(r.travel_m)


def test_two_opt_never_lengthens_a_route():
    import copy

    route = [(math.cos(i) * 40, math.sin(i * 2.3) * 40, 0.0) for i in range(24)]
    before = path_length(route, BASE)
    after, _ = _two_opt(copy.deepcopy(route), BASE, 6)
    assert path_length(after, BASE) <= before + 1e-9


def test_an_empty_input_plans_nothing_rather_than_failing():
    r = plan_order([], BASE)
    assert r.order == () and r.travel_m == 0.0 and r.solver == "none"


def test_a_single_target_is_trivially_planned():
    r = plan_order([("only", (10.0, 0.0, 0.0))], BASE)
    assert r.order == ("only",)
    assert r.travel_m == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# Honest limits: the cap is reported, not silent.
# --------------------------------------------------------------------------- #


def test_a_large_input_skips_refinement_and_says_so():
    """A truncated optimisation that looks like a full one is the silent cap this
    project logs rather than hides."""
    big = _grid(40, 60)  # 2400 > DEFAULT_REFINE_LIMIT
    r = plan_order(big, BASE)
    assert r.refined is False
    assert r.solver == "nn", "an unrefined route must not claim to be 2-opt'd"


def test_a_small_input_is_refined_and_says_so():
    r = plan_order(_grid(5, 6), BASE)
    assert r.refined is True and r.solver == "nn+2opt"


def test_the_solver_label_is_never_cuopt():
    """grid_dispatch refuses to label a greedy result 'cuopt'; so does this."""
    assert plan_order(_grid(4, 4), BASE).solver in {"nn", "nn+2opt", "none"}


# --------------------------------------------------------------------------- #
# Keep-out hook.
# --------------------------------------------------------------------------- #


def test_a_leg_the_predicate_rejects_is_not_planned_through():
    banned = (50.0, 50.0, 0.0)
    targets = [("a", (5.0, 0.0, 0.0)), ("bad", banned), ("b", (10.0, 0.0, 0.0))]
    r = plan_order(targets, BASE, is_clear=lambda p, q: q != banned)
    assert "bad" not in r.order
    assert "bad" in r.unreachable


def test_a_predicate_blocking_everything_terminates():
    r = plan_order(_grid(3, 3), BASE, is_clear=lambda p, q: False)
    assert r.order == ()
    assert len(r.unreachable) == 9


# --------------------------------------------------------------------------- #
# Record shape.
# --------------------------------------------------------------------------- #


def test_result_serialises_for_the_run_record():
    import json

    d = plan_order(_grid(4, 4), BASE).to_dict()
    json.loads(json.dumps(d))
    assert 0.0 <= d["saving_fraction"] <= 1.0
    assert set(d) >= {"solver", "travel_m", "baseline_m", "saving_fraction", "refined"}


def test_saving_fraction_is_never_negative():
    r = RouteResult(("a",), travel_m=100.0, baseline_m=50.0, solver="nn",
                    passes=0, refined=False)
    assert r.saving_fraction == 0.0
