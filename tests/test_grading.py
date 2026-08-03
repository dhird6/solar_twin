"""The engineered civil pad (`world/grading.py`) — pure-python, no Isaac.

The pad exists because GLO-30 is a *pre-grading* DSM, and building on raw terrain
made the worst Khavda row need 0.461 m of pile-height variation. These tests pin
the two properties that make the model defensible — cut/fill balance and a loud
failure when a plane is the wrong model — plus the blend that stops the pad
cutting a visible step into the desert.
"""

from __future__ import annotations

import math

from solar_twin.world.grading import GradedPad, fit_pad


class _Tilted:
    """Natural terrain: a known plane, so the fit has a right answer."""

    def __init__(self, a=0.0, b=0.0, c=0.0):
        self.a, self.b, self.c = a, b, c

    def height(self, x, y):
        return self.a + self.b * x + self.c * y


class _Bowl:
    """A dish: no plane fits it, so cut and fill are both forced."""

    def height(self, x, y):
        return 0.001 * (x * x + y * y)


class _Step:
    """Half the site is 2 m higher — the case a single plane should reject."""

    def height(self, x, y):
        return 2.0 if x > 50.0 else 0.0


def test_a_planar_site_is_recovered_exactly():
    """If the ground is already a plane, the pad must BE that plane and move no
    earth — otherwise grading would invent work on a site that needs none."""
    nat = _Tilted(a=3.0, b=0.004, c=-0.002)
    pad = fit_pad(nat, 0.0, 100.0, 0.0, 200.0)
    pad.tolerance_m = 0.0   # judge the DESIGN plane; tolerance is tested below
    assert math.isclose(pad.b, 0.004, abs_tol=1e-9)
    assert math.isclose(pad.c, -0.002, abs_tol=1e-9)
    assert pad.report.max_cut_m < 1e-6
    assert pad.report.max_fill_m < 1e-6
    assert pad.report.rms_m < 1e-6


def test_least_squares_pad_balances_cut_against_fill():
    """The central engineering claim: a least-squares plane is cut/fill
    balanced, which is what makes it a plausible contractor surface rather than
    an arbitrary one."""
    pad = fit_pad(_Bowl(), -100.0, 100.0, -100.0, 100.0)
    assert pad.report.cut_volume_m3 > 0
    assert pad.report.fill_volume_m3 > 0
    # Balanced to a few percent — not exact, because volumes weight residuals by
    # area while the fit minimises their squares.
    assert 0.9 < pad.report.balance_ratio < 1.1, pad.report.describe()


def test_pad_reports_real_quantities():
    pad = fit_pad(_Bowl(), -100.0, 100.0, -100.0, 100.0)
    d = pad.report.to_dict()
    assert d["cut_volume_m3"] > 0 and d["fill_volume_m3"] > 0
    assert d["samples"] > 100
    # The provenance must travel with the numbers: the surface is our assumption.
    assert "INFERRED" in d["provenance"]
    assert "cut" in pad.report.describe()


def test_a_stepped_site_is_REJECTED_rather_than_flattened():
    """The anti-flattery guard (`NFR-07`). A 2 m step cannot be graded by one
    plane; silently averaging it would hide metres of imaginary earthworks."""
    pad = fit_pad(_Step(), 0.0, 100.0, 0.0, 100.0)
    problems = pad.check(max_grade_pct=2.0, max_cut_fill_m=1.0)
    assert problems, "a 2 m step must not pass as a single graded pad"
    assert any("cut/fill" in p or "grade" in p for p in problems)


def test_a_gentle_real_site_passes_the_check():
    nat = _Tilted(a=0.0, b=0.0008, c=0.0003)  # ~0.09% grade
    pad = fit_pad(nat, 0.0, 300.0, 0.0, 600.0)
    assert pad.check() == []
    assert pad.report.grade_pct < 0.2


def test_grade_percent_is_the_steepest_slope():
    pad = fit_pad(_Tilted(b=0.03, c=0.04), 0.0, 100.0, 0.0, 100.0)
    # hypot(3%, 4%) = 5%
    assert math.isclose(pad.report.grade_pct, 5.0, rel_tol=1e-6)


def test_height_is_the_pad_inside_and_natural_far_outside():
    nat = _Bowl()
    pad = fit_pad(nat, -50.0, 50.0, -50.0, 50.0, blend_m=25.0)
    # Inside: the engineered plane.
    assert math.isclose(pad.height(0.0, 0.0), pad.plane(0.0, 0.0), abs_tol=1e-9)
    # Well outside the blend: untouched desert.
    far = 200.0
    assert math.isclose(pad.height(far, 0.0), nat.height(far, 0.0), abs_tol=1e-9)


def test_the_blend_has_no_step_at_either_end():
    """A linear blend leaves a slope crease; smoothstep does not. Sampling
    across the ramp, consecutive differences must not jump — a visible step in
    the ground mesh is exactly what this avoids."""
    pad = fit_pad(_Bowl(), -50.0, 50.0, -50.0, 50.0, blend_m=30.0)
    xs = [50.0 + i * 0.5 for i in range(0, 120)]  # across the ramp and past it
    zs = [pad.height(x, 0.0) for x in xs]
    diffs = [abs(zs[i + 1] - zs[i]) for i in range(len(zs) - 1)]
    biggest, typical = max(diffs), sorted(diffs)[len(diffs) // 2]
    assert biggest < 10 * max(typical, 1e-6), f"step in the blend: {biggest} vs {typical}"


def test_cut_fill_sign_convention():
    """Positive = fill (earth added). Getting this backwards would report an
    excavation as an embankment."""
    nat = _Tilted(a=0.0)
    pad = GradedPad(a=1.0, b=0.0, c=0.0, min_x=0, max_x=10, min_y=0, max_y=10, natural=nat)
    assert pad.cut_fill(5.0, 5.0) > 0          # pad above ground -> fill
    pad_low = GradedPad(a=-1.0, b=0.0, c=0.0, min_x=0, max_x=10, min_y=0, max_y=10, natural=nat)
    assert pad_low.cut_fill(5.0, 5.0) < 0      # pad below ground -> cut


def test_degenerate_footprint_does_not_invent_a_slope():
    """A zero-width footprint cannot identify a tilt; returning the mean plane is
    correct, inventing a slope from noise is not."""
    pad = fit_pad(_Bowl(), 10.0, 10.0, 0.0, 100.0)
    assert pad.b == 0.0 and pad.c == 0.0


def test_no_natural_terrain_degrades_to_the_plane():
    pad = GradedPad(a=2.0, b=0.0, c=0.0, min_x=0, max_x=1, min_y=0, max_y=1,
                    tolerance_m=0.0)   # design plane only, so the value is exact
    assert pad.height(500.0, 500.0) == 2.0   # nothing to blend toward
    assert pad.cut_fill(0.5, 0.5) == 0.0
    assert pad.check() == ["no grading report — pad was not fitted to terrain"]


# --- construction tolerance: the anti-flattery guard on the pad itself ------


def test_a_bare_plane_would_claim_zero_pile_adjustment_so_tolerance_exists():
    """The reason `tolerance_m` is not optional. `fit_line`'s residual is exactly
    0 along any line of a mathematical plane, i.e. a pad with no tolerance claims
    the piles need no adjustment at all — not a buildable claim (`NFR-07`)."""
    from solar_twin.world.dem import fit_line

    pad = fit_pad(_Tilted(b=0.001, c=0.002), 0.0, 200.0, 0.0, 200.0)

    pad.tolerance_m = 0.0
    ideal = [(s, pad.height(10.0, s)) for s in range(0, 130, 8)]
    assert fit_line(ideal)[2] < 1e-9, "a bare plane must be perfectly straight"

    pad.tolerance_m = 0.025
    built = [(s, pad.height(10.0, s)) for s in range(0, 130, 8)]
    resid = fit_line(built)[2]
    assert resid > 1e-4, "an as-built pad must need SOME pile adjustment"
    assert resid <= 2 * pad.tolerance_m, f"residual {resid} exceeds the tolerance spec"


def test_tolerance_is_deterministic_and_bounded():
    """Seeded and continuous: the builder, the mounts and the waypoints must all
    agree on the ground, so this cannot be random per call."""
    pad = GradedPad(a=0.0, b=0.0, c=0.0, min_x=0, max_x=300, min_y=0, max_y=600,
                    tolerance_m=0.025)
    assert pad.tolerance_at(123.4, 456.7) == pad.tolerance_at(123.4, 456.7)
    worst = max(
        abs(pad.tolerance_at(x * 3.0, y * 6.0)) for x in range(100) for y in range(100)
    )
    assert worst <= 0.025 + 1e-9, f"tolerance field exceeded its own spec: {worst}"
    assert worst > 0.010, "tolerance field is suspiciously flat"


def test_tolerance_seed_changes_the_surface():
    a = GradedPad(a=0.0, b=0.0, c=0.0, min_x=0, max_x=10, min_y=0, max_y=10,
                  tolerance_seed=1)
    b = GradedPad(a=0.0, b=0.0, c=0.0, min_x=0, max_x=10, min_y=0, max_y=10,
                  tolerance_seed=2)
    assert a.tolerance_at(5.0, 5.0) != b.tolerance_at(5.0, 5.0)
