#!/usr/bin/env python3
"""Aggregate archived run records into a variance report — no Isaac, no GPU.

`run.py --repeat N` writes `variance.json` as it goes. This does the same job
after the fact, which matters for two cases:

1. **Runs recorded before the harness existed** — two separate `runs/<ts>/`
   directories of the same scenario can still be compared, which is how a
   historic flip gets attributed instead of argued about.
2. **Re-analysis** — the report gained a field (renderer stability, say) after a
   long run had already finished; re-run this rather than re-run the sim.

    # a repeat set written by run.py --repeat
    PYTHONPATH=src python3 tools/kpi_variance.py runs/20260728T194510
    # or explicit run directories, in order
    PYTHONPATH=src python3 tools/kpi_variance.py runs/20260728T113834 runs/20260728T114409

Pass `--write` to (re)write `variance.json` into the first argument's directory.
It refuses to compare records from different scenarios or seeds — averaging over
two different worlds is not a variance measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solar_twin.kpi import variance as V  # noqa: E402


def _collect(paths: list[str]) -> tuple[list[dict], Path]:
    """Load run records from either one repeat-set directory or N run dirs."""
    records: list[dict] = []
    root = Path(paths[0])
    if len(paths) == 1:
        repeats = sorted(root.glob("repeat_*/results.json"))
        if repeats:
            records = [json.loads(p.read_text()) for p in repeats]
        elif (root / "results.json").exists():
            records = [json.loads((root / "results.json").read_text())]
    else:
        for p in paths:
            f = Path(p) / "results.json"
            if not f.exists():
                f = Path(p)  # allow a direct results.json path
            records.append(json.loads(f.read_text()))
    return records, root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="a repeat-set dir, or N run dirs")
    ap.add_argument("--write", action="store_true", help="write variance.json")
    ap.add_argument(
        "--allow-mixed",
        action="store_true",
        help="compare records from different scenarios/seeds anyway (you are "
        "then measuring two worlds, not one world twice — say so in the write-up)",
    )
    args = ap.parse_args(argv)

    records, root = _collect(args.paths)
    if not records:
        print(f"no results.json found under {args.paths}", file=sys.stderr)
        return 2
    if len(records) == 1:
        print(
            "only one run record found — variance needs at least two "
            "(run with --repeat N, or pass several run dirs)",
            file=sys.stderr,
        )
        return 2

    keys = {(r.get("scenario"), r.get("seed")) for r in records}
    if len(keys) > 1 and not args.allow_mixed:
        print(f"refusing to aggregate mixed (scenario, seed): {sorted(keys)}", file=sys.stderr)
        return 2

    prov = {json.dumps(r.get("perception"), sort_keys=True) for r in records}
    report = V.summarize(records)
    print(f"records: {len(records)}  scenario={records[0].get('scenario')}  "
          f"seed={records[0].get('seed')}")
    if len(prov) > 1:
        # Different decoding configs across the set is not run-to-run variance,
        # it is an A/B test. Say which, loudly.
        print("⚠ perception provenance DIFFERS across these records — this is an "
              "A/B comparison, not a variance measurement")
    print(report.describe())
    if args.write:
        out = root / "variance.json"
        out.write_text(json.dumps(report.to_dict(), indent=2))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
