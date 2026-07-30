#!/usr/bin/env python3
"""Per-state fault recall from archived run records — and the null baseline.

**Pure python. No Isaac, no GPU.** Works on any `runs/**/results.json` ever written.

    python3 tools/kpi_recall.py runs/                     # every run
    python3 tools/kpi_recall.py runs/ --perception cosmos_reason
    python3 tools/kpi_recall.py runs/20260731T023719

WHY THIS EXISTS. `detection_rate` (`KPI-01`) is **accuracy over every panel**, and
its docstring says so — but it is gated and quoted as though it were detection.
On a mostly-healthy scenario that flatters any model which under-reports.

Measured over the 20 archived Cosmos Reason runs on 2026-07-31:

  * `nominal_calm_vlm` is **82.5% healthy** and gates on
    `detection_rate_min: 0.80`. A model that calls **every panel healthy** scores
    **0.825 and passes**, having detected nothing.
  * The null model clears that gate in **13 of 20** runs, and in **3 of 20** it
    scores at or above what the real model managed.
  * Pooled by state: soiled is flagged 0.984 / named 0.516; hotspot is flagged
    0.621 / named 0.379. Injected soiling was called "hotspot" 29 times in 62.
    A pooled figure cannot show that, and the two halves have different fixes.

So this reports, per state: how often a fault was **flagged at all** (sensitivity)
and how often it was **named correctly** (discrimination), against a denominator of
faulted panels only — plus `null` , the score `detection_rate` gets for free.

⚠ A run that seeds no faults has NO recall to report. It is printed as `-`, never
as 1.00, because "nothing to find" and "found everything" are not the same result.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_records(root: Path) -> list[tuple[str, dict]]:
    """Every results.json under `root`, newest path order. A file that will not
    parse is skipped loudly rather than silently dropped."""
    if root.is_file():
        return [(str(root), json.loads(root.read_text()))]
    out = []
    for p in sorted(root.rglob("results.json")):
        try:
            out.append((str(p), json.loads(p.read_text())))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] skipping {p}: {exc}", file=sys.stderr)
    return out


def score(record: dict) -> dict:
    """Recall figures for one run record. Denominator is faulted panels only."""
    panels = record.get("panels") or []
    faulted = [p for p in panels if p.get("injected_state", "healthy") != "healthy"]
    healthy_n = len(panels) - len(faulted)

    by_state: dict[str, dict] = defaultdict(lambda: {"n": 0, "named": 0, "flagged": 0})
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    for p in faulted:
        inj = p["injected_state"]
        det = p.get("detected_state") or "unknown"
        s = by_state[inj]
        s["n"] += 1
        s["named"] += 1 if det == inj else 0
        s["flagged"] += 1 if det != "healthy" else 0
        confusion[(inj, det)] += 1

    for s in by_state.values():
        s["recall"] = s["named"] / s["n"]
        s["flagged_rate"] = s["flagged"] / s["n"]

    n_named = sum(s["named"] for s in by_state.values())
    n_flag = sum(s["flagged"] for s in by_state.values())
    return {
        "panels": len(panels),
        "faulted": len(faulted),
        # The null baseline: what you score by calling everything healthy.
        "null": healthy_n / len(panels) if panels else 0.0,
        "detection_rate": (record.get("metrics") or {}).get("detection_rate"),
        "fault_recall": n_named / len(faulted) if faulted else None,
        "fault_flagged_rate": n_flag / len(faulted) if faulted else None,
        "by_state": dict(by_state),
        "confusion": confusion,
    }


def _fmt(v) -> str:
    return "  -  " if v is None else f"{v:5.3f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path, help="a runs/ dir or a single results.json")
    ap.add_argument("--perception", default="", help="only runs using this backend")
    ap.add_argument("--state", default="", help="only report this injected state")
    args = ap.parse_args(argv)

    records = load_records(args.root)
    if args.perception:
        records = [
            (p, r)
            for p, r in records
            if (r.get("perception") or {}).get("name") == args.perception
        ]
    if not records:
        print("no matching run records", file=sys.stderr)
        return 1

    print(
        f"{'run':30s} {'scenario':22s} {'pan':>4s} {'flt':>4s} "
        f"{'KPI-01':>6s} {'null':>6s} {'flagged':>7s} {'named':>6s}"
    )
    pooled: dict[str, dict] = defaultdict(lambda: {"n": 0, "named": 0, "flagged": 0})
    pooled_conf: dict[tuple[str, str], int] = defaultdict(int)
    null_passes = beat_by_null = gated = 0

    for path, rec in records:
        s = score(rec)
        name = Path(path).parent.name
        dr = s["detection_rate"]
        print(
            f"{name:30s} {str(rec.get('scenario'))[:22]:22s} "
            f"{s['panels']:4d} {s['faulted']:4d} "
            f"{_fmt(dr)} {_fmt(s['null'])} "
            f"{_fmt(s['fault_flagged_rate'])} {_fmt(s['fault_recall'])}"
        )
        if s["faulted"]:
            gated += 1
            if s["null"] >= 0.80:  # the gate nominal_calm_vlm actually declares
                null_passes += 1
            if dr is not None and dr <= s["null"]:
                beat_by_null += 1
        for st, v in s["by_state"].items():
            if args.state and st != args.state:
                continue
            pooled[st]["n"] += v["n"]
            pooled[st]["named"] += v["named"]
            pooled[st]["flagged"] += v["flagged"]
        for k, v in s["confusion"].items():
            pooled_conf[k] += v

    if pooled:
        print("\n=== POOLED BY INJECTED STATE ===")
        print(f"{'state':10s} {'n':>4s} {'flagged':>8s} {'named':>7s}")
        for st, v in sorted(pooled.items()):
            print(
                f"{st:10s} {v['n']:4d} "
                f"{v['flagged'] / v['n']:8.3f} {v['named'] / v['n']:7.3f}"
            )
        print("\n=== WHAT IT SAID (injected -> diagnosed) ===")
        for (inj, det), n in sorted(pooled_conf.items(), key=lambda kv: -kv[1]):
            mark = "  <- MISSED" if det == "healthy" else (
                "  <- mislabelled" if det != inj else "")
            print(f"  {inj:10s} -> {det:10s} {n:4d}{mark}")

    if gated:
        print(
            f"\nruns with faults seeded: {gated}\n"
            f"  the NULL model (call everything healthy) clears a 0.80 gate in: "
            f"{null_passes}/{gated}\n"
            f"  runs where detection_rate <= that null baseline:              "
            f"{beat_by_null}/{gated}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
