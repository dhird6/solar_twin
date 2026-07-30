"""Scout → dispatch → converge → inspect: the *watchable* mission.

`orchestrator/mission.py` walks one panel at a time — ground bot advances, drone
screens, second drone confirms only when the screen is suspect. That is the right
shape for a measurement (every panel gets identical treatment, so every
denominator is honest) and the wrong shape for watching: on a healthy stretch the
confirm drone never moves at all, and the fleet never travels *to* anything.
Measured on a 12-panel run of BLOCK-02: 0 of 12 escalated, so drone 2 sat still
for the entire run.

This module models what a real O&M crew does instead, in four visible beats:

1. **SCOUT** — one drone flies a high, wide pass over the whole zone and
   `assess()`es each panel. Nothing else moves. This is the survey.
2. **DISPATCH** — for a panel the scout flagged, the ground bot drives to it.
   One vehicle moving, with a destination the viewer has just been shown why.
3. **CONVERGE** — both drones come to the flagged panel and take station.
4. **INSPECT** — the close pass runs `diagnose()`, the verdict is written to the
   panel prim and a `FaultReport` is emitted.

Panels the scout calls clean are still recorded (with a healthy verdict) so the
run record covers everything that was looked at; they just do not trigger beats
2-4. That keeps `MissionResult`'s shape and every property on it meaningful.

⚠ **This is a demonstration mission, not a measurement.** The scout pass and the
close pass judge the *same* panel from two different standoffs, so a panel can be
inspected twice, and the zone it runs over is chosen to contain faults (see
`layout.route_sites`' fault-zone mode). Both facts change what `detection_rate`
and `false_fault_rate` mean relative to `SC-01`. Quote KPIs from the sweep FSM.

Pure-python: no Isaac import (golden rule / Do-NOT list). Drives the same three
interfaces, so it runs against `FakeSimBackend` in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

from solar_twin.control.base import RobotControl
from solar_twin.orchestrator.mission import (
    Fleet,
    InspectionTarget,
    MissionResult,
    PanelResult,
    _context,
    _default_clock,
)
from solar_twin.perception.base import (
    Diagnosis,
    Perception,
    Verdict,
    frame_digest,
    frame_thumbnail,
)
from solar_twin.schema.pv_module import FaultReport, PanelState, coerce_state
from solar_twin.transport.base import Transport


class Beat(Enum):
    """The four visible beats, plus the bookkeeping one.

    Named `Beat` rather than `Phase` deliberately: these are not the sweep FSM's
    phases and must not be compared against them.
    """

    SCOUT = auto()  # survey drone sweeps the zone, flags suspects
    DISPATCH = auto()  # ground bot drives to a flagged panel
    CONVERGE = auto()  # both drones take station over it
    INSPECT = auto()  # close pass -> diagnose
    WRITEBACK = auto()  # verdict onto the panel + fault event


#: Human-readable captions, for the video overlay and the live narration.
BEAT_LABELS: dict[str, str] = {
    "SCOUT": "survey sweep — looking for suspects",
    "DISPATCH": "ground bot dispatched to a flagged panel",
    "CONVERGE": "drones converging on the fault",
    "INSPECT": "close inspection pass",
    "WRITEBACK": "writing verdict to USD",
}


@dataclass
class ScoutHit:
    """One panel the survey flagged, and the frame it was flagged from."""

    target: InspectionTarget
    verdict: Verdict
    frame_sha: Optional[str]
    frame_thumb: Optional[str]
    injected_state: str


class ScoutDispatchMission:
    """Survey a zone, then send the fleet to what the survey found."""

    def __init__(
        self,
        transport: Transport,
        control: RobotControl,
        perception: Perception,
        fleet: Fleet,
        clock: Callable[[], str] = _default_clock,
    ):
        self.transport = transport
        self.control = control
        self.perception = perception
        self.fleet = fleet
        self.clock = clock

    # -- the survey --------------------------------------------------------- #

    def _scout(
        self,
        targets: list[InspectionTarget],
        on_beat: Optional[Callable[[str, str], None]],
        on_scouted: Optional[Callable[[int, str, bool], None]],
    ) -> tuple[list[ScoutHit], list[ScoutHit]]:
        """Fly the survey drone over every target; return (flagged, clean).

        Only the survey drone moves here. The ground bot and the confirm drone
        stay where they are, which is what makes the dispatch beat read as a
        *response* rather than as more of the same sweep.
        """
        flagged: list[ScoutHit] = []
        clean: list[ScoutHit] = []
        for i, target in enumerate(targets):
            if on_beat is not None:
                on_beat(target.panel_id, Beat.SCOUT.name)
            self.control.move_to(self.fleet.screen_drone, target.scout_or_screen)
            self.transport.step()
            record = self.transport.read_panel(target.panel_id)
            frame = self.transport.capture(self.fleet.screen_drone)
            verdict = self.perception.assess(frame, _context(record))
            hit = ScoutHit(
                target=target,
                verdict=verdict,
                frame_sha=frame_digest(frame),
                frame_thumb=frame_thumbnail(frame),
                injected_state=record.state.value,
            )
            (flagged if verdict.is_suspect else clean).append(hit)
            if on_scouted is not None:
                on_scouted(i, target.panel_id, verdict.is_suspect)
        return flagged, clean

    # -- the response ------------------------------------------------------- #

    def _respond(
        self,
        hit: ScoutHit,
        result: MissionResult,
        on_beat: Optional[Callable[[str, str], None]],
    ) -> PanelResult:
        """Beats 2-4 for one flagged panel, then the writeback."""
        target = hit.target
        pid = target.panel_id
        beat = Beat.DISPATCH
        diagnosis: Optional[Diagnosis] = None
        confirm_sha: Optional[str] = None
        confirm_thumb: Optional[str] = None

        while beat is not Beat.WRITEBACK:
            if on_beat is not None:
                on_beat(pid, beat.name)

            if beat is Beat.DISPATCH:
                # The ground bot travels alone. It carries no camera in Slice 0, so
                # this beat contributes nothing to the verdict — it is the crew
                # arriving, and it is the beat that makes the run legible.
                self.control.move_to(self.fleet.ground_bot, target.approach)
                self.transport.step()
                beat = Beat.CONVERGE

            elif beat is Beat.CONVERGE:
                # Screening drone drops from its survey altitude to the working
                # standoff; the confirm drone arrives from wherever it was left.
                self.control.move_to(self.fleet.screen_drone, target.screen)
                self.control.move_to(self.fleet.confirm_drone, target.confirm)
                self.transport.step()
                beat = Beat.INSPECT

            elif beat is Beat.INSPECT:
                frame = self.transport.capture(self.fleet.confirm_drone)
                confirm_sha = frame_digest(frame)
                confirm_thumb = frame_thumbnail(frame)
                record = self.transport.read_panel(pid)
                diagnosis = self.perception.diagnose(frame, _context(record))
                beat = Beat.WRITEBACK

        if on_beat is not None:
            on_beat(pid, Beat.WRITEBACK.name)
        ts = self.clock()
        if diagnosis is not None:
            detected = coerce_state(diagnosis.fault_type)
            note = diagnosis.note
        else:
            detected = PanelState.HEALTHY
            note = hit.verdict.note
        self.transport.write_panel(pid, detected, note, ts)
        record = self.transport.read_panel(pid)
        result.fault_events.append(
            FaultReport(
                panel_id=pid,
                fault_type=detected.value,
                confidence=diagnosis.confidence if diagnosis else 0.0,
                note=note,
                timestamp=ts,
                panel_geo_position=getattr(record, "geo_position", None),
            )
        )
        return PanelResult(
            panel_id=pid,
            injected_state=hit.injected_state,
            screen_status=hit.verdict.status,
            escalated=True,
            detected_state=detected.value,
            note=note,
            screen_frame_sha=hit.frame_sha,
            confirm_frame_sha=confirm_sha,
            screen_frame_thumb=hit.frame_thumb,
            confirm_frame_thumb=confirm_thumb,
        )

    def _record_clean(self, hit: ScoutHit) -> PanelResult:
        """A panel the survey cleared: verdict written, no fleet response.

        Written back rather than skipped, so `pv:state` reflects that the panel
        was actually looked at and the run record's denominators cover the whole
        scouted zone.
        """
        ts = self.clock()
        self.transport.write_panel(hit.target.panel_id, PanelState.HEALTHY, hit.verdict.note, ts)
        return PanelResult(
            panel_id=hit.target.panel_id,
            injected_state=hit.injected_state,
            screen_status=hit.verdict.status,
            escalated=False,
            detected_state=PanelState.HEALTHY.value,
            note=hit.verdict.note,
            screen_frame_sha=hit.frame_sha,
            screen_frame_thumb=hit.frame_thumb,
        )

    # -- the whole mission -------------------------------------------------- #

    def run(
        self,
        targets: list[InspectionTarget],
        on_result: Optional[Callable[[int, PanelResult], None]] = None,
        on_phase: Optional[Callable[[str, str], None]] = None,
        on_scouted: Optional[Callable[[int, str, bool], None]] = None,
    ) -> MissionResult:
        """Survey `targets`, then respond to each flagged panel.

        Signature-compatible with `Mission.run` (`on_phase` receives beat names)
        so `run.py` can swap the two without special-casing the callbacks.
        `on_scouted(i, panel_id, suspect)` additionally fires per surveyed panel,
        because during a long survey the only progress a watcher can see is the
        drone moving.
        """
        result = MissionResult()
        flagged, clean = self._scout(targets, on_phase, on_scouted)

        # Results are ordered scouted-order, not response-order, so the record
        # reads in the same sequence the survey flew.
        responses: dict[str, PanelResult] = {}
        for hit in flagged:
            responses[hit.target.panel_id] = self._respond(hit, result, on_phase)
        for hit in clean:
            responses[hit.target.panel_id] = self._record_clean(hit)

        emitted = 0
        for target in targets:
            pr = responses.get(target.panel_id)
            if pr is None:
                continue
            result.results.append(pr)
            if on_result is not None:
                on_result(emitted, pr)
            emitted += 1

        result.steps = getattr(self.transport, "step_count", 0)
        return result
