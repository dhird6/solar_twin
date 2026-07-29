"""Extract a real plant layout from a vendor CAD-exported PDF (FR-26/FR-27).

    python3 tools/layout_from_pdf.py solar_plant_layout/<sheet>.pdf \
        --out configs/layouts/<site>.yaml [--epsg 32642]

Reads the **vector** geometry of an AutoCAD-plotted sheet and emits the canonical
table-level site file that `world/layout_import.py` consumes. Isaac-free; needs
only `pymupdf` (dev-time tool, NOT part of the runtime pipeline).

Why this is a two-source extraction
-----------------------------------
These drawings carry the note **"DO NOT SCALE THE DRAWING — ONLY WRITTEN
DIMENSIONS TO BE FOLLOWED."** That is not boilerplate: a plotted sheet holds
several viewports at different scales (block plan, pile details, key plan), and
the `N …/E …` coordinate labels are *text placed near* a leader, not the point
itself. Measuring pixels therefore gives a plausible-but-wrong layout — the exact
`NFR-07` silent-substitution failure this project exists to prevent.

So we split the two sources by what each is actually trustworthy for:

* **vector geometry → topology** (how many table rows, how many tables per row,
  their ordering and grouping, which rows sit in a maintenance corridor). Counts
  and ordering are exact regardless of scale.
* **written dimensions + survey control points → metric truth** (row pitches,
  table length, module size).

`calibrate()` then cross-checks them: the *ratios* between measured point
spacings must match the ratios between the written dimensions. If they disagree
beyond tolerance the emitted file is marked `calibration: provisional` and
`layout_import` refuses to build from it unless explicitly overridden — fail
closed (`FR-27`), never silently approximate.

⚠ Status: topology extraction is verified against
`6024-E-A10-PLE-DC-L-I-0002_01.pdf` (BLOCK-02, PLOT A10b). Metric calibration is
**provisional** — see `--report`. The clean fix is a vendor DWG/DXF or the
tracker/pile coordinate schedule, which makes calibration unnecessary; see the
module docstring of `world/layout_import.py`.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from dataclasses import dataclass, field

# Candidate table rectangle: long and thin. Bounds are generous on purpose —
# they select "a long thin filled rect" without hardcoding this sheet's scale.
_MIN_ASPECT = 20.0
_MIN_LONG_PT = 100.0

#: A survey label pair looks like `N 2664676.00` / `E 542447.79` placed together.
_COORD_RE = re.compile(r"\d{5,7}\.\d{2}")

#: Two control-point labels closer than this (pt) are treated as one N/E pair.
_PAIR_MAX_PT = 40.0


@dataclass
class TableRow:
    """One row of tracker tables, in raw PDF points (pre-calibration)."""

    y_pt: float
    x0_pt: float
    x1_pt: float
    n_tables: int

    @property
    def length_pt(self) -> float:
        return self.x1_pt - self.x0_pt


@dataclass
class Extraction:
    """What one sheet page yielded, with calibration diagnostics."""

    page: int
    rows: list[TableRow] = field(default_factory=list)
    control_points: list[tuple[float, float, float, float]] = field(default_factory=list)
    written_dims_mm: list[int] = field(default_factory=list)
    pitches_pt: list[tuple[float, int]] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)

    @property
    def n_tables(self) -> int:
        return sum(r.n_tables for r in self.rows)


def _long_thin_rects(page) -> list:
    """Every long, thin, axis-aligned rect — the tracker-table candidates."""
    out = []
    for group in page.get_drawings():
        r = group["rect"]
        w, h = r.width, r.height
        if w <= 0 or h <= 0:
            continue
        long_side, short_side = (w, h) if w >= h else (h, w)
        if short_side <= 0:
            continue
        if long_side >= _MIN_LONG_PT and long_side / short_side >= _MIN_ASPECT:
            out.append(r)
    return out


def _control_points(page) -> list[tuple[float, float, float, float]]:
    """Survey control points as (pdf_x, pdf_y, northing_m, easting_m).

    Pairs an `N <value>` label with the nearest `E <value>` label. The position
    is the *label's* centre, which is offset from the true point — hence these
    are usable for a coarse fit and residual reporting, never as exact anchors.
    """
    words = page.get_text("words")
    ns, es = [], []
    for i, w in enumerate(words):
        if w[4] in ("N", "E") and i + 1 < len(words):
            nxt = words[i + 1][4]
            if _COORD_RE.fullmatch(nxt):
                entry = (float(nxt), (w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
                (ns if w[4] == "N" else es).append(entry)
    pairs = []
    for n_val, nx, ny in ns:
        if not es:
            break
        e_val, ex, ey = min(es, key=lambda e: (e[1] - nx) ** 2 + (e[2] - ny) ** 2)
        if ((ex - nx) ** 2 + (ey - ny) ** 2) ** 0.5 < _PAIR_MAX_PT:
            pairs.append((nx, ny, n_val, e_val))
    return pairs


def _written_dims_mm(page) -> list[int]:
    """Bare 4–5 digit integers on the sheet — CAD dimension annotations in mm."""
    dims = []
    for w in page.get_text("words"):
        t = w[4]
        if re.fullmatch(r"\d{4,5}", t):
            v = int(t)
            if 500 <= v <= 60000:  # plausible mm dimension on a PV sheet
                dims.append(v)
    return sorted(collections.Counter(dims).keys())


def _rows_from_rects(rects, tol: float = 2.0) -> list[TableRow]:
    """Group table rects into rows by shared y, preserving exact counts."""
    horiz = [r for r in rects if r.width >= r.height]
    buckets: dict[float, list] = collections.defaultdict(list)
    for r in horiz:
        key = next((k for k in buckets if abs(k - r.y0) <= tol), round(r.y0, 1))
        buckets[key].append(r)
    rows = []
    for y, rs in sorted(buckets.items()):
        rows.append(
            TableRow(
                y_pt=y,
                x0_pt=min(r.x0 for r in rs),
                x1_pt=max(r.x1 for r in rs),
                n_tables=len(rs),
            )
        )
    return rows


def _pitches_pt(rows: list[TableRow]) -> list[tuple[float, int]]:
    """Distinct row-to-row spacings (pt) with counts, most common first."""
    ys = sorted(r.y_pt for r in rows)
    gaps = collections.Counter(round(b - a, 1) for a, b in zip(ys, ys[1:]) if b - a > 0.5)
    return gaps.most_common()


def extract(pdf_path: str, page_index: int) -> Extraction:
    import pymupdf  # noqa: PLC0415 — dev-tool dep, imported lazily

    doc = pymupdf.open(pdf_path)
    page = doc[page_index]
    rects = _long_thin_rects(page)
    rows = _rows_from_rects(rects)
    titles = [
        " ".join(b[4].split())
        for b in page.get_text("blocks")
        if any(k in b[4].upper() for k in ("LAYOUT", "PLOT", "KEY PLAN"))
    ]
    return Extraction(
        page=page_index,
        rows=rows,
        control_points=_control_points(page),
        written_dims_mm=_written_dims_mm(page),
        pitches_pt=_pitches_pt(rows),
        titles=titles,
    )


def calibrate(ex: Extraction, tol_frac: float = 0.01) -> dict:
    """Cross-check measured point spacings against the written dimensions.

    Returns a report dict. `ok` is True only when the two dominant measured
    pitches reproduce a pair of written dimensions to within `tol_frac`; the
    caller must treat `ok=False` as *provisional* (`FR-27`), not as a warning to
    shrug at. We compare **ratios**, because the sheet's absolute scale is what
    we distrust.
    """
    report: dict = {"ok": False, "reason": None, "candidates": []}
    if len(ex.pitches_pt) < 2:
        report["reason"] = "fewer than two distinct row pitches measured"
        return report

    (p_a, _), (p_b, _) = ex.pitches_pt[0], ex.pitches_pt[1]
    lo_pt, hi_pt = sorted((p_a, p_b))
    measured_ratio = hi_pt / lo_pt

    dims = ex.written_dims_mm
    best = None
    for i, d_lo in enumerate(dims):
        for d_hi in dims[i + 1 :]:
            ratio = d_hi / d_lo
            err = abs(ratio - measured_ratio) / measured_ratio
            scale_lo = (d_lo / 1000.0) / lo_pt
            scale_hi = (d_hi / 1000.0) / hi_pt
            scale_err = abs(scale_hi - scale_lo) / scale_lo
            cand = {
                "dim_lo_mm": d_lo,
                "dim_hi_mm": d_hi,
                "ratio_err": round(err, 5),
                "m_per_pt": round((scale_lo + scale_hi) / 2, 6),
                "scale_disagreement": round(scale_err, 5),
            }
            if best is None or err < best["ratio_err"]:
                best = cand
    report["measured_pitches_pt"] = [lo_pt, hi_pt]
    report["measured_ratio"] = round(measured_ratio, 5)
    report["best"] = best

    # An INDEPENDENT second opinion. Matching written dims against each other only
    # proves the sheet is self-consistent, not that our metres-per-point is right —
    # a wrong scale that happens to preserve ratios still passes. So also derive
    # the scale from the survey control points and require the two to agree.
    survey = _survey_scale(ex)
    report["survey_scale"] = survey

    if not (best and best["ratio_err"] <= tol_frac and best["scale_disagreement"] <= tol_frac):
        report["reason"] = (
            "no pair of written dimensions reproduces the measured pitch ratio "
            f"within {tol_frac:.1%} (best ratio_err="
            f"{best['ratio_err'] if best else 'n/a'})"
        )
        return report

    if survey.get("m_per_pt_y") is None:
        report["reason"] = "no survey control points usable for an independent scale check"
        return report

    disagree = abs(survey["m_per_pt_y"] - best["m_per_pt"]) / best["m_per_pt"]
    report["survey_vs_dims_disagreement"] = round(disagree, 5)
    if disagree > tol_frac:
        report["reason"] = (
            f"written dimensions imply {best['m_per_pt']:.4f} m/pt but survey control "
            f"points imply {survey['m_per_pt_y']:.4f} m/pt along the same axis "
            f"({disagree:.1%} apart) — the sheet's viewports/labels cannot be "
            "reconciled to one scale, so the metric layout is NOT exact"
        )
        return report

    report["ok"] = True
    report["m_per_pt"] = best["m_per_pt"]
    return report


def _survey_scale(ex: Extraction) -> dict:
    """Scale implied by the survey labels, per axis, from widely-separated pairs.

    Uses only pairs sharing one coordinate (same northing, differing easting, and
    vice versa) so a single axis is isolated. Reports the *median* of the implied
    scales plus their spread — a large spread is itself evidence of multiple
    viewports on the sheet.
    """
    import statistics

    ys, xs = [], []
    pts = ex.control_points
    for i, a in enumerate(pts):
        for b in pts[i + 1 :]:
            ax, ay, an, ae = a
            bx, by, bn, be = b
            if abs(an - bn) < 0.01 and abs(by - ay) > 200 and abs(be - ae) > 1:
                ys.append(abs((be - ae) / (by - ay)))
            if abs(ae - be) < 0.01 and abs(bx - ax) > 200 and abs(bn - an) > 1:
                xs.append(abs((bn - an) / (bx - ax)))
    out: dict = {"n_y_pairs": len(ys), "n_x_pairs": len(xs)}
    if ys:
        out["m_per_pt_y"] = round(statistics.median(ys), 6)
        out["y_spread"] = round(max(ys) - min(ys), 6)
    if xs:
        out["m_per_pt_x"] = round(statistics.median(xs), 6)
        out["x_spread"] = round(max(xs) - min(xs), 6)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="all", help="e.g. '0' or '0,1' or 'all'")
    ap.add_argument("--report", action="store_true", help="print diagnostics only")
    args = ap.parse_args(argv)

    import pymupdf  # noqa: PLC0415

    n_pages = pymupdf.open(args.pdf).page_count
    pages = range(n_pages) if args.pages == "all" else [int(p) for p in args.pages.split(",")]

    for pi in pages:
        ex = extract(args.pdf, pi)
        cal = calibrate(ex)
        print(f"=== page {pi}: {ex.n_tables} tables in {len(ex.rows)} rows")
        for t in ex.titles[:4]:
            print(f"    title: {t[:78]}")
        print(f"    control points: {len(ex.control_points)}")
        print(f"    written dims (mm): {ex.written_dims_mm[:14]}")
        print(f"    row pitches (pt): {ex.pitches_pt[:5]}")
        print(f"    table length (pt): {ex.rows[0].length_pt:.1f}" if ex.rows else "")
        print(f"    CALIBRATION ok={cal['ok']}")
        if not cal["ok"]:
            print(f"      -> PROVISIONAL: {cal['reason']}")
        print(f"      {json.dumps(cal.get('best'), indent=None)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
