"""KPI gate evaluation (FR-17) — pure-python, no Isaac.

Gates were declared in every scenario config and never checked; these tests pin
the behaviour that makes them mean something, including the two cases where
silence would be dangerous: an unmeasured gate and a worst-of-N repeat set.
"""

from __future__ import annotations

from solar_twin.kpi import gates as G


def test_max_gate_passes_at_and_below_bound():
    r = G.evaluate({"false_fault_rate_max": 0.05}, {"false_fault_rate": 0.05})
    assert r.passed
    assert r.results[0].status == "pass"
    assert r.results[0].measured == 0.05


def test_max_gate_fails_above_bound():
    r = G.evaluate({"false_fault_rate_max": 0.05}, {"false_fault_rate": 0.051})
    assert not r.passed
    assert [f.gate for f in r.failures] == ["false_fault_rate_max"]


def test_min_gate_direction():
    assert G.evaluate({"detection_rate_min": 0.9}, {"detection_rate": 0.95}).passed
    assert not G.evaluate({"detection_rate_min": 0.9}, {"detection_rate": 0.89}).passed


def test_unmeasured_gate_does_not_pass():
    """A gate naming a metric the run never produced is not a green tick."""
    r = G.evaluate({"station_keep_error_max": 0.2}, {"false_fault_rate": 0.0})
    assert not r.passed
    assert r.results[0].status == "unmeasured"
    assert r.results[0].measured is None
    assert "UNMEASURED" in r.results[0].describe()


def test_malformed_gate_does_not_pass():
    # No _max/_min suffix, and a non-numeric bound: both are author errors that
    # must surface loudly rather than evaluate to nothing.
    assert not G.evaluate({"false_fault_rate": 0.05}, {"false_fault_rate": 0.0}).passed
    assert not G.evaluate({"detection_rate_min": "high"}, {"detection_rate": 1.0}).passed


def test_no_gates_declared_is_reported_not_celebrated():
    r = G.evaluate({}, {"false_fault_rate": 0.0})
    assert r.declared == 0
    assert "none declared" in r.describe()


def test_worst_metrics_picks_by_gate_direction():
    runs = [
        {"metrics": {"false_fault_rate": 0.0, "detection_rate": 0.95}},
        {"metrics": {"false_fault_rate": 0.02, "detection_rate": 0.87}},
        {"metrics": {"false_fault_rate": 0.01, "detection_rate": 0.91}},
    ]
    worst = G.worst_metrics(
        runs, {"false_fault_rate_max": 0.05, "detection_rate_min": 0.9}
    )
    assert worst == {"false_fault_rate": 0.02, "detection_rate": 0.87}


def test_repeats_are_gated_on_the_worst_run_not_the_mean():
    """Two of three runs pass; the set does not. A fleet flies each sortie once."""
    runs = [
        {"metrics": {"false_fault_rate": 0.0}},
        {"metrics": {"false_fault_rate": 0.0}},
        {"metrics": {"false_fault_rate": 0.30}},
    ]
    gates = {"false_fault_rate_max": 0.05}
    report = G.evaluate(gates, G.worst_metrics(runs, gates), basis="worst-of-3")
    assert not report.passed
    assert report.to_dict()["basis"] == "worst-of-3"


def test_split_gate():
    assert G.split_gate("false_fault_rate_max") == ("false_fault_rate", "max")
    assert G.split_gate("detection_rate_min") == ("detection_rate", "min")
    assert G.split_gate("coverage") is None
    assert G.split_gate("_max") is None  # suffix alone names no metric


def test_report_serializes_for_the_run_record():
    r = G.evaluate({"false_fault_rate_max": 0.05}, {"false_fault_rate": 0.0})
    d = r.to_dict()
    assert d["passed"] is True
    assert d["declared"] == 1
    assert d["results"][0]["metric"] == "false_fault_rate"
