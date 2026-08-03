"""Where a turbine's blade shadow actually lands — pure geometry, no Isaac.

`KPI-03` needs a false-fault *stimulus*, and SLICE-3 learned the hard way that a
stimulus you have not located is not a stimulus. `sweeping_shadow.yaml` (SC-05)
was authored, run, and produced a hollow null: the blade shadow "sailed over the
elevated rows onto the ground" (`khavda_selfshade.yaml`'s header records it). The
scenario looked correct, the mission ran, the false-fault rate came back 0.00, and
the number meant nothing because no shadow ever touched a module.

This module exists so that failure is a computation instead of an afternoon. It
answers two questions, both before anything is rendered:

1. **Does the swept blade shadow reach the array at all?** `SweptShadow.covers()`
   over the panel positions gives the count — the honest KPI-03 denominator.
2. **How hard is a given panel hit?** `duty_fraction()` is the fraction of one
   rotor revolution during which a blade shadow lies on that point. A blade is a
   narrow moving bar, not a blanket: a panel under the swept *disc* may still only
   be shaded ~8% of the time, and quoting the disc as the stimulus overstates it
   by an order of magnitude.

## The detail that decides everything: shadows land on the MODULE plane

A shadow's reach is set by the height of the caster **above the surface it lands
on**, so the displacement uses `hub_height - target_z`, not `hub_height`. On this
site that is a 1.2% correction (a 120 m hub over 1.5 m torque tubes) and does not
matter much; on the old 14 m-hub synthetic farm it is 11% and does. It is written
this way because the class of error it belongs to — computing the shadow onto z=0
and then measuring panels that are not at z=0 — is exactly what produced the SC-05
null. `target_z` is required, not defaulted, so a caller has to think about it.

## What this is not

⚠ **Hard shadows only.** The sun is treated as a point source, so edges are sharp.
The real penumbra of a 70 m blade at 500 m is metres wide and softens the contrast
that perception actually keys on; the sun's ~0.53 deg angular diameter alone
smears the edge by ~4.6 m at that range. So `duty_fraction` is an *upper* bound on
how sharply a panel is shaded, and a `covers()` count is a bound on extent, not a
prediction of what the VLM will see. That is what `tools/verify_shade.py` is for —
this module says where to point it, and the pixels decide.

⚠ **Rotor plane is normal to the wind.** Real turbines yaw with a lag and rarely
sit exactly into the wind. One yaw angle, no misalignment modelled.

Conventions match the rest of the project (`CLAUDE.md`): stage-local metres, Z-up,
x=east, y=north, azimuth from NORTH clockwise, and `wind_dir_deg` is the met
convention bearing the wind blows *from* — imported from `world/windfield.py`
rather than re-derived, so siting, wake and shadow cannot disagree about which way
the wind goes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from solar_twin.world.windfield import downwind_unit

#: Blade chord used when a config does not give one, in metres. ⚠ INFERRED: a
#: 70 m utility blade has a max chord around 4-4.5 m (roughly 6% of length),
#: tapering to well under a metre at the tip. A single number cannot be right
#: along the whole span; this is the wide end, so `duty_fraction` is generous.
DEFAULT_BLADE_CHORD_M = 4.0

#: Blades on a utility turbine. Three, universally, for this class of machine.
DEFAULT_BLADE_COUNT = 3

#: Below this solar elevation the shadow is treated as unusable. At 3 deg a 120 m
#: hub throws a 2.3 km shadow, the flat-earth assumption below has visibly failed,
#: and the whole scene is too dim to separate "shaded" from "underexposed" anyway
#: (the confound `khavda_selfshade.yaml` documents at 01:30Z).
MIN_USABLE_ELEVATION_DEG = 3.0

#: The sun's angular diameter, degrees. Not a modelling choice: `farm_builder`
#: authors `/World/Sun` with `CreateAngleAttr(0.53)`, so this is the figure the
#: renderer actually uses and the penumbra below is physically rendered, not
#: theoretical.
SUN_ANGULAR_DIAMETER_DEG = 0.53


def penumbra_width_m(shadow_distance_m: float) -> float:
    """Width of the soft edge on a shadow cast from this far away, in metres.

    The sun is an extended source, so every shadow edge is smeared by
    `distance * tan(angular diameter)`. At Khavda's 780 m blade-shadow throw that is
    7.2 m — wider than a blade.
    """
    return abs(shadow_distance_m) * math.tan(math.radians(SUN_ANGULAR_DIAMETER_DEG))


def casts_umbra(caster_width_m: float, shadow_distance_m: float) -> bool:
    """Can a caster this wide, this far away, produce **full** shadow anywhere?

    Only if it is wider than its own penumbra. Below that it never fully occludes
    the solar disc — it dims it partially, over a smear wider than the object.

    ⚠ **This is the result that retired the blade shadow as a `KPI-03` stimulus, and
    it is geometric, not a tuning problem.** Measured 2026-07-31 on the `SC-14` stage:
    the swept-disc model said 22.9% of panels were covered and
    `tools/verify_blade_sweep.py` found no signal in the pixels — a 4.9% brightness
    dip on the most-covered panel against 7.1% on a geometrically-clear control, i.e.
    the control varied *more* than the target. The reason is here: a 4 m blade chord
    at a 780 m throw has a 7.2 m penumbra, so `casts_umbra(4.0, 780.0)` is False and
    there is no full shadow to find. `max_umbra_distance_m` gives the ceiling — about
    432 m for a 4 m blade, and Khavda's nearest real turbine stands at 546 m.

    Two honest consequences: a far turbine's blade shadow can never be a hard-edged
    stimulus at this site, and `duty_fraction` describes *geometric* coverage only —
    never assume it predicts a visible shadow.
    """
    return caster_width_m > penumbra_width_m(shadow_distance_m)


def max_umbra_distance_m(caster_width_m: float) -> float:
    """Furthest a caster this wide can be and still cast full shadow, in metres."""
    return caster_width_m / math.tan(math.radians(SUN_ANGULAR_DIAMETER_DEG))


def shadow_direction(azimuth_deg: float) -> tuple[float, float]:
    """Horizontal unit vector a shadow TRAVELS along, for a sun at `azimuth_deg`.

    Azimuth is from north, clockwise, so the sun lies along `(sin a, cos a)` and
    its shadows fall the opposite way. Kept as its own function because the sign
    of this vector is the single easiest thing to get backwards, and getting it
    backwards puts the stimulus on the far side of the plant from the panels —
    which is indistinguishable, in the KPI, from having no stimulus at all.
    """
    theta = math.radians(azimuth_deg)
    return -math.sin(theta), -math.cos(theta)


def shadow_offset_m(height_above_target_m: float, elevation_deg: float) -> float:
    """How far a shadow is displaced horizontally, for a caster this far above the
    surface it lands on.

    ``h / tan(elevation)``. Raises below `MIN_USABLE_ELEVATION_DEG` rather than
    returning a very large number, because every caller of this is trying to land
    a shadow on a specific row and a silent 2 km displacement reads as "the
    geometry is fine, the shadow just is not here".
    """
    if elevation_deg < MIN_USABLE_ELEVATION_DEG:
        raise ValueError(
            f"solar elevation {elevation_deg:.2f} deg is below the usable floor of "
            f"{MIN_USABLE_ELEVATION_DEG} deg — the shadow is longer than the flat-ground "
            "assumption supports and the scene is too dim to score"
        )
    if height_above_target_m < 0.0:
        raise ValueError("height_above_target_m must be >= 0 (caster above the surface)")
    return height_above_target_m / math.tan(math.radians(elevation_deg))


@dataclass(frozen=True)
class SweptShadow:
    """The region a rotor's blades sweep a shadow across, on one target plane.

    The rotor disc's shadow under parallel projection is an ellipse: the disc is a
    circle of radius `rotor_radius_m` standing in a vertical plane, and projecting
    it onto the ground stretches its vertical axis by `1/tan(elevation)` along the
    shadow direction while leaving its horizontal axis alone. `covers()` tests a
    point against that ellipse exactly, by inverting the 2x2 map rather than
    approximating it with a circle or an axis-aligned box.

    Build with `from_turbine`; the fields are the resolved geometry so a caller can
    print them into a scenario header and have the arithmetic on the record.
    """

    #: Ellipse centre: where the HUB's own shadow lands.
    center_x: float
    center_y: float
    #: Semi-axis along the rotor's horizontal axis (= rotor radius, unprojected).
    axis_cross: tuple[float, float]
    #: Semi-axis along the shadow direction (= rotor radius / tan(elevation)).
    axis_along: tuple[float, float]
    rotor_radius_m: float
    hub_offset_m: float
    elevation_deg: float
    azimuth_deg: float
    blade_chord_m: float = DEFAULT_BLADE_CHORD_M
    blade_count: int = DEFAULT_BLADE_COUNT

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_turbine(
        cls,
        spec: dict,
        *,
        elevation_deg: float,
        azimuth_deg: float,
        target_z: float,
        wind_dir_deg: float,
        tower_z: float = 0.0,
    ) -> "SweptShadow":
        """Resolve a `turbines:` entry — the same shape `farm_builder`, `keepout`
        and `windfield` all read — into a shadow region on the plane `target_z`.

        `tower_z` is the turbine base elevation; on graded terrain the tower and
        the modules do not stand on the same level, and the hub's height above the
        *modules* is what casts the shadow. Defaults to 0 for a flat stage.
        """
        pos = spec["pos"]
        hub_height = float(spec.get("hub_height", 18.0))
        blade_len = float(spec.get("blade_len", 8.0))
        chord = float(spec.get("blade_chord", DEFAULT_BLADE_CHORD_M))
        height = (tower_z + hub_height) - target_z
        offset = shadow_offset_m(height, elevation_deg)
        sdx, sdy = shadow_direction(azimuth_deg)
        # The rotor faces the wind, so its plane's horizontal axis is perpendicular
        # to the direction the wind travels. Reusing windfield's convention here is
        # deliberate — see the module docstring.
        wdx, wdy = downwind_unit(wind_dir_deg)
        perp = (-wdy, wdx)
        stretch = offset / height if height > 0.0 else 0.0
        return cls(
            center_x=float(pos[0]) + offset * sdx,
            center_y=float(pos[1]) + offset * sdy,
            axis_cross=(blade_len * perp[0], blade_len * perp[1]),
            axis_along=(blade_len * stretch * sdx, blade_len * stretch * sdy),
            rotor_radius_m=blade_len,
            hub_offset_m=offset,
            elevation_deg=float(elevation_deg),
            azimuth_deg=float(azimuth_deg),
            blade_chord_m=chord,
            blade_count=int(spec.get("blade_count", DEFAULT_BLADE_COUNT)),
        )

    # -- the region --------------------------------------------------------- #

    def _to_unit(self, x: float, y: float) -> tuple[float, float] | None:
        """Map a ground point into the rotor disc's own frame, where the shadow
        ellipse is the unit circle. None if the two axes are degenerate (parallel),
        which happens when the sun is exactly along the rotor's horizontal axis and
        the disc's shadow collapses to a line."""
        a, b = self.axis_cross
        c, d = self.axis_along
        det = a * d - b * c
        if abs(det) < 1e-9:
            return None
        px, py = x - self.center_x, y - self.center_y
        return (d * px - c * py) / det, (a * py - b * px) / det

    def covers(self, x: float, y: float) -> bool:
        """Is this ground point inside the swept disc shadow at some blade phase?"""
        uv = self._to_unit(x, y)
        if uv is None:
            return False
        u, v = uv
        return u * u + v * v <= 1.0

    def bbox(self) -> tuple[float, float, float, float]:
        """Axis-aligned `(min_x, min_y, max_x, max_y)` of the shadow ellipse.

        The extent of a parametrized ellipse, not of its axis endpoints — for a
        sheared ellipse those differ, and using the endpoints would under-report
        the region and hide a stimulus that is in fact present.
        """
        ex = math.hypot(self.axis_cross[0], self.axis_along[0])
        ey = math.hypot(self.axis_cross[1], self.axis_along[1])
        return (
            self.center_x - ex,
            self.center_y - ey,
            self.center_x + ex,
            self.center_y + ey,
        )

    def duty_fraction(self, x: float, y: float, samples: int = 720) -> float:
        """Fraction of one rotor revolution this point spends under a blade shadow.

        Each blade is treated as a straight bar of width `blade_chord_m` running
        from the hub to the tip. The point is "shaded" at a phase if it lies within
        half a chord of any blade's projected centre line, inside the blade's span.

        This is the number that says whether a stimulus is worth measuring. A point
        deep inside the swept disc still only sees a blade for roughly
        `blade_count * chord / (2 * pi * r)` of the time, where `r` is the point's
        distance from the hub shadow — about 5.5% at mid-span and 2.7% out at the
        tip, for three 4 m blades on a 70 m rotor. Perception is sampled at an
        instant, so a duty of 0.03 means ~3 frames in 100 carry the hazard, and a
        false-fault rate measured over too few frames is measuring nothing.
        """
        if samples <= 0:
            raise ValueError("samples must be > 0")
        if not self.covers(x, y):
            return 0.0
        uv = self._to_unit(x, y)
        if uv is None:
            return 0.0
        u, v = uv
        # In unit-disc space the blade at phase phi is the segment from the origin
        # to (cos phi, sin phi). The chord is a real-world width, so it has to be
        # carried into this frame: along the cross axis one unit is rotor_radius_m,
        # which is the axis the width is measured across.
        half_chord_u = (self.blade_chord_m / 2.0) / max(self.rotor_radius_m, 1e-9)
        r = math.hypot(u, v)
        if r < 1e-12:
            return 1.0  # the hub itself: always under a blade root
        point_angle = math.atan2(v, u)
        hits = 0
        for i in range(samples):
            phi = 2.0 * math.pi * i / samples
            for b in range(self.blade_count):
                blade = phi + 2.0 * math.pi * b / self.blade_count
                # Perpendicular distance from the point to the blade's line, in
                # unit-disc space; the blade spans r in [0, 1] so a point at radius
                # r is in span by construction (covers() already passed).
                d = abs(r * math.sin(point_angle - blade))
                along = r * math.cos(point_angle - blade)
                if d <= half_chord_u and along >= 0.0:
                    hits += 1
                    break
        return hits / samples


# --------------------------------------------------------------------------- #
# The tower's own shadow — narrower, but always there
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TowerShadow:
    """The shadow of the tower itself: a band from the base to the hub's shadow.

    **Added 2026-07-31 because the pixels found it and this module had not modelled
    it.** `tools/verify_shade.py` on the `SC-14` stage showed three tables markedly
    darker than the other 270 — glass mean 11.9/12.2/12.6 against a 13.4 median, dark
    fraction 51%/41%/42% against 24% — and their darkness ranked by *distance from
    the tower's shadow centre line* (0.6 m, 1.6 m, 2.5 m), not by blade dwell. The
    swept-disc model predicted those panels were covered, which was true, but it
    could not have predicted which of them would actually be dark.

    Why it matters more than it sounds: this shadow is **continuous**. A blade sweeps
    past a panel for ~7% of a revolution, which is what forces `SC-14`'s awkward
    run-sizing arithmetic (4.6 deg of rotor per panel, 78 panels to a revolution, a
    24-panel run being a phase lottery). A tower shadow is on the same panels in
    *every* frame, so a run of any length measures it. For `KPI-03` that is the
    better stimulus, and it is the one the site's real geometry actually delivers.

    ⚠ Narrow, though: a utility tower is ~4-6 m across at the base, so this shades a
    handful of tables, not a fifth of the array. Extent and dwell trade off against
    each other and neither model alone tells you what a run will see.
    """

    #: Shadow of the tower base (i.e. the tower foot itself).
    base_x: float
    base_y: float
    #: Where the hub's shadow lands — the far end of the band.
    tip_x: float
    tip_y: float
    half_width_m: float

    @classmethod
    def from_turbine(
        cls,
        spec: dict,
        *,
        elevation_deg: float,
        azimuth_deg: float,
        target_z: float,
        tower_z: float = 0.0,
    ) -> "TowerShadow":
        """`tower_diameter` is read from the spec if present, else INFERRED as 5.0 m —
        typical for a 120 m utility tower, and the figure the vendor digest does not
        carry (see `world/plot_digest.py`).

        No `wind_dir_deg`: a tower is a vertical cylinder, so unlike the rotor its
        shadow does not depend on which way the machine is yawed.
        """
        pos = spec["pos"]
        hub_height = float(spec.get("hub_height", 18.0))
        diameter = float(spec.get("tower_diameter", 5.0))
        offset = shadow_offset_m((tower_z + hub_height) - target_z, elevation_deg)
        sdx, sdy = shadow_direction(azimuth_deg)
        return cls(
            base_x=float(pos[0]),
            base_y=float(pos[1]),
            tip_x=float(pos[0]) + offset * sdx,
            tip_y=float(pos[1]) + offset * sdy,
            half_width_m=diameter / 2.0,
        )

    @property
    def length_m(self) -> float:
        return math.hypot(self.tip_x - self.base_x, self.tip_y - self.base_y)

    def distance_to_axis(self, x: float, y: float) -> float | None:
        """Perpendicular distance to the shadow's centre line, or None if the point is
        off either end of the band.

        Exported rather than kept private because it is the field that *explained the
        measurement*: darkness fell off monotonically with this number, which is what
        identified the mechanism. A verification tool wants to report it.
        """
        ax, ay = self.tip_x - self.base_x, self.tip_y - self.base_y
        length = math.hypot(ax, ay)
        if length < 1e-9:
            return None
        ax, ay = ax / length, ay / length
        px, py = x - self.base_x, y - self.base_y
        along = px * ax + py * ay
        if along < 0.0 or along > length:
            return None
        return abs(-px * ay + py * ax)

    def covers(self, x: float, y: float) -> bool:
        d = self.distance_to_axis(x, y)
        return d is not None and d <= self.half_width_m


# --------------------------------------------------------------------------- #
# Over a whole array
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StimulusReport:
    """What a (turbine, sun) pair does to a set of panels. The KPI-03 denominator,
    computed rather than assumed."""

    covered: int
    total: int
    mean_duty: float
    max_duty: float
    shadow: SweptShadow

    @property
    def covered_fraction(self) -> float:
        return self.covered / self.total if self.total else 0.0

    @property
    def usable(self) -> bool:
        """Is this worth rendering? Both halves have to hold: the shadow must reach
        a meaningful number of panels AND actually dwell on them. Either alone
        produces a KPI-03 that reads 0.00 for uninteresting reasons."""
        return self.covered > 0 and self.max_duty > 0.0


def assess(
    panel_xy: Sequence[tuple[float, float]],
    turbine: dict,
    *,
    elevation_deg: float,
    azimuth_deg: float,
    target_z: float,
    wind_dir_deg: float,
    tower_z: float = 0.0,
    duty_samples: int = 360,
) -> StimulusReport:
    """Score one turbine's blade shadow against a set of panel positions.

    Deliberately takes bare `(x, y)` tuples rather than a `FarmLayout`, so this
    stays testable with three hand-written points and cannot grow a dependency on
    the layout builder.
    """
    shadow = SweptShadow.from_turbine(
        turbine,
        elevation_deg=elevation_deg,
        azimuth_deg=azimuth_deg,
        target_z=target_z,
        wind_dir_deg=wind_dir_deg,
        tower_z=tower_z,
    )
    duties = [
        shadow.duty_fraction(x, y, samples=duty_samples)
        for x, y in panel_xy
        if shadow.covers(x, y)
    ]
    return StimulusReport(
        covered=len(duties),
        total=len(panel_xy),
        mean_duty=(sum(duties) / len(duties)) if duties else 0.0,
        max_duty=max(duties) if duties else 0.0,
        shadow=shadow,
    )


def best_turbine(
    panel_xy: Sequence[tuple[float, float]],
    turbines: Iterable[dict],
    *,
    elevation_deg: float,
    azimuth_deg: float,
    target_z: float,
    wind_dir_deg: float,
    tower_z: float = 0.0,
) -> tuple[int, StimulusReport] | None:
    """`(index, report)` for the turbine that shades the most panels, or None if
    none of them reach. Ranked by covered count, then by dwell — a shadow on many
    panels for no time is worth less than one on a few that lingers."""
    best: tuple[int, StimulusReport] | None = None
    for i, t in enumerate(turbines):
        try:
            rep = assess(
                panel_xy,
                t,
                elevation_deg=elevation_deg,
                azimuth_deg=azimuth_deg,
                target_z=target_z,
                wind_dir_deg=wind_dir_deg,
                tower_z=tower_z,
            )
        except ValueError:
            continue  # sun too low for this geometry; not a usable stimulus
        if not rep.usable:
            continue
        if best is None or (rep.covered, rep.mean_duty) > (best[1].covered, best[1].mean_duty):
            best = (i, rep)
    return best
