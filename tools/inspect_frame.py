#!/usr/bin/env python3
"""Capture the exact frames named panels produce, and report what is NOT glass.

**Isaac-bound — run under `./python.sh`.**

Written to test a claim rather than to illustrate one. `RISK-25`'s root cause was
argued from the *model's own wording* — Reason-1 described "an opaque tan or brown
patch ... along the lower edge" on healthy panels, and this is a desert plant, so
the inference was that bare ground is in frame and being read as soiling. Two
prompt interventions were then measured and both lost (see `cosmos_reason.
PROMPT_VERSION`), which makes the inference load-bearing for what to try next: if
ground really is in the frame, the fix is the camera; if it is not, the whole
diagnosis is wrong and cropping would waste a day.

So this reports, per panel and per pass, the **glass share** of the frame and what
the remaining pixels look like, and saves the PNG so the frame can simply be
looked at. Glass is selected the same way `tools/verify_shade.py` does it —
`blue > 1.15 x red`, which PV cells have and the tan desert floor does not — so the
two tools cannot disagree about what counts as a panel.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/inspect_frame.py \\
        --scenario configs/scenarios/nominal_calm_vlm.yaml \\
        --farm-usd assets/nominal_calm.usd --subset 5 \\
        --panels R243-C098,R254-C014 --out /tmp/frames

A low glass share on the **confirm** pass is the finding: that is the frame
`Perception.diagnose` actually judges, and every pixel of it that is not glass is
something the model was never asked to ignore.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Same constant as `verify_shade.py`, deliberately — one definition of "glass".
GLASS_BLUE_OVER_RED = 1.15


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--farm-usd", required=True)
    ap.add_argument("--subset", type=int, default=0)
    ap.add_argument("--panels", required=True, help="comma-separated panel ids")
    ap.add_argument("--out", default="", help="directory for the captured PNGs")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args(argv)

    import numpy as np

    from solar_twin.scenario import load_scenario
    from solar_twin.world.layout import FarmLayout
    from solar_twin.world.sim_runtime import SimRuntime

    scn = load_scenario(args.scenario)
    farm = dict(scn.farm_cfg)
    if args.subset:
        farm["layout"] = {**farm.get("layout", {}), "max_tables": args.subset}
    layout = FarmLayout(farm)
    targets = {t.panel_id: t for t in layout.inspection_targets(scn.mission_cfg)}

    wanted = [p.strip() for p in args.panels.split(",") if p.strip()]
    missing = [p for p in wanted if p not in targets]
    if missing:
        print(f"not inspection targets in this scenario: {missing}", file=sys.stderr)
        print(f"(the mission's stride decides which panels are visited)", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    rt = SimRuntime(
        args.farm_usd,
        camera_robots=["drone2"],
        marker_robots=[],
        headless=True,
        resolution=(args.width, args.height),
    )
    print(f"scenario={scn.name}  sun={farm.get('sun', {}).get('timestamp')}")
    print(
        f"{'panel':>12} {'pass':>8} {'glass%':>7} {'nonglass%':>10} "
        f"{'nonglass_mean_rgb':>20} {'verdict'}"
    )
    for pid in wanted:
        t = targets[pid]
        for tag, wp in (("screen", t.screen), ("confirm", t.confirm)):
            rt.set_pose("drone2", wp.x, wp.y, wp.z, 0.0)
            rt.step(3)
            fr = rt.capture("drone2")
            if fr is None:
                print(f"{pid:>12} {tag:>8}  NO FRAME")
                continue
            rgb = np.asarray(fr)[..., :3].astype(np.float32)
            glass = rgb[..., 2] > (GLASS_BLUE_OVER_RED * np.maximum(rgb[..., 0], 1.0))
            share = float(glass.mean())
            nonglass = ~glass
            if nonglass.sum():
                mean_rgb = rgb[nonglass].mean(axis=0)
                desc = f"({mean_rgb[0]:5.1f},{mean_rgb[1]:5.1f},{mean_rgb[2]:5.1f})"
                # Sandy = warm (red >= blue). That is the desert, and it is the
                # colour the model called soiling.
                sandy = mean_rgb[0] >= mean_rgb[2]
                verdict = "warm/sandy" if sandy else "cool"
            else:
                desc, verdict = "-", "all glass"
            print(
                f"{pid:>12} {tag:>8} {share * 100:7.2f} {float(nonglass.mean()) * 100:10.2f} "
                f"{desc:>20} {verdict}"
            )
            if out:
                try:
                    import imageio.v3 as iio

                    iio.imwrite(str(out / f"{pid}_{tag}.png"), np.asarray(fr)[..., :3])
                except Exception as exc:  # noqa: BLE001 — the numbers are the point
                    print(f"  [warn] could not save PNG: {exc}")

    rt.close()
    if out:
        print(f"\nframes written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
