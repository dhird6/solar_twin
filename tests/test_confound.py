"""Neighbour-adjacency confound — is a "false fault" the panel next door?

Measured 2026-07-29 on `SC-01` (`runs/20260729T130956`): of 33 healthy panels
inspected, the 7 with a faulted `C+1` neighbour produced **all 3** false alarms
(43%), and the 26 with a clean neighbourhood produced **none** (0%). Inspecting the
captured confirm frames showed why — the module is foreshortened at the confirm
standoff and the neighbouring module is in shot, so the model reports soiling that
is genuinely visible but belongs to the panel next door.

That makes `KPI-03` partly a measurement-validity question rather than a perception
one, and it is why two prompt interventions failed: the model was right about the
image.
"""

from __future__ import annotations

from solar_twin.kpi.confound import (
    ConfoundReport,
    analyse,
    neighbours,
    parse_panel_id,
)


# --------------------------------------------------------------------------- #
# Panel-id arithmetic (the `R12-C047` contract)
# --------------------------------------------------------------------------- #


def test_panel_ids_parse():
    assert parse_panel_id("R254-C014") == ("R254", 14)
    assert parse_panel_id("R12-C047") == ("R12", 47)


def test_a_non_panel_id_is_refused_rather_than_guessed():
    for bad in ("", "R254", "C014", "R254_C014", "nonsense"):
        assert parse_panel_id(bad) is None
        assert neighbours(bad) == []


def test_neighbours_keep_the_id_zero_padding():
    """`R254-C15` would match nothing in `injected_faults`, so the confound would
    silently report zero."""
    assert neighbours("R254-C014") == ["R254-C013", "R254-C015"]
    assert neighbours("R254-C000") == ["R254-C001"]  # no negative column


def test_neighbours_stay_in_the_same_row():
    """Rows are separate tracker tables metres apart across an aisle — that module
    is not in frame the way the one beside it is."""
    assert all(n.startswith("R254-") for n in neighbours("R254-C014"))


# --------------------------------------------------------------------------- #
# The confound itself
# --------------------------------------------------------------------------- #


def _record(panels, faults):
    return {
        "injected_faults": faults,
        "panels": [
            {"panel_id": pid, "injected_state": inj, "detected_state": det}
            for pid, inj, det in panels
        ],
    }


def test_a_false_alarm_beside_a_faulted_panel_is_flagged():
    rec = _record(
        [("R254-C014", "healthy", "soiled")],
        {"R254-C015": "soiled"},
    )
    r = analyse(rec)
    assert r.healthy_inspected == 1
    assert r.with_faulted_neighbour == 1
    assert r.false_alarms_with_neighbour == 1
    assert r.attributable_share == 1.0
    assert "next door" in r.describe()


def test_a_false_alarm_with_a_clean_neighbourhood_is_not_blamed_on_the_neighbour():
    """The confound must not explain away every false alarm — that would make it
    useless as a diagnostic."""
    rec = _record([("R254-C014", "healthy", "hotspot")], {"R299-C001": "soiled"})
    r = analyse(rec)
    assert r.without_faulted_neighbour == 1
    assert r.false_alarms_without_neighbour == 1
    assert r.attributable_share == 0.0


def test_the_measured_sc01_shape_reproduces():
    """The real numbers, as a regression: 7 panels beside a fault produce all 3
    false alarms, 26 clean-neighbourhood panels produce none."""
    panels = []
    faults = {}
    # 7 healthy panels each beside a soiled one; 3 of them misread.
    for i in range(7):
        pid = f"R254-C{i * 2:03d}"
        panels.append((pid, "healthy", "soiled" if i < 3 else "healthy"))
        faults[f"R254-C{i * 2 + 1:03d}"] = "soiled"
    # 26 healthy panels with nothing beside them, none misread.
    for i in range(26):
        panels.append((f"R258-C{i * 4:03d}", "healthy", "healthy"))
    r = analyse(_record(panels, faults))

    assert r.healthy_inspected == 33
    assert r.with_faulted_neighbour == 7
    assert r.without_faulted_neighbour == 26
    assert r.false_alarms == 3
    assert r.rate_with_neighbour == 3 / 7
    assert r.rate_without_neighbour == 0.0
    assert r.attributable_share == 1.0


def test_a_healthy_neighbour_is_not_a_faulted_one():
    """`injected_faults` can list a panel as `healthy`; treating any entry as a
    fault would inflate the confound to meaninglessness."""
    rec = _record([("R254-C014", "healthy", "soiled")], {"R254-C015": "healthy"})
    r = analyse(rec)
    assert r.with_faulted_neighbour == 0
    assert r.false_alarms_without_neighbour == 1


def test_faulted_panels_are_not_counted_as_false_alarms():
    """KPI-03's denominator is healthy panels only — a misdiagnosed *faulted* panel
    is a KPI-01 problem and must not appear here."""
    rec = _record([("R254-C014", "soiled", "hotspot")], {"R254-C014": "soiled"})
    r = analyse(rec)
    assert r.healthy_inspected == 0
    assert r.false_alarms == 0


def test_a_clean_run_says_so_without_dividing_by_zero():
    rec = _record([("R254-C014", "healthy", "healthy")], {})
    r = analyse(rec)
    assert r.false_alarms == 0
    assert r.attributable_share == 0.0
    assert "none was misread" in r.describe()


def test_an_empty_record_does_not_crash():
    r = analyse({})
    assert r.healthy_inspected == 0
    assert "no denominator" in r.describe()


def test_the_report_serialises_for_the_run_record():
    rec = _record([("R254-C014", "healthy", "soiled")], {"R254-C015": "soiled"})
    d = analyse(rec).to_dict()
    assert d["attributable_share"] == 1.0
    assert d["suspects"][0]["faulted_neighbours"] == {"R254-C015": "soiled"}
    assert isinstance(d["verdict"], str) and d["verdict"]


def test_offsets_are_configurable_for_a_different_camera_geometry():
    """Which neighbour lands in shot depends on view yaw, so the offsets are a
    parameter rather than a constant."""
    rec = _record([("R254-C014", "healthy", "soiled")], {"R254-C016": "soiled"})
    assert analyse(rec).with_faulted_neighbour == 0
    assert analyse(rec, offsets=(2,)).with_faulted_neighbour == 1


def test_report_defaults_are_a_valid_empty_report():
    r = ConfoundReport()
    assert r.false_alarms == 0
    assert r.rate_with_neighbour == 0.0
    assert r.rate_without_neighbour == 0.0
