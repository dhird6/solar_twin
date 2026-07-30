"""FarmLayout geometry + seeded fault determinism (no Isaac)."""

import math
import random
from pathlib import Path

from solar_twin.schema.pv_module import PanelState
from solar_twin.world.layout import (
    FarmLayout,
    fault_cells,
    soiling_tiles,
    terrain_height,
)


FARM = {
    "seed": 20260721,
    "grid": {
        "rows": 1,
        "cols": 10,
        "row_pitch": 6.0,
        "col_pitch": 2.2,
        "origin": [0.0, 0.0, 0.0],
    },
    "georef": {"lat0": 33.4484, "lon0": -112.0740, "elev0": 331.0, "heading_deg": 0.0},
    "faults": {"rate": 0.2, "states": ["hotspot", "soiled"]},
}


def test_grid_size_and_ids():
    layout = FarmLayout(FARM)
    assert layout.n_panels == 10
    assert layout.sites[0].panel_id == "R00-C000"
    assert layout.sites[-1].panel_id == "R00-C009"


def test_positions_use_col_pitch():
    layout = FarmLayout(FARM)
    assert layout.sites[1].position[0] == 2.2
    assert layout.sites[5].position[0] == 11.0


def test_seeded_faults_are_deterministic_and_sized():
    a = FarmLayout(FARM).seeded_faults()
    b = FarmLayout(FARM).seeded_faults()
    assert a == b  # same seed -> identical picks
    assert len(a) == 2  # 0.2 * 10
    assert all(s in (PanelState.HOTSPOT, PanelState.SOILED) for s in a.values())


def test_panel_records_apply_faults():
    layout = FarmLayout(FARM)
    faults = layout.seeded_faults()
    records = {r.panel_id: r for r in layout.panel_records()}
    for pid, state in faults.items():
        assert records[pid].state is state
    healthy = [r for r in records.values() if r.is_healthy]
    assert len(healthy) == 8


def test_inspection_targets_cover_all_panels():
    layout = FarmLayout(FARM)
    targets = layout.inspection_targets({"kinematics": {}})
    assert len(targets) == 10
    # screen standoff above the panel; confirm closer.
    assert targets[0].screen.z > targets[0].confirm.z


def test_standoffs_are_above_the_panel_top():
    # Regression: confirm camera used to land *below* the panel (abs-Z bug).
    # Second regression, same shape: "top" used to mean mount height + half
    # thickness, ignoring TILT — so on a 60 deg tracker the camera was placed a
    # metre under the module's raised edge. Top is the panel's highest point.
    farm = {**FARM, "panel": {"mount_height": 0.75, "height": 0.05}}
    layout = FarmLayout(farm)
    top = layout.panel_top_z()
    # length 2.0 (default) tilted 20 deg -> the upper edge rises 1.0*sin(20).
    expect = 0.75 + 1.0 * math.sin(math.radians(20.0)) + 0.025 * math.cos(
        math.radians(20.0)
    )
    assert math.isclose(top, expect)
    assert top > 0.775  # strictly higher than the old flat-panel answer
    t = layout.inspection_targets(
        {"kinematics": {"screen_standoff": 2.5, "confirm_standoff": 0.8}}
    )[0]
    assert math.isclose(t.confirm.z, expect + 0.8)  # strictly above the panel top
    assert math.isclose(t.screen.z, expect + 2.5)
    assert t.confirm.z > top


def test_panel_top_clears_a_tracker_at_its_stop():
    """The case that broke the confirm pass on the real block: a 2.278 m module
    rotated 60 deg lifts its upper edge ~0.99 m above the torque tube, so a
    0.8 m confirm standoff measured off a FLAT top puts the camera inside the
    row. Uses the real site file, driven by a real instant."""
    site_path = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"
    if not site_path.exists():  # pragma: no cover — file is committed
        return
    layout = FarmLayout(
        {
            "layout": {"kind": "file", "path": str(site_path), "max_tables": 2},
            "terrain": {"kind": "flat"},
            "georef": {"lat0": 24.088, "lon0": 69.418},
            "panel": {"mount_height": 1.5, "height": 0.035},
            "sun": {"timestamp": "2026-06-21T02:00:00Z", "tracker_max_rotation_deg": 60.0},
        }
    )
    assert math.isclose(layout.tracker_rotation_deg(), 60.0)
    site = layout.sites[0]
    # Site-file dimensions reach the panel, un-transposed: chord across the aisle.
    assert math.isclose(site.size_x_m, 2.278) and math.isclose(site.size_y_m, 1.134)
    rise = 2.278 / 2 * math.sin(math.radians(60.0))
    assert math.isclose(layout.panel_top_z(0.0, site), 1.5 + rise + 0.0175 * 0.5, rel_tol=1e-9)
    assert layout.panel_top_z(0.0, site) > 1.5 + 0.98   # the flat answer was 1.52
    t = layout.inspection_targets({"kinematics": {"confirm_standoff": 0.8}})[0]
    assert t.confirm.z > 1.5 + rise                     # clears the raised edge


def test_terrain_flat_by_default_and_deterministic():
    assert terrain_height(3.0, 4.0, {}) == 0.0
    assert terrain_height(3.0, 4.0, {"terrain": {"kind": "flat"}}) == 0.0
    hf = {"terrain": {"kind": "heightfield", "amplitude": 0.6, "wavelength": 14.0}}
    a = terrain_height(3.0, 4.0, hf)
    assert terrain_height(3.0, 4.0, hf) == a  # pure/deterministic
    assert abs(a) <= 0.6  # bounded by amplitude


def test_panels_sit_on_grade_and_standoffs_stay_above_top():
    farm = {
        **FARM,
        "panel": {"mount_height": 0.75, "height": 0.05},
        "terrain": {"kind": "heightfield", "amplitude": 0.6, "wavelength": 14.0},
    }
    layout = FarmLayout(farm)
    # At least one panel is off the z=0 plane (sitting on the grade).
    zs = [s.position[2] for s in layout.sites]
    assert any(abs(z) > 1e-6 for z in zs)
    # Each panel's confirm waypoint is strictly above THAT panel's top.
    targets = {t.panel_id: t for t in layout.inspection_targets(
        {"kinematics": {"screen_standoff": 2.5, "confirm_standoff": 0.8}}
    )}
    for s in layout.sites:
        top = layout.panel_top_z(s.position[2])
        assert targets[s.panel_id].confirm.z > top
        assert targets[s.panel_id].screen.z > targets[s.panel_id].confirm.z


def test_fault_cells_covers_hotspot_only():
    # Hotspot is genuinely cell-localized (one cell overheats) -> cell-aligned.
    hot = fault_cells(PanelState.HOTSPOT, 10, 6, random.Random("y"))
    assert 1 <= len(hot) <= 2
    assert fault_cells(PanelState.HOTSPOT, 10, 6, random.Random("y")) == hot
    # Soiling is NOT a cell-level fault — it is a film (see soiling_tiles), so
    # rendering it cell-aligned made the VLM read it as a design pattern.
    assert fault_cells(PanelState.SOILED, 10, 6, random.Random("x")) == set()
    assert fault_cells(PanelState.HEALTHY, 10, 6, random.Random("z")) == set()


def test_soiling_tiles_are_ragged_localized_and_lower_biased():
    rows, cols = 28, 16
    a = soiling_tiles(rows, cols, random.Random("s"))
    b = soiling_tiles(rows, cols, random.Random("s"))
    assert a == b  # deterministic in the rng
    assert 0 < len(a) < rows * cols  # partial coverage, not the whole panel

    # Dust pools at the LOWER edge (row 0), so the bottom half carries more.
    lower = sum(1 for r, _ in a if r < rows // 2)
    assert lower > len(a) / 2

    # Ragged, not a clean rectangle: at least one row is partially covered.
    from collections import Counter

    per_row = Counter(r for r, _ in a)
    assert any(0 < n < cols for n in per_row.values())

    # Degenerate grids are safe.
    assert soiling_tiles(0, 5, random.Random("s")) == set()




def _file_layout(max_tables=4, **mission):
    site_path = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"
    if not site_path.exists():  # pragma: no cover — committed
        return None, None
    layout = FarmLayout(
        {
            "layout": {"kind": "file", "path": str(site_path), "max_tables": max_tables},
            "terrain": {"kind": "flat"},
            "georef": {"lat0": 24.088, "lon0": 69.418},
            "panel": {"mount_height": 1.5, "height": 0.035},
        }
    )
    return layout, mission


def test_route_defaults_to_linear_so_kpis_stay_comparable():
    layout, _ = _file_layout()
    if layout is None:
        return
    assert [s.panel_id for s in layout.route_sites({})] == [s.panel_id for s in layout.sites]


def test_serpentine_reverses_alternate_tables():
    """A one-way sweep of a 128 m table means a 128 m deadhead back to the start
    of the next row, every row. Serpentine turns round instead."""
    layout, _ = _file_layout()
    if layout is None:
        return
    route = layout.route_sites({"route": "serpentine"})
    assert len(route) == len(layout.sites)              # same panels, new order
    assert {s.panel_id for s in route} == {s.panel_id for s in layout.sites}

    tables: dict[int, list] = {}
    for s in route:
        tables.setdefault(s.row, []).append(s)
    orders = list(tables.values())
    # First table runs south->north, the second north->south, and so on.
    assert orders[0][0].position[1] < orders[0][-1].position[1]
    assert orders[1][0].position[1] > orders[1][-1].position[1]
    assert orders[2][0].position[1] < orders[2][-1].position[1]

    # The point of it: no long jump between the end of one row and the start of
    # the next. Compare the worst consecutive hop against the linear order.
    def worst_hop(sites):
        return max(
            math.dist(a.position, b.position) for a, b in zip(sites, sites[1:])
        )

    assert worst_hop(route) < 0.25 * worst_hop(layout.route_sites({}))


def test_panel_stride_samples_a_coverage_sweep():
    layout, _ = _file_layout(max_tables=2)
    if layout is None:
        return
    full = layout.route_sites({})
    every4 = layout.route_sites({"panel_stride": 4})
    assert len(every4) == math.ceil(len(full) / 4)
    assert every4[0].panel_id == full[0].panel_id
    assert every4[1].panel_id == full[4].panel_id


def test_inspection_targets_follow_the_route_order():
    layout, _ = _file_layout()
    if layout is None:
        return
    cfg = {"route": "serpentine", "panel_stride": 3, "kinematics": {}}
    ids = [t.panel_id for t in layout.inspection_targets(cfg)]
    assert ids == [s.panel_id for s in layout.route_sites(cfg)]


# --------------------------------------------------------------------------- #
# `grid:id` — the dispatch-cell join key on a PanelRecord
#
# `panel_records()` used to leave `cell_id` blank, so the first suspicion-first
# demo had to stamp the join key by hand — exactly how the mission and the stage
# drift apart. It is now derived by `layout.cell_id_for`, the SAME function
# `farm_builder._cell_id_for` forwards to, so a record's `cell_id` and the prim's
# `grid:id` are the same string by construction rather than by agreement.
# --------------------------------------------------------------------------- #

#: 3 tables x 4 modules with the grid layer ON. A cell is a TABLE, so a whole row
#: of this farm shares one id.
GRID_FARM = {
    **FARM,
    "grid": {**FARM["grid"], "rows": 3, "cols": 4, "enabled": True},
}


def _grid_farm(**grid_overrides) -> dict:
    return {**GRID_FARM, "grid": {**GRID_FARM["grid"], **grid_overrides}}


def test_panel_records_stamp_the_same_cell_id_the_builder_authors():
    """One derivation, two callers. A join key with two conventions is not a
    join key — which is why `farm_builder._cell_id_for` is a one-line forward."""
    from solar_twin.schema.pv_module import cell_for_panel
    from solar_twin.world.layout import cell_id_for

    layout = FarmLayout(_grid_farm())
    for site, rec in zip(layout.sites, layout.panel_records()):
        assert rec.cell_id == cell_id_for(site, layout.cfg)
        assert rec.cell_id == cell_for_panel((site.row, site.col))


def test_a_cell_is_a_table_so_a_whole_row_shares_one_id():
    recs = FarmLayout(_grid_farm()).panel_records()
    assert {r.cell_id for r in recs} == {"G-0000", "G-0001", "G-0002"}
    assert len([r for r in recs if r.cell_id == "G-0002"]) == 4


def test_cell_id_is_empty_unless_the_grid_layer_is_enabled():
    """Off must be byte-identical to before the namespace existed: the builder
    authors no `grid:id` attribute, so a record must carry no cell either."""
    recs = FarmLayout(_grid_farm(enabled=False)).panel_records()
    assert {r.cell_id for r in recs} == {""}


def test_an_absent_grid_enabled_key_is_also_off():
    """Every farm.yaml predating the namespace omits the key entirely."""
    grid = {k: v for k, v in GRID_FARM["grid"].items() if k != "enabled"}
    farm = {**GRID_FARM, "grid": grid}
    assert {r.cell_id for r in FarmLayout(farm).panel_records()} == {""}


def test_modules_per_cell_subdivides_exactly_as_the_builder_would():
    """Reserved for a real string map; the default of 0 means 'the whole table'."""
    by_id = {r.panel_id: r.cell_id
             for r in FarmLayout(_grid_farm(modules_per_cell=2)).panel_records()}
    assert by_id["R02-C000"] == by_id["R02-C001"] == "G-0002-01"
    assert by_id["R02-C002"] == by_id["R02-C003"] == "G-0002-02"
