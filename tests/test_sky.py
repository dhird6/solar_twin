"""Physically-based sky model — pure, no Isaac, no GPU.

The point of these is the *invariant*, not the look. The `DomeLight` is both
background and ambient fill, so this texture's hemisphere mean IS the scene's
fill level, and the fill level sets how far KPI-03's shadows fill in. If the
swap from the legacy ramp to Preetham moved that mean, it would move KPI-03
without anyone editing a KPI.
"""

from __future__ import annotations

import math

import pytest

from solar_twin.world import sky

# The two measured KPI-03 sun angles (world/solar.py, asserted in test_solar.py).
SC11 = (17.2, 71.4)  # khavda_selfshade, 02:00Z
SC12 = (10.7, 69.0)  # khavda_selfshade_lowsun, 01:30Z


def test_gradient_colour_still_interpolates_the_legacy_stops():
    """The ramp is retained as the photometric anchor — if it drifts, the
    normalisation target drifts with it."""
    assert sky.gradient_colour(0.0) == pytest.approx(sky.GRADIENT_STOPS[0][1])
    assert sky.gradient_colour(1.0) == pytest.approx(sky.GRADIENT_STOPS[-1][1])
    mid = sky.gradient_colour(0.05)
    assert sky.GRADIENT_STOPS[1][1][2] > mid[2] > sky.GRADIENT_STOPS[0][1][2]


def test_gradient_colour_clamps_outside_zero_one():
    assert sky.gradient_colour(-1.0) == pytest.approx(sky.gradient_colour(0.0))
    assert sky.gradient_colour(9.0) == pytest.approx(sky.gradient_colour(1.0))


@pytest.mark.parametrize("channel", ["Y", "x", "y"])
def test_perez_coefficients_are_linear_in_turbidity(channel):
    a = sky.perez_coefficients(2.0, channel)
    b = sky.perez_coefficients(4.0, channel)
    c = sky.perez_coefficients(6.0, channel)
    for lo, mid, hi in zip(a, b, c):
        assert mid == pytest.approx((lo + hi) / 2.0)


def test_perez_is_finite_at_the_horizon():
    """1/cos(theta) diverges at exactly 90 deg — the clamp must hold."""
    coeffs = sky.perez_coefficients(sky.DEFAULT_TURBIDITY, "Y")
    v = sky.perez(math.pi / 2.0, 0.5, coeffs)
    assert math.isfinite(v)


def test_sky_is_brightest_toward_the_sun():
    """The aureole is the feature the flat ramp could not express."""
    se, sa = math.radians(SC11[0]), math.radians(SC11[1])
    at_sun = sky.sky_radiance(se, sa, se, sa)
    away = sky.sky_radiance(se, sa + math.pi, se, sa)
    assert sum(at_sun) > sum(away)


def test_low_sun_sky_is_not_identical_to_higher_sun():
    """The whole reason for the swap: the ramp rendered 17.2 deg and 10.7 deg
    nearly identically."""
    zenith_11 = sky.sky_radiance(
        math.pi / 2, 0.0, math.radians(SC11[0]), math.radians(SC11[1])
    )
    zenith_12 = sky.sky_radiance(
        math.pi / 2, 0.0, math.radians(SC12[0]), math.radians(SC12[1])
    )
    assert zenith_11 != pytest.approx(zenith_12, rel=1e-3)


def test_xyY_to_rgb_handles_degenerate_y():
    assert sky.xyY_to_linear_rgb(0.3, 0.0, 1.0) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("angles", [SC11, SC12])
def test_exposure_pins_the_hemisphere_mean_to_the_legacy_ramp(angles):
    """THE invariant. Normalised physical fill == legacy ramp fill, at BOTH
    measured sun angles — not just whichever one was calibrated."""
    elev, azim = angles
    se, sa = math.radians(elev), math.radians(azim)
    scale = sky.exposure_for(elev, azim)
    got = sky.physical_hemisphere_mean(se, sa) * scale
    want = sky.gradient_hemisphere_mean(se, sa)
    assert got == pytest.approx(want, rel=1e-9)


def test_exposure_differs_between_the_two_sun_angles():
    """If one constant served both, the normalisation would be a fudge rather
    than a per-angle invariant."""
    assert sky.exposure_for(*SC11) != pytest.approx(sky.exposure_for(*SC12), rel=1e-3)


def test_hemisphere_mean_is_solid_angle_weighted():
    """An unweighted mean over latlong rows over-counts the zenith badly. A
    constant sky must integrate to that same constant."""
    flat = sky._hemisphere_mean(lambda e, a: (0.5, 0.5, 0.5))
    assert flat == pytest.approx(0.5, rel=1e-6)


class TestSkyImage:
    def test_shape_and_dtype(self):
        import numpy as np

        arr = sky.sky_image(64, *SC11, ground_rgb=(0.30, 0.25, 0.19))
        assert arr.shape == (32, 64, 3)
        assert arr.dtype == np.uint8

    def test_zenith_row_is_bluer_than_the_horizon_row(self):
        arr = sky.sky_image(128, *SC11, ground_rgb=(0.30, 0.25, 0.19))
        h = arr.shape[0]
        zenith = arr[0].mean(axis=0).astype(float)
        horizon = arr[h // 2 - 1].mean(axis=0).astype(float)
        assert (zenith[2] - zenith[0]) > (horizon[2] - horizon[0])

    def test_no_seam_at_the_equator_row(self):
        """The below-horizon band blends from the PHYSICAL horizon colour. Using
        the legacy ramp's stop instead put a dark band above the terrain horizon
        — a bug Session 10c already fixed once."""
        arr = sky.sky_image(256, *SC11, ground_rgb=(0.30, 0.25, 0.19)).astype(float)
        h = arr.shape[0]
        last_sky = arr[h // 2 - 1].mean(axis=0)
        first_ground = arr[h // 2].mean(axis=0)
        assert abs(last_sky - first_ground).max() < 8.0

    def test_lower_hemisphere_is_not_sky_blue(self):
        """Reflections and bounce sample it; sky blue there lit the ground cold."""
        arr = sky.sky_image(128, *SC11, ground_rgb=(0.30, 0.25, 0.19)).astype(float)
        nadir = arr[-1].mean(axis=0)
        assert nadir[0] > nadir[2]  # warm, not cold

    @pytest.mark.parametrize("angles", [SC11, SC12])
    def test_rendered_hemisphere_mean_matches_the_legacy_ramp(self, angles):
        """THE invariant, measured on the 8-bit image the DomeLight integrates.

        The analytic version of this test is not sufficient: the highlight
        shoulder is non-linear, so preserving the model's mean does not preserve
        the *rendered* mean. This is the one that guarantees the sky swap did not
        change the ambient fill — and therefore did not move KPI-03's shadow
        contrast — at either measured sun angle.
        """
        import numpy as np

        elev, azim = angles
        arr = sky.sky_image(256, elev, azim, ground_rgb=(0.30, 0.25, 0.19))
        h = arr.shape[0]
        elevs = (np.pi / 2.0) - np.pi * np.arange(h) / (h - 1)
        w = np.where(elevs >= 0.0, np.cos(np.clip(elevs, 0.0, np.pi / 2)), 0.0)
        luma = (arr.astype(float) / 255.0) @ np.array([0.2126, 0.7152, 0.0722])
        got = float((luma.mean(axis=1) * w).sum() / w.sum())
        want = sky.gradient_hemisphere_mean(math.radians(elev), math.radians(azim))
        # 8-bit quantisation and the finite row count set the floor here.
        assert got == pytest.approx(want, abs=0.01)

    def test_tonemap_is_monotonic_and_bounded(self):
        import numpy as np

        x = np.linspace(0.0, 40.0, 500)
        y = sky._tonemap(x)
        assert np.all(np.diff(y) >= -1e-12)
        assert y.max() <= 1.0
        # Below the knee it must be exactly linear, or the sky's midtones shift.
        lo = np.linspace(0.0, sky._KNEE, 50)
        assert sky._tonemap(lo) == pytest.approx(lo)

    def test_no_flat_blown_disc_around_the_sun(self):
        """A hard clip renders the aureole as a saturated disc with a hard edge.
        The shoulder must leave a gradient there instead."""
        import numpy as np

        arr = sky.sky_image(256, *SC12, ground_rgb=(0.30, 0.25, 0.19))
        saturated = (arr >= 255).all(axis=-1)
        assert saturated.mean() < 0.02

    def test_tiles_horizontally(self):
        """Equirectangular wraps in azimuth; a discontinuity shows as a seam."""
        arr = sky.sky_image(128, *SC11, ground_rgb=(0.30, 0.25, 0.19)).astype(float)
        assert abs(arr[:, 0] - arr[:, -1]).max() < 12.0
