#!/usr/bin/env python3
"""Prove a SWEEPING blade shadow in pixels, by watching one panel for a whole
rotor revolution.

**Isaac-bound — run under `./python.sh`.**

`tools/verify_shade.py` cannot do this job, and it is worth being precise about why
rather than adding a flag to it. That tool samples **one frame per panel across many
panels** and compares them, which is exactly right for tracker self-shading: that
shadow is always there, so a spatial comparison works and the eastmost table is a
natural control. A blade shadow is the opposite kind of stimulus — it is *present
briefly and moves*. Sampling many panels at one phase each measures the blade's
position at 273 unrelated instants, and since a covered panel is under a blade for
only ~7% of a revolution (`world/bladeshadow.py`), the signal is diluted into the
population and the tool reports "NO STIMULUS" on a stage that genuinely has one.

So this inverts the axis: **one panel, many phases.** The rotor turns, frames are
captured, and the panel's own unshaded frames are the control — the tightest control
available, since illumination, geometry, exposure and mask all stay fixed and the
only thing that changes is where the blade is.

Two things come out of it that the geometry alone cannot give:

1. **Whether the shadow is visible at all** — a shadow that is geometrically present
   but too faint at an 8.6 deg sun to move a pixel is not a perception stimulus.
2. **The measured dwell**, i.e. the fraction of frames that are darkened. That is
   directly comparable to `bladeshadow.duty_fraction`'s prediction, so this is also
   a check on the geometry model rather than only on the render.

A second, geometrically UNCOVERED panel is swept over the same phases as a spatial
control. Its brightness must stay flat; if it dips too, something other than the
blade is moving (and the measurement is invalid).

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/verify_blade_sweep.py \\
        --scenario configs/scenarios/khavda_bladeshadow.yaml \\
        --farm-usd assets/khavda_bladeshadow.usd --out runs/blade_sweep

⚠ The rotor advances a fixed 2.3 deg per `SimRuntime.step()` at 11.5 rpm
(`rpm_to_deg_per_s(rpm)/30`), so one revolution is ~157 steps. That constant is the
kinematic proxy's, not physics — this measures what the renderer shows, which is the
only thing perception ever sees.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Imported, not redefined: every tool that scores glass must use the same rule, or
# two "verified" stimuli are not comparable (see kpi/glass.py).
from solar_twin.kpi.glass import GLASS_BLUE_OVER_RED  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify a sweeping blade shadow on one panel over a rotor revolution."
    )
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--farm-usd", required=True)
    ap.add_argument("--out", default="", help="directory for the JSON + PNGs")
    ap.add_argument(
        "--steps",
        type=int,
        default=170,
        help="sim steps to sweep (~157 = one revolution at 11.5 rpm)",
    )
    ap.add_argument(
        "--pass",
        dest="which_pass",
        choices=("confirm", "screen"),
        default="confirm",
        help="which inspection waypoint to sit at. `confirm` fills ~99%% of the frame "
        "with one module's glass, so a crossing shadow is unambiguous",
    )
    ap.add_argument(
        "--save-frames",
        type=int,
        default=0,
        help="write this many PNGs, evenly spaced across the sweep (0 = none)",
    )
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args(argv)

    import numpy as np

    from solar_twin.scenario import load_scenario
    from solar_twin.world.bladeshadow import SweptShadow
    from solar_twin.world.layout import FarmLayout
    from solar_twin.world.sim_runtime import SimRuntime
    from solar_twin.world.siting import resolve_turbines
    from solar_twin.world.solar import parse_timestamp, solar_position

    scn = load_scenario(args.scenario)
    farm = scn.farm_cfg
    layout = FarmLayout(farm)
    turbines = resolve_turbines(farm, layout)
    if not turbines:
        print("no turbines on this stage — nothing can cast a blade shadow", file=sys.stderr)
        return 2

    sun = farm.get("sun", {}) or {}
    if not sun.get("timestamp"):
        print("this scenario pins no sun.timestamp — cannot derive the sun", file=sys.stderr)
        return 2
    elev, azim = solar_position(
        layout.anchor.lat0, layout.anchor.lon0, parse_timestamp(sun["timestamp"])
    )
    mount = float((farm.get("panel", {}) or {}).get("mount_height", 1.5))
    wind_dir = float(
        (farm.get("turbine_scatter", {}) or {}).get("wind_from_deg", 250.0)
    )
    shadow = SweptShadow.from_turbine(
        turbines[0],
        elevation_deg=elev,
        azimuth_deg=azim,
        target_z=mount,
        wind_dir_deg=wind_dir,
    )

    # Pick the most-shaded panel the geometry knows about, and a clearly unshaded one
    # as the spatial control. Chosen by prediction, so the pixels are being asked to
    # confirm or refute a specific claim rather than to go looking for something.
    targets = {t.panel_id: t for t in layout.inspection_targets(scn.mission_cfg)}
    best = None
    control = None
    for site in layout.sites:
        pid = site.panel_id
        if pid not in targets:
            continue
        x, y = float(site.position[0]), float(site.position[1])
        if shadow.covers(x, y):
            duty = shadow.duty_fraction(x, y, samples=180)
            if best is None or duty > best[1]:
                best = (pid, duty, x, y)
        elif control is None:
            control = (pid, 0.0, x, y)
    if best is None:
        print(
            "geometry says NO panel is under the swept blade shadow on this stage — "
            "fix the scenario before rendering anything (see world/bladeshadow.py)",
            file=sys.stderr,
        )
        return 3

    print(f"scenario={scn.name}  sun elev={elev:.2f} azim={azim:.2f}")
    print(f"  turbine {turbines[0]['pos']}  swept-disc centre "
          f"({shadow.center_x:.1f}, {shadow.center_y:.1f})")
    print(f"  target  {best[0]} at ({best[2]:.1f}, {best[3]:.1f}) predicted duty {best[1]:.4f}")
    if control:
        print(f"  control {control[0]} at ({control[2]:.1f}, {control[3]:.1f}) "
              f"(geometrically clear)")

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

    def sweep(pid: str) -> list[float]:
        """Mean glass luminance at each rotor phase, camera fixed."""
        t = targets[pid]
        wp = t.confirm if args.which_pass == "confirm" else t.screen
        rt.set_pose("drone2", wp.x, wp.y, wp.z, 0.0)
        rt.step(3)  # let the annotator catch up with the moved camera
        series: list[float] = []
        save_every = max(1, args.steps // args.save_frames) if args.save_frames else 0
        for i in range(args.steps):
            rt.step(1)  # advances the rotor 2.3 deg AND draws the frame
            fr = rt.capture("drone2")
            if fr is None:
                series.append(float("nan"))
                continue
            rgb = np.asarray(fr)[..., :3].astype(np.float32)
            glass = rgb[..., 2] > (GLASS_BLUE_OVER_RED * np.maximum(rgb[..., 0], 1.0))
            if glass.sum() < 64:
                series.append(float("nan"))
                continue
            series.append(float(rgb.mean(axis=2)[glass].mean()))
            if save_every and i % save_every == 0 and out is not None:
                try:
                    import imageio.v3 as iio

                    iio.imwrite(str(out / f"sweep_{pid}_{i:04d}.png"), np.asarray(fr)[..., :3])
                except Exception as exc:  # noqa: BLE001 — the series is the point
                    print(f"   (png write failed: {exc})", file=sys.stderr)
        return series

    def describe(pid: str, series: list[float]) -> dict:
        vals = [v for v in series if not math.isnan(v)]
        if not vals:
            return {"panel": pid, "frames": 0, "error": "no usable frames"}
        vals_sorted = sorted(vals)
        median = vals_sorted[len(vals_sorted) // 2]
        lo, hi = min(vals), max(vals)
        # "Darkened" relative to this panel's OWN typical brightness. 0.9 is a
        # deliberately loose bar: the question here is whether the render moves at
        # all, and a 10% dip on grazing-lit glass is already visible.
        dip_thresh = 0.9 * median
        darkened = sum(1 for v in vals if v < dip_thresh)
        return {
            "panel": pid,
            "frames": len(vals),
            "glass_mean_median": round(median, 3),
            "glass_mean_min": round(lo, 3),
            "glass_mean_max": round(hi, 3),
            "relative_dip": round((median - lo) / median, 4) if median else None,
            "measured_duty": round(darkened / len(vals), 4),
            "series": [round(v, 3) for v in vals],
        }

    print(f"\nsweeping {args.steps} steps (~{args.steps * 2.3:.0f} deg of rotor)...")
    target_stats = describe(best[0], sweep(best[0]))
    control_stats = describe(control[0], sweep(control[0])) if control else None

    def show(label: str, s: dict) -> None:
        if s.get("frames"):
            print(
                f"  {label:8} {s['panel']:>12}  median {s['glass_mean_median']:7.2f}  "
                f"min {s['glass_mean_min']:7.2f}  dip {100 * (s['relative_dip'] or 0):5.1f}%  "
                f"duty {s['measured_duty']:.4f}"
            )
        else:
            print(f"  {label:8} {s['panel']:>12}  {s.get('error')}")

    print()
    show("target", target_stats)
    if control_stats:
        show("control", control_stats)

    # ---- verdict ---------------------------------------------------------- #
    predicted = best[1]
    dip = target_stats.get("relative_dip") or 0.0
    ctrl_dip = (control_stats or {}).get("relative_dip") or 0.0
    has_stimulus = dip >= 0.05 and dip > 2 * ctrl_dip
    print()
    if has_stimulus:
        print(
            f"✅ STIMULUS CONFIRMED IN PIXELS: {100 * dip:.1f}% dip on the covered panel "
            f"vs {100 * ctrl_dip:.1f}% on the geometrically-clear control.\n"
            f"   measured duty {target_stats['measured_duty']:.4f} vs predicted "
            f"{predicted:.4f}."
        )
    else:
        print(
            f"⚠ NO STIMULUS IN PIXELS: the covered panel dips only {100 * dip:.1f}% "
            f"(control {100 * ctrl_dip:.1f}%).\n"
            "   The shadow may be geometrically real and still invisible — at a grazing "
            "sun the panel is lit mostly by sky, so removing the beam changes little.\n"
            "   DO NOT report a KPI-03 number from this stage."
        )

    if out:
        summary = {
            "scenario": scn.name,
            "farm_usd": args.farm_usd,
            "sun": {"timestamp": sun.get("timestamp"), "elevation_deg": round(elev, 3),
                    "azimuth_deg": round(azim, 3)},
            "turbine": turbines[0],
            "pass": args.which_pass,
            "steps": args.steps,
            "deg_per_step": 2.3,
            "glass_rule_blue_over_red": GLASS_BLUE_OVER_RED,
            "predicted_duty": round(predicted, 4),
            "target": target_stats,
            "control": control_stats,
            "has_stimulus": bool(has_stimulus),
        }
        (out / "blade_sweep.json").write_text(json.dumps(summary, indent=2))
        print(f"\nwrote {out / 'blade_sweep.json'}")

    rt.close()
    return 0 if has_stimulus else 4


if __name__ == "__main__":
    raise SystemExit(main())
