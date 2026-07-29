"""Wind, gust and wake field — `FR-12`/`FR-13`, Isaac-free.

`FR-12` names `omni.physx.forcefields`, which does not exist on Isaac Sim 6.0.1 /
PhysX 110.1.13 (measured — `RISK-27`), so the field is authored here instead. That
makes it testable, which matters more than it might sound: a KPI measured under
gust is only quotable if the gust is reproducible, and reproducibility is a
property of this model, not of the renderer.
"""

from __future__ import annotations

import math

import pytest

from solar_twin.world.siting import min_spacing_ellipse
from solar_twin.world.windfield import (
    DEFAULT_THRUST_COEFF,
    WakeSource,
    WindField,
    downwind_unit,
    from_cfg,
)


# --------------------------------------------------------------------------- #
# Direction convention — must match siting.py or turbines are sited by one wind
# and waked by another
# --------------------------------------------------------------------------- #


def test_a_westerly_travels_east():
    """Met convention: 270 is the bearing the wind comes FROM."""
    dwx, dwy = downwind_unit(270.0)
    assert dwx == pytest.approx(1.0)
    assert dwy == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    "bearing,expected",
    [(0.0, (0.0, -1.0)), (90.0, (-1.0, 0.0)), (180.0, (0.0, 1.0)), (270.0, (1.0, 0.0))],
)
def test_cardinal_bearings(bearing, expected):
    dwx, dwy = downwind_unit(bearing)
    assert dwx == pytest.approx(expected[0], abs=1e-12)
    assert dwy == pytest.approx(expected[1], abs=1e-12)


def test_the_wake_direction_agrees_with_sitings_spacing_ellipse():
    """`siting` rejects a turbine sited downwind of another; this field must put
    the deficit in that same place. If they disagree, siting protects one region
    and the physics disturbs a different one.
    """
    wind_dir = 270.0  # westerly -> downwind is +x
    a = (0.0, 0.0)
    downwind_point = (500.0, 0.0)
    # Must be beyond the ellipse's crosswind axis (560 m) to be "far enough";
    # 500 m is still inside it, which is the anisotropy working as intended.
    crosswind_point = (0.0, 700.0)

    # siting: the downwind point is too close (inside the ellipse), the crosswind
    # point at the same range is acceptable — that anisotropy IS the wake.
    assert not min_spacing_ellipse(*a, *downwind_point, wind_dir, 980.0, 560.0)
    assert min_spacing_ellipse(*a, *crosswind_point, wind_dir, 980.0, 560.0)

    field = WindField(
        mean_speed_ms=10.0,
        wind_dir_deg=wind_dir,
        wakes=(WakeSource(0.0, 0.0, 100.0, 140.0),),
    )
    assert field.wake_deficit_at(*downwind_point, 100.0) > 0.0
    assert field.wake_deficit_at(*crosswind_point, 100.0) == 0.0


# --------------------------------------------------------------------------- #
# Wake (FR-13)
# --------------------------------------------------------------------------- #


def _wake_field(**kw):
    return WindField(
        mean_speed_ms=10.0,
        wind_dir_deg=270.0,
        wakes=(WakeSource(0.0, 0.0, 100.0, 140.0),),
        **kw,
    )


def test_no_deficit_upwind_of_the_rotor():
    """A wake is a downstream phenomenon. A field that slows the air in front of
    the turbine is not a wake, it is a bug."""
    assert _wake_field().wake_deficit_at(-200.0, 0.0, 100.0) == 0.0


def test_deficit_decays_with_downstream_distance():
    f = _wake_field()
    near = f.wake_deficit_at(200.0, 0.0, 100.0)
    far = f.wake_deficit_at(1500.0, 0.0, 100.0)
    assert near > far > 0.0


def test_deficit_is_zero_outside_the_cone_and_nonzero_inside():
    f = _wake_field()
    along = 500.0
    radius = 70.0 + 0.075 * along  # rotor radius + k*x
    assert f.wake_deficit_at(along, radius * 0.5, 100.0) > 0.0
    assert f.wake_deficit_at(along, radius * 1.5, 100.0) == 0.0


def test_the_cone_widens_downstream():
    """Jensen's defining property. A cone of constant width would leave a
    corridor beside the turbine permanently safe, which is not how wake works."""
    f = _wake_field()
    off_axis = 100.0
    # At 200 m the wake radius is 85 m, so 100 m off-axis is outside...
    assert f.wake_deficit_at(200.0, off_axis, 100.0) == 0.0
    # ...but at 1000 m it is 145 m, so the same offset is inside.
    assert f.wake_deficit_at(1000.0, off_axis, 100.0) > 0.0


def test_the_wake_is_centred_on_hub_height_not_the_ground():
    """A hub at 100 m wakes the air at 100 m, not at the panels. At 200 m
    downstream the cone radius is 70 + 0.075*200 = 85 m, so ground level (100 m
    below the hub) is outside it."""
    f = _wake_field()
    assert f.wake_deficit_at(200.0, 0.0, 100.0) > 0.0
    assert f.wake_deficit_at(200.0, 0.0, 0.0) == 0.0


def test_overlapping_wakes_combine_by_sum_of_squares_and_never_exceed_one():
    """Adding deficits linearly lets three turbines produce a NEGATIVE wind
    speed. Katic's root-sum-square is the standard fix and is also bounded."""
    many = tuple(
        WakeSource(-x, 0.0, 100.0, 140.0) for x in (0.0, 200.0, 400.0, 600.0, 800.0)
    )
    f = WindField(mean_speed_ms=10.0, wind_dir_deg=270.0, wakes=many)
    d = f.wake_deficit_at(300.0, 0.0, 100.0)
    assert 0.0 < d <= 1.0
    assert f.speed_at(300.0, 0.0, 100.0) >= 0.0


def test_speed_never_goes_negative_anywhere_on_a_crowded_grid():
    """The property that actually matters: whatever the deficit maths does, air
    must not flow backwards."""
    many = tuple(
        WakeSource(x, y, 100.0, 140.0)
        for x in range(-800, 801, 200)
        for y in range(-400, 401, 200)
    )
    f = WindField(mean_speed_ms=12.0, wind_dir_deg=270.0, wakes=many)
    for x in range(-1000, 1001, 137):
        for y in range(-500, 501, 149):
            assert f.speed_at(float(x), float(y), 100.0) >= 0.0


def test_a_turbine_with_no_thrust_casts_no_wake():
    f = WindField(
        mean_speed_ms=10.0,
        wind_dir_deg=270.0,
        wakes=(WakeSource(0.0, 0.0, 100.0, 140.0, thrust_coeff=0.0),),
    )
    assert f.wake_deficit_at(300.0, 0.0, 100.0) == pytest.approx(0.0)


def test_no_turbines_means_free_stream_everywhere():
    f = WindField(mean_speed_ms=9.0, wind_dir_deg=270.0)
    assert f.speed_at(10.0, 20.0, 30.0) == pytest.approx(9.0)


# --------------------------------------------------------------------------- #
# Gusts (FR-12) — and reproducibility
# --------------------------------------------------------------------------- #


def test_no_variation_means_a_steady_wind():
    f = WindField(mean_speed_ms=8.0, speed_variation=0.0)
    assert f.speed_at(0.0, 0.0, 10.0, t=0.0) == pytest.approx(8.0)
    assert f.speed_at(0.0, 0.0, 10.0, t=17.3) == pytest.approx(8.0)


def test_gusts_actually_vary_the_speed():
    f = WindField(mean_speed_ms=8.0, speed_variation=0.4, gust_period_s=4.0, seed=7)
    speeds = [f.speed_at(0.0, 0.0, 10.0, t=t * 0.25) for t in range(80)]
    assert max(speeds) > min(speeds)
    # ...within the declared envelope, not wildly outside it.
    assert max(speeds) <= 8.0 * 1.4 + 1e-9
    assert min(speeds) >= 8.0 * 0.6 - 1e-9


def test_direction_variation_swings_the_bearing():
    f = WindField(
        mean_speed_ms=8.0, direction_variation_deg=20.0, gust_period_s=4.0, seed=3
    )
    angles = [
        math.degrees(math.atan2(*f.velocity_at(0.0, 0.0, 10.0, t=t * 0.2)[:2][::-1]))
        for t in range(120)
    ]
    assert max(angles) - min(angles) > 1.0


def test_the_same_seed_and_time_give_the_same_wind():
    """The whole point. A gust that differs between repeats makes every KPI
    measured under it unquotable (`RISK-23` is this failure in another guise)."""
    kw = dict(mean_speed_ms=8.0, speed_variation=0.5, direction_variation_deg=20.0)
    a = WindField(seed=42, **kw)
    b = WindField(seed=42, **kw)
    for t in (0.0, 1.7, 13.25, 99.9):
        assert a.velocity_at(5.0, 6.0, 7.0, t) == b.velocity_at(5.0, 6.0, 7.0, t)


def test_different_seeds_give_different_gusts():
    kw = dict(mean_speed_ms=8.0, speed_variation=0.5)
    a = WindField(seed=1, **kw)
    b = WindField(seed=2, **kw)
    assert any(
        a.speed_at(0.0, 0.0, 10.0, t) != b.speed_at(0.0, 0.0, 10.0, t)
        for t in (0.5, 1.0, 2.0, 3.0)
    )


def test_sampling_order_does_not_change_the_field():
    """Closed-form in `t`, not a stepped RNG — so two robots queried in either
    order see the same air. A stepped generator would couple the wind to how many
    times it happened to be sampled."""
    f = WindField(mean_speed_ms=8.0, speed_variation=0.5, seed=11)
    forward = [f.speed_at(0.0, 0.0, 10.0, t) for t in (1.0, 2.0, 3.0)]
    backward = [f.speed_at(0.0, 0.0, 10.0, t) for t in (3.0, 2.0, 1.0)][::-1]
    assert forward == backward


# --------------------------------------------------------------------------- #
# Drag — what an applier turns into a force
# --------------------------------------------------------------------------- #


def test_drag_points_downwind_for_a_stationary_body():
    f = WindField(mean_speed_ms=10.0, wind_dir_deg=270.0)  # travels +x
    fx, fy, fz = f.drag_force(0.0, 0.0, 10.0, drag_area_m2=0.2)
    assert fx > 0.0
    assert fy == pytest.approx(0.0, abs=1e-9)
    assert fz == pytest.approx(0.0)


def test_a_body_moving_with_the_wind_feels_no_drag():
    """Drag depends on RELATIVE air speed. A field that pushes a body harder the
    faster it flees looks like wind and behaves like a spring."""
    f = WindField(mean_speed_ms=10.0, wind_dir_deg=270.0)
    fx, fy, fz = f.drag_force(
        0.0, 0.0, 10.0, body_velocity=(10.0, 0.0, 0.0), drag_area_m2=0.2
    )
    # Not exactly zero: `cos(radians(270))` is ~-1.8e-16, so the cross-wind
    # component carries float residue. Assert it is negligible rather than
    # pretending the arithmetic is exact.
    assert math.sqrt(fx * fx + fy * fy + fz * fz) == pytest.approx(0.0, abs=1e-12)


def test_drag_opposes_a_body_flying_upwind():
    f = WindField(mean_speed_ms=0.0, wind_dir_deg=270.0)  # still air
    fx, _, _ = f.drag_force(
        0.0, 0.0, 10.0, body_velocity=(5.0, 0.0, 0.0), drag_area_m2=0.2
    )
    assert fx < 0.0  # resists motion through still air


def test_drag_scales_with_the_square_of_relative_speed():
    f = WindField(mean_speed_ms=10.0, wind_dir_deg=270.0)
    f2 = WindField(mean_speed_ms=20.0, wind_dir_deg=270.0)
    one = f.drag_force(0.0, 0.0, 10.0, drag_area_m2=0.2)[0]
    two = f2.drag_force(0.0, 0.0, 10.0, drag_area_m2=0.2)[0]
    assert two == pytest.approx(4.0 * one)


def test_drag_matches_the_closed_form():
    """0.5 * rho * Cd * A * v^2 — pinned so a refactor cannot quietly rescale the
    forces a station-keeping KPI is measured against."""
    f = WindField(mean_speed_ms=10.0, wind_dir_deg=270.0)
    fx, _, _ = f.drag_force(
        0.0, 0.0, 10.0, drag_area_m2=0.25, drag_coeff=1.1, air_density=1.225
    )
    assert fx == pytest.approx(0.5 * 1.225 * 1.1 * 0.25 * 10.0**2)


def test_drag_in_a_wake_is_weaker_than_in_free_stream():
    """The two features have to compose: a drone sheltering behind a turbine
    should feel less, which is the whole reason to model the deficit."""
    f = WindField(
        mean_speed_ms=12.0,
        wind_dir_deg=270.0,
        wakes=(WakeSource(0.0, 0.0, 100.0, 140.0),),
    )
    sheltered = f.drag_force(300.0, 0.0, 100.0, drag_area_m2=0.2)[0]
    exposed = f.drag_force(-300.0, 0.0, 100.0, drag_area_m2=0.2)[0]
    assert 0.0 <= sheltered < exposed


def test_zero_area_feels_nothing():
    f = WindField(mean_speed_ms=10.0)
    assert f.drag_force(0.0, 0.0, 10.0, drag_area_m2=0.0) == (0.0, 0.0, 0.0)


def test_a_negative_drag_area_is_refused():
    with pytest.raises(ValueError):
        WindField(mean_speed_ms=1.0).drag_force(0.0, 0.0, 0.0, drag_area_m2=-1.0)


# --------------------------------------------------------------------------- #
# Config plumbing (IF-03)
# --------------------------------------------------------------------------- #


def test_from_cfg_reads_the_wind_block():
    cfg = {
        "seed": 20260727,
        "wind": {
            "mean_speed": 12.0,
            "direction_deg": 200.0,
            "speed_variation": 0.3,
            "direction_variation_deg": 15.0,
            "gust_period_s": 6.0,
        },
    }
    f = from_cfg(cfg)
    assert f.mean_speed_ms == 12.0
    assert f.wind_dir_deg == 200.0
    assert f.speed_variation == 0.3
    assert f.direction_variation_deg == 15.0
    assert f.gust_period_s == 6.0
    assert f.seed == 20260727  # inherits the farm seed


def test_a_config_with_no_wind_block_is_still_air():
    f = from_cfg({"seed": 1})
    assert f.mean_speed_ms == 0.0
    assert f.speed_at(0.0, 0.0, 10.0) == 0.0


def test_wake_sources_come_from_the_turbine_list():
    cfg = {
        "turbines": [
            {"pos": [10.0, 20.0], "hub_height": 120.0, "blade_len": 70.0},
        ],
        "wind": {"mean_speed": 10.0, "direction_deg": 270.0},
    }
    f = from_cfg(cfg)
    assert len(f.wakes) == 1
    w = f.wakes[0]
    assert (w.x, w.y, w.hub_height_m) == (10.0, 20.0, 120.0)
    assert w.rotor_diameter_m == 140.0  # derived from blade_len, not duplicated
    assert w.thrust_coeff == DEFAULT_THRUST_COEFF


def test_resolved_turbines_override_the_raw_list():
    """The same trap `build_keepouts` had: if a scenario scatters turbines, the
    wake must follow the scatter rather than the config's explicit list."""
    cfg = {
        "turbines": [{"pos": [-999.0, -999.0], "hub_height": 120.0, "blade_len": 70.0}],
        "wind": {"mean_speed": 10.0},
    }
    resolved = [{"pos": [50.0, 60.0], "hub_height": 120.0, "blade_len": 70.0}]
    f = from_cfg(cfg, turbines=resolved)
    assert (f.wakes[0].x, f.wakes[0].y) == (50.0, 60.0)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kw",
    [
        {"mean_speed_ms": -1.0},
        {"speed_variation": 1.5},
        {"speed_variation": -0.1},
        {"gust_period_s": 0.0},
        {"wake_decay": 0.0},
    ],
)
def test_nonsense_parameters_are_refused_at_construction(kw):
    with pytest.raises(ValueError):
        WindField(**kw)
