"""Run video compositing — pure array work, no Isaac.

The measurement runs write numbers; this writes the thing you can *watch*: a
chase view of the robot flying the row with the drone's own inspection camera
inset, captioned with the panel under inspection and the verdict the perception
layer returned for it.

Deliberately Isaac-free (it takes frames as `H x W x 3/4` uint8 arrays, from
`SimRuntime` in the sim or from anything in a test) so the layout logic is unit
tested without a GPU. `numpy`/`PIL`/`imageio` are imported lazily so importing
this module costs nothing on the pure-python side.

Why a composite rather than two files: the whole question the video answers is
"what did the drone SEE when it called that panel soiled" — the external view
and the camera view have to be on screen at the same instant to answer it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Output frame size. 720p keeps the inset camera legible without making the
#: render the bottleneck.
CANVAS = (1280, 720)
#: Inset (drone camera) width as a fraction of the canvas.
INSET_FRAC = 0.30
_PAD = 16
_BAR_H = 64

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()  # legible-ish fallback, never a crash


@dataclass
class Caption:
    """What the overlay says about this instant. All fields optional — early
    frames happen before any verdict exists."""

    panel_id: str = ""
    phase: str = ""
    verdict: str = ""
    #: Free line for run-level context (scenario, sun angle, model).
    subtitle: str = ""


def _to_rgb(frame):
    """Drop alpha and coerce to uint8 `H x W x 3`."""
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.shape[-1] > 3:
        arr = arr[..., :3]
    if arr.dtype != np.uint8:
        arr = arr.clip(0, 255).astype(np.uint8)
    return arr


def compose(main, inset=None, caption: Caption | None = None, canvas=CANVAS):
    """One video frame: `main` scaled to fill `canvas`, `inset` in the lower
    right, `caption` in a bar across the top. Returns `H x W x 3` uint8.

    `inset=None` is allowed and simply omits the picture-in-picture — a camera
    can legitimately have no frame yet on the first tick, and dropping the whole
    frame for that would put a gap in the video.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    w, h = canvas
    base = Image.fromarray(_to_rgb(main)).resize((w, h), Image.BILINEAR)

    if inset is not None:
        iw = int(w * INSET_FRAC)
        src = Image.fromarray(_to_rgb(inset))
        ih = max(1, int(iw * src.height / max(1, src.width)))
        src = src.resize((iw, ih), Image.BILINEAR)
        x0, y0 = w - iw - _PAD, h - ih - _PAD
        draw = ImageDraw.Draw(base)
        # A frame around the inset, or a dark camera view bleeds into a dark
        # scene and reads as a rendering artefact rather than a second view.
        draw.rectangle([x0 - 3, y0 - 3, x0 + iw + 2, y0 + ih + 2], fill=(235, 235, 235))
        base.paste(src, (x0, y0))
        d2 = ImageDraw.Draw(base)
        d2.text((x0 + 6, y0 + 4), "DRONE CAM", font=_font(15), fill=(255, 255, 255))

    if caption is not None:
        draw = ImageDraw.Draw(base, "RGBA")
        draw.rectangle([0, 0, w, _BAR_H], fill=(0, 0, 0, 165))
        left = " · ".join(x for x in (caption.panel_id, caption.phase) if x)
        draw.text((_PAD, 8), left, font=_font(26), fill=(255, 255, 255))
        if caption.subtitle:
            draw.text((_PAD, 40), caption.subtitle, font=_font(16), fill=(190, 190, 190))
        if caption.verdict:
            # Right-aligned so the verdict does not jitter as the panel id changes.
            f = _font(24)
            tw = draw.textlength(caption.verdict, font=f)
            colour = (110, 235, 130) if "healthy" in caption.verdict.lower() else (255, 170, 70)
            draw.text((w - tw - _PAD, 18), caption.verdict, font=f, fill=colour)

    return np.asarray(base)


@dataclass
class RunRecorder:
    """Accumulates composed frames and writes them out as an mp4.

    Two modes, because the two videos have different shapes:

    * **buffered** (default) — frames are held in RAM. At 1280x720x3 that is
      ~2.8 MB each, so a few hundred is fine and a few thousand is not.
      `max_frames` is a hard stop that LOGS when it bites — a silently truncated
      video is exactly the kind of quiet cap this project treats as a defect
      (`NFR-07`). Keeping the frames lets a caller revisit them (the tests do).
    * **streaming** (`stream_path=...`) — each frame goes straight to the encoder
      and is dropped. A minutes-long tour is thousands of frames, i.e. multiple
      GB buffered, on a box that is also holding a 75k-prim stage in the same
      unified memory. `max_frames` does not apply: there is nothing to bound.
    """

    fps: int = 15
    max_frames: int = 3000
    frames: list = field(default_factory=list)
    caption: Caption = field(default_factory=Caption)
    dropped: int = 0
    #: Set to encode incrementally instead of buffering. `write()` still returns
    #: the path, so callers do not branch.
    stream_path: str | None = None
    _writer: object = None
    _streamed: int = 0

    def __len__(self) -> int:
        """Frames accepted so far, buffered or streamed — so progress logs and
        `write()`'s own report read the same either way."""
        return self._streamed if self.stream_path else len(self.frames)

    def add(self, main, inset=None) -> None:
        if main is None:
            return
        self.add_composed(compose(main, inset, self.caption))

    def add_composed(self, frame) -> None:
        """Accept an ALREADY-composed frame (an overlay drawn elsewhere, e.g.
        `tour.annotate`) without running `compose` over it a second time."""
        if frame is None:
            return
        if self.stream_path:
            if self._writer is None:
                import imageio.v2 as imageio

                self._writer = imageio.get_writer(
                    str(self.stream_path), fps=self.fps, macro_block_size=None
                )
            self._writer.append_data(frame)
            self._streamed += 1
            return
        if len(self.frames) >= self.max_frames:
            self.dropped += 1
            return
        self.frames.append(frame)

    def write(self, path: str) -> str | None:
        if self.stream_path:
            if self._writer is None:
                return None
            self._writer.close()
            self._writer = None
            return str(self.stream_path)
        if not self.frames:
            return None
        import imageio.v2 as imageio

        writer = imageio.get_writer(str(path), fps=self.fps, macro_block_size=None)
        try:
            for fr in self.frames:
                writer.append_data(fr)
        finally:
            writer.close()
        if self.dropped:
            print(
                f"  [warn] video capped at {self.max_frames} frames; "
                f"{self.dropped} later frames dropped",
                flush=True,
            )
        return str(path)
