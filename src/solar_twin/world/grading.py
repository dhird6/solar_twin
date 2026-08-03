"""The engineered civil pad the plant actually sits on (pure-python, no Isaac).

WHY THIS EXISTS
---------------
`dem.py` samples **Copernicus GLO-30**, which is a *pre-grading* DSM: it is the
shape of the Rann of Kutch as the satellite found it, not the surface the
contractor handed over. Building on the raw DEM is wrong in a specific,
measurable way — `dem.fit_line` already reports it. On Khavda BLOCK-02 the worst
tracker row needed **0.461 m of pile-height variation** to sit on a straight
torque tube over raw terrain. Real single-axis-tracker piles have a tolerance far
tighter than that, so on the real site either the ground was graded or the row
would not have been built there. Modelling the pad closes that gap.

WHAT IS MODELLED, AND WHAT IS ASSUMED
-------------------------------------
A **least-squares plane** through the raw DEM across the hardware footprint,
blended back to natural terrain outside it.

A plane is the honest choice rather than a flat level pad, for two reasons:

* A 1.1 x 1.4 km site is **not** cut flat — that would move an absurd volume of
  earth. Utility PV is graded to a gentle, near-planar surface that keeps each
  row within pile tolerance and lets water run off.
* A least-squares plane is **cut/fill balanced by construction** (its residuals
  sum to zero), which is exactly what a contractor optimises for: moved earth is
  the cost, so you balance cut against fill instead of importing fill.

⚠ **This is `INFERRED`, not surveyed** (`NFR-07`). We hold the DC hardware
drawing, not the civil grading plan. What the pad *is* here is our engineering
assumption; what it *reports* — cut/fill depths and volumes — are real
quantities derived from it, and `max_cut_m`/`max_fill_m` are the numbers to
sanity-check against a real earthworks figure if the civil drawing ever arrives.

**Construction tolerance is modelled, and that matters.** A mathematical plane is
perfectly straight along *every* line, so `dem.fit_line` returns a residual of
exactly **0.000 m** on it — i.e. a planar pad claims the piles need zero
adjustment. That is precisely the flattering approximation `NFR-07` exists to
forbid: real grading is signed off to a *tolerance* (commonly +/-25 mm for a PV
pad), not to a plane. So the pad carries a seeded, deterministic, smooth
`tolerance_m` deviation. Measured on Khavda BLOCK-02, worst-row pile-height
variation goes 0.532 m (raw desert) -> 0.000 m (bare plane, dishonest) ->
~tolerance (graded pad as modelled).

The tolerance field uses three **incommensurate** octaves for the same reason
`world/site.py`'s ground colour does: a single sin*cos pair beats into visible
corduroy stripes across a site this size.

The pad also **fails loud** rather than silently flattening a site it does not
suit: `check()` rejects a fit whose grade exceeds the declared maximum, or whose
cut/fill exceeds a declared depth, because either means the plane is the wrong
model for that terrain and the answer is terracing, not a steeper plane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _smoothstep(t: float) -> float:
    """Hermite 3t^2-2t^3 on [0,1]. Used for the pad->natural blend because a
    LINEAR blend leaves a slope discontinuity at both ends of the ramp, which
    reads as a visible crease in the ground mesh."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


@dataclass(frozen=True)
class GradingReport:
    """What the earthworks would actually amount to. Real quantities from an
    assumed surface — quote them as such."""

    max_cut_m: float      # deepest excavation (natural above pad)
    max_fill_m: float     # deepest fill (pad above natural)
    rms_m: float
    cut_volume_m3: float
    fill_volume_m3: float
    grade_pct: float      # steepest slope of the fitted plane, percent
    samples: int

    @property
    def balance_ratio(self) -> float:
        """fill / cut. A least-squares plane should land near 1.0 — that is the
        cut/fill balance being claimed above, so it is worth asserting rather
        than trusting."""
        return self.fill_volume_m3 / self.cut_volume_m3 if self.cut_volume_m3 else float("inf")

    def describe(self) -> str:
        return (
            f"graded pad: grade {self.grade_pct:.2f}%, "
            f"cut {self.max_cut_m:.3f} m / fill {self.max_fill_m:.3f} m "
            f"(rms {self.rms_m:.3f} m), "
            f"volumes {self.cut_volume_m3:,.0f} / {self.fill_volume_m3:,.0f} m3 "
            f"(balance {self.balance_ratio:.2f}), n={self.samples}"
        )

    def to_dict(self) -> dict:
        return {
            "max_cut_m": self.max_cut_m,
            "max_fill_m": self.max_fill_m,
            "rms_m": self.rms_m,
            "cut_volume_m3": self.cut_volume_m3,
            "fill_volume_m3": self.fill_volume_m3,
            "grade_pct": self.grade_pct,
            "balance_ratio": self.balance_ratio,
            "samples": self.samples,
            "provenance": "INFERRED — least-squares pad, no civil drawing held",
        }


@dataclass
class GradedPad:
    """`z = a + b*x + c*y` over the footprint, blended to natural terrain outside.

    Heights are in the same stage-local metres `DemTerrain.height` returns, so
    this is a drop-in replacement for it in the builder.
    """

    a: float
    b: float
    c: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    blend_m: float = 40.0
    #: Construction tolerance, metres (peak deviation from the ideal plane). A
    #: real pad is signed off to a tolerance; 0.0 would claim a perfect surface
    #: and report zero pile adjustment, which is not a buildable claim.
    tolerance_m: float = 0.025
    tolerance_seed: int = 20260729
    report: GradingReport | None = field(default=None, compare=False)
    #: The natural surface, kept so the blend and cut/fill stay computable.
    natural: object | None = field(default=None, compare=False, repr=False)

    # ------------------------------------------------------------------ #
    def ideal_plane(self, x: float, y: float) -> float:
        """The design surface: the plane the contractor was aiming at."""
        return self.a + self.b * x + self.c * y

    def tolerance_at(self, x: float, y: float) -> float:
        """Deterministic as-built deviation from the design plane.

        Seeded and continuous, so the builder, the panel mounts and the drone
        waypoints all agree on where the ground is — the same contract
        `layout.terrain_height` upholds. Three incommensurate wavelengths avoid
        the corduroy banding a single sin*cos pair produces over 300+ m.
        """
        if self.tolerance_m <= 0.0:
            return 0.0
        # Seed shifts the phases; wavelengths are deliberately non-harmonic.
        ph = (self.tolerance_seed % 997) * 0.01
        v = (
            math.sin(x / 37.0 + ph) * math.cos(y / 43.0 + ph)
            + 0.5 * math.sin(x / 17.0 - ph) * math.cos(y / 19.0 + ph)
            + 0.25 * math.sin(x / 7.3 + ph) * math.cos(y / 8.1 - ph)
        )
        return self.tolerance_m * v / 1.75   # /1.75 keeps the peak at tolerance_m

    def plane(self, x: float, y: float) -> float:
        """The as-built graded surface: design plane + construction tolerance."""
        return self.ideal_plane(x, y) + self.tolerance_at(x, y)

    def _blend_weight(self, x: float, y: float) -> float:
        """1.0 inside the footprint, 0.0 beyond footprint+blend, smooth between.

        Distance is the Chebyshev distance to the footprint rectangle — the pad
        has square corners, so blending on the per-axis overshoot keeps the ramp
        the same width along an edge and around a corner.
        """
        if self.blend_m <= 0.0:
            return 1.0 if (self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y) else 0.0
        dx = max(self.min_x - x, x - self.max_x, 0.0)
        dy = max(self.min_y - y, y - self.max_y, 0.0)
        d = max(dx, dy)
        return 1.0 - _smoothstep(d / self.blend_m)

    def height(self, x: float, y: float) -> float:
        """Graded height where the plant is, natural terrain away from it."""
        w = self._blend_weight(x, y)
        if w >= 1.0:
            return self.plane(x, y)
        nat = self.natural.height(x, y) if self.natural is not None else self.plane(x, y)
        if w <= 0.0:
            return nat
        return w * self.plane(x, y) + (1.0 - w) * nat

    def cut_fill(self, x: float, y: float) -> float:
        """Pad minus natural: >0 is fill (earth added), <0 is cut (excavated)."""
        if self.natural is None:
            return 0.0
        return self.plane(x, y) - self.natural.height(x, y)

    # ------------------------------------------------------------------ #
    def check(self, max_grade_pct: float = 2.0, max_cut_fill_m: float = 1.0) -> list[str]:
        """Reasons this pad should NOT be used, or an empty list.

        Fails loud on purpose. A plane that needs metres of cut, or that slopes
        more than a plant pad plausibly would, is the wrong model for that
        terrain — the real answer would be terraced blocks, and silently
        flattening the site instead would be the flattering-approximation
        failure `NFR-07` exists to prevent.
        """
        if self.report is None:
            return ["no grading report — pad was not fitted to terrain"]
        problems = []
        if self.report.grade_pct > max_grade_pct:
            problems.append(
                f"pad grade {self.report.grade_pct:.2f}% exceeds max {max_grade_pct:.2f}% "
                f"— terrain likely needs terracing, not one plane"
            )
        worst = max(self.report.max_cut_m, self.report.max_fill_m)
        if worst > max_cut_fill_m:
            problems.append(
                f"cut/fill {worst:.3f} m exceeds max {max_cut_fill_m:.3f} m "
                f"— implausible earthworks for one planar pad"
            )
        return problems


def fit_pad(
    terrain,
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
    samples_per_axis: int = 24,
    blend_m: float = 40.0,
) -> GradedPad:
    """Least-squares plane through `terrain.height` over the footprint.

    `terrain` only needs a `height(x, y) -> float`, so this fits a real DEM, a
    flat site, or a synthetic field without knowing which.
    """
    n = max(2, int(samples_per_axis))
    pts: list[tuple[float, float, float]] = []
    for i in range(n):
        x = min_x + (max_x - min_x) * i / (n - 1)
        for j in range(n):
            y = min_y + (max_y - min_y) * j / (n - 1)
            pts.append((x, y, float(terrain.height(x, y))))

    # Normal equations for z = a + b*x + c*y, solved on centred coordinates so
    # the 3x3 stays well-conditioned at survey-sized offsets (x can be ~1e5 m,
    # and x^2 then swamps the count term).
    m = len(pts)
    mx = sum(p[0] for p in pts) / m
    my = sum(p[1] for p in pts) / m
    mz = sum(p[2] for p in pts) / m
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    sxz = sum((p[0] - mx) * (p[2] - mz) for p in pts)
    syz = sum((p[1] - my) * (p[2] - mz) for p in pts)

    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-9:
        # Degenerate footprint (a line or a point): a tilt is not identifiable,
        # so return the mean plane rather than inventing a slope.
        b = c = 0.0
    else:
        b = (sxz * syy - syz * sxy) / det
        c = (syz * sxx - sxz * sxy) / det
    a = mz - b * mx - c * my

    # Cut/fill from the same samples, each standing for one cell of the grid.
    cell_area = ((max_x - min_x) / (n - 1)) * ((max_y - min_y) / (n - 1))
    cut_v = fill_v = 0.0
    max_cut = max_fill = 0.0
    sq = 0.0
    for x, y, z in pts:
        d = (a + b * x + c * y) - z
        sq += d * d
        if d >= 0:
            fill_v += d * cell_area
            max_fill = max(max_fill, d)
        else:
            cut_v += -d * cell_area
            max_cut = max(max_cut, -d)

    grade_pct = ((b * b + c * c) ** 0.5) * 100.0
    report = GradingReport(
        max_cut_m=max_cut,
        max_fill_m=max_fill,
        rms_m=(sq / m) ** 0.5,
        cut_volume_m3=cut_v,
        fill_volume_m3=fill_v,
        grade_pct=grade_pct,
        samples=m,
    )
    return GradedPad(
        a=a, b=b, c=c,
        min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y,
        blend_m=blend_m, report=report, natural=terrain,
    )
