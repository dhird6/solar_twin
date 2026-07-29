"""The Perception interface (§6.4).

Two calls, mirroring the two-tier escalation: ``assess`` is the cheap wide
screening pass (Drone 1); ``diagnose`` is the expensive close confirm (Drone 2).
The Slice 0 impl (`ground_truth.py`) ignores the frame and reads ``pv:state``
from the panel context. `cosmos_reason.py` (later) runs a VLM on the frame with
the context as its prompt — swapping it in must NOT change orchestration.

Pure-python: no Isaac import here (golden rule / Do-NOT list).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

#: An opaque sensor frame. Sim-native hands over an ndarray; ROS 2 an Image
#: message; the ground-truth stub ignores it. The interface stays type-agnostic.
Frame = Any

#: Per-panel context passed to perception: metadata (panel_id, grid_index,
#: history) and — for the ground-truth stub only — the true ``pv:state``.
PanelContext = dict[str, Any]


def frame_digest(frame: Frame) -> str | None:
    """Exact content hash of a sensor frame — "were these the same pixels?"

    Recorded alongside each verdict (`PanelResult.screen_frame_sha`) so a
    run-to-run disagreement can be checked against the pixels instead of argued
    about. **Measured caveat (2026-07-28, `RISK-24`):** RTX capture on this build
    is *not* bit-reproducible — 4 captures from an unmoved camera gave 4 distinct
    digests. So an exact-digest difference is expected in any rendered run and is
    NOT on its own evidence that the renderer caused a verdict to change; that is
    what `frame_signature` is for.

    Returns None for a frameless backend (the fake world, Slice 0 text-only
    runs) and for anything that cannot be hashed — instrumentation must never
    be the thing that breaks a mission. Truncated to 16 hex chars: this
    distinguishes frames, it does not authenticate them.
    """
    if frame is None:
        return None
    try:
        import hashlib  # noqa: PLC0415 — lazy, keeps import cost off the ABC

        data = frame.tobytes() if hasattr(frame, "tobytes") else bytes(frame)
        return hashlib.sha256(data).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — a digest is diagnostics, not a contract
        return None


#: Thumbnail grid. 8x8 blocks over a 640x480 frame averages ~4800 pixels each,
#: which divides the renderer's per-pixel sampling noise by ~69x.
_THUMB_GRID = 8


def frame_thumbnail(frame: Frame) -> str | None:
    """Coarse luminance thumbnail of a frame — "was this the same *picture*?"

    `frame_digest` answers a question that turned out to be too strict for a
    stochastic renderer: **measured on this build, the same camera pose never
    produces the same bits twice** (4 captures from an unmoved camera → 4 distinct
    digests, 0.86/255 mean pixel difference — `RISK-24`). Exact equality would
    therefore blame the renderer for every verdict flip.

    A quantised *hash* does not fix that either — it was tried and rejected:
    with 256 blocks quantised to 16 levels, ~64% of unchanged captures still
    flipped at least one block across a quantisation boundary (measured 3/4
    distinct while holding still). Hashing forces a boundary decision that the
    data does not support.

    So this returns the coarse values themselves (an 8x8 grid of uint8 luminance,
    128 hex chars) and lets the comparison carry a **tolerance** —
    `kpi.variance.thumbnails_differ`. Noise then shows up as a difference of a
    fraction of an LSB, while a moved shadow or a different pose shows up as
    several LSB, and there is no cliff in between.

    Returns None on a frameless backend, without numpy, or on an unexpected
    shape: instrumentation degrades to "unattributed", never to a wrong answer.
    """
    if frame is None:
        return None
    try:
        import numpy as np  # noqa: PLC0415 — lazy: the ABC must import without it

        arr = np.asarray(frame)
        if arr.ndim == 3 and arr.shape[2] >= 3:
            lum = arr[..., :3].astype(np.float32).mean(axis=2)
        elif arr.ndim == 2:
            lum = arr.astype(np.float32)
        else:
            return None
        h, w = lum.shape
        if h < _THUMB_GRID or w < _THUMB_GRID:
            return None
        # Block-average onto a fixed grid, trimming the remainder rather than
        # interpolating — a resample would add its own filter to the comparison.
        bh, bw = h // _THUMB_GRID, w // _THUMB_GRID
        block = lum[: bh * _THUMB_GRID, : bw * _THUMB_GRID]
        block = block.reshape(_THUMB_GRID, bh, _THUMB_GRID, bw).mean(axis=(1, 3))
        return np.clip(block, 0, 255).astype(np.uint8).tobytes().hex()
    except Exception:  # noqa: BLE001 — diagnostics, never a mission failure
        return None


@dataclass
class Verdict:
    """Result of a screening pass."""

    status: str  # "clean" | "suspect"
    confidence: float
    note: str

    @property
    def is_suspect(self) -> bool:
        return self.status == "suspect"


@dataclass
class Diagnosis:
    """Result of a confirmation pass."""

    fault_type: str  # a PanelState value (§6.5)
    confidence: float
    note: str


class Perception(ABC):
    """Screening + confirmation. Swappable: ground-truth stub → Cosmos Reason."""

    @abstractmethod
    def assess(self, frame: Frame, context: PanelContext) -> Verdict:
        """Fast wide screening pass — is this panel worth a closer look?"""

    @abstractmethod
    def diagnose(self, frame: Frame, context: PanelContext) -> Diagnosis:
        """Close confirmation pass — what exactly is wrong with it?"""
