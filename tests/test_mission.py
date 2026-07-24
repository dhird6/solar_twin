"""The escalation FSM, end-to-end against FakeSimBackend (no GPU, no Isaac).

This is the Principle §2.6 test: the entire mission brain runs and is asserted
without launching Isaac Sim.
"""

import itertools

import pytest

from solar_twin.control.base import Waypoint
from solar_twin.orchestrator.fake_backend import FakeSimBackend
from solar_twin.orchestrator.mission import (
    Fleet,
    InspectionTarget,
    Mission,
)
from solar_twin.perception.ground_truth import GroundTruthPerception
from solar_twin.schema.pv_module import PanelRecord, PanelState, panel_id


FLEET = Fleet(ground_bot="ground_bot", screen_drone="drone1", confirm_drone="drone2")


def _panels(states: list[PanelState]) -> list[PanelRecord]:
    return [
        PanelRecord(panel_id=panel_id(1, i), grid_index=(1, i), state=s)
        for i, s in enumerate(states)
    ]


def _targets(panels: list[PanelRecord]) -> list[InspectionTarget]:
    targets = []
    for p in panels:
        _, col = p.grid_index
        x = col * 2.2
        targets.append(
            InspectionTarget(
                panel_id=p.panel_id,
                approach=Waypoint(x, -3.0, 0.0),
                screen=Waypoint(x, 0.0, 3.0),
                confirm=Waypoint(x, 0.0, 1.0),
            )
        )
    return targets


def _counter_clock():
    counter = itertools.count()
    return lambda: f"2026-07-21T00:00:{next(counter):02d}"


def _run(states: list[PanelState]):
    panels = _panels(states)
    backend = FakeSimBackend(panels)
    mission = Mission(
        transport=backend,
        control=backend,
        perception=GroundTruthPerception(),
        fleet=FLEET,
        clock=_counter_clock(),
    )
    return backend, mission.run(_targets(panels))


def test_healthy_panel_not_escalated():
    backend, result = _run([PanelState.HEALTHY])
    r = result.results[0]
    assert r.screen_status == "clean"
    assert not r.escalated
    assert r.detected_state == "healthy"
    assert result.fault_events == []


def test_fault_escalates_and_writes_back():
    backend, result = _run([PanelState.HOTSPOT])
    r = result.results[0]
    assert r.escalated
    assert r.detected_state == "hotspot"
    assert r.injected_state == "hotspot"
    assert r.correct
    # Verdict written back onto the panel + log appended (§6.1).
    pid = panel_id(1, 0)
    stored = backend.panel(pid)
    assert stored.state is PanelState.HOTSPOT
    assert len(stored.inspection_log) == 1
    assert "hotspot" in stored.inspection_log[0]
    # One fault event emitted (the /mission/fault payload).
    assert len(result.fault_events) == 1
    assert result.fault_events[0].panel_id == pid


def test_mixed_row_detection_rate_is_one():
    states = [
        PanelState.HEALTHY,
        PanelState.HOTSPOT,
        PanelState.HEALTHY,
        PanelState.SOILED,
        PanelState.HEALTHY,
    ]
    backend, result = _run(states)
    assert result.panels_inspected == 5
    assert result.faults_detected == 2
    assert result.detection_rate == pytest.approx(1.0)
    assert {e.panel_id for e in result.fault_events} == {
        panel_id(1, 1),
        panel_id(1, 3),
    }


def test_confirm_drone_only_moves_on_suspicion():
    # Healthy: screen drone moves but confirm drone never gets a pose.
    backend, _ = _run([PanelState.HEALTHY])
    assert "drone1" in backend._poses
    assert "drone2" not in backend._poses

    # Fault: confirm drone is dispatched.
    backend2, _ = _run([PanelState.SOILED])
    assert "drone2" in backend2._poses


def test_steps_counted():
    backend, result = _run([PanelState.HEALTHY, PanelState.HOTSPOT])
    # 2 steps for healthy (advance+screen), 3 for fault (advance+screen+confirm).
    assert result.steps == 5


def test_false_fault_rate_counts_only_misread_healthy_panels():
    from solar_twin.orchestrator.mission import MissionResult, PanelResult

    def pr(pid, injected, detected):
        return PanelResult(pid, injected, "clean", detected != "healthy", detected, "")

    res = MissionResult(
        results=[
            pr("A", "healthy", "healthy"),   # healthy, correct
            pr("B", "healthy", "soiled"),    # healthy -> FALSE fault
            pr("C", "healthy", "hotspot"),   # healthy -> FALSE fault
            pr("D", "soiled", "healthy"),    # a real fault MISSED — not a false fault
        ]
    )
    # 2 of 3 healthy panels were misread; the soiled panel is excluded.
    assert res.false_fault_rate == pytest.approx(2 / 3)


def test_false_fault_rate_zero_when_all_healthy_correct():
    from solar_twin.orchestrator.mission import MissionResult, PanelResult

    res = MissionResult(
        results=[
            PanelResult("A", "healthy", "clean", False, "healthy", ""),
            PanelResult("B", "healthy", "clean", False, "healthy", ""),
        ]
    )
    assert res.false_fault_rate == 0.0
    # No healthy panels -> defined as 0.0, never a divide-by-zero.
    assert MissionResult(results=[]).false_fault_rate == 0.0
