"""`run.py`'s energy block — including that it never fails a mission that completed.

Isaac-free. Uses the REAL `PanelResult` type, per the lesson in
`test_run_sortie_wiring.py`: a stub that invents attributes tests a type that does
not exist.
"""

from __future__ import annotations

import pytest

from solar_twin.orchestrator.mission import PanelResult
from solar_twin.run import _energy_report
from solar_twin.schema.pv_module import PanelState

pytest.importorskip("pvlib", reason="the energy report needs pvlib")

LAT, LON = 24.0915, 69.4205


class _Anchor:
    lat0, lon0, elev0 = LAT, LON, 0.0


class _Layout:
    anchor = _Anchor()
    sites = [object()] * 30016


def _cfg(**over):
    cfg = {
        "sun": {"timestamp": "2026-06-21T04:00:00Z", "tracker_max_rotation_deg": 60.0},
        "energy": {"row_pitch_m": 5.5, "module_width_m": 2.278},
    }
    cfg.update(over)
    return cfg


def _result(pid: str, injected: str, detected: str) -> PanelResult:
    return PanelResult(
        panel_id=pid, injected_state=injected, screen_status="clean",
        escalated=False, detected_state=detected, note="",
    )


def test_a_perfect_mission_finds_all_of_the_loss():
    results = [
        _result("P0", "soiled", "soiled"),
        _result("P1", "healthy", "healthy"),
    ]
    rep = _energy_report(_cfg(), _Layout(), results, {})
    assert rep["inspected"]["loss_found_fraction"] == pytest.approx(1.0)


def test_a_missed_fault_shows_up_as_unfound_loss():
    """⭐ The point of the block: recall in watts, not in percentage points.

    Hotspot recall is 0.40 against soiling's 0.98, and the two carry different
    derates — so a pooled recall figure hides what the misses actually cost.
    """
    results = [
        _result("P0", "hotspot", "healthy"),  # missed
        _result("P1", "soiled", "soiled"),    # found
    ]
    rep = _energy_report(_cfg(), _Layout(), results, {})
    found = rep["inspected"]["loss_found_fraction"]
    assert 0.0 < found < 1.0
    assert rep["inspected"]["lost_w_detected"] < rep["inspected"]["lost_w_actual"]


def test_a_false_alarm_overstates_the_loss():
    """Calling a healthy panel faulty must be visible as believing in loss that
    is not there — the opposite sign to a miss, and it must not cancel out."""
    results = [_result("P0", "healthy", "hotspot")]
    rep = _energy_report(_cfg(), _Layout(), results, {})
    assert rep["inspected"]["lost_w_detected"] > rep["inspected"]["lost_w_actual"]
    # No actual loss to find, so the fraction is undefined rather than 0 or inf.
    assert rep["inspected"]["loss_found_fraction"] is None


def test_an_all_healthy_mission_reports_no_loss_and_no_fraction():
    rep = _energy_report(_cfg(), _Layout(), [_result("P0", "healthy", "healthy")], {})
    assert rep["inspected"]["lost_w_actual"] == pytest.approx(0.0)
    assert rep["inspected"]["loss_found_fraction"] is None


def test_whole_stage_is_reported_separately_from_the_inspected_subset():
    """A mission that visits 2 of 30,016 panels has not measured the plant."""
    faults = {f"S{i}": PanelState.SOILED for i in range(600)}
    rep = _energy_report(_cfg(), _Layout(), [_result("P0", "soiled", "soiled")], faults)
    assert rep["inspected"]["n_panels"] == 1
    assert rep["whole_stage"]["n_panels"] == 30016
    assert rep["whole_stage"]["lost_w"] > rep["inspected"]["lost_w_actual"]


def test_a_scenario_with_no_sun_instant_gets_no_energy_block():
    """Inventing a time of day would put a confident kWh on a scenario that
    never specified one."""
    assert _energy_report({"energy": {}}, _Layout(), [_result("P0", "soiled", "soiled")], {}) is None


def test_the_caveat_travels_with_the_record():
    rep = _energy_report(_cfg(), _Layout(), [_result("P0", "soiled", "soiled")], {})
    assert "SCADA" in rep["caveat"]


def test_a_broken_config_warns_but_does_not_fail_the_mission():
    """An optional analysis must never destroy a run that completed."""

    class _Bad:
        anchor = _Anchor()

        @property
        def sites(self):
            raise RuntimeError("layout exploded")

    assert _energy_report(_cfg(), _Bad(), [_result("P0", "soiled", "soiled")], {}) is None


def test_the_block_is_json_serialisable():
    import json

    rep = _energy_report(_cfg(), _Layout(), [_result("P0", "soiled", "healthy")], {})
    json.loads(json.dumps(rep))


def test_results_are_read_through_the_real_panel_result_api():
    r = _result("P0", "soiled", "healthy")
    assert isinstance(r, PanelResult)
    assert r.injected_state == "soiled" and r.detected_state == "healthy"
