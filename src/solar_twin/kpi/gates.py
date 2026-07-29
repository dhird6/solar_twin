"""KPI gate evaluation (`FR-17`) — pure-python, Isaac-free.

A scenario declares its own bounds (`IF-03`)::

    kpi_gates:
      false_fault_rate_max: 0.05
      detection_rate_min: 0.90

Each key is ``<metric>_max`` or ``<metric>_min`` naming a metric in the run
record's ``metrics`` block. `evaluate` returns a report the run record embeds and
`run.py` prints; a breach is a non-zero exit, so a regression stops a pipeline
instead of being noticed later in a JSON file.

**A gate whose metric was never measured does not pass.** It reports
``status="unmeasured"`` and fails the report. A gate that silently evaluates to
nothing is worse than no gate: it reads as a green tick for a bound nobody
checked. Either the metric gets measured or the gate gets deleted — both are
decisions, and neither is silence.

Repeat runs (`variance.py`) are gated on the WORST run in the set, never the
mean: "it passes on average" is not a safety claim about a fleet that flies each
sortie once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Suffix → comparison. `_max` is an upper bound (measured must be <=),
#: `_min` a lower bound (measured must be >=).
_SUFFIXES = {"_max": "max", "_min": "min"}


@dataclass(frozen=True)
class GateResult:
    """One declared bound, judged."""

    gate: str  # the kpi_gates key as written, e.g. "false_fault_rate_max"
    metric: str  # the metric it names, e.g. "false_fault_rate"
    kind: str  # "max" | "min"
    bound: float
    measured: float | None  # None when the metric is absent from the run
    status: str  # "pass" | "fail" | "unmeasured" | "malformed"

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def describe(self) -> str:
        if self.status == "unmeasured":
            return f"{self.gate}: UNMEASURED (no `{self.metric}` in this run) — not a pass"
        if self.status == "malformed":
            return f"{self.gate}: MALFORMED (expected <metric>_max/_min with a number)"
        op = "<=" if self.kind == "max" else ">="
        verb = "PASS" if self.passed else "FAIL"
        return f"{self.gate}: {verb} ({self.measured:.4g} {op} {self.bound:.4g})"


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)
    #: Which run of a repeat set each measurement came from ("worst-of-N"), for
    #: the record. None for a single run.
    basis: str | None = None

    @property
    def declared(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> bool:
        """True only if every declared gate passed. An empty gate block passes —
        a scenario is allowed not to declare bounds yet (SLICE-3 establishes
        baselines before it sets thresholds) — but `declared == 0` is reported so
        "no gates" can never be mistaken for "gates passed"."""
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared,
            "passed": self.passed,
            "basis": self.basis,
            "results": [
                {
                    "gate": r.gate,
                    "metric": r.metric,
                    "kind": r.kind,
                    "bound": r.bound,
                    "measured": r.measured,
                    "status": r.status,
                }
                for r in self.results
            ],
        }

    def describe(self) -> str:
        if not self.results:
            return "kpi gates: none declared (nothing gated — not a pass)"
        head = f"kpi gates: {'PASS' if self.passed else 'FAIL'} " f"({self.declared} declared"
        head += f", basis={self.basis})" if self.basis else ")"
        return "\n".join([head, *(f"  - {r.describe()}" for r in self.results)])


def split_gate(gate: str) -> tuple[str, str] | None:
    """``"false_fault_rate_max"`` → ``("false_fault_rate", "max")``; None if the
    key does not carry a recognised bound suffix."""
    for suffix, kind in _SUFFIXES.items():
        if gate.endswith(suffix) and len(gate) > len(suffix):
            return gate[: -len(suffix)], kind
    return None


def evaluate(
    gates: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    basis: str | None = None,
) -> GateReport:
    """Judge `gates` against `metrics` (the run record's ``metrics`` block)."""
    report = GateReport(basis=basis)
    for gate, bound in sorted((gates or {}).items()):
        parsed = split_gate(gate)
        if parsed is None or not isinstance(bound, (int, float)) or isinstance(bound, bool):
            report.results.append(
                GateResult(gate, gate, "max", float("nan"), None, "malformed")
            )
            continue
        metric, kind = parsed
        raw = (metrics or {}).get(metric)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            report.results.append(
                GateResult(gate, metric, kind, float(bound), None, "unmeasured")
            )
            continue
        measured = float(raw)
        ok = measured <= float(bound) if kind == "max" else measured >= float(bound)
        report.results.append(
            GateResult(
                gate, metric, kind, float(bound), measured, "pass" if ok else "fail"
            )
        )
    return report


def worst_metrics(runs: list[dict[str, Any]], gates: dict[str, Any] | None) -> dict[str, float]:
    """Collapse N repeat runs into the worst measurement per gated metric.

    Direction comes from the gate: a `_max` gate takes the highest observed
    value, a `_min` gate the lowest. Metrics nobody gates are left out — this
    exists to feed `evaluate`, not to summarise a run (that is `variance.py`).
    """
    worst: dict[str, float] = {}
    for gate in (gates or {}):
        parsed = split_gate(gate)
        if parsed is None:
            continue
        metric, kind = parsed
        values = [
            float(r["metrics"][metric])
            for r in runs
            if isinstance((r.get("metrics") or {}).get(metric), (int, float))
            and not isinstance(r["metrics"][metric], bool)
        ]
        if values:
            worst[metric] = max(values) if kind == "max" else min(values)
    return worst
