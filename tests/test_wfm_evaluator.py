"""The Evaluator gate (FR-05 / NFR-08) — pure, no Isaac, no GPU.

Frames here are synthetic so the test is deterministic, but the thresholds they
exercise were calibrated on real Cosmos3-Edge output (see `evaluator.py`).
"""

import numpy as np
import pytest

from solar_twin.wfm.base import GeneratedFrame, WorldModel
from solar_twin.wfm.evaluator import (
    MIN_EDGE_RETENTION,
    edge_retention,
    evaluate,
    no_reference_guards,
)


def _panel_frame(shift: int = 0, cell: int = 16, size: int = 192) -> np.ndarray:
    """A synthetic PV-module-like frame: a regular dark cell lattice."""
    a = np.full((size, size), 40.0, dtype=np.float32)
    a[(np.arange(size) + shift) % cell < 2, :] = 200.0
    a[:, (np.arange(size) + shift) % cell < 2] = 200.0
    return a.astype(np.uint8)


def test_pure_noise_is_rejected():
    """Noise must be rejected — but note WHICH guard catches it.

    Uniform noise has stddev ~74, comfortably under MAX_STDDEV=100, so the stddev
    guard does NOT fire. The structure guard does: noise has no periodic module
    grid. MAX_STDDEV only catches the high-contrast blob garbage the real model
    emitted (measured 107.7), so it is a narrow backstop, not the main defence.
    """
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, (192, 192), dtype=np.uint8)
    rep = no_reference_guards(noise)
    assert not rep.ok
    assert any("grid_peak" in r for r in rep.reasons), rep.reasons
    assert rep.metrics["stddev"] < 100.0  # documents why stddev alone is not enough


def test_structureless_frame_is_rejected():
    """A smooth gradient has no module grid — the panel structure is absent."""
    g = np.tile(np.linspace(0, 255, 192, dtype=np.float32), (192, 1)).astype(np.uint8)
    rep = no_reference_guards(g)
    assert not rep.ok
    assert any("grid_peak" in r for r in rep.reasons)


def test_panel_like_frame_passes_the_prefilter():
    rep = no_reference_guards(_panel_frame())
    assert rep.ok, rep.reasons
    assert rep.metrics["grid_peak"] >= 10.0


def test_frame_without_a_seed_is_rejected_as_unverifiable():
    """The core NFR-08 rule: unconditioned generation is inadmissible.

    It must be rejected even though it passes every no-reference check, because
    nothing ties it to our panel geometry.
    """
    rep = evaluate(_panel_frame(), seed=None)
    assert not rep.ok
    assert rep.unverifiable
    assert any("cannot be verified" in r for r in rep.reasons)


def test_appearance_change_with_preserved_geometry_is_admitted():
    """Transfer's legitimate case: exposure/haze shift, same hardware."""
    seed = _panel_frame()
    hazed = np.clip(seed.astype(np.float32) * 0.7 + 40.0, 0, 255).astype(np.uint8)
    rep = evaluate(hazed, seed=seed)
    assert rep.ok, rep.reasons
    assert rep.metrics["edge_retention"] >= MIN_EDGE_RETENTION


def test_redrawn_geometry_is_rejected():
    """The failure this gate exists for: the generator moved the hardware."""
    seed = _panel_frame(shift=0)
    moved = _panel_frame(shift=7)  # cell lattice displaced
    keep = edge_retention(seed, moved)
    assert keep < MIN_EDGE_RETENTION, keep
    rep = evaluate(moved, seed=seed)
    assert not rep.ok
    assert any("geometry moved" in r for r in rep.reasons)


def test_mismatched_shapes_retain_nothing():
    assert edge_retention(_panel_frame(size=192), _panel_frame(size=128)) == 0.0


def test_worldmodel_predict_is_not_silently_an_unconditioned_generation():
    class OnlyTransfer(WorldModel):
        def transfer(self, seed, prompt, *, controls=None):
            return GeneratedFrame(frame=seed, prompt=prompt, seed_frame=seed)

    wm = OnlyTransfer()
    assert wm.transfer(_panel_frame(), "dusty").seed_frame is not None
    with pytest.raises(NotImplementedError):
        wm.predict(_panel_frame(), "hail damage")
