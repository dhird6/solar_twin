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
