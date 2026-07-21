"""Ground-truth Perception stub (Slice 0).

The "cheat" that cleanly separates orchestration logic from real vision: it
ignores the camera frame and reads the true ``pv:state`` from the panel context
(which the world put there from USD / semantics). This lets the whole escalation
loop run and be tested before any real perception exists. Swapping in
`cosmos_reason.py` later changes nothing upstream.

Pure-python: no Isaac import.
"""

from __future__ import annotations

from solar_twin.perception.base import (
    Diagnosis,
    Frame,
    PanelContext,
    Perception,
    Verdict,
)
from solar_twin.schema.pv_module import PanelState, coerce_state

#: Context key under which the world exposes the true state to the stub.
CONTEXT_STATE_KEY = "true_state"


class GroundTruthPerception(Perception):
    """Reads the injected ground-truth state instead of doing vision."""

    def _true_state(self, context: PanelContext) -> PanelState:
        return coerce_state(context.get(CONTEXT_STATE_KEY, PanelState.UNKNOWN.value))

    def assess(self, frame: Frame, context: PanelContext) -> Verdict:
        state = self._true_state(context)
        if state in (PanelState.HEALTHY,):
            return Verdict(status="clean", confidence=1.0, note="ground-truth healthy")
        return Verdict(
            status="suspect",
            confidence=1.0,
            note=f"ground-truth {state.value}",
        )

    def diagnose(self, frame: Frame, context: PanelContext) -> Diagnosis:
        state = self._true_state(context)
        return Diagnosis(
            fault_type=state.value,
            confidence=1.0,
            note=f"ground-truth confirm {state.value}",
        )
