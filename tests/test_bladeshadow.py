"""Blade-shadow geometry — the `KPI-03` stimulus locator, Isaac-free.

These tests exist because SC-05 produced a false-fault rate of 0.00 from a shadow
that never touched a module (`khavda_selfshade.yaml` records it). Every assertion
below is one way that failure could recur: the shadow pointing the wrong way, the
reach computed against the ground instead of the module plane, or a swept region
that is reported as a hit when a blade never actually dwells there.
"""

from __future__ import annotations

import math

import pytest

from solar_twin.world.bladeshadow import (
    MIN_USABLE_ELEVATION_DEG,
    SweptShadow,
    assess,
    best_turbine,
    shadow_direction,
    shadow_offset_m,
)

#: Non-degenerate reference geometry: sun in the east, wind from the east, so the
#: rotor's horizontal axis (north-south) is perpendicular to the shadow direction
#: (due west) and the disc's shadow is a clean circle.
TURBINE = {"pos": [0.0, 0.0], "hub_height": 100.0, "blade_len": 10.0}
SUN = {"elevation_deg": 45.0, "azimuth_deg": 90.0}
WIND_FROM_EAST = 90.0


def _shadow(**over):
    kw = dict(
        elevation_deg=SUN["elevation_deg"],
        azimuth_deg=SUN["azimuth_deg"],
        target_z=0.0,
        wind_dir_deg=WIND_FROM_EAST,
    )
    kw.update(over)
    return SweptShadow.from_turbine(TURBINE, **kw)


# --------------------------------------------------------------------------- #
# Direction and reach
# --------------------------------------------------------------------------- #


def test_shadow_travels_away_from_the_sun():
    """Sun in the east casts shadows to the west. This sign is the single easiest
    thing to invert, and inverting it puts the stimulus on the far side of the
    plant — which looks exactly like having no stimulus."""
    dx, dy = shadow_direction(90.0)
    assert dx == pytest.approx(-1.0)
    assert dy == pytest.approx(0.0, abs=1e-12)

    dx, dy = shadow_direction(180.0)  # sun due south -> shadow due north
    assert dx == pytest.approx(0.0, abs=1e-12)
    assert dy == pytest.approx(1.0)


def test_shadow_direction_matches_windfield_convention_for_a_southwesterly():
    """`shadow_direction` and `windfield.downwind_unit` both turn a compass bearing
    into a travel vector, and they must agree — one is 'where the light goes', the
    other 'where the air goes', but a bearing means the same thing to both."""
    from solar_twin.world.windfield import downwind_unit

    assert shadow_direction(225.0) == pytest.approx(downwind_unit(225.0))


def test_reach_is_height_over_tan_elevation():
    assert shadow_offset_m(100.0, 45.0) == pytest.approx(100.0)
    assert shadow_offset_m(100.0, 30.0) == pytest.approx(100.0 / math.tan(math.radians(30)))


def test_low_sun_is_refused_rather_than_returning_a_kilometre():
    """A 2 km shadow is not a stimulus, it is a bug that renders. Refusing loudly is
    the whole point — see the module docstring."""
    with pytest.raises(ValueError, match="usable floor"):
        shadow_offset_m(120.0, MIN_USABLE_ELEVATION_DEG - 0.1)


def test_reach_is_measured_to_the_module_plane_not_the_ground():
    """The SC-05 class of error. A hub 100 m above the ground is only 80 m above a
    20 m target plane, and its shadow lands 20 m short of where a ground-plane
    calculation puts it."""
    on_ground = _shadow(target_z=0.0)
    on_modules = _shadow(target_z=20.0)
    assert on_ground.hub_offset_m == pytest.approx(100.0)
    assert on_modules.hub_offset_m == pytest.approx(80.0)
    # ...and that difference moves the shadow, it does not merely resize it.
    assert on_modules.center_x > on_ground.center_x


def test_graded_terrain_raises_the_tower_with_the_hub():
    """A turbine standing on a rise casts further. `tower_z` and `target_z` are
    separate because on graded terrain the tower and the modules are not on the
    same level."""
    flat = _shadow(tower_z=0.0, target_z=0.0)
    on_a_rise = _shadow(tower_z=10.0, target_z=0.0)
    assert on_a_rise.hub_offset_m > flat.hub_offset_m


# --------------------------------------------------------------------------- #
# The swept region
# --------------------------------------------------------------------------- #


def test_hub_shadow_lands_downsun_at_the_computed_offset():
    s = _shadow()
    assert s.center_x == pytest.approx(-100.0)
    assert s.center_y == pytest.approx(0.0, abs=1e-9)


def test_swept_region_is_the_rotor_disc_projected():
    """At 45 deg the vertical axis projects 1:1, so a 10 m rotor sweeps a 10 m-radius
    circle of shadow.

    Probed just inside and just outside each semi-axis rather than exactly on it:
    the boundary is a float comparison against 1.0 and lands either side of it by
    rounding, and a panel sitting precisely on the edge has no dwell to measure
    anyway (`duty_fraction` there is ~0), so the distinction carries no meaning.
    """
    s = _shadow()
    assert s.covers(-100.0, 0.0)  # centre
    assert s.covers(-100.0, 9.99)  # just inside the cross-axis edge
    assert s.covers(-90.01, 0.0)  # just inside the along-light edge
    assert not s.covers(-100.0, 10.01)
    assert not s.covers(-89.99, 0.0)
    assert not s.covers(-85.0, 0.0)


def test_lower_sun_stretches_the_shadow_along_the_light_and_not_across_it():
    """The physical signature of a projected disc: the cross-axis semi-axis is the
    rotor radius at any elevation, while the along-light axis grows as 1/tan."""
    high = _shadow(elevation_deg=45.0)
    low = _shadow(elevation_deg=20.0)
    assert math.hypot(*high.axis_cross) == pytest.approx(10.0)
    assert math.hypot(*low.axis_cross) == pytest.approx(10.0)
    assert math.hypot(*low.axis_along) > math.hypot(*high.axis_along)


def test_bbox_contains_the_swept_region():
    s = _shadow(elevation_deg=25.0)
    x0, y0, x1, y1 = s.bbox()
    assert x0 <= s.center_x <= x1 and y0 <= s.center_y <= y1
    # Sample the ellipse boundary; every point of it must be inside the bbox.
    for i in range(64):
        t = 2.0 * math.pi * i / 64
        px = s.center_x + s.axis_cross[0] * math.cos(t) + s.axis_along[0] * math.sin(t)
        py = s.center_y + s.axis_cross[1] * math.cos(t) + s.axis_along[1] * math.sin(t)
        assert x0 - 1e-9 <= px <= x1 + 1e-9
        assert y0 - 1e-9 <= py <= y1 + 1e-9


def test_sun_along_the_rotor_axis_is_reported_as_no_coverage():
    """Degenerate by construction: with the wind from the north the rotor's
    horizontal axis runs east-west, and a sun due east collapses the disc's shadow
    to a line. Better to report nothing than to divide by a zero determinant."""
    s = _shadow(wind_dir_deg=0.0)
    assert not s.covers(*s.bbox()[:2])
    assert not s.covers(-100.0, 0.0)


# --------------------------------------------------------------------------- #
# Dwell — the half that stops us overstating a stimulus
# --------------------------------------------------------------------------- #


def test_duty_is_zero_outside_the_swept_region():
    s = _shadow()
    assert s.duty_fraction(0.0, 0.0) == 0.0


def test_duty_is_a_few_percent_inside_it_not_a_blanket():
    """The correction that matters for KPI-03: a panel under the swept disc is only
    actually shaded for `n_blades * chord / (2 pi r)` of each revolution. Reporting
    the disc as the stimulus overstates the hazard by more than 10x."""
    s = _shadow()
    # A point at half the rotor radius from the hub shadow: 3 blades of 4 m chord
    # sweeping a 2*pi*5 m circle -> 12/31.4 ~ 0.38... but the chord is measured
    # across the blade, so the expected duty is 3 * asin(2/10 / 0.5) / pi.
    duty = s.duty_fraction(-95.0, 0.0, samples=3600)
    expected = 3.0 * math.asin((4.0 / 2.0) / 10.0 / 0.5) / math.pi
    assert duty == pytest.approx(expected, abs=0.02)
    assert 0.0 < duty < 0.5


def test_duty_falls_off_with_distance_from_the_hub():
    """Further out, the same blade sweeps a longer arc, so any one point spends less
    of the revolution under it."""
    s = _shadow()
    near = s.duty_fraction(-97.0, 0.0, samples=3600)
    far = s.duty_fraction(-92.0, 0.0, samples=3600)
    assert near > far > 0.0


def test_wider_blades_dwell_longer():
    wide = SweptShadow.from_turbine(
        {**TURBINE, "blade_chord": 8.0},
        elevation_deg=45.0,
        azimuth_deg=90.0,
        target_z=0.0,
        wind_dir_deg=WIND_FROM_EAST,
    )
    narrow = _shadow()
    at = (-95.0, 0.0)
    assert wide.duty_fraction(*at, samples=3600) > narrow.duty_fraction(*at, samples=3600)


# --------------------------------------------------------------------------- #
# Over an array
# --------------------------------------------------------------------------- #


def test_assess_counts_only_panels_the_shadow_reaches():
    """The honest KPI-03 denominator. Two panels under the shadow, one 500 m away
    that must not be counted as exposed to a stimulus it never sees."""
    panels = [(-100.0, 0.0), (-98.0, 3.0), (500.0, 500.0)]
    rep = assess(
        panels,
        TURBINE,
        elevation_deg=45.0,
        azimuth_deg=90.0,
        target_z=0.0,
        wind_dir_deg=WIND_FROM_EAST,
    )
    assert rep.covered == 2
    assert rep.total == 3
    assert rep.covered_fraction == pytest.approx(2 / 3)
    assert rep.usable


def test_a_shadow_that_misses_everything_is_not_usable():
    """This is the SC-05 outcome, and it must be detectable before rendering rather
    than after an hour of VLM calls."""
    rep = assess(
        [(500.0, 500.0)],
        TURBINE,
        elevation_deg=45.0,
        azimuth_deg=90.0,
        target_z=0.0,
        wind_dir_deg=WIND_FROM_EAST,
    )
    assert rep.covered == 0
    assert not rep.usable


def test_best_turbine_picks_the_one_that_actually_reaches():
    panels = [(-100.0, 0.0), (-98.0, 2.0)]
    far = {"pos": [4000.0, 4000.0], "hub_height": 100.0, "blade_len": 10.0}
    got = best_turbine(
        panels,
        [far, TURBINE],
        elevation_deg=45.0,
        azimuth_deg=90.0,
        target_z=0.0,
        wind_dir_deg=WIND_FROM_EAST,
    )
    assert got is not None
    idx, rep = got
    assert idx == 1
    assert rep.covered == 2


def test_best_turbine_returns_none_when_nothing_reaches():
    got = best_turbine(
        [(0.0, 0.0)],
        [{"pos": [4000.0, 4000.0], "hub_height": 100.0, "blade_len": 10.0}],
        elevation_deg=45.0,
        azimuth_deg=90.0,
        target_z=0.0,
        wind_dir_deg=WIND_FROM_EAST,
    )
    assert got is None


# --------------------------------------------------------------------------- #
# The scenario's own claim
# --------------------------------------------------------------------------- #


def test_khavda_bladeshadow_scenario_really_has_a_stimulus():
    """The anti-SC-05 guard, and the reason this module exists.

    `khavda_bladeshadow.yaml`'s header states a measured coverage figure. That claim
    is only true for one (turbine position, sun timestamp, mount height) triple, and
    every one of those is editable — the window is 40 minutes wide and coverage
    collapses from 22.9% to 0.3% by 02:00Z. Without this test, moving the sun by
    forty minutes silently restores exactly the hollow null the scenario was written
    to fix, and the KPI would keep reporting 0.00 as a success.

    Asserts against the **composed** config via `load_scenario`, so it also pins the
    `turbine_scatter.enabled: false` precedence trap: if the scatter ever wins again,
    the resolved turbine moves and coverage drops.
    """
    import pathlib

    from solar_twin.scenario import load_scenario
    from solar_twin.world.layout import FarmLayout
    from solar_twin.world.siting import resolve_turbines
    from solar_twin.world.solar import parse_timestamp, solar_position

    root = pathlib.Path(__file__).resolve().parents[1]
    scn = load_scenario(str(root / "configs" / "scenarios" / "khavda_bladeshadow.yaml"))
    cfg = scn.farm_cfg

    turbines = resolve_turbines(cfg, None)
    assert len(turbines) == 1, (
        "the scenario must resolve to exactly ONE caster so a shadow on a module is "
        f"attributable to known geometry; got {len(turbines)}"
    )

    layout = FarmLayout(cfg)
    assert layout.tracker_rotation_deg() == pytest.approx(0.0), (
        "trackers must be stowed flat, or tracker self-shading confounds the blade "
        "shadow and no false fault is attributable"
    )

    elev, azim = solar_position(
        layout.anchor.lat0, layout.anchor.lon0, parse_timestamp(cfg["sun"]["timestamp"])
    )
    panels = [(s.position[0], s.position[1]) for s in layout.sites]
    rep = assess(
        panels[::97],  # a prime stride, so the probe cannot alias onto the row pitch
        turbines[0],
        elevation_deg=elev,
        azimuth_deg=azim,
        target_z=float(cfg["panel"]["mount_height"]),
        wind_dir_deg=250.0,  # farm_khavda_block02's turbine_scatter.wind_from_deg
    )
    assert rep.usable, "the scenario's blade shadow does not reach the array at all"
    # Measured 2026-07-31: 23.5% of the probe grid, 22.9% of all 30,016 panels.
    # The floor is deliberately well below that — this guards against the stimulus
    # vanishing, not against it drifting by a percent.
    assert rep.covered_fraction > 0.10, (
        f"only {100 * rep.covered_fraction:.1f}% of panels lie under the swept blade "
        "shadow — the scenario header claims 22.9%"
    )
    # Dwell is the half that stops us overstating it: a shadow that covers panels
    # but never lingers is not a hazard perception can be scored against.
    assert rep.max_duty > 0.05, f"max blade dwell {rep.max_duty:.4f} is too brief to score"
