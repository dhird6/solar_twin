"""Extract an EXACT plant layout from a vendor DWG/DXF (FR-26).

    # DWG -> DXF once (LibreDWG; see docs/ENVIRONMENT.md for the build):
    dwg2dxf -o site.dxf site.dwg
    python3 tools/layout_from_dxf.py site.dxf --out configs/layouts/<site>.yaml

This supersedes `tools/layout_from_pdf.py` for any site where the DWG/DXF exists,
and it is the path you should always prefer. The PDF tool has to *infer* a scale
from a plotted sheet and — as its own calibration proves — cannot do so reliably
(two independent sources disagreed by 9.2%). A DXF needs no inference at all:
model space carries real survey coordinates in real units, so the layout is exact
by construction rather than exact-if-calibration-holds.

What makes these drawings machine-readable
------------------------------------------
The CAD is self-describing, so nothing here is guessed:

* `$INSUNITS = 6` → **metres**, and model-space coordinates are the site's
  projected survey grid (Khavda: UTM 42N / EPSG:32642, verified by round-tripping
  a corner to WGS84 and landing inside the park).
* Tracker tables are **block INSERTs** whose *names* carry their dimensions, e.g.
  `MMS Table (128.58 x 2.278)` — MMS = Module Mounting Structure.
* **Layer** names carry the module count and tracker type, e.g.
  `Interior HSAT (1x112)` — HSAT = Horizontal Single-Axis Tracker, 112 modules in
  one row. Module pitch is therefore `table_length / n_modules`, not a guess.

Two traps this module handles, both of which silently corrupt a layout:

1. **Layer `DETAILS` holds legend/typical-detail copies of the tracker block.**
   They sit far outside the array and are NOT site hardware. Including them
   inflates the table count and wrecks the site bounding box.
2. **LibreDWG can emit a raw newline inside a text value**, which breaks the
   strict DXF code/value line alternation and defeats even `ezdxf.recover`.
   `repair_dxf()` re-joins those continuation lines deterministically.

⚠ Trackers ROTATE. The emitted `tilt_deg` is a *nominal/stowed* angle only; a
single fixed tilt is wrong for an HSAT site by construction (see
`farm_builder.py`'s global-tilt limitation). The tracker axis is the table's long
axis; `rotation` here is the INSERT's plan rotation, not the panel tilt.
"""

from __future__ import annotations

import argparse
import collections
import re
from dataclasses import dataclass
from pathlib import Path

#: Block names for module mounting structures / tracker tables.
_TABLE_BLOCK_RE = re.compile(r"MMS[ _]", re.IGNORECASE)

#: `... (128.58 x 2.278)` -> (128.58, 2.278). Vendors write (length x width).
_DIMS_RE = re.compile(r"\(\s*([\d.]+)\s*[xX]\s*([\d.]+)\s*\)")

#: `Interior HSAT (1x112)` -> 112 modules in 1 row.
_MODULES_RE = re.compile(r"\(\s*(\d+)\s*[xX]\s*(\d+)\s*\)")

#: Layers that are drawing furniture, never site hardware.
_NON_SITE_LAYERS = {"DETAILS", "DETAIL", "0"}

#: DXF ASCII strictly alternates: a group-code line, then a value line.
_CODE_RE = re.compile(r"^\s*-?\d+\s*$")


@dataclass(frozen=True)
class Table:
    """One tracker table at exact survey coordinates (metres)."""

    table_id: str
    easting: float
    northing: float
    rotation_deg: float
    length_m: float
    width_m: float
    n_modules: int
    module_rows: int
    layer: str

    @property
    def module_pitch_m(self) -> float:
        """Centre-to-centre module spacing along the torque tube."""
        per_row = self.n_modules / max(1, self.module_rows)
        return self.length_m / per_row if per_row else 0.0


def repair_dxf(src: Path, dst: Path) -> int:
    """Re-join value lines that a converter split with a raw newline.

    Returns the number of merges. DXF ASCII alternates code/value lines, so a
    line appearing where a code is expected but which is not an integer must be a
    continuation of the previous value — that is unambiguous, not heuristic.
    """
    lines = src.read_text(errors="replace").splitlines()
    out: list[str] = []
    expect_code = True
    merged = 0
    for line in lines:
        if expect_code:
            if _CODE_RE.match(line):
                out.append(line)
                expect_code = False
            elif out:
                out[-1] = out[-1] + " " + line.strip()
                merged += 1
        else:
            out.append(line)
            expect_code = True
    dst.write_text("\n".join(out) + "\n")
    return merged


def _parse_dims(block_name: str) -> tuple[float, float] | None:
    m = _DIMS_RE.search(block_name)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (max(a, b), min(a, b))  # (length, width) regardless of vendor order


def _parse_modules(layer: str) -> tuple[int, int] | None:
    """Return (n_modules, module_rows) from a layer like `Interior HSAT (1x112)`."""
    m = _MODULES_RE.search(layer)
    if not m:
        return None
    rows, per_row = int(m.group(1)), int(m.group(2))
    return (rows * per_row, rows)


def read_tables(dxf_path: Path) -> tuple[list[Table], dict]:
    """Every real tracker table in model space, plus a provenance/units report."""
    from ezdxf import recover  # noqa: PLC0415 — dev-tool dep

    doc, audit = recover.readfile(str(dxf_path))
    msp = doc.modelspace()
    insunits = doc.header.get("$INSUNITS")
    if insunits != 6:
        raise SystemExit(
            f"$INSUNITS={insunits}, expected 6 (metres). Refusing to guess units — "
            "re-export the DXF in metres (FR-27: fail closed, never assume scale)."
        )

    tables: list[Table] = []
    skipped: collections.Counter = collections.Counter()
    for e in msp:
        if e.dxftype() != "INSERT" or not _TABLE_BLOCK_RE.search(e.dxf.name):
            continue
        layer = e.dxf.layer
        if layer.upper() in _NON_SITE_LAYERS:
            skipped[layer] += 1
            continue
        dims = _parse_dims(e.dxf.name)
        mods = _parse_modules(layer)
        if dims is None or mods is None:
            skipped[f"unparsed:{layer}|{e.dxf.name}"] += 1
            continue
        length, width = dims
        n_mod, rows = mods
        tables.append(
            Table(
                table_id=f"T{len(tables):04d}",
                easting=round(e.dxf.insert.x, 3),
                northing=round(e.dxf.insert.y, 3),
                rotation_deg=round(float(e.dxf.rotation), 3),
                length_m=length,
                width_m=width,
                n_modules=n_mod,
                module_rows=rows,
                layer=layer,
            )
        )

    report = {
        "audit_errors": len(audit.errors),
        "insunits": insunits,
        "skipped": dict(skipped),
        "n_tables": len(tables),
        "n_modules": sum(t.n_modules for t in tables),
    }
    return tables, report


def site_yaml(tables: list[Table], report: dict, source: str, epsg: int) -> str:
    """Canonical table-level site file (IF-08) — exact, no calibration needed."""
    es = [t.easting for t in tables]
    ns = [t.northing for t in tables]
    pitches = collections.Counter(round(t.module_pitch_m, 5) for t in tables)
    lines = [
        "# GENERATED by tools/layout_from_dxf.py — do not hand-edit; re-run instead.",
        f"# source: {source}",
        "# Coordinates are EXACT model-space survey metres from the vendor CAD",
        "# ($INSUNITS=6). No scale inference is involved, unlike the PDF path.",
        "",
        f"crs: EPSG:{epsg}",
        "units: meters",
        "",
        "provenance:",
        f"  tables: {report['n_tables']}",
        f"  modules: {report['n_modules']}",
        f"  skipped_non_site: {report['skipped']!r}",
        "",
        "# Site bounding box in survey coordinates (real hardware only).",
        "extent:",
        f"  easting:  [{min(es):.3f}, {max(es):.3f}]",
        f"  northing: [{min(ns):.3f}, {max(ns):.3f}]",
        "",
        "# Anchor: stage-local (0,0) maps here. Chosen as the SW-most table so",
        "# stage coordinates stay small and positive.",
        "origin:",
        f"  easting: {min(es):.3f}",
        f"  northing: {min(ns):.3f}",
        "",
        "module:",
        f"  # pitch = table_length / modules_per_row, exact from the CAD: {dict(pitches)}",
        f"  pitch_m: {pitches.most_common(1)[0][0]}",
        "  length_m: 2.278   # = table width; confirm against the module datasheet",
        "  width_m: 1.134    # pitch minus the ~14 mm inter-module gap ⚠ verify",
        "",
        "tracker:",
        "  kind: hsat        # horizontal single-axis; tilt is DYNAMIC, not fixed",
        "  axis: long        # rotation axis = the table's long dimension",
        "  nominal_tilt_deg: 0.0   # stowed/flat; a real run sweeps this",
        "",
        "tables:",
    ]
    for t in tables:
        lines.append(
            f"  - {{ id: {t.table_id}, e: {t.easting}, n: {t.northing}, "
            f"rot_deg: {t.rotation_deg}, length_m: {t.length_m}, width_m: {t.width_m}, "
            f"modules: {t.n_modules}, module_rows: {t.module_rows}, layer: '{t.layer}' }}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dxf")
    ap.add_argument("--out", help="write the canonical site YAML here")
    ap.add_argument("--epsg", type=int, default=32642, help="projected CRS of the CAD")
    ap.add_argument("--no-repair", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.dxf)
    path = src
    if not args.no_repair:
        fixed = src.with_suffix(".fixed.dxf")
        merged = repair_dxf(src, fixed)
        print(f"repair: merged {merged} split value line(s) -> {fixed.name}")
        path = fixed

    tables, report = read_tables(path)
    print(f"tables={report['n_tables']}  modules={report['n_modules']}")
    print(f"skipped non-site: {report['skipped']}")
    by_layer = collections.Counter(t.layer for t in tables)
    for lay, n in by_layer.most_common():
        print(f"   {lay:26s} {n}")
    rots = collections.Counter(t.rotation_deg for t in tables)
    print(f"rotations: {dict(rots)}")

    # Column/row pitches straight from real coordinates — the exactness check.
    for label, vals in (
        ("easting", sorted({t.easting for t in tables})),
        ("northing", sorted({t.northing for t in tables})),
    ):
        gaps = collections.Counter(round(b - a, 2) for a, b in zip(vals, vals[1:]))
        print(f"{label}: {len(vals)} distinct, gaps={gaps.most_common(5)}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            site_yaml(tables, report, source=src.name, epsg=args.epsg)
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
