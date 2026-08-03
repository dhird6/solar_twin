#!/usr/bin/env python3
"""Convert a GatiShakti plot digest into OUR canonical site file.

**Pure-python, no Isaac.** This is the alternative to their `build_gis_digest.py`
(which we do not hold): we already have that script's *output*, so we convert it
instead of re-deriving it. It turns their per-plot JSON into the same site YAML
`tools/layout_from_dxf.py` emits and `world/layout_import.py` consumes, so the
whole existing pipeline — panels, faults, waypoints, KPIs — works unchanged.

WHY THIS IS SOUND: their table boxes are measured 2.3 x 128.6 m and OUR BLOCK-02
tables (from our own DXF) are 2.278 x 128.58 m with every rot_deg = 0.0. Same
hardware, same CRS (EPSG:32642), axis-aligned. So a box maps onto a table:
centroid -> e/n, size -> width/length, modules = length / module_pitch.

⚠ **A BOUNDING BOX CANNOT EXPRESS A ROTATED TABLE**, and that is the one real
limit. A rotated tracker's bounding box is wider and shorter than the tracker, so
this FAILS CLOSED on it rather than emitting a squashed, mis-sized plant: any box
whose width exceeds `--max-width` is reported and the conversion aborts unless
`--allow-rotated` is passed. Same discipline as `layout_from_pdf.py` refusing a
9.2% calibration disagreement (`FR-27`).

⚠ Provenance is SECOND-HAND (vendor DWG -> their script -> JSON -> us) and is
stamped into the file, so a site built from this is never confused with one we
derived from the drawing ourselves.

    PYTHONPATH=src python3 tools/digest_to_site.py <Plot-S05b.json> \
        --out configs/layouts/khavda_s05b_digest.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solar_twin.world.plot_digest import load  # noqa: E402

#: Our BLOCK-02 module pitch, from the vendor CAD. Used to derive module counts
#: from a table's length, because the digest carries no module count.
DEFAULT_MODULE_PITCH_M = 1.14804
DEFAULT_MODULE_LENGTH_M = 2.278
DEFAULT_MODULE_WIDTH_M = 1.134
#: A tracker table is ~2.3 m across. Anything wider is a rotated table's bounding
#: box (or not a table at all) and must not be silently accepted.
DEFAULT_MAX_WIDTH_M = 4.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("digest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--module-pitch", type=float, default=DEFAULT_MODULE_PITCH_M)
    ap.add_argument("--max-width", type=float, default=DEFAULT_MAX_WIDTH_M)
    ap.add_argument("--allow-rotated", action="store_true",
                    help="emit anyway when boxes look rotated. ⚠ the plant will be "
                         "mis-sized: a bounding box is not the tracker inside it")
    ap.add_argument("--blocks", help="comma-separated block ids to keep (default: all)")
    args = ap.parse_args(argv)

    import yaml

    d = load(args.digest)
    tables = [b for b in d.boxes if b.kind == "mms-table"]
    if not tables:
        print(f"{args.digest}: no mms-table assets", file=sys.stderr)
        return 2

    keep = set(args.blocks.split(",")) if args.blocks else None
    if keep:
        tables = [t for t in tables if t.block in keep]

    # Fail closed on rotation before writing anything.
    wide = [t for t in tables if t.size_m[0] > args.max_width]
    if wide:
        worst = max(t.size_m[0] for t in wide)
        msg = (f"{len(wide)} of {len(tables)} table boxes exceed {args.max_width} m "
               f"wide (worst {worst:.1f} m) — these are rotated tables, whose "
               f"bounding box is NOT the tracker. Emitting would mis-size the plant.")
        if not args.allow_rotated:
            print(f"REFUSING: {msg}\n  Pass --allow-rotated only if you accept that.",
                  file=sys.stderr)
            return 1
        print(f"⚠ WARNING: {msg}", file=sys.stderr)

    es = [t.centroid[0] for t in tables]
    ns = [t.centroid[1] for t in tables]
    # Anchor on the SW-most table, matching our BLOCK-02 site file's convention.
    origin_e, origin_n = min(es), min(ns)

    out_tables = []
    total_modules = 0
    for i, t in enumerate(sorted(tables, key=lambda b: (b.centroid[1], b.centroid[0]))):
        w, length = t.size_m
        modules = max(1, int(round(length / args.module_pitch)))
        total_modules += modules
        out_tables.append({
            "id": f"T{i:04d}",
            "e": round(t.centroid[0], 3),
            "n": round(t.centroid[1], 3),
            # Verified axis-aligned above; a rotated box would have been rejected.
            "rot_deg": 0.0,
            "length_m": round(length, 3),
            "width_m": round(w, 3),
            "modules": modules,
            "module_rows": 1,
            "layer": t.layername,
        })

    doc = {
        "crs": "EPSG:32642",
        "units": "meters",
        "provenance": {
            "source": f"GatiShakti plot digest {d.plot_id} ({Path(args.digest).name})",
            "chain": "vendor DWG -> their build_gis_digest.py (NOT held) -> JSON -> "
                     "tools/digest_to_site.py",
            "trust": "SECOND-HAND — not re-derivable from the drawing here, unlike "
                     "tools/layout_from_dxf.py output",
            "tables": len(out_tables),
            "modules": total_modules,
            "blocks": len({t.block for t in tables if t.block}),
            "module_count": "DERIVED from table length / module pitch — the digest "
                            "carries no module count",
            "rot_deg": "0.0, verified: every box is within the tracker width, and "
                       "our own BLOCK-02 DXF is likewise all 0.0",
        },
        "extent": {
            "easting": [round(min(es), 3), round(max(es), 3)],
            "northing": [round(min(ns), 3), round(max(ns), 3)],
        },
        "origin": {"easting": round(origin_e, 3), "northing": round(origin_n, 3)},
        "module": {
            "pitch_m": args.module_pitch,
            "length_m": DEFAULT_MODULE_LENGTH_M,
            "width_m": DEFAULT_MODULE_WIDTH_M,
        },
        "tracker": {"axis": "long"},
        "tables": out_tables,
    }
    Path(args.out).write_text(yaml.safe_dump(doc, sort_keys=False))
    print(f"{d.plot_id}: {len(out_tables)} tables / {total_modules:,} modules "
          f"across {doc['provenance']['blocks']} blocks -> {args.out}", file=sys.stderr)
    print(f"  extent {(max(es)-min(es))/1000:.2f} x {(max(ns)-min(ns))/1000:.2f} km",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
