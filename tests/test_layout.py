"""FarmLayout geometry + seeded fault determinism (no Isaac)."""

from solar_twin.schema.pv_module import PanelState
from solar_twin.world.layout import FarmLayout


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
