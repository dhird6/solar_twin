"""Expand a real, CAD-derived site file into panel sites (IF-08 / FR-26).

Consumes the canonical table-level site YAML that `tools/layout_from_dxf.py`
emits from a vendor DWG/DXF, and expands it into one `PanelSite` per physical
module — the same list `FarmLayout` produces for a procedural grid, so every
downstream consumer (seeded faults, `panel_records`, `inspection_targets`,
`farm_builder`, the KPI harness) works unchanged.

Pure-python: no Isaac import (`NFR-01`). `pyproj` is imported lazily inside the
geo conversion only, so the geometry path — and its tests — run without it.

Coordinate contract
-------------------
The site file holds **exact survey coordinates in metres** (easting/northing in a
projected CRS, e.g. EPSG:32642 for Khavda). We map those to the stage's local
Z-up metre frame by subtracting `origin`, so stage coordinates stay small and
positive while remaining an exact rigid translation of the real site:

    x_local = easting  - origin.easting      (+X = east)
    y_local = northing - origin.northing     (+Y = north)

`geo_position` then goes the other way, CRS → WGS84, so `pv:geo_position` is a
true lat/lon rather than the flat-earth approximation `local_to_geo` applies to a
small procedural farm. That matters here: this block is ~520 m across and the
site as a whole spans tens of kilometres.

Table → module geometry (verified against the Khavda BLOCK-02 CAD)
-----------------------------------------------------------------
A tracker table's INSERT point is its **south-west corner**, and the block
extends `+width` east and `+length` north — so the torque tube runs north-south,
which is what makes it a *horizontal single-axis tracker* (it rotates about that
N-S axis to follow the sun east-west). Modules sit in a single row along the tube:

    module k centre = ( e + width/2 ,  n + (k + 0.5) * pitch )

⚠ **Tracker tilt is dynamic.** `tilt_deg` returned here is the site file's
`nominal_tilt_deg` (stowed/flat). A real run must drive it from sun position —
a single fixed tilt is wrong for an HSAT site by construction. `azimuth_deg` is
the plan rotation of the table, which for this site is 0 for every table.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    """One tracker table, exactly as the CAD placed it."""

    table_id: str
    easting: float
    northing: float
    rot_deg: float
    length_m: float
    width_m: float
    modules: int
    module_rows: int
    layer: str = ""

    @property
    def modules_per_row(self) -> int:
        return self.modules // max(1, self.module_rows)

    @property
    def module_pitch_m(self) -> float:
        per_row = self.modules_per_row
        return self.length_m / per_row if per_row else 0.0


@dataclass(frozen=True)
class SiteSpec:
    """A parsed canonical site file."""

    crs: str
    origin_easting: float
    origin_northing: float
    module_pitch_m: float
    module_length_m: float
    module_width_m: float
    nominal_tilt_deg: float
    tables: list[TableSpec]

    @property
    def n_modules(self) -> int:
        return sum(t.modules for t in self.tables)

    def column_pitch_m(self) -> float:
        """Median across-row spacing — the gap a drone flies down between rows."""
        es = sorted({t.easting for t in self.tables})
        gaps = [b - a for a, b in zip(es, es[1:]) if b - a > 0.01]
        return statistics.median(gaps) if gaps else 0.0


def parse_site(cfg: dict) -> SiteSpec:
    """Build a `SiteSpec` from a parsed site-file dict. Pure; no I/O."""
    tables_cfg = cfg.get("tables") or []
    if not tables_cfg:
        raise ValueError("site file has no 'tables:' — nothing to expand")
    origin = cfg.get("origin") or {}
    module = cfg.get("module") or {}
    tracker = cfg.get("tracker") or {}

    tables = [
        TableSpec(
            table_id=str(t.get("id", f"T{i:04d}")),
            easting=float(t["e"]),
            northing=float(t["n"]),
            rot_deg=float(t.get("rot_deg", 0.0)),
            length_m=float(t["length_m"]),
            width_m=float(t["width_m"]),
            modules=int(t["modules"]),
            module_rows=int(t.get("module_rows", 1)),
            layer=str(t.get("layer", "")),
        )
        for i, t in enumerate(tables_cfg)
    ]
    # Origin defaults to the SW-most table so stage coords stay small + positive.
    return SiteSpec(
        crs=str(cfg.get("crs", "")),
        origin_easting=float(origin.get("easting", min(t.easting for t in tables))),
        origin_northing=float(origin.get("northing", min(t.northing for t in tables))),
        module_pitch_m=float(module.get("pitch_m", 0.0)),
        module_length_m=float(module.get("length_m", 0.0)),
        module_width_m=float(module.get("width_m", 0.0)),
        nominal_tilt_deg=float(tracker.get("nominal_tilt_deg", 0.0)),
        tables=tables,
    )


def load_site(path: str) -> SiteSpec:
    """Read + parse a canonical site YAML."""
    import yaml  # noqa: PLC0415 — lazy so importing this module needs no yaml

    with open(path) as f:
        return parse_site(yaml.safe_load(f))


def module_positions(table: TableSpec) -> list[tuple[float, float]]:
    """Every module centre on a table, as (easting, northing) in metres.

    The INSERT point is the table's SW corner; modules march north along the
    torque tube at the exact CAD-derived pitch. Multi-row tables (none in the
    Khavda BLOCK-02 drawing, but `1xN` is not guaranteed elsewhere) stack across
    the width.
    """
    pitch = table.module_pitch_m
    per_row = table.modules_per_row
    rows = max(1, table.module_rows)
    row_w = table.width_m / rows
    out: list[tuple[float, float]] = []
    for r in range(rows):
        e = table.easting + (r + 0.5) * row_w
        for k in range(per_row):
            out.append((e, table.northing + (k + 0.5) * pitch))
    return out


def to_wgs84(easting: float, northing: float, crs: str) -> tuple[float, float] | None:
    """(easting, northing) in `crs` → (lat, lon). None if pyproj is unavailable.

    Returning None rather than raising keeps the geometry path usable (and
    testable) on a machine without pyproj; callers fall back to the existing
    `GeoAnchor` flat-earth mapping and should say so, per `NFR-07`.
    """
    if not crs:
        return None
    try:
        from pyproj import Transformer  # noqa: PLC0415 — optional dep
    except ImportError:
        return None
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(
        easting, northing
    )
    return (lat, lon)


def expand_sites(site: SiteSpec, terrain_z, panel_site_cls, panel_id_fn):
    """Expand tables into per-module `PanelSite`s in stage-local metres.

    `terrain_z(x, y) -> float` supplies ground height so panels sit on the grade,
    exactly as the procedural path does. `panel_site_cls` / `panel_id_fn` are
    injected to keep this module free of a circular import back into `layout`.

    The synthetic `(row, col)` index is **(table index, module index)**. It is
    required because `schema.pv_module.panel_path()` derives the USD prim path
    from `(row, col)`, so every panel needs a unique pair even though its
    human-facing identity is really `<table_id>` + module number.
    """
    sites = []
    for ti, table in enumerate(site.tables):
        latlon_ok = True
        for mi, (e, n) in enumerate(module_positions(table)):
            x = e - site.origin_easting
            y = n - site.origin_northing
            z = terrain_z(x, y)
            geo = to_wgs84(e, n, site.crs)
            if geo is None:
                latlon_ok = False
                geo_position = (0.0, 0.0, z)
            else:
                geo_position = (geo[0], geo[1], z)
            sites.append(
                panel_site_cls(
                    panel_id=panel_id_fn(ti, mi),
                    row=ti,
                    col=mi,
                    position=(x, y, z),
                    geo_position=geo_position,
                    azimuth_deg=table.rot_deg,
                    tilt_deg=site.nominal_tilt_deg,
                )
            )
        if not latlon_ok and ti == 0:
            print(
                "  [warn] pyproj unavailable — pv:geo_position left at (0,0); "
                "install pyproj for true lat/lon (FR-20)"
            )
    return sites
