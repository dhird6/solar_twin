"""The escalation FSM — the Slice 0 mission brain.

Per panel: advance the ground bot → screen with Drone 1 (`assess`) → if suspect,
confirm with Drone 2 (`diagnose`) → write the verdict back onto the panel and
append its log → emit a fault event. Depends only on the three interfaces
(Transport, RobotControl, Perception), so it runs identically against
`FakeSimBackend` (tests) and the sim-native world (on the Spark).

Pure-python: no Isaac import (golden rule / Do-NOT list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.perception.base import (
    Diagnosis,
    PanelContext,
    Perception,
    Verdict,
    frame_digest,
    frame_thumbnail,
)
from solar_twin.schema.pv_module import FaultReport, PanelRecord, PanelState, coerce_state
from solar_twin.transport.base import Transport


# --------------------------------------------------------------------------- #
# Mission inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fleet:
    """The robot ids the mission commands."""

    ground_bot: str
    screen_drone: str
    confirm_drone: str


@dataclass(frozen=True)
class InspectionTarget:
    """One panel and the waypoints to inspect it."""

    panel_id: str
    approach: Waypoint  # ground bot
    screen: Waypoint  # screening drone
    confirm: Waypoint  # confirmation drone


# --------------------------------------------------------------------------- #
# Mission outputs (structured returns — used by run.py for the run record)
# --------------------------------------------------------------------------- #


@dataclass
class PanelResult:
    panel_id: str
    injected_state: str  # ground-truth state read before inspection
    screen_status: str  # "clean" | "suspect"
    escalated: bool
    detected_state: str  # state written back after inspection
    note: str
    #: Content hash of the frame each pass was judged FROM (None when the
    #: backend has no pixels): `_sha` is exact, `_key` is the noise-tolerant
    #: 8x8 luminance thumbnail. Two of them because RTX capture is measurably
    #: NOT bit-reproducible (`RISK-24`): the digest answers "same pixels?", the
    #: thumbnail (compared with a tolerance) answers the question attribution
    #: actually needs — "same picture?". See `perception.base.frame_thumbnail`.
    screen_frame_sha: Optional[str] = None
    confirm_frame_sha: Optional[str] = None
    screen_frame_thumb: Optional[str] = None
    confirm_frame_thumb: Optional[str] = None

    @property
    def correct(self) -> bool:
        return self.detected_state == self.injected_state


@dataclass
class MissionResult:
    results: list[PanelResult] = field(default_factory=list)
    fault_events: list[FaultReport] = field(default_factory=list)
    steps: int = 0

    @property
    def panels_inspected(self) -> int:
        return len(self.results)

    @property
    def faults_detected(self) -> int:
        return sum(1 for r in self.results if r.escalated)

    @property
    def detection_rate(self) -> float:
        """Fraction of panels whose detected state matches ground truth."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.correct) / len(self.results)

    @property
    def false_fault_rate(self) -> float:
        """KPI-03: fraction of *healthy* panels misread as faulted (detected
        state != healthy). This is the central thesis metric — a swept blade
        shadow or dust film on a good panel must not be logged as a fault. Only
        healthy panels count; returns 0.0 when there are none to judge.

        **This definition is deliberately unchanged** (`PROJECT_BIBLE.md` §6.5,
        `FR-03` — a locked contract). ``unknown`` is ``!= healthy``, so a panel
        the model failed to answer for still scores here alongside one it wrongly
        called faulty. That conflation was resolved on 2026-07-29 by *reporting
        the split*, not by redefining this metric: re-defining it would have made
        every KPI-03 number already on record non-comparable, including the two
        verified-stimulus 0.00 points. See `abstention_rate` and
        `false_alarm_rate`, which decompose this exactly::

            false_fault_rate == false_alarm_rate + (healthy abstentions / healthy)

        Why it matters that they are separate: measured 2026-07-29, one missing
        ``}`` in a VLM response moved this metric from 0.00 to 0.053 (see
        `cosmos_reason._parse_json_response`). That parse bug is fixed, but
        "we lost the answer" and "it called a fault that isn't there" need
        opposite fixes — plumbing versus model robustness — and a single number
        cannot tell you which you are looking at."""
        healthy = [r for r in self.results if r.injected_state == "healthy"]
        if not healthy:
            return 0.0
        return sum(1 for r in healthy if r.detected_state != "healthy") / len(healthy)

    @property
    def abstentions(self) -> int:
        """Panels the perception backend returned no usable verdict for.

        ``unknown`` is the taxonomy's "we did not get an answer" state — an
        unparseable response, a transport failure, a frame that never arrived. It
        is not a diagnosis, and counting it as one is what `false_fault_rate`'s
        docstring warns about.
        """
        return sum(1 for r in self.results if r.detected_state == "unknown")

    @property
    def abstention_rate(self) -> float:
        """Fraction of *all* inspected panels with no usable verdict.

        Over every panel, not just healthy ones, because losing the answer for a
        faulted panel is equally a plumbing failure — it just surfaces as a missed
        detection in `detection_rate` instead. This is the run's answerability,
        and it is the number to gate when the question is "did the pipeline
        work?" rather than "was the model right?".
        """
        if not self.results:
            return 0.0
        return self.abstentions / len(self.results)

    @property
    def false_alarm_rate(self) -> float:
        """The half of KPI-03 that is a genuine false alarm.

        Healthy panels given a *specific wrong diagnosis* — ``detected_state`` is
        neither ``healthy`` nor ``unknown``. This is the number the project is
        actually trying to drive down: the model looking at a good panel and
        naming a fault. Abstentions are excluded because no fault was claimed.

        Reported alongside `false_fault_rate` rather than replacing it, so the
        locked contract keeps its meaning and the split is still visible.
        """
        healthy = [r for r in self.results if r.injected_state == "healthy"]
        if not healthy:
            return 0.0
        misread = sum(
            1
            for r in healthy
            if r.detected_state not in ("healthy", "unknown")
        )
        return misread / len(healthy)


# --------------------------------------------------------------------------- #
# The FSM
# --------------------------------------------------------------------------- #


class Phase(Enum):
    ADVANCE = auto()  # ground bot to the panel
    SCREEN = auto()  # drone 1 fast pass -> assess
    CONFIRM = auto()  # drone 2 close pass -> diagnose (only if suspect)
    WRITEBACK = auto()  # write verdict onto the panel + emit event
    DONE = auto()


def _default_clock() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _context(record) -> PanelContext:
    """Build the per-panel context passed to Perception. ``true_state`` is the
    ground-truth channel the Slice 0 stub reads; a real VLM ignores it and uses
    the frame + the rest of the context as its prompt."""
    return {
        "true_state": record.state.value,
        "panel_id": record.panel_id,
        "grid_index": record.grid_index,
        "history": list(record.inspection_log),
    }


class Mission:
    """Runs the escalation loop over a list of inspection targets."""

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

    def run(
        self,
        targets: list[InspectionTarget],
        on_result: Optional[Callable[[int, PanelResult], None]] = None,
        on_phase: Optional[Callable[[str, str], None]] = None,
    ) -> MissionResult:
        """`on_result(i, panel_result)` fires once per finished panel.

        `on_phase(panel_id, phase_name)` fires as each phase is ENTERED, before
        the robot moves. It exists so an observer can narrate the run while it
        happens (the demo video captions frames with it) without the FSM having
        to know anything about cameras or overlays."""
        result = MissionResult()
        for i, target in enumerate(targets):
            panel_result = self._inspect(target, result, on_phase)
            result.results.append(panel_result)
            if on_result is not None:
                on_result(i, panel_result)
        result.steps = getattr(self.transport, "step_count", 0)
        return result

    def _inspect(
        self,
        target: InspectionTarget,
        result: MissionResult,
        on_phase: Optional[Callable[[str, str], None]] = None,
    ) -> PanelResult:
        pid = target.panel_id
        phase = Phase.ADVANCE
        injected = PanelState.UNKNOWN
        verdict: Optional[Verdict] = None
        diagnosis: Optional[Diagnosis] = None
        record: Optional[PanelRecord] = None
        screen_sha: Optional[str] = None
        confirm_sha: Optional[str] = None
        screen_thumb: Optional[str] = None
        confirm_thumb: Optional[str] = None

        while phase is not Phase.DONE:
            if on_phase is not None:
                on_phase(pid, phase.name)

            if phase is Phase.ADVANCE:
                self.control.move_to(self.fleet.ground_bot, target.approach)
                self.transport.step()
                phase = Phase.SCREEN

            elif phase is Phase.SCREEN:
                self.control.move_to(self.fleet.screen_drone, target.screen)
                self.transport.step()
                record = self.transport.read_panel(pid)
                injected = record.state  # ground truth, pre-verdict
                frame = self.transport.capture(self.fleet.screen_drone)
                screen_sha = frame_digest(frame)
                screen_thumb = frame_thumbnail(frame)
                verdict = self.perception.assess(frame, _context(record))
                phase = Phase.CONFIRM if verdict.is_suspect else Phase.WRITEBACK

            elif phase is Phase.CONFIRM:
                self.control.move_to(self.fleet.confirm_drone, target.confirm)
                self.transport.step()
                record = self.transport.read_panel(pid)
                frame = self.transport.capture(self.fleet.confirm_drone)
                confirm_sha = frame_digest(frame)
                confirm_thumb = frame_thumbnail(frame)
                diagnosis = self.perception.diagnose(frame, _context(record))
                phase = Phase.WRITEBACK

            elif phase is Phase.WRITEBACK:
                ts = self.clock()
                escalated = verdict is not None and verdict.is_suspect
                if escalated and diagnosis is not None:
                    detected = coerce_state(diagnosis.fault_type)
                    note = diagnosis.note
                else:
                    detected = PanelState.HEALTHY
                    note = verdict.note if verdict else "no verdict"
                self.transport.write_panel(pid, detected, note, ts)
                if escalated:
                    result.fault_events.append(
                        FaultReport(
                            panel_id=pid,
                            fault_type=detected.value,
                            confidence=diagnosis.confidence if diagnosis else 0.0,
                            note=note,
                            timestamp=ts,
                            panel_geo_position=record.geo_position if record else None,
                        )
                    )
                phase = Phase.DONE

        assert verdict is not None  # SCREEN always runs
        return PanelResult(
            panel_id=pid,
            injected_state=injected.value,
            screen_status=verdict.status,
            escalated=verdict.is_suspect,
            detected_state=(
                coerce_state(diagnosis.fault_type).value
                if (verdict.is_suspect and diagnosis)
                else PanelState.HEALTHY.value
            ),
            note=(diagnosis.note if (verdict.is_suspect and diagnosis) else verdict.note),
            screen_frame_sha=screen_sha,
            confirm_frame_sha=confirm_sha,
            screen_frame_thumb=screen_thumb,
            confirm_frame_thumb=confirm_thumb,
        )
