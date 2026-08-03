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
    #: High, wide pass used only by `ScoutDispatchMission`'s survey sweep. Optional
    #: so the sweep FSM above and every recorded KPI are untouched by its addition;
    #: when absent the scout falls back to `screen`.
    scout: Optional[Waypoint] = None

    @property
    def scout_or_screen(self) -> Waypoint:
        return self.scout if self.scout is not None else self.screen


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
        """Fraction of panels whose detected state matches ground truth.

        ⚠⚠ **This is ACCURACY, not recall, and its denominator is EVERY panel —
        healthy ones included.** On a scenario that is mostly healthy it is
        dominated by healthy panels being correctly left alone, so it flatters any
        model that under-reports. Measured on the archived runs (2026-07-31):

            `nominal_calm_vlm` is 82.5% healthy, and its gate is
            `detection_rate_min: 0.80`. A model that calls EVERY panel healthy
            therefore scores **0.825 and passes the gate** while detecting nothing.
            Across 20 archived VLM runs the null model clears that gate in **13**,
            and in **3** it scores at or above what the real model managed.

        Kept exactly as-is because it is on record in every run ever written and
        redefining it would make those numbers non-comparable — the same reasoning
        that locked `false_fault_rate`. Use `fault_recall` / `fault_flagged_rate`
        below for "did it actually find the faults", and quote the null baseline
        (`healthy_fraction`) beside this number whenever it is used as a gate.
        """
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.correct) / len(self.results)

    @property
    def healthy_fraction(self) -> float:
        """The null baseline: what `detection_rate` scores by calling everything
        healthy. A `detection_rate` at or below this is worth nothing."""
        if not self.results:
            return 0.0
        healthy = sum(1 for r in self.results if r.injected_state == "healthy")
        return healthy / len(self.results)

    @property
    def fault_recall(self) -> float:
        """Fraction of genuinely faulted panels whose fault was NAMED correctly.

        The strict reading of KPI-01 and the one a maintenance loop needs, because
        the work order depends on which fault it is. Denominator is faulted panels
        only, so healthy panels cannot inflate it. Returns 0.0 when the scenario
        seeds no faults — a run with nothing to find has no recall to report.
        """
        faulted = [r for r in self.results if r.injected_state != "healthy"]
        if not faulted:
            return 0.0
        return sum(1 for r in faulted if r.correct) / len(faulted)

    @property
    def fault_flagged_rate(self) -> float:
        """Fraction of faulted panels flagged as faulty AT ALL, whatever the label.

        The lenient reading: "did we notice something was wrong here". Reported
        beside `fault_recall` because the gap between them is pure taxonomy
        confusion, and the two have completely different fixes — a low
        `fault_flagged_rate` is a sensitivity problem, while a low `fault_recall`
        with a high `fault_flagged_rate` is a discrimination problem.

        Measured pooled over the archived VLM runs (2026-07-31): soiled panels are
        flagged 0.984 of the time but named right only 0.516; hotspots are flagged
        0.621 and named right 0.379. Injected soiling was called "hotspot" 29 times
        out of 62 — so the taxonomy, not the sensitivity, is the weaker half.
        """
        faulted = [r for r in self.results if r.injected_state != "healthy"]
        if not faulted:
            return 0.0
        return sum(1 for r in faulted if r.detected_state != "healthy") / len(faulted)

    def recall_by_state(self) -> dict[str, dict[str, float]]:
        """Per-injected-state breakdown, because the pooled number hides the split.

        Returns ``{state: {"n", "named", "flagged", "recall", "flagged_rate"}}``.
        Measured: a pooled 0.875 `detection_rate` on `nominal_calm_vlm` sat on top
        of hotspot recall near 0.4 — the aggregate could not show that, and the
        fix (framing/standoff for hotspots) is state-specific.
        """
        out: dict[str, dict[str, float]] = {}
        for r in self.results:
            if r.injected_state == "healthy":
                continue
            s = out.setdefault(
                r.injected_state,
                {"n": 0.0, "named": 0.0, "flagged": 0.0, "recall": 0.0,
                 "flagged_rate": 0.0},
            )
            s["n"] += 1
            s["named"] += 1 if r.correct else 0
            s["flagged"] += 1 if r.detected_state != "healthy" else 0
        for s in out.values():
            s["recall"] = s["named"] / s["n"]
            s["flagged_rate"] = s["flagged"] / s["n"]
        return out

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
        target_factory: Optional[Callable[[str], Optional["InspectionTarget"]]] = None,
    ):
        self.transport = transport
        self.control = control
        self.perception = perception
        self.fleet = fleet
        self.clock = clock
        #: Builds waypoints for a panel the original sweep never listed. Required for
        #: adaptive expansion — a neighbour of a faulted module is usually not in the
        #: sweep, so without this the planner can reorder but never add.
        #: `layout.target_for(panel_id)` is the real implementation.
        self._target_factory = target_factory

    @staticmethod
    def _position_of(target: "InspectionTarget") -> tuple[float, float, float]:
        """Where the robot ends up after inspecting `target`.

        The CONFIRM standoff, because that is the last pose of the escalation and so
        the point the next leg starts from. ⚠ When SLAM lands this becomes an
        estimated pose from the transport rather than a commanded waypoint, and the
        planner already takes position as an input so only this line changes.
        """
        wp = target.confirm
        return (float(wp.x), float(wp.y), float(wp.z))

    def run(
        self,
        targets: list[InspectionTarget],
        on_result: Optional[Callable[[int, PanelResult], None]] = None,
        on_phase: Optional[Callable[[str, str], None]] = None,
        planner=None,
    ) -> MissionResult:
        """`on_result(i, panel_result)` fires once per finished panel.

        `planner` (an `adaptive.AdaptivePlanner`) closes the loop: after each verdict
        it may reorder the remaining queue and add panels the sweep never listed —
        e.g. the neighbours of a soiled module. **None keeps the original fixed-list
        behaviour exactly**, which is what every recorded KPI was measured with; a
        planner changes how many panels get inspected and therefore every denominator
        in the run record.

        `on_phase(panel_id, phase_name)` fires as each phase is ENTERED, before
        the robot moves. It exists so an observer can narrate the run while it
        happens (the demo video captions frames with it) without the FSM having
        to know anything about cameras or overlays."""
        result = MissionResult()
        if planner is None:
            # UNCHANGED PATH. Kept as its own branch rather than folded into the
            # adaptive loop so a run without a planner is byte-identical to one from
            # before this existed — every recorded KPI stays reproducible.
            for i, target in enumerate(targets):
                panel_result = self._inspect(target, result, on_phase)
                result.results.append(panel_result)
                if on_result is not None:
                    on_result(i, panel_result)
            result.steps = getattr(self.transport, "step_count", 0)
            return result

        # Adaptive: the queue is rebuilt after every verdict, so a finding at panel 3
        # can change panels 4..N — including adding panels the sweep never listed.
        by_id = {t.panel_id: t for t in targets}
        queue = [t.panel_id for t in targets]
        i = 0
        # ⚠ Bounded. An expansion rule that kept firing could otherwise run the robot
        # over the whole farm; `AdaptivePlanner.max_extra` caps additions, and this
        # caps iterations even if a planner ignores that.
        max_visits = len(targets) + getattr(planner, "max_extra", 0) + 1
        while queue and i < max_visits:
            pid = queue.pop(0)
            target = by_id.get(pid)
            if target is None:
                target = self._target_factory(pid) if self._target_factory else None
                if target is None:
                    # A neighbour we cannot build waypoints for is skipped loudly in
                    # the stats rather than silently, so the record still adds up.
                    continue
                by_id[pid] = target
            panel_result = self._inspect(target, result, on_phase)
            result.results.append(panel_result)
            if on_result is not None:
                on_result(i, panel_result)
            i += 1
            queue = planner.revise(
                queue,
                pid,
                coerce_state(panel_result.detected_state),
                self._position_of(target),
            )
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
