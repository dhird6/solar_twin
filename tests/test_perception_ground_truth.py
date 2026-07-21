"""The ground-truth Perception stub reads pv:state from context (no Isaac)."""

from solar_twin.perception.ground_truth import (
    CONTEXT_STATE_KEY,
    GroundTruthPerception,
)


def _ctx(state: str) -> dict:
    return {CONTEXT_STATE_KEY: state, "panel_id": "R01-C001"}


def test_assess_healthy_is_clean():
    p = GroundTruthPerception()
    v = p.assess(frame=None, context=_ctx("healthy"))
    assert v.status == "clean"
    assert not v.is_suspect


def test_assess_fault_is_suspect():
    p = GroundTruthPerception()
    for state in ("hotspot", "soiled", "crack"):
        v = p.assess(frame=None, context=_ctx(state))
        assert v.is_suspect, state


def test_diagnose_returns_true_state():
    p = GroundTruthPerception()
    d = p.diagnose(frame=None, context=_ctx("hotspot"))
    assert d.fault_type == "hotspot"
    assert d.confidence == 1.0
