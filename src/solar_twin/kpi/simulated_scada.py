"""**SIMULATED** string-level SCADA and cell fault-probability ranking.

Pure-python, Isaac-free, no GPU. Implements path (a) of the research doc's
"Grid-Level Fault Localization & Staged Dispatch": rank dispatch cells by a
performance-ratio anomaly so expensive per-panel perception is spent
suspicion-first instead of sweeping 679,616 panels in layout order (which at the
measured ~7-12 s/panel is 55-94 days of wall-clock for one pass).

⚠⚠⚠ **EVERY NUMBER THIS MODULE PRODUCES IS SIMULATED. IT CANNOT VALIDATE REAL-PLANT
PERFORMANCE.** Stated here, in the class names, in the field names, and in the run
record, because the failure mode is subtle and flattering:

**We have no SCADA feed.** The vendor DWG is DC *hardware geometry* only — no
telemetry, no string map, no historical generation. So the "measured" string output
here is **derived from the twin's own `pv:state` and `pv:iv_yield`**, i.e. from the
very injected faults the mission is trying to discover. The prior is therefore a
deterministic function of the ground truth, and a ranker fed by it will score
close to perfectly **by construction**. That makes this a test that the dispatch
machinery works as specified, and **not** evidence that suspicion-first dispatch
beats a sweep on real hardware. A real feed is a commercial/access question, not
an engineering one.

Concretely: `SimulatedCellScore.scada_source` is always `"simulated"`, the public
entry point is `rank_cells_simulated`, and `run.py` stamps
`dispatch.scada_source` into the run record. If a real feed ever arrives, it
implements the same shape and sets `scada_source="measured"` — and only then may
`KPI-09` be quoted as anything about a plant.

**What the coarse signal is, and what it deliberately cannot do.** Score each cell
by actual-vs-weather-normalised-expected output; the normalised residual is the
anomaly. This is the standard O&M method and it is *coarse by nature*: it localises
to a string and cannot distinguish soiling from a hotspot from a crack from a diode
fault from a shadow. That is exactly the division of labour — the twin already has
something that can tell those apart (Cosmos Reason) and it is expensive.

**The posterior matters as much as the prior.** `apply_verdicts` lets confirmed
panel results push a cell's rank DOWN, so a cell whose panels were just cleared
stops ranking highly even while its PR stays depressed. That discrepancy is itself
a finding: soiling and inter-row shading depress PR without any panel being
faulty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from solar_twin.schema.pv_module import PanelRecord, PanelState

#: Marks every score this module produces. Never make this configurable — the
#: point is that a simulated number cannot be relabelled as a measured one.
SCADA_SOURCE = "simulated"

#: The one wording of the circularity warning, so `summary()` and the dispatch
#: layer's run-record block cannot drift into two differently-worded (or one
#: silently missing) caveat. A run record that carries the ranking must carry
#: this next to it.
SIMULATED_CAVEAT = (
    "SIMULATED SCADA derived from the twin's own pv:state/pv:iv_yield — the "
    "prior is a function of the ground truth being sought, so this is "
    "circular by construction and says NOTHING about real-plant performance."
)

#: Per-state DC output multiplier, applied on top of `pv:iv_yield`. These are
#: plausible O&M magnitudes, NOT measured from hardware — a soiled module loses a
#: few percent, a dropped string loses nearly everything.
#:
#: ⚠ They are the *simulation's* assumption about how a fault shows up
#: electrically. Getting them wrong changes the ranking, and nothing here can
#: detect that they are wrong, because there is no ground truth to check against.
STATE_OUTPUT_FACTOR: dict[PanelState, float] = {
    PanelState.HEALTHY: 1.00,
    PanelState.SOILED: 0.93,
    PanelState.SHADING: 0.80,
    PanelState.HOTSPOT: 0.85,
    PanelState.CRACK: 0.75,
    PanelState.DIODE_FAULT: 0.66,
    PanelState.STRING_DROPOUT: 0.05,
    # An un-inspected panel is not evidence of a fault. Treated as healthy for the
    # electrical simulation so `unknown` cannot manufacture a suspicion spike.
    PanelState.UNKNOWN: 1.00,
}


#: Minimum cell anomaly for a fully-inspected, fault-free cell to be reported as
#: "PR depressed but unexplained" — i.e. worth a human look.
#:
#: ⚠ **Provisional and arbitrary.** 2% is a conventional O&M floor for a PR deficit
#: worth investigating, but it has NOT been calibrated here and cannot be: on
#: simulated SCADA the anomaly is a deterministic function of the injected faults,
#: so there is no independent signal to tune against. Re-derive it from a real feed
#: before treating a flag (or its absence) as meaningful.
PR_UNEXPLAINED_MIN = 0.02


@dataclass(frozen=True)
class SimulatedCellScore:
    """One dispatch cell's SIMULATED performance-ratio anomaly."""

    cell_id: str
    n_panels: int
    #: Weather-normalised expected output, in arbitrary consistent units.
    expected_output: float
    #: SIMULATED "measured" output — derived from pv:state/pv:iv_yield, not sensed.
    simulated_output: float
    #: `1 - simulated/expected`, clamped to [0, 1]. This is the anomaly score and
    #: the cell's fault-probability PRIOR.
    anomaly: float
    #: Panels in this cell already confirmed by the fleet, and how many of those
    #: came back faulty. Drives the posterior.
    panels_confirmed: int = 0
    faults_confirmed: int = 0
    scada_source: str = SCADA_SOURCE

    @property
    def is_simulated(self) -> bool:
        return self.scada_source == SCADA_SOURCE

    @property
    def posterior(self) -> float:
        """Rank score after folding in what the fleet actually found.

        A fully-inspected cell drops to the fraction of its panels that were
        genuinely faulty, so a cell cleared by the fleet stops out-ranking
        un-inspected ones even if its PR is still depressed. Partially inspected
        cells interpolate on inspected fraction — the honest position between "we
        only have telemetry" and "we have looked".
        """
        if not self.n_panels:
            return 0.0
        seen = min(self.panels_confirmed, self.n_panels) / self.n_panels
        found = (self.faults_confirmed / self.panels_confirmed) if self.panels_confirmed else 0.0
        return (1.0 - seen) * self.anomaly + seen * found

    @property
    def pr_unexplained(self) -> bool:
        """A depressed PR that inspection did NOT account for.

        Not a scoring term — a **finding**. Soiling and inter-row shading depress
        a string's PR without any single module being faulty, so a fully inspected
        cell that still reads anomalous is pointing at something real that
        per-panel verdicts do not capture.
        """
        return (
            self.panels_confirmed >= self.n_panels > 0
            and self.faults_confirmed == 0
            and self.anomaly >= PR_UNEXPLAINED_MIN
        )

    def to_dict(self) -> dict:
        return {
            "cell_id": self.cell_id,
            "n_panels": self.n_panels,
            "expected_output": round(self.expected_output, 6),
            "simulated_output": round(self.simulated_output, 6),
            "anomaly": round(self.anomaly, 6),
            "posterior": round(self.posterior, 6),
            "panels_confirmed": self.panels_confirmed,
            "faults_confirmed": self.faults_confirmed,
            "pr_unexplained": self.pr_unexplained,
            # Repeated per row on purpose: a row lifted out of context must still
            # carry the fact that it is not a measurement.
            "scada_source": self.scada_source,
        }


def simulated_panel_output(record: PanelRecord, irradiance: float = 1.0) -> float:
    """SIMULATED DC output for one module, in arbitrary consistent units.

    `iv_yield` x state factor x plane-of-array irradiance. `iv_yield` is the
    panel's own recorded degradation, so a healthy-but-aged module contributes a
    mild deficit exactly as it would electrically.
    """
    factor = STATE_OUTPUT_FACTOR.get(record.state, 1.0)
    return max(0.0, float(record.iv_yield)) * factor * max(0.0, irradiance)


def expected_panel_output(record: PanelRecord, irradiance: float = 1.0) -> float:
    """Weather-normalised EXPECTED output for one module — nameplate, not condition.

    Deliberately ignores `pv:state`: expected output is what the string *should*
    make given irradiance and temperature, and comparing that against the
    simulated actual is what produces the residual. Folding condition into both
    sides would cancel the very signal being measured.
    """
    return max(0.0, irradiance)


@dataclass
class CellRollup:
    """Accumulator for one cell while scanning panels."""

    cell_id: str
    n_panels: int = 0
    expected: float = 0.0
    simulated: float = 0.0
    panels_confirmed: int = 0
    faults_confirmed: int = 0
    panel_ids: list[str] = field(default_factory=list)


def rank_cells_simulated(
    records: Iterable[PanelRecord],
    irradiance: float = 1.0,
    min_anomaly: float = 0.0,
) -> list[SimulatedCellScore]:
    """Rank dispatch cells by SIMULATED PR anomaly, worst first.

    Panels with an empty `cell_id` are **skipped, not bucketed**: a stage built
    before the `grid:` namespace existed has no cells, and inventing one would
    silently fabricate the very topology this must not invent. A caller that gets
    an empty list back should fall back to layout order.

    Ties break on `cell_id` so the ordering is deterministic for a seeded run —
    a ranker whose output depends on dict iteration order is not reproducible.
    """
    rollups: dict[str, CellRollup] = {}
    for rec in records:
        if not rec.cell_id:
            continue
        r = rollups.setdefault(rec.cell_id, CellRollup(rec.cell_id))
        r.n_panels += 1
        r.expected += expected_panel_output(rec, irradiance)
        r.simulated += simulated_panel_output(rec, irradiance)
        r.panel_ids.append(rec.panel_id)

    scores = []
    for r in rollups.values():
        anomaly = 0.0
        if r.expected > 0.0:
            anomaly = max(0.0, min(1.0, 1.0 - r.simulated / r.expected))
        if anomaly < min_anomaly:
            continue
        scores.append(
            SimulatedCellScore(
                cell_id=r.cell_id,
                n_panels=r.n_panels,
                expected_output=r.expected,
                simulated_output=r.simulated,
                anomaly=anomaly,
                panels_confirmed=r.panels_confirmed,
                faults_confirmed=r.faults_confirmed,
            )
        )
    scores.sort(key=lambda s: (-s.posterior, s.cell_id))
    return scores


def apply_verdicts(
    scores: Sequence[SimulatedCellScore],
    verdicts: dict[str, str],
    cell_of: dict[str, str],
) -> list[SimulatedCellScore]:
    """Fold confirmed panel verdicts into the cells and re-rank.

    `verdicts` maps panel_id -> detected_state; `cell_of` maps panel_id -> cell_id.
    `unknown` counts as INSPECTED BUT NOT FAULTY: it is a lost answer, not a
    finding, and letting it raise a cell's posterior would reward plumbing
    failures with more dispatch priority.
    """
    seen: dict[str, tuple[int, int]] = {}
    for pid, state in verdicts.items():
        cid = cell_of.get(pid)
        if not cid:
            continue
        n, f = seen.get(cid, (0, 0))
        is_fault = state not in (PanelState.HEALTHY.value, PanelState.UNKNOWN.value)
        seen[cid] = (n + 1, f + (1 if is_fault else 0))

    out = []
    for s in scores:
        n, f = seen.get(s.cell_id, (0, 0))
        out.append(
            SimulatedCellScore(
                cell_id=s.cell_id,
                n_panels=s.n_panels,
                expected_output=s.expected_output,
                simulated_output=s.simulated_output,
                anomaly=s.anomaly,
                panels_confirmed=n,
                faults_confirmed=f,
            )
        )
    out.sort(key=lambda s: (-s.posterior, s.cell_id))
    return out


def summary(scores: Sequence[SimulatedCellScore]) -> dict:
    """Run-record block. Carries the simulated flag at the top level so a reader
    cannot see the ranking without seeing what produced it."""
    return {
        "scada_source": SCADA_SOURCE,
        "simulated": True,
        "caveat": SIMULATED_CAVEAT,
        "n_cells": len(scores),
        "n_anomalous": sum(1 for s in scores if s.anomaly > 0.0),
        "pr_unexplained_cells": [s.cell_id for s in scores if s.pr_unexplained],
        "cells": [s.to_dict() for s in scores],
    }
