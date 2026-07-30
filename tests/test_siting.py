"""Wind-turbine siting: wake-constrained scatter, not a lattice (pure, no Isaac)."""

import math

import pytest

from solar_twin.world.siting import (
    TurbineSite,
    buildable_ring,
    lattice_score,
    min_spacing_ellipse,
    rpm_to_deg_per_s,
    scatter_turbines,
)

EXTENT = (0.3, 0.0, 321.0, 646.9)  # the real Khavda BLOCK-02 panel footprint


# --------------------------------------------------------------- wake spacing
def test_spacing_is_anisotropic_downwind_versus_across():
    """The whole reason this is not a Poisson disk: a wake is long and narrow, so
    the same separation can be legal across the wind and illegal along it."""
    # Wind FROM the south (0 travels north... bearing 180 = from south).
    # Downwind allowance 700 m, crosswind 400 m.
    args = (180.0, 700.0, 400.0)
    # 500 m due north of A is DOWNWIND of it -> inside the 700 m wake, illegal.
    assert not min_spacing_ellipse(0, 0, 0, 500, *args)
    # The same 500 m due east is CROSSWIND -> outside the 400 m limit, legal.
    assert min_spacing_ellipse(0, 0, 500, 0, *args)


def test_spacing_uses_the_reciprocal_of_the_wind_bearing():
    """Met convention: `wind_dir_deg` is where the wind comes FROM. A westerly
    (270) blows towards the east, so the wake extends east."""
    args = (270.0, 700.0, 400.0)
    assert not min_spacing_ellipse(0, 0, 500, 0, *args)   # east = downwind
    assert min_spacing_ellipse(0, 0, 0, 500, *args)       # north = crosswind


def test_a_point_is_never_far_enough_from_itself():
    assert not min_spacing_ellipse(10, 10, 10, 10, 250.0, 700.0, 400.0)


# ----------------------------------------------------------- buildable region
def test_the_buildable_ring_excludes_the_panel_field():
    zones = buildable_ring(EXTENT, setback_m=210.0, depth_m=840.0)
    min_x, min_y, max_x, max_y = EXTENT
    for x0, y0, x1, y1 in zones:
        # No zone may overlap the array footprint — a turbine inside the array
        # would shade panels and corrupt any KPI-03 measured against it.
        assert x1 <= min_x - 210.0 + 1e-9 or x0 >= max_x + 210.0 - 1e-9 or (
            y1 <= min_y - 210.0 + 1e-9 or y0 >= max_y + 210.0 - 1e-9
        ), (x0, y0, x1, y1)


# ------------------------------------------------------------------- scatter
def test_scatter_is_not_a_grid():
    """The hand-written field was two columns at fixed eastings: every machine
    shared an x with another, which `lattice_score` reports as 1.0."""
    old = [TurbineSite(-95.0, y, 120.0, 140.0, 11.0) for y in (60.0, 300.0, 540.0)] + [
        TurbineSite(415.0, y, 120.0, 140.0, 11.0) for y in (150.0, 430.0)
    ]
    assert lattice_score(old) == pytest.approx(1.0)

    new = scatter_turbines(EXTENT, n=5, seed=20260727)
    assert len(new) >= 4
    assert lattice_score(new) < 0.5


def test_scatter_respects_the_wake_rule_between_every_pair():
    sites = scatter_turbines(EXTENT, n=6, seed=11, rotor_diameter_m=140.0)
    downwind, crosswind = 7.0 * 140.0, 4.0 * 140.0
    for i, a in enumerate(sites):
        for b in sites[i + 1 :]:
            assert min_spacing_ellipse(a.x, a.y, b.x, b.y, 250.0, downwind, crosswind)


def test_scatter_keeps_every_turbine_clear_of_the_array():
    sites = scatter_turbines(EXTENT, n=6, seed=3, rotor_diameter_m=140.0)
    min_x, min_y, max_x, max_y = EXTENT
    setback = 1.5 * 140.0
    for s in sites:
        outside_x = s.x <= min_x - setback or s.x >= max_x + setback
        outside_y = s.y <= min_y - setback or s.y >= max_y + setback
        assert outside_x or outside_y, f"turbine at {s.x},{s.y} is not clear of the array"


def test_scatter_is_seeded_and_reproducible():
    """Unseeded turbine positions would move the shadows between builds, which
    would silently invalidate comparing a KPI across runs."""
    a = scatter_turbines(EXTENT, n=5, seed=42)
    b = scatter_turbines(EXTENT, n=5, seed=42)
    c = scatter_turbines(EXTENT, n=5, seed=43)
    assert [(s.x, s.y) for s in a] == [(s.x, s.y) for s in b]
    assert [(s.x, s.y) for s in a] != [(s.x, s.y) for s in c]


def test_an_impossible_count_is_reported_not_silently_truncated():
    """No silent caps (NFR-07): asking for more machines than the ring can hold
    must say so."""
    logs = []
    sites = scatter_turbines(EXTENT, n=400, seed=7, max_darts=3000, log=logs.append)
    assert len(sites) < 400
    assert any("sited" in m and "warn" in m for m in logs)


def test_geometry_is_real_utility_scale():
    sites = scatter_turbines(EXTENT, n=3, seed=5, hub_height_m=120.0, rotor_diameter_m=140.0)
    for s in sites:
        assert s.blade_len_m == pytest.approx(70.0)
        assert s.tip_height_m == pytest.approx(190.0)
        assert 10.0 <= s.rpm <= 13.0


def test_to_cfg_matches_the_shape_farm_builder_and_keepout_already_read():
    """Scattering must not require touching `keepout.build_keepouts` — it reads
    the same `turbines:` list, so recomputed no-fly volumes come for free."""
    cfg = scatter_turbines(EXTENT, n=1, seed=1)[0].to_cfg()
    assert set(cfg) == {"pos", "hub_height", "blade_len", "rpm"}
    assert len(cfg["pos"]) == 2
    assert all(isinstance(v, (int, float)) for v in cfg["pos"])


def test_lattice_score_of_a_single_turbine_is_zero_not_a_crash():
    assert lattice_score(scatter_turbines(EXTENT, n=1, seed=1)) == 0.0
    assert lattice_score([]) == 0.0


def test_scatter_returns_nothing_rather_than_crashing_on_a_degenerate_ring():
    assert scatter_turbines(EXTENT, n=3, seed=1, ring_depth_d=0.0) == []


def test_spacing_scales_with_rotor_diameter():
    """Spacing is in rotor diameters, so a bigger machine needs more room — the
    field must thin out, not stay put."""
    small = scatter_turbines(EXTENT, n=12, seed=9, rotor_diameter_m=80.0, max_darts=8000)
    big = scatter_turbines(EXTENT, n=12, seed=9, rotor_diameter_m=180.0, max_darts=8000)
    assert len(small) > len(big)


def test_nearest_neighbour_distance_is_at_least_the_crosswind_limit():
    """A weaker but very legible invariant: whatever the wind direction, no two
    machines may be closer than the crosswind allowance."""
    sites = scatter_turbines(EXTENT, n=6, seed=21, rotor_diameter_m=140.0)
    for i, a in enumerate(sites):
        for b in sites[i + 1 :]:
            assert math.dist((a.x, a.y), (b.x, b.y)) >= 4.0 * 140.0 - 1e-6


# ------------------------------------------------- keep-outs must not drift
def test_keepouts_resolve_from_the_same_scattered_field_as_the_build():
    """If `build_keepouts` read the raw `turbines:` list while the builder
    scattered, the enforced no-fly volumes would sit at the OLD positions and the
    planner would route a drone through a tower that is really somewhere else."""
    from solar_twin.world.keepout import build_keepouts
    from solar_twin.world.siting import resolve_turbines

    class _T:
        def __init__(self, e, n, ln):
            self.easting, self.northing, self.length_m = e, n, ln

    class _Site:
        origin_easting = origin_northing = 0.0
        module_length_m = 2.278
        n_modules = 0
        tables = [_T(0.0, 0.0, 128.0), _T(300.0, 0.0, 128.0)]

    class _Layout:
        site = _Site()

    cfg = {
        "seed": 20260727,
        "terrain": {"kind": "flat"},
        # An explicit list that the scatter must override — and the keep-outs
        # must follow the scatter, not this.
        "turbines": [{"pos": [-95.0, 60.0], "hub_height": 120.0, "blade_len": 70.0}],
        "turbine_scatter": {"enabled": True, "count": 3, "rotor_diameter": 140.0},
    }
    layout = _Layout()
    resolved = resolve_turbines(cfg, layout)
    kos = build_keepouts(cfg, layout)
    assert len(kos) == len(resolved) >= 1
    for spec, ko in zip(resolved, kos):
        assert ko.tower_xy == pytest.approx(tuple(spec["pos"]))
    # ...and none of them is the overridden explicit position.
    assert all(ko.tower_xy != pytest.approx((-95.0, 60.0)) for ko in kos)


def test_an_explicit_turbine_list_still_wins_when_scatter_is_off():
    """Existing configs, and any KPI run pinned to known turbine positions, must
    be unaffected."""
    from solar_twin.world.siting import resolve_turbines

    cfg = {"turbines": [{"pos": [1.0, 2.0], "hub_height": 120.0, "blade_len": 70.0}]}
    assert resolve_turbines(cfg, None) == cfg["turbines"]
    assert resolve_turbines({**cfg, "turbine_scatter": {"enabled": False}}, None) == cfg["turbines"]


def test_scatter_without_a_layout_falls_back_and_says_so():
    from solar_twin.world.siting import resolve_turbines

    logs = []
    cfg = {"turbines": [{"pos": [1.0, 2.0]}], "turbine_scatter": {"enabled": True}}
    assert resolve_turbines(cfg, None, log=logs.append) == cfg["turbines"]
    assert any("needs a site layout" in m for m in logs)


def test_the_shipped_narrow_ring_is_still_far_from_a_lattice():
    """The config ships `ring_depth_d: 2.5` so the machines read at true scale
    against the block. A narrow ring constrains one axis, so `lattice_score` rises
    from 0.00 (wide ring) to ~0.40 — still nothing like the 1.00 of the two
    evenly-spaced columns it replaced, and the wake rule still holds."""
    sites = scatter_turbines(EXTENT, n=5, seed=20260727, ring_depth_d=2.5)
    assert len(sites) == 5
    assert lattice_score(sites) <= 0.6
    for i, a in enumerate(sites):
        for b in sites[i + 1 :]:
            assert min_spacing_ellipse(a.x, a.y, b.x, b.y, 250.0, 7 * 140.0, 4 * 140.0)


# ------------------------------------------------- rotor speed (FR-11, pure half)
# One conversion, because two consumers must agree: `farm_builder`'s USD angular
# drive wants deg/s, and `sim_runtime`'s kinematic spin wants deg per update. They
# had drifted apart as two separate literals, and a hub that visually spins at one
# rate while being driven at another is the sort of thing only a blade-shadow KPI
# eventually notices.


def test_one_rpm_is_six_degrees_per_second():
    assert rpm_to_deg_per_s(1.0) == 6.0


def test_a_full_revolution_takes_sixty_seconds_at_one_rpm():
    assert rpm_to_deg_per_s(1.0) * 60.0 == pytest.approx(360.0)


@pytest.mark.parametrize("rpm,expected", [(0.0, 0.0), (10.0, 60.0), (12.0, 72.0)])
def test_typical_rotor_speeds(rpm, expected):
    assert rpm_to_deg_per_s(rpm) == pytest.approx(expected)


def test_the_kinematic_spin_rate_still_matches_the_old_literal():
    """`sim_runtime` used `rpm * 0.2` deg/update, i.e. 30 updates/s. Replacing a
    magic number must not change how fast the blade shadows sweep — the KPI-03
    stimulus depends on that rate."""
    for rpm in (5.0, 10.0, 12.0, 20.0):
        assert rpm_to_deg_per_s(rpm) / 30.0 == pytest.approx(rpm * 0.2)


# ------------------------------------- precedence: scatter wins over the list
# The obvious reading of these two config keys is the wrong one, and it cost a
# scenario its stated intent: `nominal_calm.yaml` set `turbines: []`, documented
# itself as turbine-free, and built FOUR (measured 2026-07-29). Both the docstring
# and the farm config had claimed an explicit list wins. It does not.


def test_scatter_wins_over_an_explicit_list():
    from solar_twin.world.siting import resolve_turbines

    class _T:
        def __init__(self, e, n, ln):
            self.easting, self.northing, self.length_m = e, n, ln

    class _Site:
        origin_easting = origin_northing = 0.0
        module_length_m = 2.278
        n_modules = 0
        tables = [_T(0.0, 0.0, 128.0), _T(300.0, 0.0, 128.0)]

    class _Layout:
        site = _Site()

    cfg = {
        "seed": 1,
        "turbines": [{"pos": [-95.0, 60.0], "hub_height": 120.0, "blade_len": 70.0}],
        "turbine_scatter": {"enabled": True, "count": 2, "rotor_diameter": 140.0},
    }
    resolved = resolve_turbines(cfg, _Layout())
    assert all(tuple(t["pos"]) != (-95.0, 60.0) for t in resolved)


def test_an_empty_turbine_list_does_not_mean_no_turbines():
    """The trap, pinned. `turbines: []` means "no explicit positions", NOT "no
    turbines" — the scatter still supplies its own."""
    from solar_twin.world.siting import resolve_turbines

    class _T:
        def __init__(self, e, n, ln):
            self.easting, self.northing, self.length_m = e, n, ln

    class _Site:
        origin_easting = origin_northing = 0.0
        module_length_m = 2.278
        n_modules = 0
        tables = [_T(0.0, 0.0, 128.0), _T(300.0, 0.0, 128.0)]

    class _Layout:
        site = _Site()

    cfg = {
        "seed": 1,
        "turbines": [],
        "turbine_scatter": {"enabled": True, "count": 2, "rotor_diameter": 140.0},
    }
    assert len(resolve_turbines(cfg, _Layout())) > 0


def test_disabling_the_scatter_is_how_you_get_a_turbine_free_stage():
    """What the KPI-03 and SC-01 scenarios actually need in order to isolate their
    stressor from blade shadows."""
    from solar_twin.world.siting import resolve_turbines

    cfg = {"seed": 1, "turbines": [], "turbine_scatter": {"enabled": False}}
    assert resolve_turbines(cfg, None) == []


def test_every_scenario_claiming_no_turbines_actually_disables_the_scatter():
    """The reproducibility guard. A scenario writing `turbines: []` is stating an
    intent ("isolate my stressor from blade shadows") that the empty list alone
    does not deliver — the scatter wins. Measured 2026-07-29: rebuilding
    `khavda_selfshade_lowsun` produced 3 turbines while the stage its recorded
    KPI-03 was measured on has 0, so the scenario no longer regenerated what it
    measured.

    Checks the **effective** config via `load_scenario`, not the override block: a
    scenario extending `configs/farm.yaml` (no scatter defined) is fine with a bare
    empty list, while one extending `farm_khavda_block02.yaml` (scatter enabled) is
    not. Testing the raw override would flag the former and is how a guard becomes
    noise nobody trusts.
    """
    import pathlib

    from solar_twin.scenario import load_scenario
    from solar_twin.world.siting import resolve_turbines

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((root / "configs" / "scenarios").glob("*.yaml")):
        farm_cfg = load_scenario(str(path)).farm_cfg
        declared_none = not (farm_cfg.get("turbines") or [])
        scatter_on = bool((farm_cfg.get("turbine_scatter") or {}).get("enabled"))
        if declared_none and scatter_on:
            offenders.append(path.name)
        elif declared_none:
            # ...and with the scatter off it really does resolve to nothing.
            assert resolve_turbines(farm_cfg, None) == [], path.name
    assert not offenders, (
        "these scenarios resolve to a turbine-free stage in intent but build "
        "turbines because `turbine_scatter` is still enabled: " + ", ".join(offenders)
    )


# --------------------------------------------------- interspersed placement
# A co-located wind+solar park stands its machines AMONG the DC blocks rather
# than around them. That is the truer layout for Khavda, and it is also the one
# that can be physically impossible on a given plot — so these tests pin both the
# geometry rule and the refusal to fake it.

BLADE_TIP_D = 0.5


def _grid_layout(n_x, n_y, table_w=4.3, table_len=128.0, pitch_x=11.8, pitch_y=200.0):
    """Tables on one regular grid — a single DC block, all maintenance aisles."""
    return [
        (
            i * pitch_x,
            j * pitch_y,
            i * pitch_x + table_w,
            j * pitch_y + table_len,
        )
        for i in range(n_x)
        for j in range(n_y)
    ]


def _block_layout(n_bx, n_by, per_block=8, gap_m=400.0):
    """DC blocks separated by wide corridors — the multi-block plot shape.

    This distinction is the whole subject of these tests: `_grid_layout` is one
    block, whose gaps are 5-6 m aisles, and no utility turbine fits in it.
    `_block_layout` is a plot, whose gaps BETWEEN blocks are where a co-located
    park actually puts its machines. Measured on the real layouts (2026-07-30):
    BLOCK-02's largest interior clearing is 25 m, the 24-block S05b plot's is
    >=500 m.
    """
    table_w, table_len, pitch_x = 4.3, 128.0, 11.8
    block_w = per_block * pitch_x
    out = []
    for bx in range(n_bx):
        for by in range(n_by):
            x0 = bx * (block_w + gap_m)
            y0 = by * (table_len + gap_m)
            for i in range(per_block):
                out.append((x0 + i * pitch_x, y0, x0 + i * pitch_x + table_w, y0 + table_len))
    return out


def _extent_of(fp):
    return (min(r[0] for r in fp), min(r[1] for r in fp),
            max(r[2] for r in fp), max(r[3] for r in fp))


def test_a_turbine_never_stands_within_a_blade_length_of_a_table():
    """The rule that makes this placement legal at all: blades must sweep open
    ground, not glass. A rotor of diameter D reaches D/2 from the tower axis."""
    from solar_twin.world.siting import PLACEMENT_INTERSPERSED, scatter_turbines

    fp = _block_layout(5, 5)
    extent = _extent_of(fp)
    sites = scatter_turbines(
        extent, n=4, seed=7, rotor_diameter_m=140.0,
        placement=PLACEMENT_INTERSPERSED, footprints=fp, table_clearance_d=0.6,
    )
    assert sites, "this layout has room; siting nothing would be the other bug"
    for s in sites:
        for x0, y0, x1, y1 in fp:
            # Distance from the point to the rectangle, 0 when inside.
            dx = max(x0 - s.x, 0.0, s.x - x1)
            dy = max(y0 - s.y, 0.0, s.y - y1)
            assert math.hypot(dx, dy) >= BLADE_TIP_D * s.rotor_diameter_m, (
                f"turbine at ({s.x:.1f}, {s.y:.1f}) sweeps blades over a table"
            )


def test_interspersed_turbines_land_inside_the_array_not_around_it():
    """The whole point of the placement: they are IN the footprint. A ring would
    pass the clearance test above trivially, so this is what distinguishes them."""
    from solar_twin.world.siting import PLACEMENT_INTERSPERSED, scatter_turbines

    fp = _block_layout(5, 5)
    extent = _extent_of(fp)
    sites = scatter_turbines(
        extent, n=4, seed=7, rotor_diameter_m=140.0,
        placement=PLACEMENT_INTERSPERSED, footprints=fp,
    )
    assert sites
    for s in sites:
        assert extent[0] <= s.x <= extent[2] and extent[1] <= s.y <= extent[3]


def test_a_clearance_inside_the_rotor_sweep_is_refused_not_clamped():
    """Asking for less than the blade tip is a physical contradiction, so it
    raises. Silently clamping it up would hide a config that means something
    impossible."""
    from solar_twin.world.siting import PLACEMENT_INTERSPERSED, scatter_turbines

    fp = _grid_layout(4, 2)
    with pytest.raises(ValueError, match="blade tip"):
        scatter_turbines(
            (0.0, 0.0, 100.0, 100.0), n=1, seed=1,
            placement=PLACEMENT_INTERSPERSED, footprints=fp, table_clearance_d=0.4,
        )


def test_a_dense_single_block_sites_nothing_and_says_why():
    """`NFR-07`, no silent caps. A block with only maintenance aisles cannot hold
    a utility machine, and the honest output is zero turbines plus a reason — not
    a quiet fallback to a ring, which would let the stage claim a hybrid layout it
    does not have."""
    from solar_twin.world.siting import PLACEMENT_INTERSPERSED, scatter_turbines

    # 5.9 m aisles: the real spacing inside one Khavda DC block.
    fp = _grid_layout(20, 1, pitch_x=10.2, pitch_y=200.0)
    extent = (min(r[0] for r in fp), min(r[1] for r in fp),
              max(r[2] for r in fp), max(r[3] for r in fp))
    said = []
    sites = scatter_turbines(
        extent, n=5, seed=3, rotor_diameter_m=140.0,
        placement=PLACEMENT_INTERSPERSED, footprints=fp, log=said.append,
    )
    assert sites == []
    assert any("no room BETWEEN its blocks" in m for m in said), said


def test_interspersed_without_footprints_falls_back_and_says_so():
    from solar_twin.world.siting import (
        PLACEMENT_INTERSPERSED, buildable_ring, scatter_turbines,
    )

    said = []
    sites = scatter_turbines(
        EXTENT, n=3, seed=5, placement=PLACEMENT_INTERSPERSED, footprints=None,
        ring_depth_d=6.0, log=said.append,
    )
    assert any("needs table footprints" in m for m in said), said
    # ...and having fallen back, it really did use the perimeter ring.
    zones = buildable_ring(EXTENT, 1.5 * 140.0, 6.0 * 140.0)
    for s in sites:
        assert any(x0 <= s.x <= x1 and y0 <= s.y <= y1 for x0, y0, x1, y1 in zones)


def test_interspersed_is_seeded_and_reproducible():
    from solar_twin.world.siting import PLACEMENT_INTERSPERSED, scatter_turbines

    fp = _block_layout(5, 5)
    extent = _extent_of(fp)
    kw = dict(placement=PLACEMENT_INTERSPERSED, footprints=fp, rotor_diameter_m=140.0)
    a = scatter_turbines(extent, n=4, seed=99, **kw)
    b = scatter_turbines(extent, n=4, seed=99, **kw)
    c = scatter_turbines(extent, n=4, seed=100, **kw)
    assert len(a) >= 2
    assert [(s.x, s.y) for s in a] == [(s.x, s.y) for s in b]
    assert [(s.x, s.y) for s in a] != [(s.x, s.y) for s in c]


def test_wake_spacing_still_holds_between_interspersed_machines():
    """The placement changes where darts may land, not the physics that rejects
    them. A turbine in another's wake is just as wrong inside the array."""
    from solar_twin.world.siting import (
        PLACEMENT_INTERSPERSED, min_spacing_ellipse, scatter_turbines,
    )

    fp = _block_layout(6, 6)
    extent = _extent_of(fp)
    sites = scatter_turbines(
        extent, n=5, seed=11, rotor_diameter_m=140.0,
        placement=PLACEMENT_INTERSPERSED, footprints=fp,
    )
    assert len(sites) >= 2
    for i, a in enumerate(sites):
        for b in sites[i + 1:]:
            assert min_spacing_ellipse(a.x, a.y, b.x, b.y, 250.0, 7 * 140.0, 4 * 140.0)


def test_an_unknown_placement_is_rejected_rather_than_defaulted():
    from solar_twin.world.siting import resolve_turbines

    cfg = {"seed": 1, "turbine_scatter": {"enabled": True, "placement": "middle"}}
    with pytest.raises(ValueError, match="placement"):
        resolve_turbines(cfg, _FakeLayout())


class _FakeLayout:
    class site:
        pass


def test_table_footprints_agree_with_the_extent_they_are_measured_against():
    """`table_extent` is the hull of `table_footprints`. They live in one module
    precisely so this stays true; the northing and half-chord conventions are easy
    to get right once and wrong twice."""
    from solar_twin.world.site import table_extent, table_footprints

    class T:
        def __init__(self, e, n, ln):
            self.easting, self.northing, self.length_m = e, n, ln

    class S:
        origin_easting = 100.0
        origin_northing = 200.0
        module_length_m = 4.3
        tables = [T(100.0, 200.0, 128.0), T(140.0, 260.0, 96.0)]

    fp = table_footprints(S)
    hull = (min(r[0] for r in fp), min(r[1] for r in fp),
            max(r[2] for r in fp), max(r[3] for r in fp))
    assert hull == pytest.approx(table_extent(S))


def test_interspersed_turbines_are_surrounded_by_panels_not_parked_in_a_void():
    """The rule clearance alone does not give you.

    A plot's bounding box is mostly air, and under a 980 x 560 m wake ellipse
    uniform darts survive best in the biggest holes. Measured on the real S05b
    plot before the enclosure rule existed: 1 of 8 machines had panels on all four
    sides and one had none within 800 m — every one of them legally "inside the
    array". This pins the difference between inside-the-hull and among-the-blocks.
    """
    from solar_twin.world.siting import (
        MIN_OCCUPIED_QUADRANTS, PLACEMENT_INTERSPERSED, _Occupancy, scatter_turbines,
    )

    # An L of blocks: a large void in one corner, which is the trap.
    fp = [r for r in _block_layout(6, 6) if not (r[0] > 2000.0 and r[1] > 1500.0)]
    extent = _extent_of(fp)
    sites = scatter_turbines(
        extent, n=5, seed=17, rotor_diameter_m=140.0,
        placement=PLACEMENT_INTERSPERSED, footprints=fp,
    )
    assert sites
    occ = _Occupancy(extent, fp, step_m=25.0)
    for s in sites:
        q = occ.occupied_quadrants(s.x, s.y, 4.0 * s.rotor_diameter_m)
        assert q >= MIN_OCCUPIED_QUADRANTS, (
            f"turbine at ({s.x:.0f}, {s.y:.0f}) has panels in only {q}/4 quadrants "
            f"— it is beside the plant, not in it"
        )


def test_the_candidate_lattice_does_not_make_the_field_a_grid():
    """`interior_candidates` enumerates on a 25 m lattice, and a lattice is the one
    thing this module exists not to produce. The jitter is what keeps them apart,
    so this fails if that jitter is ever dropped."""
    from solar_twin.world.siting import (
        PLACEMENT_INTERSPERSED, lattice_score, scatter_turbines,
    )

    fp = _block_layout(7, 7)
    sites = scatter_turbines(
        _extent_of(fp), n=6, seed=23, rotor_diameter_m=140.0,
        placement=PLACEMENT_INTERSPERSED, footprints=fp,
    )
    assert len(sites) >= 3
    assert lattice_score(sites) < 1.0
    # ...and no coordinate sits exactly on the enumeration lattice.
    assert not all(abs(s.x % 25.0) < 1e-9 for s in sites)


def test_enclosure_uses_a_prefix_sum_that_agrees_with_the_naive_count():
    """`_Occupancy` is an optimisation, and an optimisation that disagrees with the
    obvious implementation is just a bug that runs fast."""
    from solar_twin.world.siting import _Occupancy

    fp = _block_layout(4, 4)
    extent = _extent_of(fp)
    occ = _Occupancy(extent, fp, step_m=25.0)
    for x, y in [(200.0, 300.0), (900.0, 900.0), (1500.0, 200.0), (0.0, 0.0)]:
        for r in (200.0, 560.0):
            naive = sum(
                any(
                    not (x1 <= qx0 or x0 >= qx1 or y1 <= qy0 or y0 >= qy1)
                    for x0, y0, x1, y1 in fp
                )
                for qx0, qy0, qx1, qy1 in (
                    (x, y, x + r, y + r), (x - r, y, x, y + r),
                    (x - r, y - r, x, y), (x, y - r, x + r, y),
                )
            )
            fast = occ.occupied_quadrants(x, y, r)
            # The grid rounds outward by up to one cell, so it may see a table the
            # exact test misses — never the other way round.
            assert fast >= naive, (x, y, r, fast, naive)
