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
    MissionResult,
    PanelResult,
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


def test_on_phase_narrates_every_phase_entry():
    """The demo video captions frames from this hook, so it must fire on entry
    (before the robot moves) and name the panel it is about."""
    panels = _panels([PanelState.SOILED, PanelState.HEALTHY])
    backend = FakeSimBackend(panels)
    mission = Mission(backend, backend, GroundTruthPerception(), FLEET)
    seen: list[tuple[str, str]] = []
    mission.run(_targets(panels), on_phase=lambda pid, ph: seen.append((pid, ph)))

    suspect, healthy = panels[0].panel_id, panels[1].panel_id
    # The suspect panel walks the full escalation; the healthy one skips CONFIRM.
    assert [ph for pid, ph in seen if pid == suspect] == [
        "ADVANCE", "SCREEN", "CONFIRM", "WRITEBACK",
    ]
    assert [ph for pid, ph in seen if pid == healthy] == ["ADVANCE", "SCREEN", "WRITEBACK"]
    # Panels are narrated in order, not interleaved.
    assert [pid for pid, _ in seen] == [suspect] * 4 + [healthy] * 3


def test_on_phase_is_optional():
    panels = _panels([PanelState.HEALTHY])
    backend = FakeSimBackend(panels)
    mission = Mission(backend, backend, GroundTruthPerception(), FLEET)
    assert mission.run(_targets(panels)).panels_inspected == 1


class TestDetectionRateFlattersAndRecallDoesNot:
    """⚠⚠ `detection_rate` (KPI-01) is ACCURACY over EVERY panel, healthy included.

    Measured over 20 archived Cosmos Reason runs on 2026-07-31: `nominal_calm_vlm`
    is 82.5% healthy and gates on `detection_rate_min: 0.80`, so a model that calls
    every panel healthy scores 0.825 and PASSES while detecting nothing. The null
    model clears that gate in 13 of the 20 runs, and in 3 of them it scores at or
    above what the real model managed.

    These pin the metrics that cannot be gamed that way, and pin the null baseline
    so a gate can always be compared against it.
    """

    @staticmethod
    def _res(pairs):
        """pairs = [(injected, detected), ...] -> a MissionResult."""
        return MissionResult(
            results=[
                PanelResult(
                    panel_id=f"R00-C{i:03d}",
                    injected_state=inj,
                    screen_status="suspect" if det != "healthy" else "clean",
                    escalated=det != "healthy",
                    detected_state=det,
                    note="",
                )
                for i, (inj, det) in enumerate(pairs)
            ]
        )

    def test_a_null_model_passes_the_gate_that_detection_rate_defines(self):
        """The finding, as an executable demonstration rather than a claim.

        33 healthy + 7 faulted, every panel called healthy: nothing is detected,
        yet `detection_rate` is 0.825 and clears the scenario's 0.80 gate.
        """
        pairs = [("healthy", "healthy")] * 33 + [("soiled", "healthy")] * 7
        r = self._res(pairs)

        assert r.detection_rate == pytest.approx(0.825)
        assert r.detection_rate > 0.80  # the real gate in nominal_calm_vlm.yaml

        # ...and the honest metrics correctly report that it found nothing.
        assert r.fault_recall == 0.0
        assert r.fault_flagged_rate == 0.0
        # The null baseline equals the score, which is the tell.
        assert r.healthy_fraction == pytest.approx(r.detection_rate)

    def test_recall_denominator_excludes_healthy_panels(self):
        """Healthy panels must not be able to inflate recall — that is the whole
        difference from `detection_rate`."""
        pairs = [("healthy", "healthy")] * 90 + [
            ("hotspot", "hotspot"),
            ("hotspot", "healthy"),
        ]
        r = self._res(pairs)
        assert r.detection_rate == pytest.approx(91 / 92)  # flattering
        assert r.fault_recall == pytest.approx(0.5)  # honest

    def test_flagged_and_named_separate_sensitivity_from_discrimination(self):
        """A fault seen but MISLABELLED is flagged and not named. The gap between
        the two is taxonomy confusion, and it has a different fix from a miss."""
        pairs = [
            ("soiled", "hotspot"),  # noticed, wrong name
            ("soiled", "soiled"),  # noticed, right name
            ("hotspot", "healthy"),  # missed outright
            ("hotspot", "healthy"),  # missed outright
        ]
        r = self._res(pairs)
        assert r.fault_flagged_rate == pytest.approx(0.5)  # 2 of 4 noticed
        assert r.fault_recall == pytest.approx(0.25)  # 1 of 4 named

    def test_recall_by_state_shows_a_split_the_pooled_number_hides(self):
        """The measured shape: soiling is caught, hotspots are not. A pooled
        recall of 0.5 here would report neither."""
        pairs = [("soiled", "soiled")] * 4 + [("hotspot", "healthy")] * 4
        by = self._res(pairs).recall_by_state()

        assert by["soiled"]["recall"] == pytest.approx(1.0)
        assert by["hotspot"]["recall"] == pytest.approx(0.0)
        assert by["soiled"]["n"] == 4 and by["hotspot"]["n"] == 4

    def test_a_run_with_no_seeded_faults_reports_no_recall(self):
        """A scenario with nothing to find has no recall to report -- it must not
        read as a perfect score. `khavda_selfshade` is exactly this: all-healthy
        by design, and it reported detection_rate 1.00 across all 5 repeats."""
        r = self._res([("healthy", "healthy")] * 10)
        assert r.detection_rate == pytest.approx(1.0)  # ...which means nothing here
        assert r.fault_recall == 0.0
        assert r.fault_flagged_rate == 0.0
        assert r.recall_by_state() == {}
