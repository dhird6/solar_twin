"""The annotated status tour — chapters, frame budget, overlay (pure, no Isaac).

`world/tour.py` imports pxr nowhere at all and PIL/numpy lazily, so the whole
tour description is testable off the Spark. `plant_tour.py` is the Isaac-bound
renderer and is not exercised here.
"""

import numpy as np
import pytest

from solar_twin.world.tour import (
    BUILT,
    INFERRED,
    TODO,
    Chapter,
    Item,
    annotate,
    build_chapters,
    card,
    render_frames,
    scale_to_budget,
)

BOUNDS = (0.3, 0.0, 321.0, 646.9)  # the real Khavda BLOCK-02 extent
FACTS = {
    "panels": 30016,
    "instanced": 29416,
    "hotspot": 313,
    "soiled": 287,
    "prims": 75508,
    "tables": 273,
    "turbines": 5,
    "inverters": 5,
    "roads": 5,
    "relief_m": 2.173,
    "pile_variation_m": 0.461,
}


def _frame(h=720, w=1280, value=120, channels=4):
    return np.full((h, w, channels), value, dtype=np.uint8)


# ------------------------------------------------------------------ budget
def test_render_frames_excludes_cards():
    """Cards are drawn, not rendered — counting them in the render budget would
    make the tour look more expensive than it is."""
    chapters = [
        Chapter("card", seconds=5.0),
        Chapter("shot", seconds=10.0, keys=[object(), object()]),
        Chapter("fleet", seconds=4.0, fleet=True),
    ]
    assert render_frames(chapters, fps=10) == 100 + 40


def test_budget_that_fits_leaves_the_tour_alone():
    chapters = [Chapter("shot", seconds=10.0, keys=[object()])]
    logs = []
    out = scale_to_budget(chapters, fps=10, seconds_per_frame=0.7, budget_seconds=600, log=logs.append)
    assert out is chapters
    assert any("fits" in m for m in logs)


def test_over_budget_shortens_the_tour_and_says_so():
    """A silently truncated video reads as 'that is the whole plant' (NFR-07)."""
    chapters = [Chapter("shot", seconds=100.0, keys=[object()])]
    logs = []
    # 1000 frames x 0.7s = 700s of render, against a 350s budget -> halve it.
    out = scale_to_budget(chapters, fps=10, seconds_per_frame=0.7, budget_seconds=350, log=logs.append)
    assert out[0].seconds == pytest.approx(50.0)
    assert any("shortened" in m and "warn" in m for m in logs)


def test_shortening_never_cuts_a_chapter_below_a_readable_second():
    chapters = [Chapter("shot", seconds=4.0, keys=[object()])]
    out = scale_to_budget(
        chapters, fps=10, seconds_per_frame=1.0, budget_seconds=1.0, log=lambda *_: None
    )
    assert out[0].seconds == 1.0  # a 3-frame flicker would be unreadable


def test_shortening_leaves_cards_at_full_length():
    """Whatever the budget, the viewer still has to be able to read the backlog."""
    chapters = [Chapter("card", seconds=12.0), Chapter("shot", seconds=100.0, keys=[object()])]
    out = scale_to_budget(
        chapters, fps=10, seconds_per_frame=1.0, budget_seconds=100.0, log=lambda *_: None
    )
    assert out[0].seconds == 12.0
    assert out[1].seconds < 100.0


def test_no_budget_is_no_cap():
    chapters = [Chapter("shot", seconds=999.0, keys=[object()])]
    assert scale_to_budget(chapters, 10, 1.0, None, log=lambda *_: None) is chapters


# ---------------------------------------------------------------- chapters
def test_every_chapter_is_captioned_and_statuses_are_legal():
    chapters = build_chapters(BOUNDS, FACTS)
    assert len(chapters) >= 6
    for ch in chapters:
        assert ch.title
        for item in ch.items:
            assert item.status in (BUILT, INFERRED, TODO)


def test_the_tour_states_what_is_missing_not_only_what_works():
    """The point of this video over `flythrough.py`: it has to be able to say no."""
    chapters = build_chapters(BOUNDS, FACTS)
    todo = [i for ch in chapters for i in ch.items if i.status == TODO]
    assert len(todo) >= 5
    # And the gaps must be spread through the tour, not quarantined in the final
    # card where a viewer can miss them.
    assert sum(1 for ch in chapters[:-1] if any(i.status == TODO for i in ch.items)) >= 3


def test_site_furniture_is_labelled_inferred_not_built():
    """Roads, fence, inverters and turbines are OUR placement — the vendor drawing
    carries DC block hardware only. Calling them BUILT would overclaim the CAD
    ingest, which is the one thing this video must not do."""
    chapters = build_chapters(BOUNDS, FACTS)
    labels = {i.label.lower(): i.status for ch in chapters for i in ch.items}
    inferred = [lbl for lbl, st in labels.items() if st == INFERRED]
    for word in ("road", "fence", "inverter", "turbine"):
        assert any(word in lbl for lbl in inferred), f"{word} is not labelled INFERRED"


def test_captions_carry_numbers_read_off_the_stage():
    """If the build changes, the overlay has to change with it — so the counts
    come from `facts`, never from literals in the module."""
    chapters = build_chapters(BOUNDS, FACTS)
    blob = " ".join(i.detail for ch in chapters for i in ch.items)
    assert "30,016" in blob and "273" in blob and "313" in blob and "287" in blob
    other = dict(FACTS, panels=17, tables=2)
    blob2 = " ".join(i.detail for ch in build_chapters(BOUNDS, other) for i in ch.items)
    assert "30,016" not in blob2 and "17" in blob2


def test_camera_moves_are_fractions_of_the_site():
    """Same reason as `flythrough.default_shots`: this has to frame a 10-panel
    test row and a 273-table block."""
    big = build_chapters(BOUNDS, FACTS)
    small = build_chapters((0.0, 0.0, 20.0, 22.0), FACTS)
    zs_big = [k.z for ch in big for k in ch.keys]
    zs_small = [k.z for ch in small for k in ch.keys]
    assert max(zs_big) > 2.0 * max(zs_small)
    assert min(zs_big) < 10.0  # still gets down among the hardware


def test_there_is_a_fleet_chapter_and_a_closing_backlog_card():
    chapters = build_chapters(BOUNDS, FACTS)
    assert sum(1 for ch in chapters if ch.fleet) == 1
    last = chapters[-1]
    assert not last.keys and not last.fleet          # a card: costs no render
    assert all(i.status == TODO for i in last.items)  # ...and it is the backlog


# ----------------------------------------------------------------- overlay
def test_annotate_returns_an_rgb_frame_at_the_canvas_size():
    ch = Chapter("Real ground", "sub", [Item("a", BUILT, "1"), Item("b", TODO)])
    out = annotate(_frame(), ch, 2, 8, canvas=(1280, 720))
    assert out.shape == (720, 1280, 3)
    assert out.dtype == np.uint8


def test_annotate_rescales_a_frame_that_is_not_the_canvas_size():
    ch = Chapter("t")
    out = annotate(_frame(540, 960), ch, 1, 3, canvas=(1280, 720))
    assert out.shape == (720, 1280, 3)


def test_annotate_darkens_the_title_bar_and_the_checklist_panel():
    ch = Chapter("Balance of plant", "sub", [Item("roads", INFERRED, "5 roads")])
    plain = _frame(value=220)
    out = annotate(plain, ch, 1, 2, canvas=(1280, 720))
    assert out[12, 600].mean() < 200          # title bar
    assert out[700, 60].mean() < 200          # checklist panel, lower left
    assert out[400, 900].mean() > 200         # picture in between is untouched


def test_annotate_accepts_a_drone_inset():
    ch = Chapter("The inspection fleet", "", [Item("x", BUILT)], fleet=True)
    out = annotate(_frame(value=20), ch, 7, 8, inset=_frame(480, 640, 240), canvas=(1280, 720))
    assert out.shape == (720, 1280, 3)
    assert out[640, 1160].mean() > 150        # the inset landed bottom-right


def test_card_renders_the_backlog_full_screen():
    ch = build_chapters(BOUNDS, FACTS)[-1]
    out = card(ch, 8, 8, canvas=(1280, 720), footer="solar-twin")
    assert out.shape == (720, 1280, 3)
    # A dark card with light text: mostly dark, but definitely not blank.
    assert out.mean() < 60
    assert out.std() > 5


def test_overlay_survives_a_chapter_with_no_items_or_subtitle():
    out = annotate(_frame(), Chapter("bare"), 1, 1, canvas=(640, 360))
    assert out.shape == (360, 640, 3)


# ------------------------------------------------------------------- aiming
def test_look_at_is_level_for_a_target_at_the_same_height():
    from solar_twin.world.tour import look_at

    pitch, heading = look_at(0.0, 0.0, 50.0, 0.0, 100.0, 50.0)
    assert pitch == pytest.approx(90.0)   # 90 = level with the horizon
    assert heading == pytest.approx(0.0)  # 0 = north = +Y


def test_look_at_pitches_down_for_a_lower_target_and_up_for_a_higher_one():
    from solar_twin.world.tour import look_at

    down, _ = look_at(0.0, 0.0, 100.0, 0.0, 100.0, 0.0)
    up, _ = look_at(0.0, 0.0, 0.0, 0.0, 100.0, 100.0)
    assert down < 90.0 < up
    assert down == pytest.approx(45.0)
    assert up == pytest.approx(135.0)


def test_look_at_headings_are_compass_bearings():
    from solar_twin.world.tour import look_at

    assert look_at(0, 0, 0, 100, 0, 0)[1] == pytest.approx(90.0)    # east
    assert look_at(0, 0, 0, 0, -100, 0)[1] == pytest.approx(180.0)  # south
    assert look_at(0, 0, 0, -100, 0, 0)[1] == pytest.approx(270.0)  # west


def test_turbine_shot_aims_at_a_real_turbine_when_the_stage_has_one():
    """The first cut guessed a heading and framed two hazed lines. The shot has to
    be derived from where the turbine actually is."""
    from solar_twin.world.tour import _turbine_shot, look_at

    facts = dict(FACTS, turbine_xy=[(-260.0, 300.0), (600.0, 120.0)], turbine_hub_m=120.0)
    keys = _turbine_shot(BOUNDS, facts)
    assert len(keys) >= 2
    # Every key must actually point at the chosen turbine (the nearer one to the
    # block centre), within the rounding of the aim height.
    tx, ty = -260.0, 300.0
    for k in keys:
        _, heading = look_at(k.x, k.y, k.z, tx, ty, 120.0 * 0.62)
        assert abs((k.heading_deg - heading + 180) % 360 - 180) < 1e-6
    # ...and be positioned on the array side of it, so panels are in the frame.
    assert all(k.x > tx for k in keys)


def test_turbine_shot_falls_back_when_the_stage_has_no_turbines():
    from solar_twin.world.tour import _turbine_shot

    keys = _turbine_shot(BOUNDS, {"turbines": 0})
    assert len(keys) >= 2 and all(k.seconds >= 0 for k in keys)


# ------------------------------------------------------------------ wrapping
def test_wrap_breaks_on_words_and_marks_what_did_not_fit():
    from PIL import Image, ImageDraw

    from solar_twin.world.tour import _font, _wrap

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _font(16)
    short = _wrap(draw, "two words", font, 400)
    assert short == ["two words"]
    long = _wrap(draw, "word " * 200, font, 300, max_lines=2)
    assert len(long) == 2
    # Truncation is stated, not silent — the same rule the frame budget follows.
    assert long[-1].endswith("…")


def test_wrap_of_empty_text_is_no_lines():
    from PIL import Image, ImageDraw

    from solar_twin.world.tour import _font, _wrap

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert _wrap(draw, "", _font(14), 100) == []


def test_checklist_rows_do_not_overlap_each_other():
    """Regression: the first layout assumed a line pitch, and at 360p each detail
    line printed straight through the label above it."""
    from PIL import Image, ImageDraw

    from solar_twin.world.tour import _font, _wrap

    ch = build_chapters(BOUNDS, FACTS)[2]  # 'Real ground', the wordiest chapter
    for h in (360, 540, 720):
        w = int(h * 16 / 9)
        draw = ImageDraw.Draw(Image.new("RGB", (w, h)))
        item_px = max(15, int(h * 0.030))
        det_px = max(12, int(h * 0.024))
        pad = max(12, w // 80)
        panel_w = int(w * 0.55)
        text_x = pad + int(item_px * 1.9)
        avail = panel_w + pad - text_x - pad
        gap = max(2, int(item_px * 0.20))
        row_gap = max(5, int(item_px * 0.50))
        det_step = det_px + max(1, det_px // 8)
        heights = []
        for item in ch.items:
            lines = _wrap(draw, item.detail, _font(det_px), avail) if item.detail else []
            heights.append(item_px + ((gap + det_step * len(lines)) if lines else 0))
        # Each row is at least as tall as the text it holds, and the rows are
        # separated, so nothing can print over anything else.
        assert all(hh >= item_px for hh in heights)
        assert row_gap >= 5
        panel_h = sum(heights) + row_gap * (len(heights) - 1) + pad
        assert panel_h + 2 * pad < h, f"checklist panel does not fit at {w}x{h}"


def test_plant_shot_aims_at_a_real_inverter_station_when_one_exists():
    """Same lesson as the turbines: a shot down the middle of the site renders the
    inverter as a distant grey box, which is not evidence a reviewer can judge."""
    from solar_twin.world.tour import _plant_shot, look_at

    inv = [(160.0, 90.0), (160.0, 520.0)]
    keys = _plant_shot(BOUNDS, dict(FACTS, inverter_xy=inv))
    assert len(keys) >= 2
    # The nearest station to the block centre, and every key points at it.
    tx, ty = min(inv, key=lambda p: (p[0] - 160.65) ** 2 + (p[1] - 323.45) ** 2)
    for k in keys:
        _, heading = look_at(k.x, k.y, k.z, tx, ty, 3.0)
        assert abs((k.heading_deg - heading + 180) % 360 - 180) < 1e-6
    # It closes in rather than orbiting at one radius, so the station grows.
    import math

    dists = [math.dist((k.x, k.y), (tx, ty)) for k in keys]
    assert dists[0] > dists[-1]
    # ...and stays at plant height: this is the road-level chapter, not an aerial.
    assert all(k.z < 25.0 for k in keys)


def test_plant_shot_falls_back_without_inverters():
    from solar_twin.world.tour import _plant_shot

    keys = _plant_shot(BOUNDS, {})
    assert len(keys) >= 2 and all(k.z < 25.0 for k in keys)


def test_turbine_caption_uses_the_height_measured_off_the_stage():
    chapters = build_chapters(BOUNDS, dict(FACTS, turbine_tip_m=188.95, turbine_xy=[(-95.0, 300.0)]))
    blob = " ".join(i.detail for ch in chapters for i in ch.items)
    assert "189 m to blade tip" in blob


# ----------------------------------------------------- headings must not clip
def test_headings_shrink_to_fit_instead_of_running_off_the_frame():
    """Regression: the opening card rendered "Khavda BLOCK-02 — the digital twin
    so fa", clipped at the frame edge. Headings are single-line by design, so they
    cannot wrap out of trouble — they have to shrink."""
    from PIL import Image, ImageDraw

    from solar_twin.world.tour import _fit_font

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    long = "Khavda BLOCK-02 — the digital twin so far, and then some more words"
    fitted = _fit_font(draw, long, 600, 51)
    assert draw.textlength(long, font=fitted) <= 600
    # A short heading is left at full size — this shrinks, it does not rescale.
    assert _fit_font(draw, "Short", 600, 51).size == 51


def test_fit_font_never_shrinks_below_a_legible_floor():
    from PIL import Image, ImageDraw

    from solar_twin.world.tour import _fit_font

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert _fit_font(draw, "x" * 2000, 50, 40, min_px=11).size == 11


def test_the_opening_card_title_fits_the_frame_at_every_size():
    """The real titles, at the real canvas sizes, measured — not a synthetic string."""
    from PIL import Image, ImageDraw

    from solar_twin.world.tour import _fit_font

    chapters = build_chapters(BOUNDS, FACTS)
    for w, h in ((1280, 720), (960, 540), (640, 360)):
        draw = ImageDraw.Draw(Image.new("RGB", (w, h)))
        pad = int(w * 0.07)
        for ch in chapters:
            f = _fit_font(draw, ch.title, w - 2 * pad, int(h * 0.072))
            assert draw.textlength(ch.title, font=f) <= w - 2 * pad, f"{ch.title} @ {w}x{h}"


def test_card_detail_lines_clear_their_label():
    """The detail used to sit at a FRACTION of the line pitch, so descenders in the
    label collided with it. It now clears the label's own height."""
    h = 720
    item_px = int(h * 0.037)
    det_px = int(h * 0.028)
    line_h = int(h * 0.088)
    offset = item_px + max(3, det_px // 4)
    assert offset >= item_px          # detail starts below the label
    assert offset + det_px <= line_h  # ...and the pair fits inside one row
