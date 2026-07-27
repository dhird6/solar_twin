"""Solar position + HSAT tracker angle (pure, no Isaac).

Anchored on the real site: Khavda A10b BLOCK-02 at 24.088 N, 69.418 E.
"""

import datetime as dt
import math

from solar_twin.world.solar import (
    parse_timestamp,
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
