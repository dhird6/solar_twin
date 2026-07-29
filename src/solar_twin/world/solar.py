"""Solar position + single-axis tracker angle (pure, Isaac-free).

Why this exists: the Khavda site is a **horizontal single-axis tracker** (HSAT)
farm, so there is no such thing as a static panel tilt. Authoring every row flat
is only correct as a stow pose; a real farm mid-morning has every row rotated
toward the sun. Driving tilt from a real solar vector gives three things at once:

1. a twin that looks like the site actually looks at a given time of day,
2. **inter-row self-shading**, which is a far better `KPI-03` false-fault stimulus
   than the turbine-blade geometry that produced a hollow null in SLICE-3 — it
   lands on the panel surface, predictably, across many panels at once,
3. a sun direction for the stage light that agrees with the panel angles, instead
   of the two being set independently and silently disagreeing.

Accuracy, stated honestly (`NFR-07`): `solar_position` implements the standard
NOAA solar-position equations, good to roughly 0.1–0.5° for our latitudes and
years. That is far better than needed to place a shadow, and it is **not** an
ephemeris — do not use it for anything requiring arc-second accuracy. Atmospheric
refraction near the horizon is not modelled.
"""

from __future__ import annotations

import datetime as _dt
import math

#: Typical mechanical rotation limit of a commercial HSAT tracker, degrees either
#: side of horizontal. ⚠ verify against the actual tracker datasheet for the site.
DEFAULT_MAX_ROTATION_DEG = 60.0


def solar_position(
    lat_deg: float, lon_deg: float, when_utc: _dt.datetime
) -> tuple[float, float]:
    """Return (elevation_deg, azimuth_deg) of the sun. Azimuth is from NORTH,
    clockwise (so 90° = east, 180° = south), matching the compass convention the
    rest of the project uses for `GeoAnchor.heading_deg`.

    Negative elevation means the sun is below the horizon (night).
    """
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=_dt.timezone.utc)
    when_utc = when_utc.astimezone(_dt.timezone.utc)

    doy = when_utc.timetuple().tm_yday
    hours = when_utc.hour + when_utc.minute / 60.0 + when_utc.second / 3600.0

    # Fractional year (radians).
    g = 2.0 * math.pi / 365.0 * (doy - 1 + (hours - 12.0) / 24.0)

    # Equation of time (minutes) and solar declination (radians).
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )

    # True solar time -> hour angle. `4 * lon` converts degrees to minutes.
    tst = (hours * 60.0) + eqtime + 4.0 * lon_deg
    ha = math.radians(tst / 4.0 - 180.0)

    lat = math.radians(lat_deg)
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zenith = math.acos(cos_zen)
    elevation = 90.0 - math.degrees(zenith)

    # Azimuth from north, clockwise. atan2 form avoids the quadrant ambiguity the
    # arccos form has around solar noon.
    az = math.degrees(
        math.atan2(
            math.sin(ha),
            math.cos(ha) * math.sin(lat) - math.tan(decl) * math.cos(lat),
        )
    )
    azimuth = (az + 180.0) % 360.0
    return (elevation, azimuth)


def tracker_rotation_deg(
    elevation_deg: float,
    azimuth_deg: float,
    axis_azimuth_deg: float = 0.0,
    max_rotation_deg: float = DEFAULT_MAX_ROTATION_DEG,
) -> float:
    """Ideal HSAT rotation about its (horizontal) axis, degrees.

    Derivation, so the sign convention is checkable rather than folklore. Build the
    unit sun vector in local ENU:

        e = cos(elev) * sin(az)      # east
        n = cos(elev) * cos(az)      # north
        u = sin(elev)                # up

    The tracker axis is horizontal, pointing along `axis_azimuth_deg` (0 = north,
    which is the Khavda case: torque tubes run north-south). Rotating about that
    axis can only swing the panel normal within the plane perpendicular to it, so
    the achievable normal is spanned by *up* and the *cross-axis* horizontal
    direction. Projecting the sun onto that plane gives

        rotation = atan2(cross_axis_component, up_component)

    which is positive when the sun is on the +cross-axis side (east, for a N-S
    axis, i.e. morning) — the panel faces east in the morning, as it must.

    At night (elevation <= 0) the tracker returns to **stow (0°)**, flat, which is
    what real trackers do. Result is clamped to ±`max_rotation_deg`.
    """
    if elevation_deg <= 0.0:
        return 0.0
    elev = math.radians(elevation_deg)
    az = math.radians(azimuth_deg)
    axis = math.radians(axis_azimuth_deg)

    e = math.cos(elev) * math.sin(az)
    n = math.cos(elev) * math.cos(az)
    u = math.sin(elev)

    # Horizontal direction perpendicular to the axis, 90° clockwise from it.
    cross = e * math.cos(axis) - n * math.sin(axis)
    rot = math.degrees(math.atan2(cross, u))
    return max(-max_rotation_deg, min(max_rotation_deg, rot))


def cross_axis_angle_deg(
    elevation_deg: float, azimuth_deg: float, axis_azimuth_deg: float = 0.0
) -> float:
    """The sun's projection angle onto the tracker's cross-axis plane, degrees
    from vertical — i.e. the rotation a tracker with *no* mechanical limit would
    take. Equals `tracker_rotation_deg` until the real tracker hits its stop.

    Kept separate because the gap between this and the clamped rotation is
    exactly what causes inter-row self-shading: while the tracker still tracks
    truly, its shadow is the row's own footprint and lands in the aisle; once it
    is pinned at the stop, the shadow lengthens without limit.
    """
    return tracker_rotation_deg(
        elevation_deg, azimuth_deg, axis_azimuth_deg, max_rotation_deg=90.0
    )


def shadow_chord_m(
    module_width_m: float,
    elevation_deg: float,
    azimuth_deg: float,
    axis_azimuth_deg: float = 0.0,
    max_rotation_deg: float = DEFAULT_MAX_ROTATION_DEG,
) -> float:
    """Width of one row's shadow measured ACROSS the aisle (cross-axis), metres.

    Cross-section geometry in the plane perpendicular to the torque tube. The
    module chord `w` is tilted `beta` from horizontal; the sun's projected ray
    arrives `theta` from vertical. Projecting both chord endpoints down the ray
    onto a horizontal plane gives

        chord = w * cos(theta - beta) / cos(theta)

    Two sanity anchors: at `theta == beta` (true tracking, panel normal on the
    sun) this is `w / cos(theta)`, and at `theta == beta == 0` (flat panel, sun
    overhead) it is `w`. Height cancels — for equal-height rows on level ground
    the shadow's *width* does not depend on how high the torque tube sits, only
    its position does.

    Returns 0.0 at or below the horizon (a stowed tracker at night). Near the
    horizon `cos(theta)` collapses and the chord runs away, so the result is
    capped at 1000x the module width rather than returning `inf`.
    """
    if elevation_deg <= 0.0:
        return 0.0
    theta = abs(cross_axis_angle_deg(elevation_deg, azimuth_deg, axis_azimuth_deg))
    beta = abs(
        tracker_rotation_deg(
            elevation_deg, azimuth_deg, axis_azimuth_deg, max_rotation_deg
        )
    )
    cos_theta = math.cos(math.radians(theta))
    if cos_theta <= 1e-3:
        return module_width_m * 1000.0
    chord = module_width_m * math.cos(math.radians(theta - beta)) / cos_theta
    return min(chord, module_width_m * 1000.0)


def self_shaded_fraction(
    row_pitch_m: float,
    module_width_m: float,
    elevation_deg: float,
    azimuth_deg: float,
    axis_azimuth_deg: float = 0.0,
    max_rotation_deg: float = DEFAULT_MAX_ROTATION_DEG,
) -> float:
    """Fraction (0..1) of a row's surface shaded by the row up-sun of it.

    This is the `KPI-03` stimulus, quantified before it is rendered — SLICE-3's
    turbine shadow was measured only after the fact and turned out to miss the
    panels entirely, producing a hollow "0% false faults". Computing the expected
    shading first means a scenario can be *asserted* to have a stimulus.

    Assumptions, all of which flatter the shadow (`NFR-07`): identical adjacent
    rows, equal height, level ground, and **no backtracking** — a real tracker
    controller would rotate back toward flat to avoid exactly this. So treat the
    number as the worst case, not as what the plant does.
    """
    if row_pitch_m <= 0.0:
        return 0.0
    chord = shadow_chord_m(
        module_width_m, elevation_deg, azimuth_deg, axis_azimuth_deg, max_rotation_deg
    )
    if chord <= row_pitch_m:
        return 0.0
    return min(1.0, (chord - row_pitch_m) / chord)


def parse_timestamp(value: str | _dt.datetime) -> _dt.datetime:
    """Accept an ISO-8601 string (or a datetime) as a UTC instant.

    A bare string with no offset is treated as UTC and NOT as local time — being
    explicit here avoids a silent multi-hour shadow error, which for a solar site
    is the difference between morning and afternoon shading.
    """
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)
