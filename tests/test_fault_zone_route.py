"""`route: fault_zone` — the survey window for ScoutDispatchMission (no Isaac).

The route exists to solve one measured problem: on the whole plot a sparse fault
rate makes a sequential sweep useless to watch. At `faults.rate` 5e-4 over 679,616
modules there are ~340 faults, so a 24-panel window starting at panel 0 has a ~1%
chance of containing one — and a run that flags nothing is indistinguishable from
a broken escalation path. These tests pin the properties that make it work, and
the guard that keeps it from being mistaken for a measurement route.
"""

from solar_twin.schema.pv_module import PanelState
from solar_twin.world.layout import FarmLayout


def _farm(rate: float, cols: int = 400, seed: int = 20260730) -> dict:
    return {
        "seed": seed,
        "grid": {
            "rows": 1,
            "cols": cols,
            "row_pitch": 6.0,
            "col_pitch": 2.2,
            "origin": [0.0, 0.0, 0.0],
        },
        "georef": {"lat0": 24.09, "lon0": 69.42, "elev0": 0.0, "heading_deg": 0.0},
        "faults": {"rate": rate, "states": ["hotspot", "soiled"]},
    }


def _zone(layout: FarmLayout, **mission):
    cfg = {"route": "fault_zone"}
    cfg.update(mission)
    return layout.route_sites(cfg)


def test_window_contains_a_seeded_fault():
    """The whole point: the survey must have something to find."""
    layout = FarmLayout(_farm(0.01))  # 4 faults in 400 panels
    zone = _zone(layout, zone_panels=24)
    faults = layout.seeded_faults()
    assert any(s.panel_id in faults for s in zone)


def test_window_is_the_requested_size():
    layout = FarmLayout(_farm(0.01))
    assert len(_zone(layout, zone_panels=24)) == 24
    assert len(_zone(layout, zone_panels=8)) == 8


def test_window_is_contiguous_so_commutes_stay_short():
    """Contiguity is what keeps the fleet's travel inside the zone small — a
    window picked by nearest-fault could straddle the plot."""
    layout = FarmLayout(_farm(0.01))
    zone = _zone(layout, zone_panels=12)
    all_ids = [s.panel_id for s in layout.sites]
    start = all_ids.index(zone[0].panel_id)
    assert [s.panel_id for s in zone] == all_ids[start : start + 12]


def test_window_is_centred_on_the_fault_not_merely_adjacent():
    layout = FarmLayout(_farm(0.01))
    zone = _zone(layout, zone_panels=24)
    faults = layout.seeded_faults()
    hit = [i for i, s in enumerate(zone) if s.panel_id in faults]
    assert hit, "expected a fault in the window"
    # The centred fault sits near the middle, not at an edge.
    assert 4 <= hit[0] <= 19


def test_zone_index_selects_a_different_fault():
    """So a demo can be re-run over another part of the plant."""
    layout = FarmLayout(_farm(0.02))  # 8 faults
    a = _zone(layout, zone_panels=12, zone_index=0)
    b = _zone(layout, zone_panels=12, zone_index=1)
    assert a[0].panel_id != b[0].panel_id


def test_zone_index_past_the_end_clamps_instead_of_raising():
    layout = FarmLayout(_farm(0.01))
    zone = _zone(layout, zone_panels=12, zone_index=999)
    assert len(zone) == 12


def test_window_clamps_at_the_start_of_the_site_list():
    """A fault in panel 0 must not produce a negative slice start, which in Python
    would silently wrap to the END of the plot — a window nowhere near the fault."""
    # Search seeds until the first seeded fault lands inside the first half-window.
    for seed in range(200):
        layout = FarmLayout(_farm(0.02, cols=100, seed=seed))
        faults = layout.seeded_faults()
        positions = [i for i, s in enumerate(layout.sites) if s.panel_id in faults]
        if positions and min(positions) < 6:
            zone = _zone(layout, zone_panels=12, zone_index=0)
            assert len(zone) == 12
            assert zone[0].panel_id == layout.sites[0].panel_id
            all_ids = [s.panel_id for s in layout.sites]
            start = all_ids.index(zone[0].panel_id)
            assert [s.panel_id for s in zone] == all_ids[start : start + 12]
            return
    raise AssertionError("no seed put a fault near the start; widen the search")


def test_window_clamps_at_the_end_of_the_site_list():
    for seed in range(200):
        layout = FarmLayout(_farm(0.02, cols=100, seed=seed))
        faults = layout.seeded_faults()
        positions = [i for i, s in enumerate(layout.sites) if s.panel_id in faults]
        if positions and max(positions) > 94:
            zone = _zone(layout, zone_panels=12, zone_index=len(positions) - 1)
            assert len(zone) == 12
            assert zone[-1].panel_id == layout.sites[-1].panel_id
            return
    raise AssertionError("no seed put a fault near the end; widen the search")


def test_no_faults_falls_back_loudly_rather_than_returning_nothing():
    """An all-healthy stage (the wide shot's `rate: 0.0`) must still return a
    window, so the run proceeds and the warning explains why nothing escalates."""
    layout = FarmLayout(_farm(0.0))
    assert layout.seeded_faults() == {}
    zone = _zone(layout, zone_panels=10)
    assert [s.panel_id for s in zone] == [s.panel_id for s in layout.sites[:10]]


def test_fault_zone_ignores_stride():
    """Subsampling could drop the very panel the window was built around."""
    layout = FarmLayout(_farm(0.01))
    zone = _zone(layout, zone_panels=12, panel_stride=7)
    all_ids = [s.panel_id for s in layout.sites]
    start = all_ids.index(zone[0].panel_id)
    assert [s.panel_id for s in zone] == all_ids[start : start + 12]


def test_fault_zone_is_deterministic():
    a = _zone(FarmLayout(_farm(0.01)), zone_panels=16)
    b = _zone(FarmLayout(_farm(0.01)), zone_panels=16)
    assert [s.panel_id for s in a] == [s.panel_id for s in b]


def test_linear_route_is_untouched_by_the_new_branch():
    """The measurement default must not have moved: every recorded KPI was swept
    in `linear` order."""
    layout = FarmLayout(_farm(0.01))
    assert [s.panel_id for s in layout.route_sites({})] == [
        s.panel_id for s in layout.sites
    ]


def test_zone_panels_larger_than_the_stage_returns_the_whole_stage():
    layout = FarmLayout(_farm(0.02, cols=10))
    zone = _zone(layout, zone_panels=50)
    assert len(zone) == 10


def test_faults_in_window_are_slice0_taxonomy_states():
    layout = FarmLayout(_farm(0.01))
    faults = layout.seeded_faults()
    zone = _zone(layout, zone_panels=24)
    states = {faults[s.panel_id] for s in zone if s.panel_id in faults}
    assert states, "expected at least one fault in the window"
    assert states <= {PanelState.HOTSPOT, PanelState.SOILED}
