"""Camera-path maths for the site flythrough (pure, no Isaac).

`world/flythrough.py` imports pxr only inside render(), so the path helpers are
importable and testable off the Spark.
"""

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
