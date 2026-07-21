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
