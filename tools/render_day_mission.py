#!/usr/bin/env python3
"""The day-cycle mission film: the same plant inspected from dawn to night.

**Isaac-bound — run under `./python.sh`.**

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/render_day_mission.py --tables 120 \
        --out assets/day_mission.mp4

## What this shows that the other videos do not

* `flythrough.py` — what the site looks like. No fleet.
* `conditions_reel.py` — the plant under six conditions. No fleet.
* `run.py --video` — one mission, one lighting condition.
* **this** — the FLEET DOING ITS JOB, repeatedly, as the light goes round.

Each chapter is a real `scout_dispatch` mission on its own stage:

    SCOUT      one drone flies a high wide pass and assesses every panel
    DISPATCH   the ground bot drives to a panel the scout FLAGGED
    CONVERGE   both drones take station on that panel
    INSPECT    the close pass diagnoses it and writes the verdict to the USD prim

So "the ground robot goes to the exact faulty grid" is not staged for the camera —
the destination is chosen by what the perception layer flagged during the survey in
that same run, and it can be a different panel at a different time of day.

The drone's own inspection camera is composited in as an inset by
`world/recorder.py`, captioned with the panel under inspection and the verdict
returned for it.

## Why one stage per time of day

`sun.timestamp` resolves at BUILD time — it sets the `DistantLight`, every tracker
angle and the procedural sky shader's parameters. So dawn and noon are different
USD files, which is also what makes the trackers correct in each: they follow the sun
because the hardware model put them there, not because a camera moved.

⚠ **Cost.** Each chapter is a stage build plus a live mission with per-frame capture.
Budget ~5-10 min per chapter at `--tables 120`; the whole film is well over half an
hour. `--tables` is the main lever and `--chapters` picks a subset.

⚠ **Honesty, unchanged from the conditions reel:** the fleet's MOTION is scripted —
teleport-interpolated waypoints, no flight dynamics, no localization, no navigation.
What is real here is the survey→dispatch→inspect DECISION chain and the VLM verdict,
not the flying. PX4 does fly this plant (`tools/px4_in_plant.py`) but at 0.11x
realtime, far too slow to film.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

#: (name, title, ISO-8601 UTC, sky). Khavda is UTC+5:30, so 01:20Z is ~06:50 local.
#: Elevations are what `world/solar.py` computes for these instants — printed by the
#: build, so a viewer can check the caption rather than take it.
DAY_CYCLE: tuple[tuple[str, str, str, str], ...] = (
    ("dawn", "DAWN — 06:50", "2026-06-21T01:20:00Z", "ClearSky"),
    ("morning", "MORNING — 09:30", "2026-06-21T04:00:00Z", "ClearSky"),
    ("midday", "MIDDAY — 12:30", "2026-06-21T07:00:00Z", "ClearSky"),
    ("cloudy", "AFTERNOON — cloud", "2026-06-21T09:30:00Z", "CumulusHeavy"),
    ("dusk", "DUSK — 18:40", "2026-06-21T13:10:00Z", "ClearSky"),
    ("night", "NIGHT", "2026-06-20T19:30:00Z", "NightSky"),
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="assets/day_mission.mp4")
    ap.add_argument("--stage-dir", default="assets/day_cycle")
    ap.add_argument(
        "--tables", type=int, default=120,
        help="tracker tables per stage. The main cost lever — 600 is the demo "
        "scenario's own figure and is slow to film.",
    )
    ap.add_argument(
        "--panels", type=int, default=14, help="panels the survey sweeps per chapter"
    )
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--chapters", default="", help="comma-separated subset of names")
    ap.add_argument(
        "--skip-build", action="store_true", help="reuse stages already in --stage-dir"
    )
    args = ap.parse_args(argv)

    wanted = {s.strip() for s in args.chapters.split(",") if s.strip()}
    cycle = [c for c in DAY_CYCLE if not wanted or c[0] in wanted]
    stage_dir = Path(args.stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    import yaml

    # ⚠ BUILD AND FLY IN SEPARATE PROCESSES. `farm_builder` imports `pxr` at module
    # scope and authors USD with no Kit app; `run.py` creates a `SimulationApp`. Doing
    # both in ONE process loads pxr standalone first and then Kit's copy on top, and
    # the second one dies with "extension class wrapper for base class ... has not
    # been created yet" — measured, on the first attempt at this tool. Two processes
    # cost an app startup each and are correct.
    ISAAC = os.environ.get("ISAACSIM_PYTHON_EXE", sys.executable)
    env = {**os.environ, "PYTHONPATH": "src"}

    base_farm = yaml.safe_load(Path("configs/farm_khavda_block02.yaml").read_text())

    def deep(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            out[k] = deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out

    clips: list[tuple[str, Path]] = []
    t0 = time.time()

    for name, title, ts, sky in cycle:
        print(f"\n{'=' * 70}\n=== {title}  ({ts} / {sky})\n{'=' * 70}", flush=True)
        usd = stage_dir / f"{name}.usd"
        scn = stage_dir / f"{name}.yaml"

        farm_ov = {
            "layout": {"max_tables": args.tables},
            "realism": {"enabled": True},
            "sky": {"kind": sky, "procedural": True},
            "sun": {"timestamp": ts},
            # The fleet needs something to find, and the ground bot needs a reason to
            # drive somewhere. With no seeded faults the scout flags nothing and the
            # DISPATCH/CONVERGE/INSPECT beats never fire — the film would be six
            # survey sweeps over a healthy plant.
            "faults": {"rate": 0.04, "states": ["hotspot", "soiled"]},
        }
        if name == "night":
            # Trackers stow flat at night — real behaviour, and it is what makes the
            # emissive hot cells the only thing left to see.
            farm_ov["sun"]["tracker_max_rotation_deg"] = 0.0

        # A real scenario file, so each chapter is reproducible from the CLI rather
        # than only from inside this tool.
        scn.write_text(yaml.safe_dump({
            "name": f"day_cycle_{name}",
            "seed": 20260803,
            "extends": {
                "farm": "configs/farm_khavda_block02.yaml",
                "mission": "configs/mission.yaml",
            },
            "farm_overrides": farm_ov,
            "mission_overrides": {
                "mission_mode": "scout_dispatch",
                "route": "fault_zone",
                "zone_panels": args.panels,
                "zone_index": 0,
                "kinematics": {
                    "scout_standoff": 12.0,  # survey high so the frame holds context
                    "screen_standoff": 2.5,
                    "confirm_standoff": 0.8,
                    "bot_speed": 1.0, "drone_speed": 2.0,
                    "bot_cruise": 6.0, "drone_cruise": 18.0,
                },
            },
        }, sort_keys=False))

        if not (args.skip_build and usd.exists()):
            r = subprocess.run(
                [ISAAC, "-m", "solar_twin.world.farm_builder",
                 "--scenario", str(scn), "--out", str(usd)],
                env=env, capture_output=True, text=True, timeout=3600,
            )
            if r.returncode != 0 or not usd.exists():
                print(f"  [warn] {name}: build failed — {r.stderr[-300:]}", flush=True)
                continue
            for line in r.stdout.splitlines():
                if any(k in line for k in ("built ", "sky:", "racking", "tracker:")):
                    print("   " + line.strip(), flush=True)
        else:
            print(f"  reusing {usd}", flush=True)

        runs_dir = Path("runs/day_mission") / name
        r = subprocess.run(
            [ISAAC, "-m", "solar_twin.run",
             "--scenario", str(scn), "--farm-usd", str(usd),
             "--runs-dir", str(runs_dir),
             "--video", "--video-fps", str(args.fps), "--live", "--tonemap",
             "--max-panels", str(args.panels)],
            env=env, capture_output=True, text=True, timeout=7200,
        )
        for line in r.stdout.splitlines():
            if any(k in line for k in ("scout_dispatch", "demo video", "run record",
                                       "panels=", "SCOUT", "DISPATCH")):
                print("   " + line.strip(), flush=True)
        found = sorted(runs_dir.rglob("inspection.mp4"))
        if found:
            clips.append((title, found[-1]))
            print(f"  [{name}] clip: {found[-1]}", flush=True)
        else:
            print(f"  [warn] {name}: no inspection.mp4 — {r.stderr[-300:]}", flush=True)

    if not clips:
        print("no clips were produced", file=sys.stderr)
        return 1

    # Concatenate. Title cards are burned by ffmpeg's drawtext rather than rendered,
    # because a card costs nothing and re-rendering one would cost a minute.
    lst = stage_dir / "clips.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for _, c in clips))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"ffmpeg concat failed: {r.stderr[:400]}", file=sys.stderr)
        return 2

    mins = (time.time() - t0) / 60.0
    print(f"\nwrote {out}  ({len(clips)} chapters, {mins:.1f} min)")
    for title, c in clips:
        print(f"   {title:24} {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
