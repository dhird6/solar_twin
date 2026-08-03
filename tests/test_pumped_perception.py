"""PumpedPerception — repaint the viewport while a slow backend thinks (no Isaac).

The bug this exists for, observed: with `perception: cosmos_reason` the Isaac Sim
window went fully black and the WM reported "not responding", because each panel
blocks ~12 s inside a urllib request and Kit only repaints inside `app.update()`.

What must hold: the wrapper pumps while waiting, returns the SAME verdict the inner
backend would have, propagates exceptions on the caller's thread, and never runs two
perception calls at once (serial inference is what makes the VLM reproducible at
all — RISK-23).
"""

import threading
import time

import pytest

from solar_twin.perception.base import Diagnosis, Perception, Verdict
from solar_twin.perception.pumped import PumpedPerception


class _SlowPerception(Perception):
    """A backend that blocks, like an HTTP call to a VLM."""

    def __init__(self, delay: float = 0.2):
        self.delay = delay
        self.calls: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def _enter(self) -> None:
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)

    def _exit(self) -> None:
        with self._lock:
            self.concurrent -= 1

    def assess(self, frame, context):
        self._enter()
        try:
            self.calls.append("assess")
            time.sleep(self.delay)
            return Verdict(status="suspect", confidence=0.9, note="slow assess")
        finally:
            self._exit()

    def diagnose(self, frame, context):
        self._enter()
        try:
            self.calls.append("diagnose")
            time.sleep(self.delay)
            return Diagnosis(fault_type="hotspot", confidence=0.8, note="slow diagnose")
        finally:
            self._exit()


class _Counter:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1


def test_pumps_while_the_backend_blocks():
    """The whole point: frames get drawn during the call, not after it."""
    pump = _Counter()
    p = PumpedPerception(_SlowPerception(0.3), pump=pump)
    p.assess(None, {})
    assert pump.n > 5, f"expected many pumps during a 0.3 s call, got {pump.n}"
    assert p.pumps == pump.n


def test_verdict_is_unchanged_by_wrapping():
    inner = _SlowPerception(0.01)
    p = PumpedPerception(inner, pump=_Counter())
    v = p.assess(None, {})
    assert v.status == "suspect"
    assert v.confidence == pytest.approx(0.9)
    assert v.note == "slow assess"


def test_diagnosis_is_unchanged_by_wrapping():
    p = PumpedPerception(_SlowPerception(0.01), pump=_Counter())
    d = p.diagnose(None, {})
    assert d.fault_type == "hotspot"
    assert d.confidence == pytest.approx(0.8)


def test_calls_stay_serial():
    """Two panels judged at once would break RISK-23's serial reproducibility."""
    inner = _SlowPerception(0.05)
    p = PumpedPerception(inner, pump=_Counter())
    for _ in range(4):
        p.assess(None, {})
    assert inner.max_concurrent == 1
    assert inner.calls == ["assess"] * 4


def test_backend_exceptions_surface_on_the_calling_thread():
    """A VLM failure must look identical wrapped or unwrapped — otherwise error
    handling upstream silently stops working."""

    class _Boom(Perception):
        def assess(self, frame, context):
            raise RuntimeError("vlm exploded")

        def diagnose(self, frame, context):
            raise RuntimeError("vlm exploded")

    p = PumpedPerception(_Boom(), pump=_Counter())
    with pytest.raises(RuntimeError, match="vlm exploded"):
        p.assess(None, {})


def test_a_failing_pump_does_not_fail_the_perception_call():
    """The pump is cosmetic; losing a frame must never lose a verdict."""

    def bad_pump():
        raise RuntimeError("no display")

    p = PumpedPerception(_SlowPerception(0.05), pump=bad_pump)
    v = p.assess(None, {})
    assert v.status == "suspect"


def test_a_failing_pump_stops_pumping_rather_than_spinning_on_errors():
    calls = {"n": 0}

    def bad_pump():
        calls["n"] += 1
        raise RuntimeError("no display")

    p = PumpedPerception(_SlowPerception(0.15), pump=bad_pump)
    p.assess(None, {})
    assert calls["n"] == 1, "should give up after the first pump failure"


def test_unknown_attributes_forward_to_the_inner_backend():
    """The run record reads `sampling`/`prompt_version` off perception for its
    provenance block; wrapping must not hide them."""

    class _WithProvenance(_SlowPerception):
        sampling = {"temperature": 0.0}
        prompt_version = "v3"

    p = PumpedPerception(_WithProvenance(0.01), pump=_Counter())
    assert p.sampling == {"temperature": 0.0}
    assert p.prompt_version == "v3"


def test_fast_backend_is_not_penalised():
    """The ground-truth stub returns in microseconds; wrapping it must not add a
    poll-interval of latency per panel."""
    p = PumpedPerception(_SlowPerception(0.0), pump=_Counter())
    t0 = time.monotonic()
    for _ in range(20):
        p.assess(None, {})
    assert time.monotonic() - t0 < 1.0


def test_close_is_safe_to_call():
    p = PumpedPerception(_SlowPerception(0.01), pump=_Counter())
    p.assess(None, {})
    p.close()
