"""Solar position + HSAT tracker angle (pure, no Isaac).

Anchored on the real site: Khavda A10b BLOCK-02 at 24.088 N, 69.418 E.
"""

import datetime as dt
import math

from solar_twin.world.solar import (
    cross_axis_angle_deg,
    parse_timestamp,
    self_shaded_fraction,
    shadow_chord_m,
    solar_position,
    tracker_rotation_deg,
)

LAT, LON = 24.088, 69.418  # the real block, from the vendor CAD georeference


def test_june_solar_noon_is_nearly_overhead():
    """Khavda (24.088 N) sits just north of the June declination (+23.44), so the
    solstice sun passes within ~1 degree of zenith. Solar noon at 69.418 E is
    12h - 69.418/15 = 07:22 UTC."""
    elev, _ = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 7, 22))
    assert 88.0 < elev <= 90.0, elev
    # Azimuth is deliberately NOT asserted here: within a degree of zenith it is
    # ill-conditioned (a tiny angular displacement swings it tens of degrees) and
    # carries no physical meaning. test_december_noon_is_due_south covers it.


def test_december_noon_is_due_south_and_low():
    """A well-conditioned azimuth check: the December sun is ~42 deg up, due south."""
    elev, az = solar_position(LAT, LON, dt.datetime(2026, 12, 21, 7, 22))
    assert 40.0 < elev < 46.0, elev
    assert abs(az - 180.0) < 3.0, az


def test_night_is_below_the_horizon():
    elev, _ = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 20, 0))
    assert elev < 0.0, elev


def test_morning_sun_is_east_afternoon_is_west():
    e_am, az_am = solar_position(LAT, LON, dt.datetime(2026, 3, 21, 3, 0))
    e_pm, az_pm = solar_position(LAT, LON, dt.datetime(2026, 3, 21, 9, 30))
    assert e_am > 0 and e_pm > 0
    assert 45.0 < az_am < 135.0, az_am    # eastern half
    assert 225.0 < az_pm < 315.0, az_pm   # western half


def test_tracker_faces_east_in_the_morning_west_in_the_afternoon():
    """The sign convention that matters: a N-S axis tracker must chase the sun."""
    elev_am, az_am = solar_position(LAT, LON, dt.datetime(2026, 3, 21, 3, 0))
    rot_am = tracker_rotation_deg(elev_am, az_am)
    elev_pm, az_pm = solar_position(LAT, LON, dt.datetime(2026, 3, 21, 9, 30))
    rot_pm = tracker_rotation_deg(elev_pm, az_pm)
    assert rot_am > 5.0, rot_am    # tilted toward the east
    assert rot_pm < -5.0, rot_pm   # tilted toward the west


def test_tracker_is_flat_at_solar_noon_on_a_north_south_axis():
    """With the sun due south, a N-S axis tracker has nothing to rotate for."""
    elev, az = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 7, 22))
    assert abs(tracker_rotation_deg(elev, az)) < 12.0


def test_tracker_stows_flat_at_night():
    assert tracker_rotation_deg(-5.0, 90.0) == 0.0


def test_rotation_is_clamped_to_the_mechanical_limit():
    # Sun barely above the horizon in the east -> ideal angle is near 90 degrees.
    assert math.isclose(tracker_rotation_deg(1.0, 90.0, max_rotation_deg=60.0), 60.0)
    assert math.isclose(tracker_rotation_deg(1.0, 270.0, max_rotation_deg=60.0), -60.0)


def test_naive_timestamp_is_treated_as_utc_not_local():
    a = parse_timestamp("2026-06-21T06:22:00")
    b = parse_timestamp("2026-06-21T06:22:00+00:00")
    assert a == b
    assert a.tzinfo is not None


# --- Inter-row self-shading: the KPI-03 stimulus, quantified ----------------
# Khavda BLOCK-02 module chord and the two row pitches present in the CAD.
MODULE_W = 2.278
PITCH_5, PITCH_6 = 5.0, 6.0


def test_shadow_chord_equals_module_width_with_the_sun_overhead():
    """Flat panel, sun straight up -> the shadow is the panel's own footprint."""
    assert math.isclose(shadow_chord_m(MODULE_W, 90.0, 180.0), MODULE_W, rel_tol=1e-6)


def test_shadow_chord_matches_w_over_cos_while_the_tracker_still_tracks():
    """Below the mechanical stop the panel normal is ON the sun, so the closed
    form collapses to w / cos(theta) — an independent check of the projection."""
    elev, az = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 4, 0))
    theta = cross_axis_angle_deg(elev, az)
    assert abs(theta) < 60.0, theta  # still tracking truly, not clamped
    expect = MODULE_W / math.cos(math.radians(abs(theta)))
    assert math.isclose(shadow_chord_m(MODULE_W, elev, az), expect, rel_tol=1e-9)


def test_no_self_shading_once_the_tracker_tracks_truly():
    """Mid-morning the trackers are off their stops and the aisle is clear: a
    true-tracking row's shadow is w/cos(theta) = 3.27 m, under both pitches."""
    elev, az = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 4, 0))
    assert self_shaded_fraction(PITCH_5, MODULE_W, elev, az) == 0.0
    assert self_shaded_fraction(PITCH_6, MODULE_W, elev, az) == 0.0


def test_the_scenario_timestamp_actually_produces_on_panel_shading():
    """⚠ THE ANTI-HOLLOW-NULL GUARD. SLICE-3's KPI-03 read 0.00 because the
    turbine shadow missed the panels entirely — the metric was right and the
    STIMULUS was absent. configs/scenarios/khavda_selfshade.yaml claims 02:00Z
    pins the trackers at their 60 deg stop and shades a real fraction of every
    row. Assert that here, so editing the timestamp cannot silently gut the test.
    """
    elev, az = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 2, 0))
    assert 15.0 < elev < 20.0, elev                      # low, but well up
    assert math.isclose(tracker_rotation_deg(elev, az), 60.0)   # pinned at the stop
    assert cross_axis_angle_deg(elev, az) > 70.0                # sun is past it
    # ~30% shaded at the 5 m pitch, ~17% at the 6 m pitch. Both unmistakable.
    assert 0.25 < self_shaded_fraction(PITCH_5, MODULE_W, elev, az) < 0.35
    assert 0.12 < self_shaded_fraction(PITCH_6, MODULE_W, elev, az) < 0.22


def test_self_shading_deepens_as_the_sun_drops():
    """Monotone in the right direction: earlier morning -> longer shadow."""
    fracs = []
    for hour_utc in (2.5, 2.0, 1.5, 1.0):
        elev, az = solar_position(
            LAT, LON, dt.datetime(2026, 6, 21) + dt.timedelta(hours=hour_utc)
        )
        fracs.append(self_shaded_fraction(PITCH_5, MODULE_W, elev, az))
    assert fracs == sorted(fracs), fracs
    assert fracs[0] < 0.15 and fracs[-1] > 0.6


def test_night_and_degenerate_inputs_shade_nothing():
    assert shadow_chord_m(MODULE_W, -3.0, 90.0) == 0.0
    assert self_shaded_fraction(PITCH_5, MODULE_W, -3.0, 90.0) == 0.0
    assert self_shaded_fraction(0.0, MODULE_W, 20.0, 90.0) == 0.0


def test_the_low_sun_scenario_timestamp_shades_about_half_the_row():
    """Same anti-hollow-null guard, for the SECOND KPI-03 point.
    configs/scenarios/khavda_selfshade_lowsun.yaml claims 01:30Z roughly doubles
    the shaded fraction while dimming the scene. Assert the geometry it claims,
    so the timestamp cannot be edited into a weaker stimulus unnoticed."""
    elev, az = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 1, 30))
    assert 9.0 < elev < 12.5, elev                              # low, not sunrise
    assert math.isclose(tracker_rotation_deg(elev, az), 60.0)    # still on the stop
    assert cross_axis_angle_deg(elev, az) > 77.0
    assert 9.5 < shadow_chord_m(MODULE_W, elev, az) < 12.0       # ~10.9 m chord
    # ~54% shaded at the 5 m pitch, ~45% at 6 m — about half the module.
    assert 0.50 < self_shaded_fraction(PITCH_5, MODULE_W, elev, az) < 0.60
    assert 0.40 < self_shaded_fraction(PITCH_6, MODULE_W, elev, az) < 0.50


def test_the_two_kpi03_points_differ_only_by_a_real_step_in_shading():
    """The pair has to be a curve, not two names for the same stimulus."""
    hard = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 2, 0))
    low = solar_position(LAT, LON, dt.datetime(2026, 6, 21, 1, 30))
    f_hard = self_shaded_fraction(PITCH_5, MODULE_W, *hard)
    f_low = self_shaded_fraction(PITCH_5, MODULE_W, *low)
    assert f_low - f_hard > 0.15, (f_hard, f_low)
    assert low[0] < hard[0]  # and the scene really is dimmer, not just shadier
