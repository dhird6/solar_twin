"""Run-to-run variance of a KPI — pure-python, Isaac-free.

**Why this exists.** The world is seeded; the model is not necessarily. Two
identical 24-panel runs (2026-07-28) disagreed on one panel — `R258-C013` came
back `soiled` in one and `hotspot` in the other — which means a KPI quoted from
a single run carries unquantified variance. `SESSIONS.md` recorded that as a
finding; this module makes it a measurement.

Feed it the ``results.json`` dicts of N repeats of the SAME seeded scenario
(`run.py --repeat N`, or archived run directories) and it reports:

- a **spread** per metric (min / median / max, and the range) instead of one
  number, so a KPI can be quoted honestly as "0.00, N=5, range 0.00";
- every panel the repeats **disagreed** about, each attributed to a cause.

**Attribution is the point.** A flip has two possible sources needing opposite
fixes, and the frame fingerprints tell them apart:

- ``model`` — the model saw the same picture and answered differently. The
  decoding path is the suspect (see `cosmos_reason.DEFAULT_SAMPLING`: serial
  requests measured repeatable, batched ones not).
- ``render`` — the picture itself changed, so the model was asked a different
  question. The renderer/scene is the suspect, not the VLM.
- ``both`` — the screening picture matched but the confirm picture did not, so
  the two causes cannot be separated on this evidence.
- ``unknown`` — nothing recorded to judge on (a frameless backend, or a run
  from before fingerprints existed).

**"Same picture" is a tolerance, not an equality** (`THUMB_TOLERANCE` below).
Bit-exact digests were the obvious design and are the wrong one: RTX capture is
measurably stochastic on this build — four captures from a camera that never
moved gave four distinct digests (`RISK-24`) — so exact comparison would blame
the renderer for every flip forever. Both are reported anyway (`FrameStability`):
"same bits" and "same picture", because the gap between them is the noise floor,
and a run where the *picture* really moved is a run that is not comparing like
with like.

Aggregating over dicts rather than `MissionResult` objects is deliberate: the
same code then works on runs recorded weeks ago, and this module stays testable
with no Isaac, no GPU and no VLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any

#: Mean absolute per-cell difference (in 0-255 LSB) below which two frame
#: thumbnails count as the SAME picture. Set from **both sides** of the gap and
#: from a real pose distribution, not one convenient camera position:
#:
#: Single pose (`tools/probe_render_determinism.py`, 640x480, low-sun stage):
#:   same scene, camera held still          0.36 LSB (max cell 2)
#:   same scene, left the pose and returned 0.59 LSB (max cell 4)
#:   same scene, +10 settling steps         0.20 LSB (max cell 1)
#:   DIFFERENT panel (shaded vs. control)  35.5  LSB (max cell 62) <- real signal
#:
#: All 40 screening poses of `runs/20260728T200755` (3 repeats, **static stage** —
#: no turbines, fixed sun, teleporting robots, so every difference is renderer
#: noise by construction):
#:   min 0.36 · median 0.83 · p90 2.05 · max 2.19 (excluding one case below)
#:
#: A first calibration at 1.0 came from the single pose above and flagged 12 of
#: those 40 panels — i.e. the one-pose figure did not describe the distribution.
#: 3.0 clears every settled pose (p100 2.19) while sitting ~12x below the smallest
#: real difference, so the gap is still wide, not a knife edge.
#:
#: ⚠ **One systematic exception per CAMERA, deliberately left visible.** The first
#: frame a camera takes in a repeat exceeds this: repeat 1 approaches from the
#: stage's home pose while later repeats arrive from the previous repeat's last
#: panel, so the renderer's accumulation history genuinely differs. Measured on
#: both cameras and reproduced across two independent repeat sets:
#:   first SCREENED panel (R258-C000, low-sun set)   11.08 LSB
#:   first CONFIRMED panel (R258-C004, demo set)     38.11 and 38.05 LSB
#: The confirm figure repeating to within 0.06 LSB across separate runs is what
#: makes it an approach artifact rather than noise. Expect up to one flagged panel
#: per camera per repeat set, always at that camera's first use, and read it as
#: such — the verdicts on those panels were unaffected in both sets.
#:
#: Re-measure (both sides) if the renderer, resolution or scene changes. Never
#: widen this to make a result look better: it would hide exactly the "these runs
#: saw different pictures" finding the number exists to surface.
THUMB_TOLERANCE = 3.0

#: Metrics summarised by default — the ones `run.py` records per run.
DEFAULT_METRICS = (
    "false_fault_rate",  # KPI-03
    "false_alarm_rate",  # KPI-03's genuine-false-alarm half
    "abstention_rate",  # KPI-03's lost-answer half (over all panels)
    "detection_rate",  # KPI-01
    "faults_detected",
    "panels_inspected",
)


def thumbnails_differ(
    thumbs: list[str | None], tolerance: float = THUMB_TOLERANCE
) -> bool | None:
    """Do these frame thumbnails show materially different pictures?

    Compared with a tolerance rather than by equality, because the renderer is
    stochastic (`perception.base.frame_thumbnail`). Returns None when any
    thumbnail is missing or malformed — "cannot tell", which the caller must
    report as unattributed rather than resolve either way.
    """
    if not thumbs or any(t is None for t in thumbs):
        return None
    try:
        grids = [bytes.fromhex(t) for t in thumbs]  # type: ignore[arg-type]
    except ValueError:
        return None
    if len({len(g) for g in grids}) != 1 or not grids[0]:
        return None
    base = grids[0]
    for g in grids[1:]:
        mean_delta = sum(abs(a - b) for a, b in zip(base, g)) / len(base)
        if mean_delta > tolerance:
            return True
    return False


@dataclass(frozen=True)
class MetricSpread:
    """One metric across N repeats."""

    metric: str
    values: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def min(self) -> float:
        return min(self.values)

    @property
    def max(self) -> float:
        return max(self.values)

    @property
    def median(self) -> float:
        return float(median(self.values))

    @property
    def range(self) -> float:
        return self.max - self.min

    @property
    def stable(self) -> bool:
        return self.range == 0.0

    def quote(self) -> str:
        """How the number should appear in a doc: never bare."""
        if self.stable:
            return f"{self.median:.4g} (N={self.n}, identical across repeats)"
        return (
            f"{self.median:.4g} median (N={self.n}, range "
            f"{self.min:.4g}–{self.max:.4g})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "n": self.n,
            "min": self.min,
            "median": self.median,
            "max": self.max,
            "range": self.range,
            "stable": self.stable,
            "quote": self.quote(),
        }


@dataclass(frozen=True)
class PanelDisagreement:
    """A panel the repeats did not agree on."""

    panel_id: str
    injected_state: str
    detected_states: tuple[str, ...]  # one per repeat, in run order
    screen_statuses: tuple[str, ...]
    cause: str  # "model" | "render" | "both" | "unknown"
    frames_identical: bool | None  # None when digests are missing

    def describe(self) -> str:
        return (
            f"{self.panel_id} (injected {self.injected_state}): "
            f"{' / '.join(self.detected_states)} → cause={self.cause}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "injected_state": self.injected_state,
            "detected_states": list(self.detected_states),
            "screen_statuses": list(self.screen_statuses),
            "cause": self.cause,
            "frames_identical": self.frames_identical,
        }


@dataclass
class FrameStability:
    """Were the *pixels* identical across repeats, panel by panel?

    Reported for every compared panel, not only the ones that flipped — a run
    where nothing flips still answers "is this renderer bit-reproducible across
    repeats?", which is otherwise only observable when a verdict happens to
    change. `identical == compared` is positive evidence the renderer is stable;
    any `changed` means the model was being asked different questions, and a
    stable KPI in that condition is luck rather than reproducibility.
    """

    compared: int = 0
    identical: int = 0  # bit-identical (exact digest)
    changed: int = 0
    unknown: int = 0  # no digests recorded (frameless backend / older run)
    changed_panels: list[str] = field(default_factory=list)
    #: Same counts on the noise-tolerant signature — "same picture", as opposed
    #: to "same bits". The gap between the two IS the renderer's sampling noise.
    same_picture: int = 0
    different_picture: int = 0
    picture_unknown: int = 0
    #: The panels whose PICTURE differed — the ones worth looking at. Kept
    #: separate from `changed_panels` (merely not bit-identical), which on this
    #: renderer is every panel and therefore names nothing useful.
    different_picture_panels: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.compared == 0:
            return "no panels compared"
        if self.unknown == self.compared and self.picture_unknown == self.compared:
            return "no frame digests recorded — renderer stability unmeasured"
        if self.changed == 0 and self.different_picture == 0:
            return (
                f"renderer stable: {self.identical}/{self.compared} panels "
                f"bit-identical across repeats"
            )
        bits = (
            f"{self.identical}/{self.compared} bit-identical"
            if self.changed == 0
            else f"NOT bit-reproducible ({self.changed}/{self.compared} differ)"
        )
        if self.picture_unknown == self.compared:
            return f"frames {bits}; picture-level unmeasured (no thumbnails recorded)"
        if self.different_picture == 0:
            # The expected case on this build: the bits differ every time, the
            # picture does not — the renderer jitters below the level that
            # changes the image, so repeats still compare like with like.
            return (
                f"frames {bits}, but {self.same_picture}/{self.compared} show the "
                f"SAME picture — the differences are renderer sampling noise"
            )
        return (
            f"frames {bits}; ⚠ {self.different_picture}/{self.compared} panels show a "
            f"MATERIALLY different picture between repeats "
            f"({', '.join(self.different_picture_panels[:5])}) — those panels are "
            f"not comparing like with like"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "compared": self.compared,
            "identical": self.identical,
            "changed": self.changed,
            "unknown": self.unknown,
            "same_picture": self.same_picture,
            "different_picture": self.different_picture,
            "picture_unknown": self.picture_unknown,
            "changed_panels": self.changed_panels[:50],
            "different_picture_panels": self.different_picture_panels[:50],
            "verdict": self.verdict,
        }


@dataclass
class VarianceReport:
    n_runs: int
    metrics: dict[str, MetricSpread] = field(default_factory=dict)
    disagreements: list[PanelDisagreement] = field(default_factory=list)
    frames: FrameStability = field(default_factory=FrameStability)
    panels_compared: int = 0
    #: Panels present in some repeats but not all — a truncated or differently
    #: routed run got mixed into the set. Called out rather than averaged over.
    partial_panels: list[str] = field(default_factory=list)

    @property
    def agreement_rate(self) -> float:
        """Fraction of compared panels that returned the same verdict every
        time. 1.0 with n_runs == 1 is not evidence of anything."""
        if not self.panels_compared:
            return 0.0
        return (self.panels_compared - len(self.disagreements)) / self.panels_compared

    @property
    def causes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.disagreements:
            out[d.cause] = out.get(d.cause, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_runs": self.n_runs,
            "panels_compared": self.panels_compared,
            "agreement_rate": self.agreement_rate,
            "causes": self.causes,
            "frames": self.frames.to_dict(),
            "partial_panels": self.partial_panels,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "disagreements": [d.to_dict() for d in self.disagreements],
        }

    def describe(self) -> str:
        lines = [f"variance over N={self.n_runs} repeats:"]
        for name, spread in self.metrics.items():
            flag = "" if spread.stable else "  ⚠ varies"
            lines.append(f"  {name} = {spread.quote()}{flag}")
        lines.append(
            f"  per-panel agreement {self.agreement_rate:.3f} "
            f"({len(self.disagreements)}/{self.panels_compared} panels flipped)"
        )
        lines.append(f"  frames: {self.frames.verdict}")
        if self.causes:
            lines.append(f"  flip causes: {self.causes}")
        for d in self.disagreements[:10]:
            lines.append(f"    - {d.describe()}")
        if len(self.disagreements) > 10:
            lines.append(f"    ... {len(self.disagreements) - 10} more")
        if self.partial_panels:
            lines.append(
                f"  ⚠ {len(self.partial_panels)} panel(s) not present in every "
                f"repeat — the runs do not cover the same panels: "
                f"{', '.join(self.partial_panels[:5])}"
            )
        return "\n".join(lines)


def _attribute(entries: list[dict[str, Any]]) -> tuple[str, bool | None]:
    """Decide whether a disagreement came from the renderer or the model.

    Judged on the **noise-tolerant signature** (`screen_frame_key`), not the
    exact digest: RTX capture is measurably not bit-reproducible (`RISK-24` —
    4 captures from an unmoved camera gave 4 distinct digests at 0.86/255 mean
    difference), so exact equality would blame the renderer for every single
    flip. Records written before signatures existed carry only digests; those
    fall back to `unknown` rather than to a conclusion the data cannot support.
    """
    screen_differs = thumbnails_differ([e.get("screen_frame_thumb") for e in entries])
    if screen_differs is None:
        return "unknown", None
    if not screen_differs:
        # Same picture in, different verdict out: the model moved.
        seen = [e.get("confirm_frame_thumb") for e in entries]
        seen = [c for c in seen if c is not None]
        if len(seen) > 1 and thumbnails_differ(seen):
            # The close pass saw a different picture — cannot be separated.
            return "both", False
        return "model", True
    return "render", False


def summarize(
    runs: list[dict[str, Any]], metrics: tuple[str, ...] = DEFAULT_METRICS
) -> VarianceReport:
    """Aggregate N run records (``results.json`` dicts) of the same scenario."""
    report = VarianceReport(n_runs=len(runs))
    if not runs:
        return report

    for name in metrics:
        values = [
            float(r["metrics"][name])
            for r in runs
            if isinstance((r.get("metrics") or {}).get(name), (int, float))
            and not isinstance(r["metrics"][name], bool)
        ]
        if len(values) == len(runs):
            report.metrics[name] = MetricSpread(name, tuple(values))

    # Group per-panel results by panel_id, preserving run order.
    by_panel: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        for panel in run.get("panels") or []:
            by_panel.setdefault(panel["panel_id"], []).append(panel)

    for pid, entries in by_panel.items():
        if len(entries) != len(runs):
            report.partial_panels.append(pid)
            continue
        report.panels_compared += 1

        # Renderer stability, judged on every panel — the screening pass is the
        # one frame every panel produces.
        report.frames.compared += 1
        screen = [e.get("screen_frame_sha") for e in entries]
        if any(s is None for s in screen):
            report.frames.unknown += 1
        elif len(set(screen)) == 1:
            report.frames.identical += 1
        else:
            report.frames.changed += 1
            report.frames.changed_panels.append(pid)

        differs = thumbnails_differ([e.get("screen_frame_thumb") for e in entries])
        if differs is None:
            report.frames.picture_unknown += 1
        elif differs:
            report.frames.different_picture += 1
            report.frames.different_picture_panels.append(pid)
        else:
            report.frames.same_picture += 1

        detected = tuple(e.get("detected_state", "") for e in entries)
        if len(set(detected)) == 1:
            continue
        cause, identical = _attribute(entries)
        report.disagreements.append(
            PanelDisagreement(
                panel_id=pid,
                injected_state=entries[0].get("injected_state", ""),
                detected_states=detected,
                screen_statuses=tuple(e.get("screen_status", "") for e in entries),
                cause=cause,
                frames_identical=identical,
            )
        )
    report.disagreements.sort(key=lambda d: d.panel_id)
    report.partial_panels.sort()
    return report
