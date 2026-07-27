"""The WorldModel interface — the generative seam (FR-18).

A world model *generates* frames; it never judges panels. That separation is the
whole point: `Perception` (Cosmos Reason) answers "what is wrong with this panel",
`WorldModel` (Cosmos Transfer/Predict) answers "what would this panel look like in
dust, haze, low sun". Keeping them behind different interfaces is what stops a
generator's hallucination from becoming a verdict.

Pure-python: no Isaac import (`NFR-01`).

Why `transfer` and not `predict` is the primary method
-----------------------------------------------------
`transfer` is **conditioned on a seed frame** we rendered ourselves, so the panel
geometry, module grid and authored fault mask survive into the output and only
*appearance* changes. `predict`/text-to-image invents geometry from nothing, which
was measured to be unusable for panel-level fault work: six Cosmos3-Edge
generations produced attractive images with no physically valid module structure,
including one photorealistic frame that was not a PV module at all. See
`evaluator.py` for the measurements.

That is a statement about the *task*, not about the model's quality: nothing
anchors an unconditioned generator to our layout, so nothing can preserve it.

⚠ Cosmos3-Edge, the on-box option, explicitly **rejects** video-to-video and
transfer control — so it cannot implement `transfer`. A conforming Transfer
backend runs off-box (`NFR-05`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: An opaque image frame, same convention as `perception.base.Frame`
#: (an `H x W x {3,4}` uint8 ndarray from `Transport.capture`).
Frame = Any


@dataclass
class GeneratedFrame:
    """One generated frame plus the provenance needed to audit it.

    `seed` is retained deliberately: without the frame it was conditioned on there
    is no way to verify that geometry was preserved, and an unverifiable frame must
    never reach `Perception` (`NFR-08`).
    """

    frame: Frame
    prompt: str
    seed_frame: Frame | None = None
    #: Which panels the seed frame contained, and their authored ground-truth
    #: state — the reference a verdict on this frame can be scored against.
    panel_ids: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class WorldModel(ABC):
    """Generate appearance variants of a seed frame. Never judges panels."""

    @abstractmethod
    def transfer(
        self, seed: Frame, prompt: str, *, controls: dict[str, Frame] | None = None
    ) -> GeneratedFrame:
        """Re-render `seed` under `prompt` while preserving its geometry.

        `controls` carries the conditioning branches a Transfer-class model wants
        (depth, segmentation, edge). Passing them is what makes the output
        geometrically faithful rather than merely plausible.
        """

    def predict(self, seed: Frame, prompt: str, *, num_frames: int = 1) -> list[GeneratedFrame]:
        """Roll the world forward from `seed` (future-state synthesis).

        Optional: the default raises, because most backends will implement only
        `transfer` and a caller must not silently get an unconditioned generation
        when it asked for a prediction.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement predict()")
