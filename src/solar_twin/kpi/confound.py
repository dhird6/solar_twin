"""Neighbour-adjacency confound: is a "false fault" the panel next door?

Pure-python, Isaac-free, and works on any archived run record — same contract as
`kpi/variance.py`.

## Why this exists

`KPI-03` scores a healthy panel as a false fault when the model reports a defect.
That is only valid if the frame the model judged shows **one** panel. Measured
2026-07-29 on `SC-01` (`runs/20260729T130956`), it does not:

    healthy panels inspected                     33
      ...with a faulted C+1 neighbour             7   -> 3 false-alarmed  (43%)
      ...with no faulted neighbour               26   -> 0 false-alarmed   (0%)

Every false alarm sat in the fifth of panels that had a faulted neighbour, and none
occurred anywhere else. Inspecting the captured confirm frames shows why: at the
confirm standoff the module is tilted ~46 deg to a nadir camera, so it is
foreshortened and the neighbouring module is in shot. The model reports soiling that
is genuinely visible — it just belongs to the panel next door, while ground truth is
scored against the target alone.

So this is a **measurement-validity defect, not a perception weakness**, which is
also why two prompt interventions could not fix it (see
`perception.cosmos_reason.PROMPT_VERSION`): the model was right about the image.

Reporting it separately keeps `KPI-03` honest without redefining it — the same
choice made for `abstention_rate` (§6.5 / `FR-03` is a locked contract). A KPI-03
quoted without this number may be measuring the neighbour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

#: `R12-C047` -> row `R12`, column 47. The panel-id contract (`CLAUDE.md`).
_PANEL_ID = re.compile(r"^(?P<row>[A-Za-z]+\d+)-C(?P<col>\d+)$")

#: Column offsets counted as "adjacent". The confirm camera looks along the torque
#: tube, so the in-frame neighbour is the next module in column order; both
#: directions are checked because which one lands in shot depends on view yaw.
DEFAULT_OFFSETS: tuple[int, ...] = (-1, 1)


def parse_panel_id(panel_id: str) -> tuple[str, int] | None:
    """``"R254-C014"`` -> ``("R254", 14)``; None if it is not a panel id."""
    m = _PANEL_ID.match(panel_id or "")
    if m is None:
        return None
    return m.group("row"), int(m.group("col"))


def neighbours(panel_id: str, offsets: Iterable[int] = DEFAULT_OFFSETS) -> list[str]:
    """Adjacent panel ids in the same row, zero-padded exactly as ids are.

    Same row only: rows are separate tracker tables metres apart, so the module
    across the aisle is not in frame the way the one beside it is.
    """
    parsed = parse_panel_id(panel_id)
    if parsed is None:
        return []
    row, col = parsed
    width = len(panel_id.rsplit("-C", 1)[1])
    out = []
    for off in offsets:
        nxt = col + off
        if nxt >= 0:
            out.append(f"{row}-C{nxt:0{width}d}")
    return out


@dataclass
class ConfoundReport:
    """How much of a run's false-fault rate could be the neighbouring panel."""

    healthy_inspected: int = 0
    #: Healthy panels whose neighbour was seeded with a fault.
    with_faulted_neighbour: int = 0
    #: ...of those, how many the model called faulty.
    false_alarms_with_neighbour: int = 0
    #: Healthy panels with a clean neighbourhood, and their false alarms.
    without_faulted_neighbour: int = 0
    false_alarms_without_neighbour: int = 0
    #: Per-panel detail, for the run record.
    suspects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def false_alarms(self) -> int:
        return self.false_alarms_with_neighbour + self.false_alarms_without_neighbour

    @property
    def rate_with_neighbour(self) -> float:
        if not self.with_faulted_neighbour:
            return 0.0
        return self.false_alarms_with_neighbour / self.with_faulted_neighbour

    @property
    def rate_without_neighbour(self) -> float:
        if not self.without_faulted_neighbour:
            return 0.0
        return self.false_alarms_without_neighbour / self.without_faulted_neighbour

    @property
    def attributable_share(self) -> float:
        """Fraction of this run's false alarms that sit beside a faulted panel.

        1.0 means every false alarm could be the neighbour. It is an upper bound on
        the confound, not proof of it — a panel can be adjacent to a fault *and*
        genuinely misread. Treat it as "how much of this number is unsafe to quote".
        """
        if not self.false_alarms:
            return 0.0
        return self.false_alarms_with_neighbour / self.false_alarms

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy_inspected": self.healthy_inspected,
            "with_faulted_neighbour": self.with_faulted_neighbour,
            "without_faulted_neighbour": self.without_faulted_neighbour,
            "false_alarms": self.false_alarms,
            "false_alarms_with_neighbour": self.false_alarms_with_neighbour,
            "false_alarms_without_neighbour": self.false_alarms_without_neighbour,
            "rate_with_neighbour": round(self.rate_with_neighbour, 6),
            "rate_without_neighbour": round(self.rate_without_neighbour, 6),
            "attributable_share": round(self.attributable_share, 6),
            "suspects": self.suspects,
            "verdict": self.describe(),
        }

    def describe(self) -> str:
        if not self.healthy_inspected:
            return "no healthy panels inspected — KPI-03 has no denominator here"
        if not self.false_alarms:
            return (
                f"no false alarms over {self.healthy_inspected} healthy panels; "
                f"{self.with_faulted_neighbour} of them sat beside a faulted panel "
                f"and none was misread"
            )
        return (
            f"{self.false_alarms} false alarm(s) over {self.healthy_inspected} healthy "
            f"panels; {self.false_alarms_with_neighbour} of them sit beside a FAULTED "
            f"panel ({self.attributable_share * 100:.0f}% of the total). Rate beside a "
            f"fault {self.rate_with_neighbour * 100:.0f}% "
            f"({self.false_alarms_with_neighbour}/{self.with_faulted_neighbour}) vs "
            f"{self.rate_without_neighbour * 100:.0f}% "
            f"({self.false_alarms_without_neighbour}/{self.without_faulted_neighbour}) "
            f"elsewhere"
            + (
                " — ⚠ the whole false-fault rate may be the panel next door"
                if self.attributable_share >= 0.999
                else ""
            )
        )


def analyse(
    record: dict[str, Any], offsets: Iterable[int] = DEFAULT_OFFSETS
) -> ConfoundReport:
    """Score one run record's `panels` block against its `injected_faults`."""
    faults = record.get("injected_faults") or {}
    report = ConfoundReport()
    for panel in record.get("panels") or []:
        if panel.get("injected_state") != "healthy":
            continue
        report.healthy_inspected += 1
        pid = panel.get("panel_id", "")
        faulted = {
            n: faults[n]
            for n in neighbours(pid, offsets)
            # A neighbour listed in `injected_faults` as healthy is not a fault.
            if faults.get(n) not in (None, "healthy")
        }
        misread = panel.get("detected_state") not in (None, "healthy")
        if faulted:
            report.with_faulted_neighbour += 1
            if misread:
                report.false_alarms_with_neighbour += 1
        else:
            report.without_faulted_neighbour += 1
            if misread:
                report.false_alarms_without_neighbour += 1
        if misread:
            report.suspects.append(
                {
                    "panel_id": pid,
                    "detected_state": panel.get("detected_state"),
                    "faulted_neighbours": faulted,
                }
            )
    return report
