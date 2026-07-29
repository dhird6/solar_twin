"""KPI-03's two halves: a false alarm and a lost answer are not the same failure.

Owner decision, 2026-07-29: `false_fault_rate` keeps its definition — it is a
locked contract (`PROJECT_BIBLE.md` §6.5, `FR-03`) and redefining it would make
every number already recorded non-comparable, including the two verified-stimulus
0.00 points. Instead the split is *reported*: `abstention_rate` and
`false_alarm_rate` decompose it exactly.

The bug that forced the decision: one VLM response missing its closing brace was
mapped to `unknown` and scored as a false fault, moving KPI-03 from 0.00 to 0.053
while the model had in fact said `healthy` with confidence 1.0. The parse bug is
fixed; these tests are about the metric no longer hiding which failure occurred.
"""

from __future__ import annotations

from solar_twin.orchestrator.mission import MissionResult, PanelResult


def _panel(pid: str, injected: str, detected: str) -> PanelResult:
    return PanelResult(
        panel_id=pid,
        injected_state=injected,
        screen_status="suspect" if detected != "healthy" else "clean",
        escalated=detected != "healthy",
        detected_state=detected,
        note="",
    )


def _result(*panels: PanelResult) -> MissionResult:
    return MissionResult(results=list(panels))


# ----------------------------------------------- the locked metric is unchanged
def test_false_fault_rate_still_counts_unknown_as_a_false_fault():
    """The contract is deliberately NOT relaxed — an unanswered healthy panel
    still scores in KPI-03, exactly as it did before. Anything else would silently
    improve every historic number."""
    r = _result(
        _panel("R1-C1", "healthy", "healthy"),
        _panel("R1-C2", "healthy", "unknown"),
    )
    assert r.false_fault_rate == 0.5


# ----------------------------------------------------------- the new decomposition
def test_abstention_and_false_alarm_split_the_two_failure_modes():
    r = _result(
        _panel("R1-C1", "healthy", "healthy"),  # correct
        _panel("R1-C2", "healthy", "hotspot"),  # genuine false alarm
        _panel("R1-C3", "healthy", "unknown"),  # lost answer
        _panel("R1-C4", "healthy", "healthy"),  # correct
    )
    # Both bad panels score in the locked metric...
    assert r.false_fault_rate == 0.5
    # ...but they are now distinguishable.
    assert r.false_alarm_rate == 0.25
    assert r.abstentions == 1
    assert r.abstention_rate == 0.25


def test_the_decomposition_is_exact_not_approximate():
    """`false_fault_rate == false_alarm_rate + healthy-abstention share`. If this
    drifts, the two new metrics stop explaining the locked one and become
    decoration."""
    cases = (
        ("healthy", "healthy"),
        ("healthy", "hotspot"),
        ("healthy", "soiled"),
        ("healthy", "unknown"),
        ("healthy", "unknown"),
        ("hotspot", "hotspot"),
        ("soiled", "unknown"),
    )
    r = _result(*(_panel(f"R1-C{i}", a, b) for i, (a, b) in enumerate(cases)))

    healthy = [p for p in r.results if p.injected_state == "healthy"]
    healthy_abstentions = sum(1 for p in healthy if p.detected_state == "unknown")
    assert r.false_fault_rate == (
        r.false_alarm_rate + healthy_abstentions / len(healthy)
    )


def test_abstention_rate_spans_all_panels_not_only_healthy_ones():
    """Losing the answer for a *faulted* panel is equally a plumbing failure; it
    just surfaces as a missed detection rather than a false fault. A rate scoped
    to healthy panels would report 0.0 for a run that answered nothing."""
    r = _result(
        _panel("R1-C1", "hotspot", "unknown"),
        _panel("R1-C2", "soiled", "unknown"),
    )
    assert r.abstention_rate == 1.0
    assert r.abstentions == 2
    # No healthy panels to judge, so KPI-03 is vacuously 0.00 — precisely the
    # case where quoting it alone would be misleading.
    assert r.false_fault_rate == 0.0
    assert r.false_alarm_rate == 0.0


def test_a_clean_run_scores_zero_on_both_halves():
    r = _result(
        _panel("R1-C1", "healthy", "healthy"),
        _panel("R1-C2", "hotspot", "hotspot"),
    )
    assert r.false_fault_rate == 0.0
    assert r.false_alarm_rate == 0.0
    assert r.abstention_rate == 0.0
    assert r.abstentions == 0


def test_empty_run_does_not_divide_by_zero():
    r = _result()
    assert r.abstention_rate == 0.0
    assert r.false_alarm_rate == 0.0
    assert r.abstentions == 0


# ------------------------------------------------------- the metrics are gateable
def test_the_new_metrics_are_gateable_like_any_other():
    """`gates.py` resolves `<metric>_max` against the run record's metrics block,
    so these need no special-casing to be enforceable — but that only holds if
    they are actually emitted under these names."""
    from solar_twin.kpi.gates import evaluate

    record = {
        "metrics": {
            "false_fault_rate": 0.10,
            "false_alarm_rate": 0.00,
            "abstention_rate": 0.10,
        }
    }
    report = evaluate(
        {"abstention_rate_max": 0.05, "false_alarm_rate_max": 0.05}, record["metrics"]
    )
    by_gate = {r.gate: r for r in report.results}
    assert by_gate["false_alarm_rate_max"].status == "pass"
    assert by_gate["abstention_rate_max"].status == "fail"
    assert not report.passed


def test_a_zero_bound_is_a_real_bound_not_a_missing_one():
    """The KPI-03 scenarios declare `abstention_rate_max: 0.0` — zero tolerance,
    because a lost verdict is a plumbing bug rather than a budget to spend. `0.0`
    is falsy, so a truthiness check anywhere in gate parsing would silently drop
    the strictest gate in the suite.
    """
    from solar_twin.kpi.gates import evaluate, worst_metrics

    assert evaluate({"abstention_rate_max": 0.0}, {"abstention_rate": 0.0}).passed
    report = evaluate({"abstention_rate_max": 0.0}, {"abstention_rate": 0.02})
    assert not report.passed
    assert report.results[0].status == "fail"
    assert report.results[0].bound == 0.0  # not dropped, not coerced to NaN

    # ...and it survives the worst-of-N collapse used for repeat sets.
    worst = worst_metrics(
        [{"metrics": {"abstention_rate": 0.0}}, {"metrics": {"abstention_rate": 0.05}}],
        {"abstention_rate_max": 0.0},
    )
    assert worst["abstention_rate"] == 0.05


def test_every_kpi03_scenario_gates_both_halves():
    """A metric that is reported but never gated drifts. `FR-17`'s whole point is
    that a declared bound gets checked, so every scenario carrying a
    `false_fault_rate_max` must also bound its two halves.
    """
    import pathlib

    import yaml

    scenarios = sorted(
        (pathlib.Path(__file__).resolve().parents[1] / "configs" / "scenarios").glob(
            "*.yaml"
        )
    )
    assert scenarios, "no scenario configs found"

    checked = 0
    for path in scenarios:
        gates = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
            "kpi_gates"
        ) or {}
        if "false_fault_rate_max" not in gates:
            continue  # not a KPI-03 scenario
        checked += 1
        assert "false_alarm_rate_max" in gates, f"{path.name} gates KPI-03 but not 03a"
        assert "abstention_rate_max" in gates, f"{path.name} gates KPI-03 but not 03b"
        assert gates["abstention_rate_max"] == 0.0, (
            f"{path.name} tolerates abstentions; a lost verdict is a bug, and a "
            f"KPI-03 computed over dropped verdicts is not a measurement"
        )
    assert checked >= 5, f"expected the KPI-03 scenarios to be found, saw {checked}"


def test_variance_summarises_the_new_metrics_by_default():
    """A KPI must be quotable as a spread (`RISK-23`), so the new metrics have to
    be in `DEFAULT_METRICS` or `--repeat N` would silently not track them."""
    from solar_twin.kpi.variance import DEFAULT_METRICS, summarize

    assert "abstention_rate" in DEFAULT_METRICS
    assert "false_alarm_rate" in DEFAULT_METRICS

    runs = [
        {"metrics": {"abstention_rate": 0.0, "false_alarm_rate": 0.0}},
        {"metrics": {"abstention_rate": 0.25, "false_alarm_rate": 0.0}},
    ]
    report = summarize(runs)
    assert report.metrics["abstention_rate"].range == 0.25
    assert report.metrics["false_alarm_rate"].stable
