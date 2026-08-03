#!/usr/bin/env python3
"""Can visual SLAM work on a solar farm AT ALL? Measure before building.

⚠⚠ **The risk this exists to test, stated up front.** A utility PV plant is close to
the worst case for visual SLAM: several hundred identical tables at an identical
pitch, over flat low-texture desert, with almost no distinctive landmarks. Visual
odometry needs features that are (a) plentiful, (b) *distinctive* enough to match
without ambiguity, and (c) well spread. A field of repeating panels can fail (b)
catastrophically — every panel corner looks like every other panel corner, so a
matcher aliases one row onto the next and the pose estimate jumps a whole row pitch.

Building the ROS 2 + cuVSLAM stack first and discovering that afterwards would repeat
`SC-05` and the blade-shadow penumbra: months of correct engineering against a
stimulus that was never there. This measures the scene first, from OUR stage, at OUR
inspection altitude.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/slam_feasibility.py assets/khavda_block02_real.usd
    PYTHONPATH=src $ISAAC tools/slam_feasibility.py assets/khavda_block02_real.usd \\
        --alt 12 --baseline 0.30 --frames 8

## What it measures, and what each number decides

* **features/frame** (ORB). Below ~150 a VSLAM front end is starving; cuVSLAM's own
  guidance is that a few hundred well-spread features is a working regime.
* **match ratio between consecutive frames.** Frame-to-frame tracking IS visual
  odometry; if consecutive frames along a survey line do not match, nothing downstream
  can work.
* **⭐ aliasing rate.** The one that matters here. For each feature, compare the best
  match against the SECOND best (Lowe's ratio). A repeating structure produces many
  near-ties — a high ambiguous fraction means the matcher cannot tell which panel it
  is looking at. This is the failure mode a feature COUNT completely hides.
* **stereo disparity validity.** What fraction of pixels get a usable depth from the
  pair, which bounds how much structure cuVSLAM could triangulate.

Every number is measured on rendered frames from the built stage; nothing here is
quoted from a datasheet or a paper.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("usd", help="a stage built by world.farm_builder")
    ap.add_argument("--alt", type=float, default=12.0, help="survey altitude, m AGL")
    ap.add_argument("--baseline", type=float, default=0.30, help="stereo baseline, m")
    ap.add_argument("--frames", type=int, default=6, help="frames along the survey line")
    ap.add_argument("--step", type=float, default=4.0, help="metres between frames")
    ap.add_argument("--start", type=float, nargs=3, default=(60.0, 60.0, 0.0))
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args(argv)

    if not Path(args.usd).exists():
        raise SystemExit(f"{args.usd} not found — build it first")

    from solar_twin.world.sim_runtime import SimRuntime

    runtime = SimRuntime(
        args.usd,
        camera_robots=["slam_probe"],
        marker_robots=[],
        headless=True,
        resolution=(1280, 720),
        overview_capture=False,
        tonemap=True,
        stereo_baseline_m=args.baseline,
    )

    import cv2
    import numpy as np

    orb = cv2.ORB_create(nfeatures=2000)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    def gray(frame):
        arr = np.asarray(frame)
        if arr.ndim == 3 and arr.shape[2] >= 3:
            return cv2.cvtColor(arr[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        return arr.astype(np.uint8)

    x0, y0, _ = args.start
    rows = []
    prev_desc = prev_kp = None

    for i in range(args.frames):
        x = x0 + i * args.step
        runtime.set_pose("slam_probe", x, y0, args.alt, yaw=0.0)
        pair = runtime.capture_stereo("slam_probe")
        if pair is None:
            raise SystemExit("no stereo pair — is --baseline > 0?")
        left, right = pair
        gl, gr = gray(left), gray(right)

        kp, desc = orb.detectAndCompute(gl, None)
        n_feat = 0 if desc is None else len(kp)

        # ⭐ Aliasing: for each feature, how close is the 2nd-best match to the best?
        # A repeating field of panels makes them near-identical, and THAT is what
        # breaks a matcher — not a shortage of corners.
        ambiguous = matched = 0
        if prev_desc is not None and desc is not None and len(desc) > 2:
            knn = matcher.knnMatch(desc, prev_desc, k=2)
            for pair_m in knn:
                if len(pair_m) < 2:
                    continue
                best, second = pair_m
                matched += 1
                # Lowe's ratio test: >0.75 means the second candidate is nearly as
                # good, i.e. the match is not trustworthy.
                if second.distance > 0 and best.distance / second.distance > 0.75:
                    ambiguous += 1
        alias = (ambiguous / matched) if matched else float("nan")
        good = matched - ambiguous

        # Stereo disparity validity.
        sgbm = cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=96, blockSize=7,
            P1=8 * 7 * 7, P2=32 * 7 * 7, uniquenessRatio=10,
        )
        disp = sgbm.compute(gl, gr).astype(np.float32) / 16.0
        valid = float((disp > 0).mean())

        # Spread: fraction of an 8x8 image grid containing at least one feature. A
        # cluster of 500 features in one corner is not the same as 500 spread out.
        spread = 0.0
        if kp:
            h, w = gl.shape
            cells = {(int(k.pt[1] / h * 8), int(k.pt[0] / w * 8)) for k in kp}
            spread = len(cells) / 64.0

        rows.append({
            "frame": i, "x": round(x, 2),
            "features": n_feat,
            "spread": round(spread, 3),
            "matched": matched,
            "good_matches": good,
            "aliasing_rate": None if matched == 0 else round(alias, 4),
            "disparity_valid": round(valid, 4),
            "mean_intensity": round(float(gl.mean()), 1),
        })
        prev_desc, prev_kp = desc, kp

    # ⚠ EVERYTHING must be printed/written BEFORE close(). Isaac launches with
    # `--/app/fastShutdown=True`, so `SimulationApp.close()` tears the process down
    # immediately and any statement after it silently never runs — measured: this
    # tool exited 0 having produced no output at all.
    print(f"\n  {Path(args.usd).name} @ {args.alt:.0f} m AGL, baseline {args.baseline:.2f} m")
    print(f"  {'frame':>5} {'feats':>6} {'spread':>7} {'matched':>8} {'good':>6} "
          f"{'aliasing':>9} {'disp_ok':>8} {'mean':>6}")
    for r in rows:
        al = "n/a" if r["aliasing_rate"] is None else f"{r['aliasing_rate']:.1%}"
        print(f"  {r['frame']:>5} {r['features']:>6} {r['spread']:>7.2f} "
              f"{r['matched']:>8} {r['good_matches']:>6} {al:>9} "
              f"{r['disparity_valid']:>7.1%} {r['mean_intensity']:>6.1f}")

    scored = [r for r in rows if r["aliasing_rate"] is not None]
    mean_feat = sum(r["features"] for r in rows) / max(1, len(rows))
    mean_alias = sum(r["aliasing_rate"] for r in scored) / max(1, len(scored))
    mean_good = sum(r["good_matches"] for r in scored) / max(1, len(scored))
    mean_disp = sum(r["disparity_valid"] for r in rows) / max(1, len(rows))

    print(f"\n  features/frame   {mean_feat:8.0f}   (<150 starves a VSLAM front end)")
    print(f"  aliasing rate    {mean_alias:8.1%}   (⭐ high = repeating panels are "
          f"indistinguishable)")
    print(f"  good matches     {mean_good:8.0f}   (unambiguous frame-to-frame links)")
    print(f"  disparity valid  {mean_disp:8.1%}   (pixels with usable stereo depth)")

    verdict = []
    if mean_feat < 150:
        verdict.append("FEATURE-STARVED: too few corners to track")
    if mean_alias > 0.6:
        verdict.append(
            "SEVERE ALIASING: most matches are ambiguous — a matcher cannot tell one "
            "panel row from the next, which is the degenerate case for this scene"
        )
    if mean_good < 50:
        verdict.append("TOO FEW UNAMBIGUOUS MATCHES to estimate motion frame-to-frame")
    if mean_disp < 0.2:
        verdict.append("SPARSE DEPTH: the stereo pair recovers little structure")
    print("\n  VERDICT: " + ("; ".join(verdict) if verdict else
                             "no blocker found at this altitude — VSLAM is worth trying"))
    print("  ⚠ MEASURED on rendered frames from this stage; not a flight test.\n")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "usd": args.usd, "altitude_m": args.alt, "baseline_m": args.baseline,
            "frames": rows,
            "mean_features": mean_feat, "mean_aliasing_rate": mean_alias,
            "mean_good_matches": mean_good, "mean_disparity_valid": mean_disp,
            "verdict": verdict or ["no blocker found"],
        }, indent=2))
        print(f"  wrote {out}\n")

    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
