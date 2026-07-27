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


