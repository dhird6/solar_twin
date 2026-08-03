"""Physically-based daylight sky radiance (pure, Isaac-free).

Replaces the hand-tuned four-stop colour ramp that `farm_builder` used to bake
into the `DomeLight`'s latlong texture. The ramp looked plausible and had no
physics in it: the horizon-to-zenith falloff, the sun's aureole and the sky's
*hue* shift with sun elevation and atmospheric turbidity in ways a fixed set of
stops cannot express, and the Khavda site is measured at two very different sun
elevations (`SC-11` 17.2 deg, `SC-12` 10.7 deg) that the ramp rendered nearly
identically.

**The invariant that matters more than the appearance.** The `DomeLight` is BOTH
the visible background and the ambient fill — one object, so they cannot diverge
(the Session-10c fix; an emissive geometry dome lit the desert floor blue and the
measurement caught it). That means this texture's *mean radiance over the upper
hemisphere* is the scene's ambient level, and the ambient level sets how far
shadows fill in. `KPI-03`'s whole stimulus is a dark-glass differential between
self-shaded rows and an unshaded control table (measured +12.5 points), so a sky
that is merely *brighter* would lift the shadows and shrink that differential —
silently weakening the hazard the KPI exists to measure.

So `sky_image` **normalises the physical model to the legacy ramp's own
solid-angle-weighted hemisphere mean** (`gradient_hemisphere_mean`). The ramp is
retained for exactly that purpose: it is the photometric anchor, not a fallback
look. The swap therefore changes the sky's *distribution* (gradient shape, hue,
aureole) while holding its *total* fill constant by construction — which is the
only version of this change that cannot move a KPI by accident.

**Model: Preetham et al. 1999** ("A Practical Analytic Model for Daylight"), i.e.
the Perez all-weather formula with turbidity-driven coefficients, Kittler zenith
luminance and the Preetham zenith-chromaticity polynomials. Chosen over
Hosek-Wilkie deliberately:

- Preetham is fully analytic — ~40 coefficients in closed form. Hosek-Wilkie
  needs an embedded radiance dataset (thousands of floats) which is a data blob
  this repo would have to carry and verify.
- ⚠ **Preetham is known to degrade below roughly 10 deg sun elevation**, which is
  exactly where `SC-12` sits (10.7 deg). Hosek-Wilkie was motivated by that very
  weakness. This is a real accuracy limit, stated rather than hidden.
- It does **not** matter for the shading geometry. The shadow that `KPI-03`
  measures is cast by `/World/Sun`, a `DistantLight` whose direction comes from
  `world/solar.py` and is untouched here. This module only decides what colour
  and how bright the *sky* is, i.e. the ambient fill and the background — and the
  fill is pinned by the normalisation above. Sun elevation accuracy is a
  `world/solar.py` concern and is unchanged.

Turbidity defaults to 4.0: Kutch is arid and dusty, so a clean-maritime 2.0 would
be wrong, while the 6+ of a polluted city would wash the sky grey.
"""

from __future__ import annotations

import math

#: Legacy gradient stops: (fraction up from the horizon, RGB). Kept as the
#: PHOTOMETRIC ANCHOR for the physical model (see module docstring) — not as a
#: look to fall back to.
GRADIENT_STOPS = (
    (0.00, (0.78, 0.74, 0.66)),
    (0.10, (0.62, 0.66, 0.71)),
    (0.35, (0.35, 0.50, 0.72)),
    (1.00, (0.13, 0.28, 0.62)),
)

#: Arid, dusty site. 2.0 is clean maritime air; 6+ is urban haze.
DEFAULT_TURBIDITY = 4.0

#: Rec.709 / sRGB primaries, linear (no transfer curve applied here).
_XYZ_TO_RGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)


def gradient_colour(frac: float) -> tuple[float, float, float]:
    """Linearly interpolate `GRADIENT_STOPS`. `frac` is 0 at the horizon, 1 at
    the zenith."""
    frac = min(1.0, max(0.0, frac))
    for (f0, c0), (f1, c1) in zip(GRADIENT_STOPS, GRADIENT_STOPS[1:]):
        if frac <= f1:
            t = 0.0 if f1 == f0 else (frac - f0) / (f1 - f0)
            return tuple(a + (b - a) * t for a, b in zip(c0, c1))
    return GRADIENT_STOPS[-1][1]


# --- Preetham / Perez ---------------------------------------------------- #

#: Perez coefficients (A..E) as linear functions of turbidity, per xyY channel.
#: Table 2 of Preetham et al. 1999. Each entry is (slope, intercept).
_PEREZ = {
    "Y": ((0.1787, -1.4630), (-0.3554, 0.4275), (-0.0227, 5.3251),
          (0.1206, -2.5771), (-0.0670, 0.3703)),
    "x": ((-0.0193, -0.2592), (-0.0665, 0.0008), (-0.0004, 0.2125),
          (-0.0641, -0.8989), (-0.0033, 0.0452)),
    "y": ((-0.0167, -0.2608), (-0.0950, 0.0092), (-0.0079, 0.2102),
          (-0.0441, -1.6537), (-0.0109, 0.0529)),
}


def perez_coefficients(turbidity: float, channel: str) -> tuple[float, ...]:
    """The five Perez coefficients (A..E) for one xyY channel."""
    return tuple(m * turbidity + c for m, c in _PEREZ[channel])


def perez(theta: float, gamma: float, coeffs: tuple[float, ...]) -> float:
    """Perez all-weather luminance/chromaticity distribution.

    `theta` is the view direction's zenith angle, `gamma` the angle between the
    view direction and the sun. Clamped just short of the horizon: `1/cos(theta)`
    diverges at exactly 90 deg and the model has no meaning below it.
    """
    a, b, c, d, e = coeffs
    ct = max(math.cos(min(theta, math.radians(89.5))), 1e-4)
    return (1.0 + a * math.exp(b / ct)) * (
        1.0 + c * math.exp(d * gamma) + e * math.cos(gamma) ** 2
    )


def zenith_luminance(turbidity: float, sun_zenith: float) -> float:
    """Kittler zenith luminance in kcd/m^2. Only its *relative* size matters
    here — the image is renormalised afterwards."""
    chi = (4.0 / 9.0 - turbidity / 120.0) * (math.pi - 2.0 * sun_zenith)
    return (4.0453 * turbidity - 4.9710) * math.tan(chi) - 0.2155 * turbidity + 2.4192


def _poly_zenith(t: float, ts: float, m: tuple[tuple[float, ...], ...]) -> float:
    t2, t1, t0 = m
    return (
        t * t * (t2[0] * ts**3 + t2[1] * ts**2 + t2[2] * ts + t2[3])
        + t * (t1[0] * ts**3 + t1[1] * ts**2 + t1[2] * ts + t1[3])
        + (t0[0] * ts**3 + t0[1] * ts**2 + t0[2] * ts + t0[3])
    )


def zenith_chromaticity(turbidity: float, sun_zenith: float) -> tuple[float, float]:
    """Preetham zenith chromaticity (x, y)."""
    x = _poly_zenith(
        turbidity, sun_zenith,
        ((0.00166, -0.00375, 0.00209, 0.0),
         (-0.02903, 0.06377, -0.03202, 0.00394),
         (0.11693, -0.21196, 0.06052, 0.25886)),
    )
    y = _poly_zenith(
        turbidity, sun_zenith,
        ((0.00275, -0.00610, 0.00317, 0.0),
         (-0.04214, 0.08970, -0.04153, 0.00516),
         (0.15346, -0.26756, 0.06670, 0.26688)),
    )
    return x, y


def xyY_to_linear_rgb(x: float, y: float, Y: float) -> tuple[float, float, float]:
    """CIE xyY -> linear Rec.709 RGB. Not clamped; the caller tone-maps."""
    if y <= 1e-6:
        return (0.0, 0.0, 0.0)
    X = x * Y / y
    Z = (1.0 - x - y) * Y / y
    return tuple(
        row[0] * X + row[1] * Y + row[2] * Z for row in _XYZ_TO_RGB
    )


def sky_radiance(
    view_elev: float,
    view_azim: float,
    sun_elev: float,
    sun_azim: float,
    turbidity: float = DEFAULT_TURBIDITY,
) -> tuple[float, float, float]:
    """Relative linear RGB radiance of the sky in one direction (radians).

    Relative, not absolute: the model's absolute scale is discarded by the
    hemisphere normalisation in `sky_image`, so callers must not read these as
    cd/m^2.
    """
    theta = math.pi / 2.0 - max(view_elev, 0.0)
    theta_s = math.pi / 2.0 - max(sun_elev, 0.0)
    cos_gamma = max(-1.0, min(1.0, math.sin(view_elev) * math.sin(sun_elev)
                    + math.cos(view_elev) * math.cos(sun_elev)
                    * math.cos(view_azim - sun_azim)))
    gamma = math.acos(cos_gamma)

    out = {}
    for ch in ("Y", "x", "y"):
        coeffs = perez_coefficients(turbidity, ch)
        # Perez is a *distribution*: normalise it by its own value at the zenith
        # so the zenith terms below set the absolute level.
        f = perez(theta, gamma, coeffs)
        f0 = perez(0.0, theta_s, coeffs)
        out[ch] = f / f0 if f0 != 0.0 else 0.0

    Yz = max(0.0, zenith_luminance(turbidity, theta_s))
    xz, yz = zenith_chromaticity(turbidity, theta_s)
    return xyY_to_linear_rgb(xz * out["x"], yz * out["y"], Yz * out["Y"])


def _hemisphere_mean(sample, width: int = 128) -> float:
    """Solid-angle-weighted mean luminance of the upper hemisphere.

    `sample(elev, azim) -> rgb`. The cos-weighting is what makes this the number
    the `DomeLight` actually integrates: rows near the zenith cover far less
    solid angle than rows near the horizon, so an unweighted mean would
    mis-state the fill by a large factor.
    """
    rows, total, wsum = width // 4, 0.0, 0.0
    for j in range(rows):
        elev = (math.pi / 2.0) * (j + 0.5) / rows
        w = math.cos(elev)
        acc = 0.0
        for i in range(16):
            azim = 2.0 * math.pi * (i + 0.5) / 16
            r, g, b = sample(elev, azim)
            acc += 0.2126 * r + 0.7152 * g + 0.0722 * b
        total += w * acc / 16
        wsum += w
    return total / wsum if wsum else 0.0


def gradient_hemisphere_mean(sun_elev: float, sun_azim: float) -> float:
    """The legacy ramp's hemisphere mean — the photometric anchor.

    Includes the ramp's sun aureole, because that glow was part of the fill the
    old stages were lit by.
    """

    def sample(elev: float, azim: float) -> tuple[float, float, float]:
        r, g, b = gradient_colour(elev / (math.pi / 2.0))
        cos_sep = math.sin(elev) * math.sin(sun_elev) + math.cos(elev) * math.cos(
            sun_elev
        ) * math.cos(azim - sun_azim)
        glow = max(0.0, cos_sep) ** 8
        return (r + 0.42 * glow, g + 0.36 * glow, b + 0.22 * glow)

    return _hemisphere_mean(sample)


def physical_hemisphere_mean(
    sun_elev: float, sun_azim: float, turbidity: float = DEFAULT_TURBIDITY
) -> float:
    """The un-normalised physical model's hemisphere mean."""
    return _hemisphere_mean(
        lambda e, a: sky_radiance(e, a, sun_elev, sun_azim, turbidity)
    )


def exposure_for(
    sun_elev_deg: float, sun_azim_deg: float, turbidity: float = DEFAULT_TURBIDITY
) -> float:
    """Scale that makes the physical sky carry the legacy ramp's ambient fill.

    This is the number that keeps the swap KPI-neutral. Recomputed per sun angle
    rather than pinned as a constant, so the invariant holds at 02:00Z and
    01:30Z alike instead of only at whichever one was calibrated.
    """
    se, sa = math.radians(sun_elev_deg), math.radians(sun_azim_deg % 360.0)
    phys = physical_hemisphere_mean(se, sa, turbidity)
    if phys <= 0.0:
        return 0.0
    return gradient_hemisphere_mean(se, sa) / phys


#: Highlight rolloff knee. Below this, radiance is linear; above it, a smooth
#: shoulder compresses toward 1.0 instead of clipping flat.
#:
#: ⚠ This is not cosmetic. A physically-based sky at 10-17 deg sun elevation runs
#: well past 1.0 near the aureole — measured, the horizon row's mean red came out
#: 201/255 pre-clip against 168/255 post-clip. A hard clip therefore (a) throws
#: away radiance, so the hemisphere mean the DomeLight integrates lands BELOW the
#: legacy anchor and shadows fill in less than they used to, and (b) renders the
#: sun's surround as a flat blown disc with a hard edge. The knee keeps the
#: rolloff smooth; `_solve_scale` then recovers the mean on the actual image.
_KNEE = 0.75


def _tonemap(x):
    """Linear below `_KNEE`, smooth shoulder above it, asymptotic to 1.0."""
    import numpy as np

    hi = x > _KNEE
    out = np.array(x, dtype=np.float64, copy=True)
    if hi.any():
        e = out[hi] - _KNEE
        out[hi] = _KNEE + (1.0 - _KNEE) * (e / (e + (1.0 - _KNEE)))
    return np.clip(out, 0.0, 1.0)


def _row_weights(elevs):
    """Solid-angle weight per latlong row, zero below the horizon."""
    import numpy as np

    return np.where(elevs >= 0.0, np.cos(np.clip(elevs, 0.0, math.pi / 2)), 0.0)


def _solve_scale(raw, elevs, target: float, lo: float, hi: float) -> float:
    """Bisect for the exposure whose TONE-MAPPED image hits `target` mean.

    The analytic `exposure_for` is only correct before the shoulder; once
    highlights roll off, the relationship between scale and rendered mean is
    non-linear. Monotonic though, so bisection is exact enough and cheap.
    """
    import numpy as np

    w = _row_weights(elevs)
    wsum = w.sum()
    luma = np.array([0.2126, 0.7152, 0.0722])

    def rendered_mean(s: float) -> float:
        img = _tonemap(raw * s)
        per_row = (img @ luma).mean(axis=1)
        return float((per_row * w).sum() / wsum)

    if rendered_mean(hi) < target:  # unreachable even fully exposed
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if rendered_mean(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def sky_image(
    width: int,
    sun_elev_deg: float,
    sun_azim_deg: float,
    ground_rgb: tuple[float, float, float],
    turbidity: float = DEFAULT_TURBIDITY,
):
    """Equirectangular (latlong) sky as a `uint8` H x W x 3 numpy array.

    Row 0 is the zenith and row H-1 the nadir, per the USD latlong convention.
    The lower hemisphere is NOT sky: it blends the horizon colour into a lifted
    ground tone over ~25 deg, because looking down from altitude puts that region
    on screen beyond the finite ground mesh, where a dark value reads as a hole in
    the world (Session 10c). Both of those behaviours are carried over
    deliberately — only the upper hemisphere's model has changed.
    """
    import numpy as np

    height = width // 2
    se = math.radians(max(0.0, sun_elev_deg))
    sa = math.radians(sun_azim_deg % 360.0)

    elevs = (math.pi / 2.0) - math.pi * np.arange(height) / (height - 1)
    azims = 2.0 * math.pi * np.arange(width) / width

    # Vectorised Perez over the (elev, azim) grid.
    up = elevs >= 0.0
    e = np.clip(elevs[up], 0.0, math.pi / 2.0)[:, None]
    a = azims[None, :]
    theta = (math.pi / 2.0) - e
    theta_s = (math.pi / 2.0) - se
    cos_gamma = np.clip(
        np.sin(e) * math.sin(se) + np.cos(e) * math.cos(se) * np.cos(a - sa), -1.0, 1.0
    )
    gamma = np.arccos(cos_gamma)
    ct = np.maximum(np.cos(np.minimum(theta, math.radians(89.5))), 1e-4)

    chans = {}
    for ch in ("Y", "x", "y"):
        A, B, C, D, E = perez_coefficients(turbidity, ch)
        f = (1.0 + A * np.exp(B / ct)) * (
            1.0 + C * np.exp(D * gamma) + E * cos_gamma**2
        )
        f0 = perez(0.0, theta_s, perez_coefficients(turbidity, ch))
        chans[ch] = f / f0 if f0 != 0.0 else np.zeros_like(f)

    Yz = max(0.0, zenith_luminance(turbidity, theta_s))
    xz, yz = zenith_chromaticity(turbidity, theta_s)
    Yv = np.maximum(Yz * chans["Y"], 0.0)
    xv = np.maximum(xz * chans["x"], 1e-6)
    yv = np.maximum(yz * chans["y"], 1e-6)

    X = xv * Yv / yv
    Z = (1.0 - xv - yv) * Yv / yv
    rgb_up = np.maximum(
        np.stack(
            [
                _XYZ_TO_RGB[k][0] * X + _XYZ_TO_RGB[k][1] * Yv + _XYZ_TO_RGB[k][2] * Z
                for k in range(3)
            ],
            axis=-1,
        ),
        0.0,
    )

    # Exposure solved against the TONE-MAPPED result, so the invariant holds on
    # the image the DomeLight actually integrates, not just on the analytic model.
    raw = np.zeros((height, width, 3), dtype=np.float64)
    raw[up] = rgb_up
    guess = exposure_for(sun_elev_deg, sun_azim_deg, turbidity)
    target = gradient_hemisphere_mean(se, sa)
    scale = _solve_scale(raw, elevs, target, 0.0, max(guess * 32.0, 1e-6))

    img = np.zeros((height, width, 3), dtype=np.float64)
    img[up] = _tonemap(rgb_up * scale)

    # Below the horizon: horizon haze -> lifted ground tone (Session 10c).
    down = ~up
    if down.any():
        # Start the blend from the PHYSICAL sky's own lowest row, azimuth-averaged
        # — not from the legacy ramp's horizon stop. Using a colour the upper
        # hemisphere no longer renders would put a visible seam along the equator
        # row, which is the "dark brown band above the terrain horizon" that
        # Session 10c already had to fix once.
        # Taken from the TONE-MAPPED horizon row: the pre-shoulder mean sits well
        # above what actually renders there (measured 201 vs 168 in red), so
        # blending from it reintroduces the very seam this avoids.
        haze = img[up][-1].mean(axis=0) if up.any() else np.zeros(3)
        gnd = np.array([v * 1.5 for v in ground_rgb])
        t = np.clip(np.degrees(-elevs[down]) / 25.0, 0.0, 1.0)[:, None]
        img[down] = (haze[None, :] + (gnd - haze)[None, :] * t)[:, None, :]

    # ROUND, do not truncate. `.astype(np.uint8)` truncates toward zero, so the
    # quantisation error is uniform in [-1, 0] LSB with mean -0.5 -- a systematic
    # bias, not noise. Measured: it put the delivered hemisphere mean 0.00196
    # BELOW the exposure `_solve_scale` bisected for (SC-11 anchor 0.547490,
    # truncated 0.545529), identical at widths 128/256/512/1024 -- so it is not
    # the "finite row count" it was blamed on, and it is the entire residual that
    # commit 1fe0eb6 records as unavoidable "8-bit quantisation".
    #
    # It always darkens, and being one-directional it can never average out across
    # `--repeat N`. `textures.py` already rounds at all four of its conversion
    # sites; this was the outlier. Decoding at the bin centre leaves <2.2e-5.
    return np.clip(np.rint(np.clip(img, 0.0, 1.0) * 255.0), 0, 255).astype(np.uint8)


def write_sky_texture(
    path: str,
    sun_elev_deg: float,
    sun_azim_deg: float,
    ground_rgb: tuple[float, float, float],
    width: int = 1024,
    turbidity: float = DEFAULT_TURBIDITY,
) -> str:
    """Render `sky_image` to a PNG and return the path. Generated at build time
    next to the USD, never committed (`CLAUDE.md`: no large binaries)."""
    from pathlib import Path

    from PIL import Image

    arr = sky_image(width, sun_elev_deg, sun_azim_deg, ground_rgb, turbidity)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, mode="RGB").save(path)
    return path
