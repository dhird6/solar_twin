"""Energy model — the "does the plant make power" pillar (Isaac-free).

⚠ These tests check the model is SELF-CONSISTENT and physically sane. They cannot
check it is right about Khavda: we hold no SCADA feed, which is assumption (3) in
`energy/model.py`. Nothing here should ever be cited as validation.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from solar_twin.energy.model import (
    DERATE_BY_STATE,
    ModuleSpec,
    PlantSpec,
    UNVALIDATED_CAVEAT,
    _surface_orientation,
    energy_kwh,
    fault_cost,
    instant_power,
    money,
    plant_from_layout_cfg,
)
from solar_twin.schema.pv_module import PanelState
from solar_twin.world.solar import solar_position

pvlib = pytest.importorskip("pvlib", reason="energy model needs pvlib")

#: The real site anchor, so the numbers under test are the ones the twin renders.
LAT, LON = 24.0915, 69.4205


def _plant(**kw) -> PlantSpec:
    base = dict(latitude=LAT, longitude=LON, n_modules=30016, max_rotation_deg=60.0)
    base.update(kw)
    return PlantSpec(**base)


def _at(hour: int, minute: int = 0) -> _dt.datetime:
    return _dt.datetime(2026, 6, 21, hour, minute, tzinfo=_dt.timezone.utc)


def _power(when, plant=None, **kw):
    plant = plant or _plant()
    elev, azim = solar_position(LAT, LON, when)
    return instant_power(
        plant, when, sun_elevation_deg=elev, sun_azimuth_deg=azim, **kw
    )


# --------------------------------------------------------------------------- #
# The sun the energy model uses must be the sun the stage was rendered with.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("hour", [2, 4, 6, 8, 10, 12])
def test_our_solar_position_agrees_with_pvlib(hour):
    """⭐ The load-bearing agreement.

    `world/solar.py` drives the rendered tracker angles and the stage light; pvlib
    drives the irradiance. If they disagreed, the energy model would be describing a
    different sun than the picture — the exact trap `solar.py`'s header warns about.
    Measured 2026-08-03: within 0.13 deg elevation / 0.17 deg azimuth, inside
    `solar.py`'s documented 0.1-0.5 deg claim. The tolerance here is that claim, so
    a regression in EITHER implementation fails rather than quietly biasing kWh.
    """
    import pandas as pd

    when = _at(hour)
    e_ours, a_ours = solar_position(LAT, LON, when)
    sp = pvlib.solarposition.get_solarposition(pd.DatetimeIndex([when]), LAT, LON)
    assert e_ours == pytest.approx(float(sp["apparent_elevation"].iloc[0]), abs=0.5)
    assert a_ours == pytest.approx(float(sp["azimuth"].iloc[0]), abs=0.5)


# --------------------------------------------------------------------------- #
# Tracker orientation — a sign error here costs nothing at noon and everything
# at 07:00, so it is checked at both.
# --------------------------------------------------------------------------- #


def test_positive_rotation_faces_east_for_a_north_south_axis():
    tilt, az = _surface_orientation(45.0, axis_azimuth_deg=0.0)
    assert tilt == 45.0
    assert az == pytest.approx(90.0)


def test_negative_rotation_faces_west():
    tilt, az = _surface_orientation(-45.0, axis_azimuth_deg=0.0)
    assert tilt == 45.0
    assert az == pytest.approx(270.0)


def test_the_array_faces_east_in_the_morning_and_west_in_the_afternoon():
    morning = _power(_at(3))
    afternoon = _power(_at(11))
    assert morning.tracker_rotation_deg > 0, "morning tracker should face east"
    assert afternoon.tracker_rotation_deg < 0, "afternoon tracker should face west"


# --------------------------------------------------------------------------- #
# Physical sanity.
# --------------------------------------------------------------------------- #


def test_night_produces_nothing_rather_than_raising():
    """Night is a legitimate instant — a caller must be able to integrate across it."""
    p = _power(_at(20))
    assert p.is_dark
    assert p.ac_power_w == 0.0
    assert p.poa_global == 0.0


def test_power_never_exceeds_the_inverter_rating():
    plant = _plant()
    for hour in range(0, 24):
        p = _power(_at(hour), plant)
        assert p.ac_power_w <= plant.ac_nameplate_w() + 1.0


def test_power_is_never_negative():
    """pvwatts returns a negative tare draw below turn-on; that must not leak out."""
    for hour in (1, 2, 13, 14):
        assert _power(_at(hour)).ac_power_w >= 0.0


def test_poa_peaks_nearer_noon_than_dawn():
    assert _power(_at(7)).poa_global > _power(_at(2)).poa_global


def test_cell_runs_hotter_than_the_air_under_load():
    p = _power(_at(7), air_temp_c=38.0)
    assert p.cell_temp_c > 38.0, "an irradiated module is hotter than ambient"


def test_wind_cools_the_cell():
    calm = _power(_at(7), air_temp_c=38.0, wind_speed_ms=0.5)
    windy = _power(_at(7), air_temp_c=38.0, wind_speed_ms=8.0)
    assert windy.cell_temp_c < calm.cell_temp_c


def test_hotter_cells_make_less_power():
    """gamma_pdc is negative, so this is the sign check on the temperature term."""
    cool = _power(_at(7), air_temp_c=10.0)
    hot = _power(_at(7), air_temp_c=45.0)
    assert hot.ac_power_w < cool.ac_power_w


def test_specific_yield_is_in_a_plausible_range_for_a_clear_desert_day():
    """A ceiling, not a forecast — but a ceiling in the right order of magnitude.

    Clear-sky summer solstice at 24N should beat a typical Indian annual average
    (~4.5-5.5 kWh/kWp/day) and fall well short of the ~11 kWh/kWp/day that a
    24-hour-sun thought experiment would give. Catches a units error or a
    nameplate mix-up, which is what this test is for.
    """
    plant = _plant(row_pitch_m=5.5, module_width_m=2.278)
    points = [
        _power(_at(0) + _dt.timedelta(minutes=30 * i), plant, air_temp_c=38.0)
        for i in range(48)
    ]
    kwh = energy_kwh(points, 30 * 60)
    specific = kwh / (plant.dc_nameplate_w() / 1000.0)
    assert 5.0 < specific < 9.0, f"specific yield {specific:.2f} kWh/kWp/day is implausible"


# --------------------------------------------------------------------------- #
# Shading — the thing a geometric twin can do that a spreadsheet cannot.
# --------------------------------------------------------------------------- #


def test_self_shading_appears_at_low_sun_and_vanishes_at_noon():
    plant = _plant(row_pitch_m=5.5, module_width_m=2.278)
    dawn = _power(_at(2), plant)
    noon = _power(_at(7), plant)
    assert dawn.shaded_fraction > 0.0, "low sun must self-shade at this row pitch"
    assert noon.shaded_fraction == 0.0, "a high sun must not self-shade"


def test_tighter_row_pitch_shades_more():
    when = _at(2)
    wide = _power(when, _plant(row_pitch_m=8.0, module_width_m=2.278))
    tight = _power(when, _plant(row_pitch_m=3.0, module_width_m=2.278))
    assert tight.shaded_fraction > wide.shaded_fraction


def test_shading_costs_power():
    when = _at(2)
    unshaded = _power(when, _plant())  # no pitch given -> shading disabled
    shaded = _power(when, _plant(row_pitch_m=3.0, module_width_m=2.278))
    assert shaded.ac_power_w < unshaded.ac_power_w


def test_shading_is_applied_to_beam_only_not_the_whole_plane():
    """A fully shaded module still sees diffuse sky, so power must not go to zero."""
    p = _power(_at(2), _plant(row_pitch_m=0.5, module_width_m=2.278))
    assert p.shaded_fraction > 0.5
    assert p.ac_power_w > 0.0, "diffuse irradiance survives a beam shadow"


# --------------------------------------------------------------------------- #
# Faults -> money. The point of the pillar.
# --------------------------------------------------------------------------- #


def test_healthy_plant_loses_nothing():
    loss = _cost({f"R00-C{i:03d}": PanelState.HEALTHY for i in range(10)})
    assert loss.n_faulted == 0
    assert loss.lost_w == pytest.approx(0.0, abs=1e-6)


def _cost(states, plant=None, hour=7):
    plant = plant or _plant()
    when = _at(hour)
    elev, azim = solar_position(LAT, LON, when)
    return fault_cost(
        plant, when, sun_elevation_deg=elev, sun_azimuth_deg=azim, states=states
    )


def test_a_dropped_string_costs_more_than_a_crack():
    dropout = _cost({"R00-C000": PanelState.STRING_DROPOUT})
    crack = _cost({"R00-C000": PanelState.CRACK})
    assert dropout.lost_w > crack.lost_w


def test_more_faulted_modules_cost_more():
    one = _cost({"R00-C000": PanelState.SOILED})
    many = _cost({f"R00-C{i:03d}": PanelState.SOILED for i in range(100)})
    assert many.lost_w > one.lost_w
    assert many.n_faulted == 100


def test_loss_scales_with_the_share_of_the_array_affected():
    """600 soiled of 30,016 at 15% each is ~0.3% of the plant. Pins the arithmetic."""
    loss = _cost({f"R00-C{i:04d}": PanelState.SOILED for i in range(600)})
    expected = 600 * DERATE_BY_STATE[PanelState.SOILED] / 30016
    assert loss.lost_fraction == pytest.approx(expected, rel=0.05)


def test_shading_state_does_not_double_count():
    """Shading is modelled geometrically, so its DERATE entry must stay zero."""
    assert DERATE_BY_STATE[PanelState.SHADING] == 0.0
    assert _cost({"R00-C000": PanelState.SHADING}).lost_w == pytest.approx(0.0, abs=1e-6)


def test_unknown_state_costs_nothing():
    """Never assume a loss we have not established."""
    assert DERATE_BY_STATE[PanelState.UNKNOWN] == 0.0


def test_every_panel_state_has_a_derate():
    """A new fault type must not silently cost zero because nobody added it here."""
    missing = [s for s in PanelState if s not in DERATE_BY_STATE]
    assert not missing, f"PanelState(s) with no energy derate: {missing}"


def test_night_faults_cost_nothing_and_do_not_divide_by_zero():
    loss = _cost({"R00-C000": PanelState.STRING_DROPOUT}, hour=20)
    assert loss.lost_w == pytest.approx(0.0)
    assert loss.lost_fraction == 0.0


# --------------------------------------------------------------------------- #
# The caveat must survive, and the units must be honest.
# --------------------------------------------------------------------------- #


def test_results_carry_the_unvalidated_caveat():
    """A modelled number that loses its caveat gets quoted as a measurement."""
    assert _power(_at(7)).caveat == UNVALIDATED_CAVEAT
    assert _cost({"R00-C000": PanelState.SOILED}).caveat == UNVALIDATED_CAVEAT
    assert "no SCADA" in UNVALIDATED_CAVEAT.lower() or "scada" in UNVALIDATED_CAVEAT.lower()


def test_energy_integration_matches_a_hand_computation():
    from solar_twin.energy.model import PowerPoint

    pt = PowerPoint(
        when_utc=_at(7), sun_elevation_deg=45.0, sun_azimuth_deg=90.0,
        tracker_rotation_deg=0.0, ghi=0.0, dni=0.0, dhi=0.0, poa_global=0.0,
        cell_temp_c=25.0, shaded_fraction=0.0, dc_power_w=0.0, ac_power_w=1000.0,
    )
    # 1 kW held for two 1-hour steps = 2 kWh.
    assert energy_kwh([pt, pt], 3600) == pytest.approx(2.0)


def test_energy_rejects_a_nonpositive_step():
    with pytest.raises(ValueError):
        energy_kwh([], 0)


def test_money_has_no_default_tariff():
    """A hard-coded tariff is how a number acquires a currency it was never quoted in."""
    import inspect

    sig = inspect.signature(money)
    assert sig.parameters["tariff_per_kwh"].default is inspect.Parameter.empty
    assert money(100.0, 2.5) == pytest.approx(250.0)


def test_ac_nameplate_defaults_to_a_dc_ac_ratio():
    plant = _plant(n_modules=1000, module=ModuleSpec(pdc0_w=500.0))
    assert plant.dc_nameplate_w() == 500_000.0
    assert plant.ac_nameplate_w() == pytest.approx(500_000.0 / 1.2)


def test_explicit_ac_nameplate_wins():
    assert _plant(pac0_w=9_000.0).ac_nameplate_w() == 9_000.0


# --------------------------------------------------------------------------- #
# Config plumbing — the model must read the stage the twin actually built.
# --------------------------------------------------------------------------- #


class _StubAnchor:
    lat0, lon0, elev0 = LAT, LON, 12.0


class _StubLayout:
    anchor = _StubAnchor()
    sites = [object()] * 273


def test_plant_from_layout_cfg_reads_the_built_stage():
    cfg = {
        "sun": {"tracker_max_rotation_deg": 45.0},
        "energy": {"row_pitch_m": 5.5, "module_width_m": 2.278,
                   "module": {"pdc0_w": 600.0}},
    }
    plant = plant_from_layout_cfg(cfg, _StubLayout())
    assert plant.latitude == LAT
    assert plant.altitude_m == 12.0
    assert plant.n_modules == 273
    assert plant.max_rotation_deg == 45.0, "must honour the stage's tracker limit"
    assert plant.row_pitch_m == 5.5
    assert plant.module.pdc0_w == 600.0


def test_plant_from_layout_cfg_defaults_are_safe():
    """An absent energy block must disable shading, not crash or invent a pitch."""
    plant = plant_from_layout_cfg({}, _StubLayout())
    assert plant.row_pitch_m == 0.0
    assert plant.max_rotation_deg == 60.0
