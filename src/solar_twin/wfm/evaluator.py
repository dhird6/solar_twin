"""The Evaluator gate — no generated frame reaches Perception unchecked (FR-05/NFR-08).

Purpose: stop a generator from inventing panel detail or geometrically-wrong
shadows that would become false fault ground truth. That is the exact failure this
project exists to prevent, so the gate is **fail-closed**: a frame it cannot
verify is rejected, not passed with a warning.

Measured finding: no-reference checks are NOT sufficient
-------------------------------------------------------
This was calibrated against six real Cosmos3-Edge generations, each also judged by
eye. Image statistics do not separate good from bad:

    image          stddev  hi-freq  grid-peak   human verdict
    edge_gen        107.7    0.348       12.9   pure noise, unusable
    edge_gen2        78.6    0.445       16.8   good PV array
    iso_guid         87.8    0.360       20.8   smeared PV
    iso_steps        79.1    0.537       29.3   good PV array
    edge_khavda      85.9    0.455        7.7   incoherent panel texture
    edge_try2        86.2    0.313       32.5   photoreal, NOT a PV module

Two things kill the no-reference approach outright:

* `edge_try2` is not a photovoltaic module at all, yet scores the **highest**
  grid-periodicity of the set — higher than both genuinely good frames. Any
  "has a regular cell grid" test passes it.
* The *good* frames carry **more** high-frequency energy than the noise frame,
  because a real cell grid is high-frequency. So "less noise" is backwards.

Only one extreme is reliably separable: a total absence of periodic structure,
caught by `grid_peak`. `stddev` is a narrow backstop for the specific
high-contrast blob garbage this model emitted — uniform random noise measures ~74
and slips straight under it, so it is not a general noise test. Both are kept as a
cheap pre-filter and are explicitly **necessary, not sufficient**.

The gate that actually works is reference-based: compare the generated frame with
the seed frame it was conditioned on and require the panel geometry to survive.
That is only possible for **Transfer**-class generation. An unconditioned
text-to-image frame has no reference, therefore cannot be verified, therefore
cannot be admitted — which is a conclusion about the pipeline, not a tuning knob.

Pure-python: no Isaac import. numpy is imported lazily so this module imports
anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Narrow backstop for high-contrast blob garbage. Only `edge_gen` (107.7)
#: exceeded it; every plausible frame measured 78-88. ⚠ It is NOT a general noise
#: test — uniform random noise measures ~74 and slips under it. `MIN_GRID_PEAK` is
#: what actually catches unstructured frames. ⚠ provisional: 6 frames, one model.
MAX_STDDEV = 100.0

#: Structure guard: below this there is no periodic module grid at all
#: (`edge_khavda` = 7.7 failed; the weakest plausible frame was 12.9).
MIN_GRID_PEAK = 10.0

#: Reference guard: fraction of the seed's panel edges that must still be present,
#: in place, in the generated frame. Geometry preservation is the whole point of
#: conditioning, so this is deliberately strict.
MIN_EDGE_RETENTION = 0.60


@dataclass
class EvalReport:
    """Why a frame was admitted or rejected. Always populated, never just a bool."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    #: True when only no-reference checks could run. Such a frame is NOT admissible
    #: for training/evaluating Perception, however good it looks (`NFR-08`).
    unverifiable: bool = False


def _gray(frame):
    import numpy as np  # noqa: PLC0415 — lazy

    a = np.asarray(frame, dtype=np.float32)
    if a.ndim == 3:
        a = a[..., :3].mean(axis=2)
    return a


def _grid_peak(gray) -> float:
    """Strength of the strongest periodic structure, min over both axes.

    A PV module is a regular cell lattice, so a real panel frame has a sharp
    non-DC spectral peak in both the row-mean and column-mean profiles.
    """
    import numpy as np  # noqa: PLC0415

    def peak(sig):
        s = np.abs(np.fft.rfft(sig - sig.mean()))
        s[:3] = 0.0  # kill DC + the lowest bins (vignetting, gradients)
        m = float(s.mean())
        return float(s.max() / m) if m > 0 else 0.0

    return min(peak(gray.mean(axis=0)), peak(gray.mean(axis=1)))


def _edge_map(gray, thresh_frac: float = 0.5):
    """Binary edge map from a cheap gradient magnitude — no scipy dependency."""
    import numpy as np  # noqa: PLC0415

    gy = np.zeros_like(gray)
    gx = np.zeros_like(gray)
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    mag = np.hypot(gx, gy)
    if mag.max() <= 0:
        return mag > 1.0  # all-False
    return mag > (thresh_frac * mag.mean() + 1e-6) * 2.0


def edge_retention(seed, generated) -> float:
    """Fraction of the seed's edge pixels that survive in the generated frame.

    This is the geometry-preservation test: a Transfer output may change colour,
    exposure and haze freely, but a module edge that moves or vanishes means the
    generator re-drew the hardware, and any fault verdict on it would be scored
    against the wrong ground truth.
    """
    import numpy as np  # noqa: PLC0415

    a, b = _gray(seed), _gray(generated)
    if a.shape != b.shape:
        return 0.0
    ea, eb = _edge_map(a), _edge_map(b)
    n = int(ea.sum())
    if n == 0:
        return 0.0
    # Dilate the generated edges by one pixel so sub-pixel shifts are tolerated
    # while genuine displacement still fails.
    d = eb.copy()
    d[1:, :] |= eb[:-1, :]
    d[:-1, :] |= eb[1:, :]
    d[:, 1:] |= eb[:, :-1]
    d[:, :-1] |= eb[:, 1:]
    return float(np.logical_and(ea, d).sum() / n)


def no_reference_guards(frame) -> EvalReport:
    """Cheap pre-filter. NECESSARY, NOT SUFFICIENT — see the module docstring.

    Passing this says only "not obvious garbage". It does NOT make a frame
    admissible; `evaluate()` still marks it unverifiable without a seed.
    """
    gray = _gray(frame)
    sd = float(gray.std())
    gp = _grid_peak(gray)
    reasons = []
    if sd > MAX_STDDEV:
        reasons.append(f"stddev {sd:.1f} > {MAX_STDDEV} — looks like noise, not a scene")
    if gp < MIN_GRID_PEAK:
        reasons.append(
            f"grid_peak {gp:.1f} < {MIN_GRID_PEAK} — no periodic module grid; "
            "the panel structure was not preserved"
        )
    return EvalReport(ok=not reasons, reasons=reasons, metrics={"stddev": sd, "grid_peak": gp})


def evaluate(generated, seed=None) -> EvalReport:
    """Gate one generated frame. `seed` is the frame it was conditioned on.

    Without `seed` the frame is **rejected as unverifiable**, regardless of how
    good it looks — there is no way to show its geometry matches our layout, and
    admitting it would let hallucinated detail define fault ground truth.
    """
    report = no_reference_guards(generated)
    if not report.ok:
        return report

    if seed is None:
        report.ok = False
        report.unverifiable = True
        report.reasons.append(
            "no seed frame — an unconditioned generation cannot be verified against "
            "our panel geometry, so it is not admissible for Perception (NFR-08). "
            "Use a Transfer-class backend conditioned on a real render."
        )
        return report

    keep = edge_retention(seed, generated)
    report.metrics["edge_retention"] = keep
    if keep < MIN_EDGE_RETENTION:
        report.ok = False
        report.reasons.append(
            f"edge_retention {keep:.2f} < {MIN_EDGE_RETENTION} — panel geometry moved "
            "or was redrawn relative to the seed render"
        )
    return report
