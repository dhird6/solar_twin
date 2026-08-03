#!/usr/bin/env python3
"""Verify a KPI-03 shading stimulus EXISTS before spending an hour measuring it.

**Isaac-bound — run under `./python.sh`.**

SLICE-3's lesson, twice learned: a false-fault rate of 0.00 is meaningless if the
hazard never reached the panels. The first null came from a turbine shadow that
sailed over the rows onto the ground; the second near-miss came from *scoring
whole-frame brightness*, where dark desert in frame separated "shaded" from
"unshaded" while the panels were identically lit.

So this does two things the run itself cannot:

1. Captures the **exact frames at the exact waypoints** the mission will send to
   the VLM (same `SimRuntime`, same standoffs, same camera).
2. Scores only **PV-glass pixels** — selected by the cell's blue cast
   (`blue > 2.0 x red`), which the tan desert floor and grey hardware do not
   have — so ground, sky and structure cannot be mistaken for a shaded module.
   Whole-frame statistics are also printed, precisely so the two can be compared
   and the trap stays visible rather than being quietly avoided.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/verify_shade.py \
        --scenario configs/scenarios/khavda_selfshade_lowsun.yaml \
        --farm-usd assets/khavda_selfshade_lowsun.usd --subset 5 --out runs/<ts>

Expect the tables with an eastern neighbour to read well above the eastmost
(control) table. If they do not, the stimulus is absent — fix the geometry, do
not report the KPI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ⚠ Imported, not redefined: both of these must be identical in every tool that
# scores glass. The ratio was 1.15 and a physically-based sky measurably broke it
# (shadowed sand is sky-lit, so it turns blue and passes). See kpi/glass.py.
from solar_twin.kpi.glass import (  # noqa: E402
    DARK_FRACTION_OF_BRIGHT,
    GLASS_BLUE_OVER_RED,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--farm-usd", required=True)
    ap.add_argument("--subset", type=int, default=0, help="first N tracker tables")
    ap.add_argument("--out", default="", help="directory for the captured PNGs")
    ap.add_argument(
        "--col",
        type=int,
        default=50,
        help="which module along each table to sample (mid-table by default — the "
        "row ends see sky past the neighbour and are not representative)",
    )
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

    # One module per distinct row, ordered by **stage x**, not by row index:
    # panel-row numbering does not run west-to-east on this site (R258 is the
    # westmost table, R243 the eastmost), and picking the control by row number
    # compares the wrong panel — which is how this tool first reported "no
    # stimulus" on a stage that had a 23-point one.
    rows: dict[int, tuple[float, str]] = {}
    for site in layout.sites:
        if site.col == args.col:
            rows.setdefault(site.row, (float(site.position[0]), site.panel_id))
    # The sun is in the EAST, so each table is shaded by its eastern neighbour and
    # the largest-x table has none: that one is the control.
    panels = [pid for _, (_, pid) in sorted(rows.items(), key=lambda kv: kv[1][0])]
    if not panels:
        print(f"no panels at col={args.col}", file=sys.stderr)
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
        f"{'panel':>12} {'pass':>8} {'glass%':>7} {'glass_mean':>10} "
        f"{'glass_dark%':>11} | {'frame_mean':>10} {'frame_dark%':>11}"
    )
    results = []
    detail = []  # (panel, pass, glass_dark, glass_share) for the JSON sidecar
    for pid in panels:
        t = targets[pid]
        for tag, wp in (("screen", t.screen), ("confirm", t.confirm)):
            rt.set_pose("drone2", wp.x, wp.y, wp.z, 0.0)
            rt.step(3)
            fr = rt.capture("drone2")
            if fr is None:
                print(f"{pid:>12} {tag:>8}  NO FRAME")
                continue
            rgb = np.asarray(fr)[..., :3].astype(np.float32)
            lum = rgb.mean(axis=2)
            glass = rgb[..., 2] > (GLASS_BLUE_OVER_RED * np.maximum(rgb[..., 0], 1.0))
            share = float(glass.mean())
            if glass.sum() < 64:
                # Too little glass in frame to say anything — report it rather
                # than divide by a handful of pixels.
                print(f"{pid:>12} {tag:>8} {share * 100:7.2f}   <64 glass px — no verdict")
                continue
            gl = lum[glass]
            bright = float(np.percentile(gl, 90))
            g_dark = float((gl < bright * DARK_FRACTION_OF_BRIGHT).mean())
            f_dark = float((lum < float(np.percentile(lum, 90)) * DARK_FRACTION_OF_BRIGHT).mean())
            print(
                f"{pid:>12} {tag:>8} {share * 100:7.2f} {gl.mean():10.1f} "
                f"{g_dark * 100:11.1f} | {lum.mean():10.1f} {f_dark * 100:11.1f}"
            )
            results.append((pid, tag, g_dark))
            detail.append((pid, tag, g_dark, share))
            if out:
                try:
                    import imageio.v3 as iio

                    iio.imwrite(str(out / f"shade_{pid}_{tag}.png"), np.asarray(fr)[..., :3])
                except Exception as exc:  # noqa: BLE001 — the numbers are the point
                    print(f"   (png write failed: {exc})", file=sys.stderr)

    screens = [(pid, d) for pid, tag, d in results if tag == "screen"]
    if len(screens) >= 2:
        # `panels` is x-ordered, so the last entry is the eastmost table — the one
        # with no occluder up-sun.
        control_pid, control = screens[-1]
        shaded = [d for pid, d in screens[:-1]]
        print(
            f"\ncontrol {control_pid} (no eastern neighbour): "
            f"{control * 100:.1f}% dark glass\n"
            f"shaded rows: {', '.join(f'{d * 100:.1f}%' for d in shaded)}\n"
            f"differential vs control: "
            f"{(sum(shaded) / len(shaded) - control) * 100:+.1f} points"
        )
        differential = sum(shaded) / len(shaded) - control
        if differential < 0.05:
            print(
                "⚠ NO STIMULUS: the shaded rows are not meaningfully darker than "
                "the control. Do not report a KPI-03 number from this stage."
            )
        # ⭐ ARCHIVE THE NUMBERS, not just the PNGs. Until 2026-07-31 this printed
        # to a console and saved images, so a differential could only be re-checked
        # by someone re-deriving it from the frames by hand -- which is exactly how
        # a mask artifact survived long enough to be written into a session log as
        # a headline. The mask share is recorded on purpose: it is the field that
        # would have caught it, since a share near 1.0 means "% dark glass" has
        # degenerated into whole-frame brightness.
        if out:
            import json

            summary = {
                "scenario": scn.name,
                "sun": farm.get("sun", {}).get("timestamp"),
                "farm_usd": args.farm_usd,
                "glass_rule_blue_over_red": GLASS_BLUE_OVER_RED,
                "dark_fraction_of_bright": DARK_FRACTION_OF_BRIGHT,
                "control_panel": control_pid,
                "control_dark": control,
                "shaded_dark": shaded,
                "differential": differential,
                "has_stimulus": bool(differential >= 0.05),
                "per_pass": [
                    {"panel": pid, "pass": tag, "glass_share": sh, "glass_dark": d}
                    for pid, tag, d, sh in detail
                ],
            }
            (out / "stimulus.json").write_text(json.dumps(summary, indent=2))
            print(f"\nwrote {out / 'stimulus.json'}")
    rt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
