"""Audit an ingested site layout against the drawing it came from.

    python3 tools/audit_layout.py configs/layouts/khavda_a10b_block02.yaml \
        --pdf solar_plant_layout/6024-E-A10-PLE-DC-L-I-0002_01.pdf

Answers one question with evidence: **is this layout the WHOLE drawing, or a
subset?** `layout_from_dxf.py` reports what it ingested, but a tool's own count
cannot corroborate itself — if it silently dropped a layer, its report would drop
the same tables. So this counts the hardware a second time, independently, out of
the plotted PDF's vector geometry, and reconciles the two.

It also lists what the request "flag anything ambiguous rather than guessing"
actually asks for: overlapping tables, tables missing dimension data, and tables
whose stated module count disagrees with their own length.

**How a tracker table is recognised in the PDF.** A table is a long thin
rectangle: 128.58 m x 2.278 m at Khavda, an aspect ratio of ~56:1. Nothing else on
a DC block sheet has that signature, so counting elongated paths with a plausible
chord finds tables without needing to interpret layers or blocks. The scale comes
from the longest table itself (its length is known from the ingest), which avoids
the calibration problem that makes `layout_from_pdf.py` fail closed — that tool
needs absolute survey scale to place hardware, whereas this one only needs a ratio
to classify shapes.

⚠ Needs `pymupdf` (dev-only; not a runtime dependency of the twin). The PDF is a
CROSS-CHECK, never a source: geometry for the simulation comes from the DXF path.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

#: A table's aspect ratio is ~56:1; the shortest (1x56) table is ~28:1. Anything
#: past 18:1 with a module-chord-ish short side is a table or a legend swatch of
#: one, and the reconciliation below separates those two cases.
MIN_ASPECT = 18.0
#: Module pitch along the torque tube, for the length-vs-count consistency check.
PITCH_TOLERANCE = 0.02


def load_layout(path: Path) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def audit_tables(tables: list[dict], chord_m: float) -> dict:
    """Geometry problems in the ingested tables, listed rather than approximated."""
    issues: dict[str, list] = {
        "overlapping": [],
        "missing_dims": [],
        "pitch_mismatch": [],
        "duplicate_id": [],
        "duplicate_position": [],
    }
    half = chord_m / 2.0
    boxes = []
    for t in tables:
        if not t.get("length_m") or not t.get("modules") or not t.get("width_m"):
            issues["missing_dims"].append(t.get("id", "<no id>"))
            continue
        # A table's stored point is its SOUTHERN insert; modules run north of it.
        boxes.append((t["id"], t["e"] - half, t["n"], t["e"] + half, t["n"] + t["length_m"]))
        pitch = t["length_m"] / t["modules"]
        # The pitch is a real invariant: a 112-module table is 128.58 m because
        # each module steps 1.148 m. A table whose length and count disagree has
        # had one of the two guessed.
        if abs(pitch - 1.14804) > PITCH_TOLERANCE:
            issues["pitch_mismatch"].append(
                {"id": t["id"], "length_m": t["length_m"], "modules": t["modules"], "pitch_m": round(pitch, 4)}
            )

    for name, key in (("duplicate_id", "id"), ("duplicate_position", None)):
        if key:
            counts = collections.Counter(t.get(key) for t in tables)
        else:
            counts = collections.Counter((round(t["e"], 3), round(t["n"], 3)) for t in tables)
        issues[name] = [k for k, v in counts.items() if v > 1]

    # O(n^2) on purpose: 273 tables is 37k comparisons, and a spatial index would
    # hide the one thing worth reading here — which specific pair overlaps.
    for i in range(len(boxes)):
        ai, ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            bi, bx0, by0, bx1, by1 = boxes[j]
            ix = min(ax1, bx1) - max(ax0, bx0)
            iy = min(ay1, by1) - max(ay0, by0)
            if ix > 1e-6 and iy > 1e-6:
                issues["overlapping"].append(
                    {"a": ai, "b": bi, "overlap_x_m": round(ix, 3), "overlap_y_m": round(iy, 3)}
                )
    return issues


def count_pdf_tables(pdf_path: Path, modal_table_m: float, chord_m: float) -> dict:
    """Independently count table-shaped paths per page, bucketed by length."""
    import fitz

    doc = fitz.open(pdf_path)
    shapes = []
    for pno in range(len(doc)):
        for p in doc[pno].get_drawings():
            r = p["rect"]
            w, h = r.width, r.height
            if w <= 0.01 or h <= 0.01:
                continue
            lo, hi = min(w, h), max(w, h)
            if hi / lo >= MIN_ASPECT:
                shapes.append((pno, hi, lo))
    if not shapes:
        return {"error": "no elongated paths found — is this the right sheet?"}
    # Calibrate off the modal long side WITHIN THE LONGEST CLUSTER, against the
    # modal table length. Two wrong anchors were tried first and both are worth
    # recording:
    #   * the MAXIMUM long side — biased every length ~3% low, because the longest
    #     elongated shape on the sheet is a legend swatch a few metres longer than
    #     any real table.
    #   * the MODE over all elongated shapes — off by 30x, because most shapes
    #     passing an aspect filter are thin hatch and dimension lines, not tables.
    # Restricting to shapes within 80% of the longest, then taking the mode, lands
    # on the 259 identical full-length tables: the one anchor the sheet is
    # guaranteed to carry many copies of.
    longest_pt = max(hi for _, hi, _ in shapes)
    cluster = [round(hi, 1) for _, hi, _ in shapes if hi >= 0.8 * longest_pt]
    modal_pt = collections.Counter(cluster).most_common(1)[0][0]
    mpp = modal_table_m / modal_pt

    per_page: collections.Counter = collections.Counter()
    lengths: collections.Counter = collections.Counter()
    chords: collections.Counter = collections.Counter()
    for pno, hi, lo in shapes:
        chord = lo * mpp
        # Reject shapes whose short side is not a module chord: those are lines,
        # hatch boundaries and dimension leaders, not tables.
        if not (0.6 * chord_m < chord < 1.6 * chord_m):
            continue
        per_page[pno] += 1
        lengths[round(hi * mpp, 1)] += 1
        chords[round(chord, 2)] += 1
    return {
        "pages": dict(per_page),
        "total": sum(per_page.values()),
        "length_histogram_m": dict(sorted(lengths.items())),
        "chord_histogram_m": dict(chords.most_common(6)),
        "scale_m_per_pt": round(mpp, 6),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("layout", help="generated site YAML from tools/layout_from_dxf.py")
    ap.add_argument("--pdf", help="the same drawing as PDF, for an independent count")
    args = ap.parse_args(argv)

    lay = load_layout(Path(args.layout))
    tables = lay["tables"]
    chord_m = float((lay.get("module") or {}).get("length_m", 2.278))
    total_modules = sum(t["modules"] for t in tables)

    print(f"layout: {args.layout}")
    print(f"  tables: {len(tables)}   modules: {total_modules}")
    lengths = collections.Counter(round(t["length_m"], 1) for t in tables)
    print(f"  length histogram (m): {dict(sorted(lengths.items()))}")
    by_layer = collections.Counter(t["layer"] for t in tables)
    for lay_name, n in by_layer.most_common():
        print(f"    {n:4d} x {lay_name}")
    rots = sorted({t["rot_deg"] for t in tables})
    print(f"  rotations present: {rots}")
    prov = lay.get("provenance") or {}
    print(f"  ingest provenance: {prov}")

    # --- module arithmetic: the layer name states the count, so it is checkable --
    stated = 0
    for lay_name, n in by_layer.items():
        import re

        m = re.search(r"1x(\d+)", lay_name)
        if m:
            stated += int(m.group(1)) * n
    print(f"\nmodule arithmetic: layer names imply {stated}, table records sum to {total_modules}"
          f" -> {'MATCH' if stated == total_modules else 'MISMATCH'}")

    # --- geometry audit ------------------------------------------------------
    issues = audit_tables(tables, chord_m)
    print("\ngeometry audit (each list is a thing to look at, not a thing to guess):")
    for name, items in issues.items():
        print(f"  {name:20s} {len(items)}")
        for it in items[:10]:
            print(f"      {it}")
    problems = sum(len(v) for v in issues.values())

    # --- independent count from the PDF ------------------------------------
    rc = 0
    if args.pdf:
        modal_len = collections.Counter(t["length_m"] for t in tables).most_common(1)[0][0]
        pdf = count_pdf_tables(Path(args.pdf), modal_len, chord_m)
        print(f"\nindependent count from {args.pdf}:")
        if "error" in pdf:
            print(f"  [warn] {pdf['error']}")
        else:
            for k, v in pdf.items():
                print(f"  {k}: {v}")
            residual = pdf["total"] - len(tables)
            skipped = sum((prov.get("skipped_non_site") or {}).values())
            print(f"\nRECONCILIATION: pdf {pdf['total']} - layout {len(tables)} = {residual}")
            if residual == skipped:
                print(
                    f"  OK — the residual equals the {skipped} entit(y/ies) the ingest reported "
                    f"skipping ({prov.get('skipped_non_site')}), i.e. the legend swatches that show "
                    f"one of each HSAT type. Nothing is missing."
                )
            elif residual == 0:
                print("  OK — exact match.")
            else:
                rc = 1
                print(
                    f"  ⚠ UNEXPLAINED: {residual} table-shaped shapes in the PDF are not accounted "
                    f"for by the layout plus its {skipped} reported skips. The ingest may be partial."
                )
    if problems:
        rc = 1
        print(f"\n⚠ {problems} geometry issue(s) above need a human decision.")
    else:
        print("\nno geometry issues: no overlaps, no missing dimensions, no pitch mismatches.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
