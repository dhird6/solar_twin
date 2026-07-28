"""The annotated status tour — what the twin HAS, and what it still lacks.

`flythrough.py` answers "what does the site look like". This answers a different
question, the one a reviewer actually asks: **which parts of this are real, which
are our invention, and what is still missing?** So every shot carries a checklist
of the things visible in it, each tagged with its status, and the tour ends on a
card listing everything still to build.

Pure python on purpose — chapters, the frame budget and the overlay drawing are
all testable without Isaac (`plant_tour.py` is the Isaac-bound renderer that
consumes this). `numpy`/`PIL` are imported lazily so the pure-python side pays
nothing for importing the module.

**Honesty rules this module exists to enforce** (`NFR-07`):
- `INFERRED` is a first-class status, not a footnote. The roads, fence, inverter
  stations and turbines are *our* placement, not the vendor drawing's, and a video
  that showed them unlabelled would be overclaiming the CAD ingest.
- `TODO` items are rendered in the same list as the built ones, in the shot where
  their absence is visible. Missing hardware is easiest to hide by never pointing
  the camera at where it would be.
- `scale_to_budget` LOGS when it shortens the tour. A silently truncated video
  reads as "that's the whole plant" when it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Status of one thing the twin models. The three-way split is the point: a demo
#: that only says "done / not done" cannot express "this is in the video, it looks
#: real, and we made it up", which is the status most of the site furniture has.
BUILT = "BUILT"
INFERRED = "INFERRED"  # in the twin, but our invention — not from the drawing
TODO = "TODO"  # not modelled at all

#: Overlay colours per status (RGB). Amber for INFERRED because it must not read
#: as either a pass or a failure — it reads as "believe the geometry, not the
#: position".
_STATUS_RGB = {
    BUILT: (110, 235, 130),
    INFERRED: (255, 185, 70),
    TODO: (150, 155, 165),
}

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass(frozen=True)
class Item:
    """One line of the checklist: a thing, its status, and the number that backs
    it up. `detail` is where measured quantities go — "273 tables" is checkable,
    "accurate layout" is not."""

    label: str
    status: str
    detail: str = ""


@dataclass
class Chapter:
    """One shot of the tour plus what it is evidence for.

    `keys` are `flythrough.Key` camera keyframes. A chapter with no keys is a
    full-screen card (title / closing list), which costs no render at all.
    """

    title: str
    subtitle: str = ""
    items: list[Item] = field(default_factory=list)
    keys: list = field(default_factory=list)
    #: Seconds of finished video. For a card this is pure hold time.
    seconds: float = 6.0
    #: Chase the fleet instead of flying the scripted camera. Handled by the
    #: renderer; here only so the chapter list stays the single description of
    #: the tour.
    fleet: bool = False


def _font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _marker(draw, x: int, y: int, status: str, r: int = 7) -> None:
    """Status dot. Drawn as geometry rather than a glyph because the tick and
    cross codepoints are not in every DejaVu build on this box, and a missing
    glyph renders as a hollow box that reads as a rendering fault."""
    colour = _STATUS_RGB.get(status, (200, 200, 200))
    box = [x - r, y - r, x + r, y + r]
    if status == TODO:
        # Hollow: nothing is there yet, and the marker should look like it.
        draw.ellipse(box, outline=colour, width=2)
    else:
        draw.ellipse(box, fill=colour)
    if status == BUILT:
        # A tick inside the dot, in the panel's own background colour.
        draw.line([(x - 3, y), (x - 1, y + 3), (x + 4, y - 4)], fill=(18, 20, 26), width=2)
    elif status == INFERRED:
        # A bar: "present, but ours".
        draw.line([(x - 3, y), (x + 3, y)], fill=(18, 20, 26), width=2)


def _wrap(draw, text: str, font, width: int, max_lines: int = 2) -> list[str]:
    """Greedy word wrap to a measured pixel width.

    Marks truncation with an ellipsis instead of dropping the tail silently — the
    same rule the frame budget follows. A caption that quietly loses its second
    half is a caption that misinforms.
    """
    words = str(text).split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for i, word in enumerate(words):
        trial = f"{cur} {word}".strip()
        if not cur or draw.textlength(trial, font=font) <= width:
            cur = trial
            continue
        lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            cur = ""
            # Everything from here on did not fit.
            lines[-1] = lines[-1] + " …"
            break
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def look_at(
    x: float, y: float, z: float, tx: float, ty: float, tz: float
) -> tuple[float, float]:
    """`(pitch_deg, heading_deg)` that aims a camera at `(tx, ty, tz)`.

    In this project's convention (verified on the 6.0.1 build): heading is a
    compass bearing with 0 = north = +Y, and pitch 90 is level with the horizon,
    below 90 looks down, above 90 looks up. Worth having as a function rather
    than hand-tuned angles per shot: the turbine chapter aims at a turbine whose
    position is read off the stage, and a 190 m blade tip needs the camera to
    pitch UP, which is easy to get backwards by hand.
    """
    import math

    dx, dy = tx - x, ty - y
    flat = math.hypot(dx, dy)
    heading = math.degrees(math.atan2(dx, dy)) % 360.0
    pitch = 90.0 + math.degrees(math.atan2(tz - z, max(1e-6, flat)))
    return pitch, heading


def annotate(frame, chapter: Chapter, index: int, total: int, inset=None, canvas=None):
    """Burn the chapter title and its checklist onto one rendered frame.

    Returns `H x W x 3` uint8. `inset` (the drone camera) is delegated to
    `recorder.compose` so the fleet chapter looks like the inspection videos
    rather than inventing a second picture-in-picture style.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    from solar_twin.world.recorder import compose

    if inset is not None:
        base_arr = compose(frame, inset, caption=None, canvas=canvas or (frame.shape[1], frame.shape[0]))
        base = Image.fromarray(base_arr)
    else:
        arr = np.asarray(frame)
        if arr.shape[-1] > 3:
            arr = arr[..., :3]
        base = Image.fromarray(arr.astype(np.uint8))
        if canvas and (base.width, base.height) != tuple(canvas):
            base = base.resize(tuple(canvas), Image.BILINEAR)

    w, h = base.width, base.height
    draw = ImageDraw.Draw(base, "RGBA")
    pad = max(12, w // 80)

    # --- title bar -------------------------------------------------------
    bar_h = int(h * 0.115)
    draw.rectangle([0, 0, w, bar_h], fill=(0, 0, 0, 170))
    f_title = _font(max(20, int(h * 0.045)))
    f_sub = _font(max(13, int(h * 0.027)))
    draw.text((pad, int(bar_h * 0.10)), chapter.title, font=f_title, fill=(255, 255, 255))
    if chapter.subtitle:
        draw.text((pad, int(bar_h * 0.60)), chapter.subtitle, font=f_sub, fill=(195, 200, 210))
    # Chapter counter, right-aligned so it does not shift as titles change length.
    tag = f"{index}/{total}"
    tw = draw.textlength(tag, font=f_sub)
    draw.text((w - tw - pad, int(bar_h * 0.36)), tag, font=f_sub, fill=(170, 175, 185))

    # --- checklist panel, lower left -------------------------------------
    # Laid out from MEASURED text rather than a fixed line height: the first
    # version assumed a line pitch, and at 360p the detail line printed straight
    # through the label above it. Every size below is derived from the font
    # metrics so the panel is legible at 360p and at 720p.
    if chapter.items:
        item_px = max(15, int(h * 0.030))
        det_px = max(12, int(h * 0.024))
        f_item, f_det = _font(item_px), _font(det_px)
        panel_w = int(w * 0.55)
        text_x = pad + int(item_px * 1.9)
        avail = panel_w + pad - text_x - pad  # text width inside the panel
        gap = max(2, int(item_px * 0.20))  # label -> its own detail
        row_gap = max(5, int(item_px * 0.50))  # item -> next item
        det_step = det_px + max(1, det_px // 8)

        laid = []
        for item in chapter.items:
            lines = _wrap(draw, item.detail, f_det, avail) if item.detail else []
            height = item_px + ((gap + det_step * len(lines)) if lines else 0)
            laid.append((item, lines, height))

        panel_h = sum(h_ for *_, h_ in laid) + row_gap * (len(laid) - 1) + pad
        y0 = h - panel_h - pad
        draw.rectangle([pad, y0, pad + panel_w, y0 + panel_h], fill=(18, 20, 26, 190))
        y = y0 + pad // 2
        for item, lines, height in laid:
            _marker(draw, pad + int(item_px * 0.85), y + item_px // 2, item.status, r=max(6, item_px // 3))
            draw.text(
                (text_x, y),
                item.label,
                font=f_item,
                fill=(240, 242, 246) if item.status != TODO else (170, 175, 185),
            )
            yy = y + item_px + gap
            for line in lines:
                draw.text((text_x, yy), line, font=f_det, fill=_STATUS_RGB.get(item.status, (180, 180, 180)))
                yy += det_step
            y += height + row_gap

    return np.asarray(base.convert("RGB"))


def card(chapter: Chapter, index: int, total: int, canvas=(1280, 720), footer: str = ""):
    """A full-screen text card — the opening titles and the closing "what's
    next" list. Costs no render, so the tour can afford to state its own scope
    instead of leaving the viewer to infer it."""
    import numpy as np
    from PIL import Image, ImageDraw

    w, h = canvas
    base = Image.new("RGB", (w, h), (14, 16, 21))
    draw = ImageDraw.Draw(base)
    pad = int(w * 0.07)

    draw.text((pad, int(h * 0.11)), chapter.title, font=_font(int(h * 0.072)), fill=(255, 255, 255))
    if chapter.subtitle:
        draw.text(
            (pad, int(h * 0.215)), chapter.subtitle, font=_font(int(h * 0.033)), fill=(160, 200, 255)
        )
    # A rule under the heading, so the list below reads as a list and not as
    # more heading.
    draw.line([(pad, int(h * 0.28)), (w - pad, int(h * 0.28))], fill=(60, 66, 80), width=2)

    f_item = _font(int(h * 0.037))
    f_det = _font(int(h * 0.028))
    line_h = int(h * 0.082)
    for i, item in enumerate(chapter.items):
        cy = int(h * 0.335) + i * line_h
        _marker(draw, pad + 10, cy + 10, item.status, r=9)
        draw.text((pad + 40, cy), item.label, font=f_item, fill=(238, 240, 245))
        if item.detail:
            draw.text(
                (pad + 40, cy + int(line_h * 0.44)),
                item.detail,
                font=f_det,
                fill=_STATUS_RGB.get(item.status, (180, 180, 180)),
            )

    if footer:
        draw.text((pad, int(h * 0.93)), footer, font=_font(int(h * 0.026)), fill=(120, 126, 140))
    tag = f"{index}/{total}"
    f_sub = _font(int(h * 0.026))
    draw.text(
        (w - draw.textlength(tag, font=f_sub) - pad, int(h * 0.93)),
        tag,
        font=f_sub,
        fill=(120, 126, 140),
    )
    return np.asarray(base)


# ---------------------------------------------------------------------- #
# Frame budget
# ---------------------------------------------------------------------- #
def render_frames(chapters: list[Chapter], fps: int) -> int:
    """Frames the tour will actually render (cards are free — they are drawn,
    not rendered, so they do not consume the render budget)."""
    return sum(int(round(c.seconds * fps)) for c in chapters if c.keys or c.fleet)


def scale_to_budget(
    chapters: list[Chapter],
    fps: int,
    seconds_per_frame: float,
    budget_seconds: float | None,
    log=print,
) -> list[Chapter]:
    """Shorten every rendered chapter proportionally until the tour fits a
    wall-clock render budget, and say so out loud.

    Measured on this box, a frame costs ~0.71 s at 960x540 **regardless of
    altitude** — the ground-level shots are no dearer than the aerial, which is
    the opposite of what Session 10d assumed. So the cost of a tour is set by its
    frame COUNT alone, which makes it a budget worth stating rather than a
    mystery worth fearing.

    Returns the (possibly shortened) chapters. Cards are never shortened: they
    carry the text a reviewer has to be able to read.
    """
    n = render_frames(chapters, fps)
    if budget_seconds is None or n == 0:
        return chapters
    projected = n * seconds_per_frame
    if projected <= budget_seconds:
        log(f"  budget: {n} frames x {seconds_per_frame:.2f}s = {projected / 60:.1f} min (fits)")
        return chapters
    factor = budget_seconds / projected
    out = []
    for c in chapters:
        if c.keys or c.fleet:
            # Floor at 1 s: a chapter cut to a handful of frames is a flicker the
            # viewer cannot read, which defeats the point of showing it.
            c = Chapter(
                title=c.title,
                subtitle=c.subtitle,
                items=c.items,
                keys=c.keys,
                seconds=max(1.0, c.seconds * factor),
                fleet=c.fleet,
            )
        out.append(c)
    log(
        f"  [warn] tour shortened x{factor:.2f} to fit a {budget_seconds / 60:.0f} min "
        f"render budget: {n} -> {render_frames(out, fps)} frames. "
        f"Raise --budget-minutes for the full-length tour."
    )
    return out


# ---------------------------------------------------------------------- #
# The tour itself
# ---------------------------------------------------------------------- #
def _turbine_shot(bounds: tuple[float, float, float, float], facts: dict) -> list:
    """Frame the turbine chapter on a turbine whose position came off the stage.

    The first version guessed a heading and got a pair of hazed white lines 160 m
    away: technically the turbines, but no evidence of anything. Aiming at the
    real prim, from a stand-off sized to the machine, puts a turbine and the array
    it stands beside in the same frame — which is the actual claim of the chapter
    ("these are outside the panel footprint"). Falls back to a fixed westward
    look if the stage has no turbines.
    """
    from solar_twin.world.flythrough import Key

    min_x, min_y, max_x, max_y = bounds
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    tip = float(facts.get("turbine_tip_m") or 190.0)
    xy = facts.get("turbine_xy") or []
    if not xy:
        return [
            Key(cx, cy - (max_y - min_y) * 0.10, 60.0, 80.0, 285.0, 20.0, 0.0, ""),
            Key(cx - (max_x - min_x) * 0.10, cy + (max_y - min_y) * 0.10, 80.0, 84.0, 270.0, 22.0, 9.0, ""),
        ]
    # The turbine nearest the middle of the block: it has the most panels behind
    # it, so the "outside the footprint" point is visible in one frame.
    tx, ty = min(xy, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    # The framing is LENS ARITHMETIC, not taste, and it has bitten twice:
    #   * 2.4 tip-heights away -> two hazed white lines. Correct geometry, no
    #     evidence of anything.
    #   * 1.05 tip-heights, camera 38 m up aimed at the hub -> a fine turbine and
    #     NO PANELS. A 22 mm lens on a 36 mm aperture has a ~49 deg vertical
    #     field, so aiming 18 deg up puts the frame's bottom edge 6 deg below
    #     horizontal, and from 38 m up that first meets the ground 355 m away —
    #     past the turbine itself. The array was under the frame the whole time.
    # So: stand off 1.4 tip-heights, keep the camera LOW, and aim low enough that
    # the bottom edge hits the ground close in. That puts the panels in the
    # foreground and the machine standing clear of them — which is the chapter's
    # entire claim, shown rather than asserted.
    stand = tip * 1.4
    aim_z = tip * 0.37
    # Approach from the array side, so the panels lie between camera and turbine.
    sx = 1.0 if tx < cx else -1.0
    keys = []
    for along, out, z, secs in (
        (-0.40, 1.50, 0.07, 0.0),
        (-0.05, 1.40, 0.09, 5.0),
        (0.35, 1.30, 0.12, 4.0),
    ):
        x = tx + sx * stand * out
        y = ty + stand * along
        cz = tip * z
        pitch, heading = look_at(x, y, cz, tx, ty, aim_z)
        keys.append(Key(x, y, cz, pitch, heading, 22.0, secs, ""))
    return keys


def _plant_shot(bounds: tuple[float, float, float, float], facts: dict) -> list:
    """Frame the balance-of-plant chapter on a real inverter station.

    Same lesson as the turbines: a shot down the middle of the site technically
    contains the roads and the inverters, but renders them as a light strip and a
    distant grey box. The chapter's claim is that this furniture is OUR invention,
    and a viewer cannot evaluate that claim against a box eight pixels wide.
    """
    from solar_twin.world.flythrough import Key

    min_x, min_y, max_x, max_y = bounds
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    span_y = max_y - min_y
    xy = facts.get("inverter_xy") or []
    if not xy:
        return [
            Key(cx, min_y + span_y * 0.04, 8.0, 87.0, 0.0, 22.0, 0.0, ""),
            Key(cx, min_y + span_y * 0.30, 6.5, 88.0, 0.0, 22.0, 6.0, ""),
            Key(cx, min_y + span_y * 0.50, 14.0, 82.0, 0.0, 20.0, 5.0, ""),
        ]
    ix, iy = min(xy, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    aim_z = 3.0  # roughly the middle of a station's own height
    keys = []
    # Fly in along the aisle towards the station, then past it at road height, so
    # the road, the fence line and the station are all in shot at some point.
    for dist, side, z, secs in ((95.0, -0.35, 16.0, 0.0), (48.0, -0.10, 9.0, 6.0), (22.0, 0.55, 6.0, 5.0)):
        x = ix + side * dist
        y = iy - dist
        pitch, heading = look_at(x, y, z, ix, iy, aim_z)
        keys.append(Key(x, y, z, pitch, heading, 22.0, secs, ""))
    return keys


def build_chapters(bounds: tuple[float, float, float, float], facts: dict) -> list[Chapter]:
    """The tour, sized from the stage's own bounds and captioned with numbers
    read off the stage (`facts`) rather than numbers typed in here.

    Camera moves are fractions of the site for the same reason `flythrough.py`'s
    are: this has to frame a 10-panel test row and a 273-table block.
    """
    from solar_twin.world.flythrough import Key

    min_x, min_y, max_x, max_y = bounds
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    span_x = max_x - min_x
    span_y = max_y - min_y
    high = max(150.0, 0.72 * max(span_x, span_y))

    n_panels = facts.get("panels", 0)
    n_tables = facts.get("tables", 0)
    n_prims = facts.get("prims", 0)
    n_inst = facts.get("instanced", 0)
    n_hot = facts.get("hotspot", 0)
    n_soil = facts.get("soiled", 0)
    n_turb = facts.get("turbines", 0)
    n_inv = facts.get("inverters", 0)
    n_roads = facts.get("roads", 0)
    relief = facts.get("relief_m")
    pile = facts.get("pile_variation_m")

    chapters: list[Chapter] = [
        Chapter(
            title="Khavda BLOCK-02 — the digital twin so far",
            subtitle="what is built, what is inferred, what is still missing",
            seconds=7.0,
            items=[
                Item(
                    "Real site, real survey coordinates",
                    BUILT,
                    f"Adani Khavda PLOT A10b BLOCK-02, EPSG:32642 · {n_tables} tables · {n_panels:,} modules",
                ),
                Item(
                    "Everything here is built from a script + a config",
                    BUILT,
                    "no GUI steps — farm_builder.py + configs/farm_khavda_block02.yaml",
                ),
                Item(
                    "Status of each part is labelled in the shot it appears in",
                    BUILT,
                    "filled = built · barred = our invention · hollow = not modelled",
                ),
            ],
        ),
        # 1. Establishing aerial: the extent is the headline.
        Chapter(
            title="The block",
            subtitle=f"{span_x:.0f} m x {span_y:.0f} m of real layout, straight off the vendor drawing",
            seconds=11.0,
            items=[
                Item("Layout ingested from vendor CAD", BUILT, f"{n_tables} tracker tables, {n_panels:,} modules"),
                Item("Instanced so the whole plant fits", BUILT, f"{n_inst:,} instanced · {n_prims:,} prims total"),
                Item("USD stage is the source of truth", BUILT, "Z-up, metres, pv: attributes on every module"),
                Item("Second block / full 30 GW site", TODO, "one DC block ingested; the plot has many"),
            ],
            keys=[
                Key(cx, min_y - span_y * 0.55, high, 56.0, 0.0, 24.0, 0.0, ""),
                Key(cx, min_y - span_y * 0.30, high * 0.80, 54.0, 0.0, 24.0, 6.0, ""),
                Key(cx * 0.80, min_y - span_y * 0.18, high * 0.62, 58.0, 12.0, 22.0, 5.0, ""),
            ],
        ),
        # 2. Terrain. Shot from low and raking so relief is legible; a nadir view
        #    of 2 m of relief over 1.4 km looks perfectly flat.
        Chapter(
            title="Real ground",
            subtitle="Copernicus GLO-30 DEM — the plant stands on the Rann of Kutch, not on a plane",
            seconds=10.0,
            items=[
                Item(
                    "Terrain sampled from a real DEM",
                    BUILT,
                    f"Copernicus GLO-30 via AWS Open Data (no login)"
                    + (f" · {relief:.1f} m of relief" if relief else ""),
                ),
                Item(
                    "Torque tubes fitted STRAIGHT through the grade",
                    BUILT,
                    "a 128 m tube is a rigid beam — draping it would flatter the twin"
                    + (f" · worst row needs {pile:.2f} m of pile variation" if pile else ""),
                ),
                Item(
                    "Module heights follow the real ground",
                    BUILT,
                    f"panel z spans {facts['panel_z_span_m']:.2f} m across the block"
                    if facts.get("panel_z_span_m")
                    else "sampled per tracker table, not per module",
                ),
                Item("Engineered/graded civil surface", TODO, "GLO-30 is pre-grading; only the civil drawings have it"),
                Item("Pile + torque-tube hardware geometry", TODO, "mounts are implied by panel height, not modelled"),
            ],
            keys=[
                # Pitched DOWN harder than feels natural: at 89 deg most of the
                # frame was sky and horizon haze, and 2.2 m of relief over 1.4 km
                # only reads when the rows themselves fill the picture.
                Key(min_x - span_x * 0.10, min_y + span_y * 0.10, 14.0, 80.0, 22.0, 28.0, 0.0, ""),
                Key(min_x + span_x * 0.18, min_y + span_y * 0.34, 8.0, 83.0, 20.0, 28.0, 5.0, ""),
                Key(min_x + span_x * 0.30, min_y + span_y * 0.52, 20.0, 74.0, 8.0, 24.0, 5.0, ""),
            ],
        ),
        # 3. Balance of plant, at road level where the furniture is.
        Chapter(
            title="Balance of plant",
            subtitle="roads, inverter stations and the perimeter — present, but OUR placement",
            seconds=11.0,
            items=[
                Item("Internal access roads", INFERRED, f"{n_roads} roads — the drawing carries DC hardware only"),
                Item("Inverter / transformer stations", INFERRED, f"{n_inv} stations, placed by us"),
                Item("Perimeter fence", INFERRED, "posts + wires, ours"),
                Item("Substation, control room, cable trenches", TODO, "the rest of the balance of plant"),
            ],
            keys=_plant_shot(bounds, facts),
        ),
        # 4. The modules themselves: tilt, cell grid, and the fault signatures the
        #    perception stack is scored against.
        Chapter(
            title="Trackers and modules",
            subtitle="single-axis tracker angle driven by a real solar position; faults are real geometry",
            seconds=10.0,
            items=[
                Item("Tracker angle from real sun position", BUILT, "HSAT rotation + sun both from configs/…sun.timestamp"),
                Item("Per-module PV cell grid + PBR glass", BUILT, "12 x 6 cells, dust film material for soiling"),
                Item(
                    "Seeded fault signatures on real modules",
                    BUILT,
                    f"{n_hot} hotspot + {n_soil} soiled, written to pv:state on the prim",
                ),
                Item("Tracker backtracking", TODO, "not modelled, so self-shading is worst-case"),
            ],
            keys=[
                Key(min_x + span_x * 0.42, cy - span_y * 0.16, 4.0, 84.0, 0.0, 35.0, 0.0, ""),
                Key(min_x + span_x * 0.46, cy + span_y * 0.02, 3.2, 86.0, 8.0, 35.0, 5.0, ""),
                Key(min_x + span_x * 0.52, cy + span_y * 0.14, 9.0, 78.0, 20.0, 28.0, 5.0, ""),
            ],
        ),
        # 5. Turbines. Framed from inside the site looking out, because the point
        #    is that they are OUTSIDE the array.
        Chapter(
            title="Wind turbines",
            subtitle="Khavda is a real hybrid wind+solar park — but this placement is ours",
            seconds=9.0,
            items=[
                Item(
                    "Utility-class turbines, blades turning",
                    INFERRED,
                    f"{n_turb} turbines"
                    + (
                        f" · {facts['turbine_tip_m']:.0f} m to blade tip"
                        if facts.get("turbine_tip_m")
                        else " · 120 m hub, 70 m blade"
                    ),
                ),
                Item(
                    "Kept OUTSIDE the panel footprint on purpose",
                    BUILT,
                    "a turbine inside the array would shade panels and any KPI would be our artefact",
                ),
                Item("Keep-out enforced in the planner", BUILT, "control-agnostic no-fly volumes; last run cleared 92.1 m"),
                Item("Physical collision / rotor wake", TODO, "PhysX colliders inert until Pegasus flies the drones"),
            ],
            keys=_turbine_shot(bounds, facts),
        ),
        # 6. The fleet. Chase view + the drone's own camera, because the whole
        #    claim of the project is "the robot saw it and called it".
        Chapter(
            title="The inspection fleet",
            subtitle="ground bot + screening and confirming drones, flying the real block",
            seconds=14.0,
            fleet=True,
            items=[
                Item("Fleet flies interpolated waypoints", BUILT, "cruise 16 m/s, easing to 2 m/s for the shot"),
                Item("Drone camera is the perception input", BUILT, "the inset is the frame the VLM actually scores"),
                Item("Cosmos Reason VLM verdicts, written back to USD", BUILT, "nvidia/cosmos-reason1-7b, served locally"),
                Item("Real flight dynamics (Pegasus / PX4)", TODO, "motion is kinematic — no aero, no battery, no wind"),
            ],
            keys=[],
        ),
    ]

    chapters.append(
        Chapter(
            title="What is still missing",
            subtitle="the honest backlog, in the order it matters",
            seconds=13.0,
            items=[
                Item("Flight dynamics: Pegasus Simulator / PX4", TODO, "v5.1.0 targets Isaac 5.1; this box runs 6.0.1"),
                Item("ROS 2 bridge as a Transport", TODO, "camera->ROS 2 is proven on this box; the bridge is unwritten"),
                Item("VLM run-to-run variance is unquantified", TODO, "two identical runs disagreed on one panel — no KPI is a constant yet"),
                Item("Rest of the balance of plant + graded surface", TODO, "substation, control room, trenches, piles"),
                Item("Closed maintenance loop", TODO, "verdicts are written; nothing is dispatched to act on them"),
            ],
        )
    )
    return chapters
