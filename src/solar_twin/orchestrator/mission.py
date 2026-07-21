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
from solar_twin.perception.base import Diagnosis, PanelContext, Perception, Verdict
from solar_twin.schema.pv_module import PanelState, coerce_state
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

    @property
    def correct(self) -> bool:
        return self.detected_state == self.injected_state


@dataclass
class MissionResult:
    results: list[PanelResult] = field(default_factory=list)
    fault_events: list[dict] = field(default_factory=list)
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

    def run(self, targets: list[InspectionTarget]) -> MissionResult:
        result = MissionResult()
        for target in targets:
            result.results.append(self._inspect(target, result))
        result.steps = getattr(self.transport, "step_count", 0)
        return result

    def _inspect(self, target: InspectionTarget, result: MissionResult) -> PanelResult:
        pid = target.panel_id
        phase = Phase.ADVANCE
        injected = PanelState.UNKNOWN
        verdict: Optional[Verdict] = None
        diagnosis: Optional[Diagnosis] = None

        while phase is not Phase.DONE:
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
                verdict = self.perception.assess(frame, _context(record))
                phase = Phase.CONFIRM if verdict.is_suspect else Phase.WRITEBACK

            elif phase is Phase.CONFIRM:
                self.control.move_to(self.fleet.confirm_drone, target.confirm)
                self.transport.step()
                record = self.transport.read_panel(pid)
                frame = self.transport.capture(self.fleet.confirm_drone)
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
                        {
                            "panel_id": pid,
                            "state": detected.value,
                            "note": note,
                            "timestamp": ts,
                            "confidence": diagnosis.confidence if diagnosis else 0.0,
                        }
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
        )
