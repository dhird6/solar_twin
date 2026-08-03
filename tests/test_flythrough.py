"""Camera-path maths for the site flythrough (pure, no Isaac).

`world/flythrough.py` imports pxr only inside render(), so the path helpers are
importable and testable off the Spark.
"""

import math

import pytest

from solar_twin.world.flythrough import Key, _smoothstep, default_shots, interpolate


def test_smoothstep_is_clamped_and_eased():
    assert _smoothstep(-1.0) == 0.0
    assert _smoothstep(2.0) == 1.0
    assert _smoothstep(0.5) == pytest.approx(0.5)
    # Eased, not linear: the first tenth covers less ground than a ramp would,
    # which is what stops the aerial starting with a visible jolt.
    assert _smoothstep(0.1) < 0.1
    assert _smoothstep(0.9) > 0.9


def test_interpolate_emits_one_key_per_frame():
    keys = [Key(0, 0, 10, 90, 0, seconds=0.0), Key(10, 0, 10, 90, 0, seconds=2.0)]
    frames = interpolate(keys, fps=12)
    assert len(frames) == 24 + 1                    # + the final keyframe
    xs = [f.x for f in frames]
    assert xs == sorted(xs)                         # monotone travel
    assert xs[0] == pytest.approx(0.0)
    assert xs[-1] == pytest.approx(10.0)            # lands exactly on the key


def test_heading_takes_the_short_way_round():
    """A pan from 350 to 10 degrees must cross north (+20), not spin -340."""
    keys = [Key(0, 0, 10, 90, 350.0, seconds=0.0), Key(0, 0, 10, 90, 10.0, seconds=1.0)]
    frames = interpolate(keys, fps=10)
    # The last entry is the final keyframe verbatim (heading 10); the SWEEP is
    # everything before it, and it must rise 350 -> 370, never fall through 180.
    sweep = [f.heading_deg for f in frames[:-1]]
    assert sweep == sorted(sweep)
    assert min(sweep) >= 350.0 - 1e-6
    assert max(sweep) <= 370.0 + 1e-6
    assert frames[-1].heading_deg % 360.0 == pytest.approx(10.0)


def test_shots_are_sized_from_the_stage_bounds():
    """The same tour has to frame a 10-panel row and a 273-table block, so every
    move is a fraction of the site, never an absolute metre count."""
    small = default_shots((0.0, 0.0, 20.0, 22.0))
    big = default_shots((0.0, 0.0, 319.0, 647.0))
    # A 647 m block is flown much higher than a 22 m row. Not an unbounded ratio:
    # a tiny row is floored at a sensible altitude rather than skimming the panels.
    assert max(k.z for k in big) > 2.5 * max(k.z for k in small)
    assert max(k.z for k in small) == pytest.approx(120.0)   # the floor
    # The establishing shot stands off by a fraction of the site, so it frames it.
    assert abs(big[0].y) > 5 * abs(small[0].y)
    # Both start south of the site looking north at it, and end over the rows.
    assert small[0].y < 0.0 and big[0].y < 0.0
    assert all(k.pitch_deg > 0 for k in big)


def test_the_tour_descends_to_road_level_and_climbs_again():
    keys = default_shots((0.0, 0.0, 319.0, 647.0))
    zs = [k.z for k in keys]
    assert zs[0] > 100.0            # establishing aerial
    assert min(zs) < 10.0           # gets down onto the road with the inverters
    assert zs[-1] > min(zs)         # and lifts off it again


# --------------------------------------------------------------------------- #
# Framing regression: the camera must point AT the plant.
#
# `assets/khavda_s05b_tour.mp4` rendered a plant with no visible modules. The
# panels were not the fault — every stage-side check on them passed (see
# `test_panel_visibility_usd.py`). The shot list was: it took its standoff from
# `max(span_x, span_y)` but travelled and pulled back along `span_y`, so on a
# footprint measured at 1738 x 161 m the establishing aerial sat 956 m above a
# 161 m-wide strip (a 2.278 m module = 1.8 px) and the road-level shots looked
# north out of the site while the plant ran away east.
# --------------------------------------------------------------------------- #

#: The measured S05b subset footprint, in the elongated form that broke the tour.
WIDE = (1122.3, 0.0, 2860.9, 160.7)
#: The same 20 tables after `subset_site` was fixed to pick a compact patch.
COMPACT = (1122.3, 0.0, 1237.3, 159.5)
TALL = (0.0, 0.0, 320.0, 647.0)


@pytest.mark.parametrize("bounds", [TALL, WIDE, COMPACT], ids=["tall", "wide", "compact"])
def test_aerial_standoff_stays_proportionate_to_the_short_span(bounds):
    """Standoff must be sized so the site spans the frame, not so it becomes a
    thread across the middle of it.

    Sizing altitude off the long axis alone is what put the camera 956 m over a
    161 m strip. The guard is on the SHORT span, because that is the one that
    decides whether the plant fills the frame from directly above it.
    """
    min_x, min_y, max_x, max_y = bounds
    span_across = min(max_x - min_x, max_y - min_y)
    highest = max(k.z for k in default_shots(bounds))
    assert highest <= max(120.0, 3.0 * span_across), (
        f"aerial climbs to {highest:.0f} m over a {span_across:.0f} m-wide site"
    )


def test_the_tour_travels_the_LONG_axis_not_the_short_one():
    """A site that runs east-west must be toured along its length.

    The road-level pass is the heart of the tour; spending it crossing 161 m of a
    1738 m plant is how the video ended up showing open desert.
    """
    for bounds in (TALL, WIDE):
        min_x, min_y, max_x, max_y = bounds
        keys = default_shots(bounds)
        travel_x = max(k.x for k in keys) - min(k.x for k in keys)
        travel_y = max(k.y for k in keys) - min(k.y for k in keys)
        long_is_x = (max_x - min_x) > (max_y - min_y)
        if long_is_x:
            assert travel_x > travel_y, "toured across the short axis of a wide site"
        else:
            assert travel_y > travel_x, "toured across the short axis of a tall site"
        # And the travel must cover a real fraction of the long span.
        long_span = max(max_x - min_x, max_y - min_y)
        assert max(travel_x, travel_y) > 0.8 * long_span


def test_lateral_offsets_are_relative_to_the_centre_not_scaled_world_coords():
    """`cx * 0.75` moved the camera by a quarter of its ABSOLUTE easting, which is
    a spanwise nudge near the origin and a 498 m excursion at Khavda's coordinates.
    Two footprints of identical shape must produce identically-shaped paths.
    """
    shape = (0.0, 0.0, 320.0, 647.0)
    shifted = (10_000.0, 5_000.0, 10_320.0, 5_647.0)
    for a, b in zip(default_shots(shape), default_shots(shifted)):
        assert b.x - a.x == pytest.approx(10_000.0), (
            f"shot {a.label!r} drifted {b.x - a.x - 10_000.0:+.1f} m when the site "
            "was translated — an offset is being scaled off a world coordinate"
        )
        assert b.y - a.y == pytest.approx(5_000.0)
        assert b.z == pytest.approx(a.z)
        assert b.heading_deg == pytest.approx(a.heading_deg)
