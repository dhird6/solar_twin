"""Keep the viewport alive while a slow perception backend thinks.

The problem, measured: `cosmos_reason` spends ~12 s per panel inside a blocking
`urllib` request. Kit only repaints inside `app.update()`, so for those 12 s the
Isaac Sim window paints nothing — and because the app stops pumping its event
loop, the window manager marks it **"not responding"** and the compositor shows a
black or stale surface. Over a 24-panel survey that is ~5 minutes of a window that
looks crashed while the run is perfectly healthy. `docs/ENVIRONMENT.md` recorded
this as needing "perception on a worker thread with the app pumped on the main
thread (not done)". This is that.

The shape: `PumpedPerception` wraps any `Perception` and runs the real call on a
worker thread, while the calling (main) thread pumps a `pump()` callback until the
worker finishes. Perception is I/O-bound — it is waiting on an HTTP socket — so the
GIL is released for essentially the whole wait and the main thread really does get
to render.

Deliberately NOT concurrency: exactly one perception call is in flight at a time,
and the wrapper blocks until it returns, so the FSM's ordering, the run record and
`RISK-23`'s serial-inference reproducibility are all unchanged. This makes the
window repaint during a call; it does not batch, reorder or parallelise anything.

Pure-python: no Isaac import. `pump` is an opaque callable — `run.py` passes the
runtime's step, tests pass a counter.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional

from solar_twin.perception.base import Diagnosis, Frame, PanelContext, Perception, Verdict

#: How long the main thread sleeps between pumps when it has nothing to draw.
#: Small enough that a pump is never far behind the worker, large enough that a
#: fast backend (the stub, microseconds) does not spin.
_POLL_S = 0.01


class PumpedPerception(Perception):
    """Run `inner`'s calls on a worker thread, pumping `pump()` while they block.

    `pump()` is called repeatedly from the calling thread until the wrapped call
    returns. It must be cheap and must not raise; an exception from a *pump* would
    otherwise abort a perception call that was about to succeed, so it is swallowed
    after the first failure and pumping stops for the rest of that call.
    """

    def __init__(
        self,
        inner: Perception,
        pump: Callable[[], None],
        poll_s: float = _POLL_S,
        max_wait_s: Optional[float] = None,
    ):
        self._inner = inner
        self._pump = pump
        self._poll_s = float(poll_s)
        self._max_wait_s = max_wait_s
        # One worker: perception is serial by contract (see the module docstring).
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="perception")
        #: Diagnostics — how many pumps happened, i.e. frames the viewport got to
        #: draw that it would otherwise have missed. 0 means this wrapper bought
        #: nothing and something is wrong with the pump.
        self.pumps = 0

    # -- the interface ------------------------------------------------------ #

    def assess(self, frame: Frame, context: PanelContext) -> Verdict:
        return self._pumped(self._inner.assess, frame, context)

    def diagnose(self, frame: Frame, context: PanelContext) -> Diagnosis:
        return self._pumped(self._inner.diagnose, frame, context)

    # -- the mechanism ------------------------------------------------------ #

    def _pumped(self, call, frame: Frame, context: PanelContext):
        future: Future = self._pool.submit(call, frame, context)
        pumping = True
        started = time.monotonic()
        while not future.done():
            if pumping:
                try:
                    self._pump()
                    self.pumps += 1
                except Exception as exc:  # noqa: BLE001 — see the class docstring
                    print(
                        f"  [warn] viewport pump failed ({exc!r}); perception "
                        "continues but the window will freeze for this call",
                        flush=True,
                    )
                    pumping = False
            else:
                time.sleep(self._poll_s)
            if (
                self._max_wait_s is not None
                and time.monotonic() - started > self._max_wait_s
            ):
                # Stop pumping and just wait. The backend's own timeout is what
                # bounds the call; this only stops us spinning render forever.
                break
        # `result()` re-raises whatever the backend raised, on THIS thread, so
        # error handling upstream is identical to calling the backend directly.
        return future.result()

    def close(self) -> None:
        self._pool.shutdown(wait=False)

    def __getattr__(self, name: str):
        """Forward anything else (e.g. `sampling`, `prompt_version`) to the inner
        backend, so the run record's provenance block is unaffected by wrapping."""
        return getattr(self._inner, name)
