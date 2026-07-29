"""CAD-derived site expansion (IF-08 / FR-26) — pure, no Isaac, no pyproj needed.

Numbers here are the real Khavda BLOCK-02 values from
`configs/layouts/khavda_a10b_block02.yaml`, so a regression in the expansion
maths shows up as a wrong *real* site, not a wrong toy.
"""

import math
from pathlib import Path

from solar_twin.world.layout import FarmLayout, PanelSite
from solar_twin.world.layout_import import (
    module_positions,
    parse_site,
    to_wgs84,
)

# One 1x112 tracker table plus a short one, at true survey coordinates.
SITE = {
    "crs": "EPSG:32642",
    "origin": {"easting": 542440.651, "northing": 2664033.041},
    "module": {"pitch_m": 1.14804, "length_m": 2.278, "width_m": 1.134},
    "tracker": {"kind": "hsat", "nominal_tilt_deg": 0.0},
    "tables": [
        {
            "id": "T0000",
            "e": 542440.651,
            "n": 2664033.041,
            "rot_deg": 0.0,
            "length_m": 128.58,
            "width_m": 2.278,
            "modules": 112,
            "module_rows": 1,
            "layer": "Interior HSAT (1x112)",
        },
        {
            "id": "T0001",
            "e": 542446.651,
            "n": 2664033.041,
            "rot_deg": 0.0,
            "length_m": 64.404,
            "width_m": 2.278,
            "modules": 56,
            "module_rows": 1,
            "layer": "Interior HSAT (1x56)",
        },
    ],
}


def test_module_pitch_is_derived_not_assumed():
    site = parse_site(SITE)
    long_table, short_table = site.tables
    # 128.58 / 112 and 64.404 / 56 — both must come out of the CAD dimensions.
    assert math.isclose(long_table.module_pitch_m, 128.58 / 112, rel_tol=1e-9)
    assert math.isclose(short_table.module_pitch_m, 64.404 / 56, rel_tol=1e-9)


def test_module_count_and_layout_along_torque_tube():
    site = parse_site(SITE)
    assert site.n_modules == 168  # 112 + 56
    pos = module_positions(site.tables[0])
    assert len(pos) == 112
    # Single row -> every module shares one easting, at the table's mid-width.
    eastings = {round(e, 6) for e, _ in pos}
    assert eastings == {round(542440.651 + 2.278 / 2, 6)}
    # Modules march north at the exact pitch, first one half a pitch in.
    pitch = site.tables[0].module_pitch_m
    # abs_tol, not rel_tol: differencing two ~2.66e6 m northings loses precision
    # to float cancellation (eps at that magnitude is ~5e-10), so a 1e-12 relative
    # tolerance is unachievable regardless of correctness. 1 um is ample here.
    assert math.isclose(pos[0][1], 2664033.041 + pitch / 2, abs_tol=1e-6)
    assert math.isclose(pos[1][1] - pos[0][1], pitch, abs_tol=1e-6)
    # The last module must fit inside the table, not hang off the end.
    assert pos[-1][1] < 2664033.041 + site.tables[0].length_m


def test_column_pitch_is_the_aisle_between_tables():
    site = parse_site(SITE)
    assert math.isclose(site.column_pitch_m(), 6.0, rel_tol=1e-9)


def test_grid_path_is_untouched_by_the_new_switch():
    """`layout.kind` defaults to grid, so every pre-existing config still works."""
    layout = FarmLayout(
        {
            "seed": 1,
            "terrain": {"kind": "flat"},
            "grid": {"rows": 1, "cols": 2, "row_pitch": 6.0, "col_pitch": 2.2},
            "georef": {"lat0": 0.0, "lon0": 0.0},
        }
    )
    assert layout.kind == "grid"
    assert layout.n_panels == 2
    assert layout.sites[0].azimuth_deg == 0.0


def test_real_block02_site_expands_exactly():
    """End-to-end against the committed, CAD-derived Khavda BLOCK-02 site file."""
    site_path = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"
    if not site_path.exists():  # pragma: no cover — file is committed
        return
    layout = FarmLayout(
        {
            "seed": 20260727,
            "terrain": {"kind": "flat"},
            "layout": {"kind": "file", "path": str(site_path)},
        }
    )
    assert layout.kind == "file"
    # The exact hardware count from the drawing: 273 tables, 30,016 modules.
    assert len(layout.site.tables) == 273
    assert layout.n_panels == 30016

    # Stage-local coordinates are origin-anchored, so nothing is negative and the
    # block's real footprint is preserved. Note the HARDWARE extent (~319 x 647 m)
    # is larger than the insert-point extent (319 x 518 m): each table's modules
    # run a further 128.58 m north of its SW-corner insert.
    min_x, min_y, max_x, max_y = layout.bounds()
    assert min_x >= 0.0 and min_y >= 0.0
    assert 300.0 < max_x < 340.0, max_x
    assert 630.0 < max_y < 660.0, max_y

    # Panel IDs and (row, col) pairs must be unique — `panel_path()` derives the
    # USD prim path from (row, col), so a collision would silently overwrite prims.
    assert len({s.panel_id for s in layout.sites}) == 30016
    assert len({(s.row, s.col) for s in layout.sites}) == 30016


def test_panel_site_defaults_keep_grid_path_compatible():
    s = PanelSite("R00-C000", 0, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert s.azimuth_deg == 0.0
    assert s.tilt_deg == 0.0


def test_to_wgs84_lands_on_the_real_site_or_is_absent():
    """EPSG:32642 must put BLOCK-02 inside the Khavda park, if pyproj is present.

    Zone 43N would land ~6 degrees east (Madhya Pradesh), so this also guards
    against a silently wrong CRS in the site file.
    """
    got = to_wgs84(542440.651, 2664033.041, "EPSG:32642")
    if got is None:
        return  # pyproj not installed — geometry path still valid
    lat, lon = got
    assert 23.5 < lat < 24.5, lat
    assert 68.5 < lon < 70.0, lon


def test_subset_is_a_crop_not_a_different_site():
    """A `--subset` build must be a genuine crop of the full site.

    Two properties, both load-bearing:
      * panel coordinates are identical to the full build, and
      * panel IDs are NOT renumbered.
    If IDs shifted, `R00-C000` would mean different hardware in a subset run and
    verdicts would be written onto the wrong panels.
    """
    site_path = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"
    if not site_path.exists():  # pragma: no cover
        return
    base = {"seed": 1, "terrain": {"kind": "flat"}}
    full = FarmLayout({**base, "layout": {"kind": "file", "path": str(site_path)}})
    sub = FarmLayout(
        {**base, "layout": {"kind": "file", "path": str(site_path), "max_tables": 5}}
    )
    assert len(sub.site.tables) == 5
    assert sub.n_panels < full.n_panels

    full_pos = {s.panel_id: s.position for s in full.sites}
    for s in sub.sites:
        assert s.panel_id in full_pos, s.panel_id
        assert s.position == full_pos[s.panel_id]

    # The band is the SOUTHERNMOST tables, so the subset is contiguous farm a drone
    # can fly down — not a scatter of tables with impossible gaps between them.
    sub_ns = {t.northing for t in sub.site.tables}
    full_ns = sorted({t.northing for t in full.site.tables})
    assert max(sub_ns) <= full_ns[len(sub_ns) - 1], "subset is not the southern band"


def test_subset_bounds_are_smaller_and_origin_anchored():
    site_path = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"
    if not site_path.exists():  # pragma: no cover
        return
    sub = FarmLayout(
        {
            "seed": 1,
            "terrain": {"kind": "flat"},
            "layout": {"kind": "file", "path": str(site_path), "max_tables": 5},
        }
    )
    min_x, min_y, max_x, max_y = sub.bounds()
    assert min_x >= 0.0 and min_y >= 0.0
    assert max_x < 40.0 and max_y < 140.0  # 5 tables ~ one short band
