"""Adaptive inspection — findings changing where the robot goes (Isaac-free)."""

from __future__ import annotations

import pytest

from solar_twin.orchestrator.adaptive import (
    EXPANSION,
    AdaptivePlanner,
    ExpansionRule,
)
from solar_twin.schema.pv_module import PanelState, panel_id, parse_panel_id

ORIGIN = (0.0, 0.0, 0.0)


def _catalog(rows=12, cols=12, pitch=5.0):
    return {
        panel_id(r, c): (c * pitch, r * pitch, 1.0)
        for r in range(rows)
        for c in range(cols)
    }


# --------------------------------------------------------------------------- #
# panel-id round trip — expansion derives neighbours from it, so a mis-parse
# would send the robot to the wrong panels.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("row,col", [(0, 0), (12, 47), (99, 999), (272, 5)])
def test_panel_id_round_trips(row, col):
    assert parse_panel_id(panel_id(row, col)) == (row, col)


def test_a_row_above_99_still_parses():
    """The full plot has 6,213 tables, so >99 rows is the normal case: `{row:02d}`
    is a MINIMUM width and a fixed-width slice would mis-parse R272."""
    assert parse_panel_id("R272-C047") == (272, 47)


@pytest.mark.parametrize("bad", ["", "R12", "C047", "R12_C047", "panel", "R-C"])
def test_a_malformed_panel_id_raises(bad):
    with pytest.raises(ValueError):
        parse_panel_id(bad)


# --------------------------------------------------------------------------- #
# Expansion rules — the domain claim, encoded so it can be argued with.
# --------------------------------------------------------------------------- #


def test_every_panel_state_has_an_expansion_rule():
    """A new fault type must not silently inherit someone else's clustering."""
    missing = [s for s in PanelState if s not in EXPANSION]
    assert not missing, f"states with no expansion rule: {missing}"


def test_module_local_faults_expand_less_than_soiling():
    """Soiling drifts; a crack does not spread to the next module."""
    assert EXPANSION[PanelState.SOILED].radius > EXPANSION[PanelState.HOTSPOT].radius
    assert EXPANSION[PanelState.CRACK].radius == 0


def test_electrical_faults_expand_along_the_row_only():
    """A series string runs along the torque tube, so a square patch is wrong."""
    for state in (PanelState.STRING_DROPOUT, PanelState.DIODE_FAULT):
        assert EXPANSION[state].along_row_only, f"{state} should follow the string"


def test_healthy_and_unknown_never_expand():
    assert EXPANSION[PanelState.HEALTHY].radius == 0
    assert EXPANSION[PanelState.UNKNOWN].radius == 0


def test_neighbours_are_bounded_by_the_catalog():
    """A panel at the block edge has no neighbours off the stage."""
    p = AdaptivePlanner(_catalog(5, 5))
    corner = p.neighbours_of(panel_id(0, 0), ExpansionRule(2, False))
    assert all(n in p.catalog for n in corner)
    assert panel_id(0, 0) not in corner


def test_along_row_only_stays_on_its_row():
    p = AdaptivePlanner(_catalog())
    got = p.neighbours_of(panel_id(5, 5), ExpansionRule(3, along_row_only=True))
    assert all(parse_panel_id(n)[0] == 5 for n in got)


def test_a_malformed_id_costs_an_expansion_not_the_mission():
    p = AdaptivePlanner(_catalog())
    assert p.neighbours_of("garbage", ExpansionRule(2)) == []


# --------------------------------------------------------------------------- #
# ⭐ The loop itself.
# --------------------------------------------------------------------------- #


def test_a_fault_adds_its_neighbours_to_the_queue():
    p = AdaptivePlanner(_catalog())
    out = p.revise([panel_id(9, 9)], panel_id(5, 5), PanelState.SOILED, ORIGIN)
    assert len(out) > 1
    assert p.stats.triggered, "no panel recorded as triggered"


def test_a_healthy_verdict_adds_nothing():
    p = AdaptivePlanner(_catalog())
    before = [panel_id(9, 9), panel_id(9, 8)]
    out = p.revise(list(before), panel_id(5, 5), PanelState.HEALTHY, ORIGIN)
    assert sorted(out) == sorted(before)
    assert not p.stats.triggered


def test_an_abstention_never_drives_the_robot():
    """⭐ UNKNOWN is 'we did not get an answer', not 'there is a fault'. Expanding on
    it would let a model that fails to answer send the robot round the farm."""
    p = AdaptivePlanner(_catalog())
    out = p.revise([panel_id(9, 9)], panel_id(5, 5), PanelState.UNKNOWN, ORIGIN)
    assert out == [panel_id(9, 9)]
    assert p.stats.dropped_unknown == 1
    assert not p.stats.triggered


def test_provenance_records_which_finding_added_each_panel():
    """A run record must be able to answer 'why did the robot go there?'."""
    p = AdaptivePlanner(_catalog())
    p.revise([], panel_id(5, 5), PanelState.SOILED, ORIGIN)
    assert set(p.stats.triggered.values()) == {panel_id(5, 5)}


def test_a_visited_panel_is_never_requeued():
    p = AdaptivePlanner(_catalog())
    q = p.revise([panel_id(5, 6)], panel_id(5, 5), PanelState.SOILED, ORIGIN)
    q = p.revise(q, panel_id(5, 6), PanelState.SOILED, ORIGIN)
    assert panel_id(5, 5) not in q and panel_id(5, 6) not in q


def test_expansion_is_capped_so_the_robot_can_stop():
    """⭐ An autonomous robot that cannot stop is worse than one that never started."""
    p = AdaptivePlanner(_catalog(20, 20), max_extra=5)
    q = []
    for c in range(10):
        q = p.revise(q, panel_id(10, c), PanelState.SOILED, ORIGIN)
    assert len(p.stats.triggered) <= 5


def test_no_duplicates_are_ever_queued():
    p = AdaptivePlanner(_catalog())
    q = p.revise([], panel_id(5, 5), PanelState.SOILED, ORIGIN)
    q = p.revise(q, panel_id(5, 6), PanelState.SOILED, ORIGIN)
    assert len(q) == len(set(q))


def test_the_queue_is_reordered_from_the_robots_actual_position():
    """⭐ Position is an INPUT. When SLAM lands it becomes an estimated pose and this
    module does not change — so the reorder must genuinely depend on it."""
    cat = _catalog(10, 10)
    near_far = [panel_id(9, 9), panel_id(0, 1)]
    a = AdaptivePlanner(cat).revise(list(near_far), panel_id(0, 0),
                                    PanelState.HEALTHY, (0.0, 0.0, 0.0))
    b = AdaptivePlanner(cat).revise(list(near_far), panel_id(0, 0),
                                    PanelState.HEALTHY, (45.0, 45.0, 0.0))
    assert a[0] != b[0], "reorder ignored where the robot actually is"


def test_reorder_can_be_switched_off():
    cat = _catalog()
    q = [panel_id(9, 9), panel_id(0, 1)]
    out = AdaptivePlanner(cat, reorder=False).revise(
        list(q), panel_id(0, 0), PanelState.HEALTHY, ORIGIN
    )
    assert out == q


def test_a_panel_missing_from_the_catalog_is_kept_not_dropped():
    p = AdaptivePlanner(_catalog())
    out = p.revise(["NOT-IN-CATALOG"], panel_id(5, 5), PanelState.HEALTHY, ORIGIN)
    assert "NOT-IN-CATALOG" in out


def test_budget_check_can_trim_the_queue_and_is_counted():
    p = AdaptivePlanner(_catalog(), budget_check=lambda q, pos: list(q)[:1])
    out = p.revise([panel_id(1, 1), panel_id(2, 2), panel_id(3, 3)],
                   panel_id(0, 0), PanelState.HEALTHY, ORIGIN, charge_remaining_s=10.0)
    assert len(out) == 1
    assert p.stats.skipped_no_budget == 2


def test_stats_serialise_for_the_run_record():
    import json

    p = AdaptivePlanner(_catalog())
    p.revise([], panel_id(5, 5), PanelState.SOILED, ORIGIN)
    d = p.stats.to_dict()
    json.loads(json.dumps(d))
    assert d["n_triggered"] == len(p.stats.triggered)


# --------------------------------------------------------------------------- #
# ⭐ Mission.run's identity guarantee: no planner == the original fixed list.
# --------------------------------------------------------------------------- #


def _mini_mission():
    from solar_twin.control.base import Waypoint
    from solar_twin.orchestrator.fake_backend import FakeSimBackend
    from solar_twin.orchestrator.mission import Fleet, InspectionTarget, Mission
    from solar_twin.perception.ground_truth import GroundTruthPerception
    from solar_twin.schema.pv_module import PanelRecord

    records, targets = [], []
    for c in range(6):
        pid = panel_id(0, c)
        state = PanelState.SOILED if c == 2 else PanelState.HEALTHY
        records.append(PanelRecord(panel_id=pid, grid_index=(0, c), state=state))
        targets.append(
            InspectionTarget(
                panel_id=pid,
                approach=Waypoint(c * 5.0, -2.0, 0.0),
                screen=Waypoint(c * 5.0, 0.0, 4.0),
                confirm=Waypoint(c * 5.0, 0.0, 2.0),
            )
        )
    be = FakeSimBackend(records)
    fleet = Fleet(ground_bot="bot", screen_drone="d1", confirm_drone="d2")
    return Mission(be, be, GroundTruthPerception(), fleet), targets


def test_no_planner_visits_exactly_the_targets_given():
    mission, targets = _mini_mission()
    result = mission.run(list(targets))
    assert [r.panel_id for r in result.results] == [t.panel_id for t in targets]


def test_no_planner_matches_the_pre_adaptive_behaviour_exactly():
    """Two runs with no planner must agree panel-for-panel, verdict-for-verdict."""
    a, targets = _mini_mission()
    b, _ = _mini_mission()
    ra, rb = a.run(list(targets)), b.run(list(targets))
    assert [(r.panel_id, r.detected_state) for r in ra.results] == [
        (r.panel_id, r.detected_state) for r in rb.results
    ]


def test_a_planner_that_adds_nothing_leaves_the_sweep_alone():
    """An expansion table of all-zeros must reproduce the fixed sweep's panel set."""
    mission, targets = _mini_mission()
    flat = {s: ExpansionRule(0) for s in PanelState}
    planner = AdaptivePlanner(
        {t.panel_id: (t.confirm.x, t.confirm.y, t.confirm.z) for t in targets},
        expansion=flat,
        reorder=False,
    )
    result = mission.run(list(targets), planner=planner)
    assert sorted(r.panel_id for r in result.results) == sorted(
        t.panel_id for t in targets
    )


def test_the_loop_cannot_run_away_without_a_target_factory():
    """Expansion names panels the sweep never listed; with no factory to build
    waypoints they must be skipped, not spun on forever."""
    mission, targets = _mini_mission()
    planner = AdaptivePlanner(
        {panel_id(0, c): (c * 5.0, 0.0, 2.0) for c in range(40)}, max_extra=20
    )
    result = mission.run(list(targets), planner=planner)
    assert len(result.results) <= len(targets) + 20 + 1
