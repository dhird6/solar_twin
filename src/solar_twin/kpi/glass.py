"""One definition of "this pixel is PV glass", shared by every tool that needs it.

Pure-python, Isaac-free, operates on `H x W x 3` uint8/float arrays.

`tools/verify_shade.py` and `tools/inspect_frame.py` each carried their own copy of
this rule with a comment saying they must not disagree. They now import it, so the
guarantee is structural.

## Why the threshold is 2.0 and not 1.15

The rule selects glass by how much bluer than red a pixel is. A PV cell's diffuse
constant is `(0.02, 0.04, 0.13)` — **blue/red ≈ 6.5** — while sunlit tan desert is
warm (blue/red < 1). So a low threshold looks safe, and 1.15 was used for months.

**It is not safe under a physically-based sky.** Ground in *shadow* receives no
direct sun: it is lit only by the sky dome, so it takes the sky's colour and turns
blue. Measured 2026-07-31 on `SC-11`, mask share of the whole frame:

    glass rule       legacy sky      Preetham sky (correctly-lit ground)
    blue > 1.15*red    ~24%            99.2 / 99.5 / 99.4 / 99.3%   <- broken
    blue > 2.00*red    ~22%            23.2 / 24.9 / 32.2 / 33.9%   <- sane

At a 99% mask the "% dark glass" statistic *is* the whole-frame statistic — the
exact confound `verify_shade` exists to prevent, and the third time this project
has been bitten by scoring whole-frame brightness. It manufactured a fake `SC-11`
differential of **+23.7 points**; re-derived under this rule it is **+12.6**,
against the legacy stage's **+13.8**.

2.0 is chosen with margin on both sides: it is far below the cell's own 6.5, and
above everything sky-lit ground reached in the sweep (which topped out under 2.0
in every frame measured). Between 1.15 and 2.0 the sky-lit arm is still partly
admitted — 1.8 still let in 39–45% of some frames — so the shoulder is real and
2.0 sits past it.

⚠ **Numbers measured under the old rule are not comparable to numbers measured
under this one.** Anything quoted from before 2026-07-31 carries the 1.15 rule.
"""

from __future__ import annotations

#: Blue must exceed red by this factor for a pixel to count as PV glass.
#: See the module docstring — this was 1.15 and a blue sky broke it.
GLASS_BLUE_OVER_RED = 2.0

#: A pixel is "dark" below this fraction of the *masked* bright reference, so the
#: threshold follows the panel's own illumination instead of the frame's.
DARK_FRACTION_OF_BRIGHT = 0.55


def glass_mask(rgb, ratio: float = GLASS_BLUE_OVER_RED):
    """Boolean `H x W` mask selecting PV-glass pixels in an RGB array.

    `rgb` is `H x W x >=3`; only the first three channels are read, so an RGBA
    frame straight off the renderer works unchanged. Red is floored at 1.0 so a
    pure-black pixel cannot divide the comparison into admitting everything.
    """
    import numpy as np

    a = np.asarray(rgb)[..., :3].astype(np.float32)
    return a[..., 2] > (ratio * np.maximum(a[..., 0], 1.0))


def dark_fraction(rgb, mask=None, fraction: float = DARK_FRACTION_OF_BRIGHT):
    """Fraction of masked pixels darker than `fraction` of that mask's own P90.

    Returns `(mask_share, dark_share)`, both 0..1, or `(share, None)` when the
    mask selects too little to be meaningful — a caller must not read a dark
    fraction off a handful of pixels.
    """
    import numpy as np

    a = np.asarray(rgb)[..., :3].astype(np.float32)
    m = glass_mask(a) if mask is None else mask
    share = float(m.mean())
    if m.sum() < 50:
        return share, None
    lum = a.mean(axis=2)
    bright = float(np.percentile(lum[m], 90))
    return share, float((lum[m] < fraction * bright).mean())
