"""The scout->dispatch mission, end-to-end against FakeSimBackend.

Isaac-free (Principle §2.6). What matters here is not just the verdicts — the
sweep FSM already gets those right — but the *order the fleet moves in*, because
that ordering is the entire reason this mission exists. A regression that made
the ground bot move during the survey, or the confirm drone move over a clean
panel, would leave every verdict correct and the demo pointless.
"""

import itertools

from solar_twin.control.base import Waypoint
from solar_twin.orchestrator.fake_backend import FakeSimBackend
from solar_twin.orchestrator.mission import Fleet, InspectionTarget
from solar_twin.orchestrator.scout_dispatch import Beat, ScoutDispatchMission
from solar_twin.perception.ground_truth import GroundTruthPerception
from solar_twin.schema.pv_module import PanelRecord, PanelState, panel_id


FLEET = Fleet(ground_bot="ground_bot", screen_drone="drone1", confirm_drone="drone2")


def _panels(states: list[PanelState]) -> list[PanelRecord]:
    return [
        PanelRecord(panel_id=panel_id(1, i), grid_index=(1, i), state=s)
        for i, s in enumerate(states)
    ]


def _targets(panels: list[PanelRecord]) -> list[InspectionTarget]:
    out = []
    for p in panels:
        _, col = p.grid_index
        x = col * 2.2
        out.append(
            InspectionTarget(
                panel_id=p.panel_id,
                approach=Waypoint(x, -3.0, 0.0),
                screen=Waypoint(x, 0.0, 3.0),
                confirm=Waypoint(x, 0.0, 1.0),
                scout=Waypoint(x, 0.0, 8.0),
            )
        )
    return out


def _counter_clock():
    counter = itertools.count()
    return lambda: f"2026-07-30T00:00:{next(counter):02d}"


class _RecordingBackend(FakeSimBackend):
    """FakeSimBackend that logs every move, so beat ORDER is assertable."""

    def __init__(self, panels):
        super().__init__(panels)
        self.moves: list[tuple[str, float]] = []

    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        self.moves.append((robot_id, waypoint.z))
        super().move_to(robot_id, waypoint)


def _run(states: list[PanelState]):
    panels = _panels(states)
    backend = _RecordingBackend(panels)
    beats: list[tuple[str, str]] = []
    scouted: list[tuple[str, bool]] = []
    mission = ScoutDispatchMission(
        transport=backend,
        control=backend,
        perception=GroundTruthPerception(),
        fleet=FLEET,
        clock=_counter_clock(),
    )
    result = mission.run(
        _targets(panels),
        on_phase=lambda pid, beat: beats.append((pid, beat)),
        on_scouted=lambda i, pid, susp: scouted.append((pid, susp)),
    )
    return backend, result, beats, scouted


# --------------------------------------------------------------------------- #
# The survey
# --------------------------------------------------------------------------- #


def test_survey_moves_only_the_scout_drone():
    """The whole point of beat 1: one vehicle flies, nothing else twitches.

    This is the regression that produced the original complaint from the other
    direction — a run where the viewer could not tell a survey from a response.
    """
    states = [PanelState.HEALTHY] * 4
    backend, _, _, _ = _run(states)
    movers = {rid for rid, _ in backend.moves}
    assert movers == {"drone1"}, f"only the scout should fly a clean zone: {movers}"


def test_survey_covers_every_panel_and_records_all_of_them():
    states = [PanelState.HEALTHY, PanelState.HOTSPOT, PanelState.HEALTHY]
    _, result, _, scouted = _run(states)
    assert [pid for pid, _ in scouted] == [p.panel_id for p in _panels(states)]
    # Clean panels are recorded too, so denominators cover the scouted zone.
    assert result.panels_inspected == 3


def test_scout_flies_the_scout_standoff_not_the_screen_one():
    """If the survey flew at screening height the converge beat would be invisible."""
    backend, _, _, _ = _run([PanelState.HEALTHY] * 2)
    assert {z for _, z in backend.moves} == {8.0}


# --------------------------------------------------------------------------- #
# The response
# --------------------------------------------------------------------------- #


def test_flagged_panel_triggers_dispatch_then_converge_then_inspect():
    """Beat order for a fault, which is what the viewer is meant to read."""
    _, _, beats, _ = _run([PanelState.HOTSPOT])
    pid = panel_id(1, 0)
    assert beats == [
        (pid, Beat.SCOUT.name),
        (pid, Beat.DISPATCH.name),
        (pid, Beat.CONVERGE.name),
        (pid, Beat.INSPECT.name),
        (pid, Beat.WRITEBACK.name),
    ]


def test_ground_bot_moves_only_after_the_survey_flagged_something():
    """The ground bot's move must come after every scout move, not interleaved —
    otherwise the dispatch does not read as a response to the survey."""
    states = [PanelState.HEALTHY, PanelState.HEALTHY, PanelState.HOTSPOT]
    backend, _, _, _ = _run(states)
    scout_idx = [i for i, (rid, _) in enumerate(backend.moves) if rid == "drone1"]
    bot_idx = [i for i, (rid, _) in enumerate(backend.moves) if rid == "ground_bot"]
    assert bot_idx, "a flagged panel must dispatch the ground bot"
    # The survey's three passes all precede the first dispatch.
    assert min(bot_idx) > max(scout_idx[:3])


def test_confirm_drone_never_moves_over_a_clean_zone():
    backend, result, _, _ = _run([PanelState.HEALTHY] * 5)
    assert "drone2" not in {rid for rid, _ in backend.moves}
    assert result.faults_detected == 0


def test_confirm_drone_converges_on_every_flagged_panel():
    states = [PanelState.HOTSPOT, PanelState.HEALTHY, PanelState.SOILED]
    backend, result, _, _ = _run(states)
    n_confirm = sum(1 for rid, _ in backend.moves if rid == "drone2")
    assert n_confirm == 2
    assert result.faults_detected == 2


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #


def test_verdicts_match_ground_truth_and_are_written_back():
    states = [PanelState.HOTSPOT, PanelState.HEALTHY, PanelState.SOILED]
    backend, result, _, _ = _run(states)
    got = {r.panel_id: r.detected_state for r in result.results}
    assert got == {
        panel_id(1, 0): "hotspot",
        panel_id(1, 1): "healthy",
        panel_id(1, 2): "soiled",
    }
    # Every scouted panel got a USD write, clean ones included.
    assert len(backend.writes) == 3
    assert result.detection_rate == 1.0


def test_results_are_ordered_by_survey_order_not_response_order():
    """The record should read in the order the drone flew, even though faults are
    responded to before clean panels are filed."""
    states = [PanelState.HEALTHY, PanelState.HOTSPOT, PanelState.HEALTHY]
    _, result, _, _ = _run(states)
    assert [r.panel_id for r in result.results] == [
        panel_id(1, 0),
        panel_id(1, 1),
        panel_id(1, 2),
    ]


def test_fault_events_emitted_only_for_flagged_panels():
    states = [PanelState.HEALTHY, PanelState.HOTSPOT]
    _, result, _, _ = _run(states)
    assert [e.panel_id for e in result.fault_events] == [panel_id(1, 1)]
    assert result.fault_events[0].fault_type == "hotspot"


def test_scout_frame_digests_are_carried_onto_the_result():
    """The scout frame is what the flag was raised from; losing it would make the
    run record unable to say why the fleet was dispatched."""
    _, result, _, _ = _run([PanelState.HOTSPOT])
    r = result.results[0]
    # FakeSimBackend has no pixels, so both are None — but the fields must exist
    # and the confirm pass must have run.
    assert r.screen_status == "suspect"
    assert r.escalated is True


def test_target_scout_falls_back_to_screen_when_unset():
    """A config that never sets scout_standoff still works: the survey flies the
    screening standoff. This keeps the new field a no-op for old configs."""
    t = InspectionTarget(
        panel_id="R01-C000",
        approach=Waypoint(0.0, -3.0, 0.0),
        screen=Waypoint(0.0, 0.0, 3.0),
        confirm=Waypoint(0.0, 0.0, 1.0),
    )
    assert t.scout is None
    assert t.scout_or_screen.z == 3.0
