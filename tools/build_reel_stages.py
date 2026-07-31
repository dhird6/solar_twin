#!/usr/bin/env python3
"""Build one stage per condition for the reel (Isaac-bound).

**Isaac-bound — run under `./python.sh`.**

The sun vector, all 273 tracker angles and the procedural sky shader are resolved at
**build** time, so "the same plant at night" is a different USD rather than a different
camera. That is also what makes the reel honest: the trackers are stowed flat at night
and pinned at their limit at low sun because the *hardware model* put them there, not
because someone posed them for the shot.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/build_reel_stages.py --subset 60 --out-dir assets/reel

⚠ `--subset` is a real trade. The full block is 81,961 prims and renders fine, but it
costs ~2.5 min per build and six of them is 15 minutes before a single frame. A 60-table
subset is ~18k prims, builds in well under a minute, and still fills a wide shot. Use
`--subset 0` for the full plot when the reel is final.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a stage per reel condition.")
    ap.add_argument(
        "--farm", default="configs/farm_khavda_block02.yaml", help="base farm config"
    )
    ap.add_argument("--out-dir", default="assets/reel")
    ap.add_argument(
        "--subset",
        type=int,
        default=60,
        help="tracker tables to author (0 = the whole plot). See the module note.",
    )
    ap.add_argument(
        "--only", default="", help="comma-separated condition names, for a re-render"
    )
    args = ap.parse_args(argv)

    import yaml

    from solar_twin.world.conditions_reel import CONDITIONS
    from solar_twin.world.farm_builder import build

    with open(args.farm) as f:
        base = yaml.safe_load(f)

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def deep_merge(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out

    built = {}
    for cond in CONDITIONS:
        if wanted and cond.name not in wanted:
            continue
        cfg = deep_merge(base, cond.farm_overrides())
        if args.subset:
            cfg["layout"] = {**(cfg.get("layout") or {}), "max_tables": args.subset}
        out = out_dir / f"{cond.name}.usd"
        print(f"\n=== {cond.name}: {cond.title} — {cond.sun_timestamp} / {cond.sky} ===",
              flush=True)
        t0 = time.perf_counter()
        build(cfg, str(out))
        built[cond.name] = str(out)
        print(f"  [{cond.name}] {time.perf_counter() - t0:.1f}s -> {out}", flush=True)

    print("\nstages:")
    for k, v in built.items():
        print(f"  {k:8} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
