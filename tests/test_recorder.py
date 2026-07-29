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


class _FakeWriter:
    """Stands in for the imageio encoder. `imageio` lives only in Isaac Sim's
    bundled Python, so the streaming LOGIC has to be testable without it —
    injecting the writer is what keeps this test Isaac-free."""

    def __init__(self):
        self.written = 0
        self.closed = False

    def append_data(self, frame):
        self.written += 1

    def close(self):
        self.closed = True


def test_streaming_recorder_does_not_buffer_frames(tmp_path):
    """A minutes-long tour is thousands of frames — at 720p that is multiple GB
    held in RAM on a box that is also holding a 75k-prim stage in the same
    unified memory. Streaming keeps the count without keeping the pixels."""
    out = tmp_path / "tour.mp4"
    fake = _FakeWriter()
    r = RunRecorder(fps=10, stream_path=str(out))
    r._writer = fake                     # pre-opened, so no imageio import
    for _ in range(4):
        r.add_composed(_frame(360, 640, 40))
    assert r.frames == []                # nothing buffered
    assert len(r) == 4                   # ...but the count is still reported
    assert fake.written == 4
    # `write()` returns the STREAM path, so callers do not branch on the mode.
    assert r.write("ignored.mp4") == str(out)
    assert fake.closed


def test_streaming_ignores_the_frame_cap_because_there_is_nothing_to_bound():
    r = RunRecorder(max_frames=2, stream_path="x.mp4")
    r._writer = _FakeWriter()
    for _ in range(5):
        r.add_composed(_frame(360, 640, 10))
    assert len(r) == 5 and r.dropped == 0


def test_streaming_write_returns_none_if_nothing_was_ever_added(tmp_path):
    r = RunRecorder(stream_path=str(tmp_path / "empty.mp4"))
    assert r.write("ignored.mp4") is None


def test_streaming_really_encodes_an_mp4_when_imageio_is_available(tmp_path):
    """The fake-writer tests cover the branching; this one proves the real
    encoder is driven correctly. Skips off the Spark (imageio ships with Isaac's
    python, not the system one)."""
    pytest.importorskip("imageio")
    out = tmp_path / "tour.mp4"
    r = RunRecorder(fps=10, stream_path=str(out))
    for _ in range(4):
        r.add_composed(_frame(360, 640, 40))
    assert r.write("ignored.mp4") == str(out)
    assert out.exists() and out.stat().st_size > 0


def test_add_composed_skips_compose_and_keeps_the_frame_as_given():
    """`tour.annotate` has already drawn its own overlay; running `compose` over
    it again would paste a second caption bar on top."""
    r = RunRecorder()
    given = _frame(360, 640, 77)
    r.add_composed(given)
    assert r.frames[0].shape == (360, 640, 3)
    assert np.array_equal(r.frames[0], given)


def test_buffered_len_matches_the_frame_list():
    r = RunRecorder()
    r.add(_frame(540, 960, 10))
    assert len(r) == len(r.frames) == 1
