"""Run-video compositing (pure, no Isaac, no GPU)."""

import numpy as np
import pytest

from solar_twin.world.recorder import CANVAS, Caption, RunRecorder, compose


def _frame(h, w, value, channels=3):
    return np.full((h, w, channels), value, dtype=np.uint8)


def test_compose_returns_the_canvas_size_regardless_of_input_size():
    out = compose(_frame(540, 960, 100))
    assert out.shape == (CANVAS[1], CANVAS[0], 3)
    assert out.dtype == np.uint8


def test_compose_drops_alpha_from_rgba_sources():
    # Isaac annotators hand back H x W x 4; a stray alpha channel would make
    # imageio write a broken frame rather than fail loudly.
    out = compose(_frame(480, 640, 80, channels=4), _frame(480, 640, 200, channels=4))
    assert out.shape[-1] == 3


def test_inset_is_composited_into_the_lower_right():
    out = compose(_frame(540, 960, 30), _frame(480, 640, 240))
    h, w, _ = out.shape
    # The bright inset must land bottom-right and leave the left side dark.
    assert out[h - 80, w - 120].mean() > 150
    assert out[h - 80, 120].mean() < 90


def test_missing_inset_still_produces_a_frame():
    # A camera can have no frame on the first tick; dropping the whole frame
    # would put a hole in the video.
    assert compose(_frame(540, 960, 50), None).shape == (CANVAS[1], CANVAS[0], 3)


def test_caption_bar_darkens_the_top_of_the_frame():
    plain = compose(_frame(540, 960, 200))
    capped = compose(_frame(540, 960, 200), caption=Caption("R00-C001", "screening pass"))
    assert capped[10, 400].mean() < plain[10, 400].mean() - 40
    # ...and leaves the picture below it alone.
    assert capped[400, 400].mean() == pytest.approx(plain[400, 400].mean(), abs=2)


def test_recorder_accumulates_and_ignores_none():
    r = RunRecorder(fps=10)
    r.add(_frame(540, 960, 10))
    r.add(None)
    assert len(r.frames) == 1


def test_recorder_caps_frames_and_counts_what_it_dropped():
    # A silently truncated video reads as "the run ended there" — the cap has to
    # be observable (NFR-07, no silent caps).
    r = RunRecorder(max_frames=2)
    for _ in range(5):
        r.add(_frame(540, 960, 10))
    assert len(r.frames) == 2
    assert r.dropped == 3


def test_write_returns_none_with_no_frames(tmp_path):
    assert RunRecorder().write(str(tmp_path / "x.mp4")) is None
