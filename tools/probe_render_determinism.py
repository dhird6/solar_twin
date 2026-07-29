#!/usr/bin/env python3
"""Is a capture from a fixed camera pose bit-reproducible? (`RISK-24`)

**Isaac-bound — run under `./python.sh`.**

`run.py --repeat` records a digest of every frame the VLM judged, and a low-sun
repeat set showed **40/40 panels rendered differently between repeats** while
every verdict stayed identical. That raises the question this probe answers: is
the difference imperceptible sampling jitter (a progressively-refined RTX buffer
that never converges to the same bits), or are the frames materially different?
The distinction matters — the first makes a stable KPI evidence of robustness to
noise, the second means repeats are not comparing like with like at all.

Three measurements, on one stage, one camera, no mission:

1. **Hold still.** N captures without moving. Any difference here is the
   renderer alone — nothing in the scene changed.
2. **Leave and return.** Move away, come back to the same pose, capture.
   Adds camera-transform round-tripping to the above.
3. **Warm-up sensitivity.** Captures after 1 vs. several `step()`s, to show
   whether extra settling frames converge the image.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/probe_render_determinism.py \
        --farm-usd assets/khavda_selfshade_lowsun.usd --x 7.1 --y 60 --z 5
"""

from __future__ import annotations

import argparse
import hashlib


def _digest(frame) -> str:
    return hashlib.sha256(frame.tobytes()).hexdigest()[:16]


def _thumb(frame):
    # The production thumbnail, so this probe validates what the pipeline uses.
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "src"))
    from solar_twin.perception.base import frame_thumbnail

    return frame_thumbnail(frame)


def _thumb_delta(thumbs, np):
    """(max, mean) absolute per-cell difference vs the first thumbnail, in LSB.
    This is the quantity `kpi.variance.THUMB_TOLERANCE` is set against."""
    vals = [np.frombuffer(bytes.fromhex(t), dtype=np.uint8).astype(np.int16)
            for t in thumbs if t]
    if len(vals) < 2:
        return (0.0, 0.0)
    diffs = [np.abs(v - vals[0]) for v in vals[1:]]
    return (float(max(d.max() for d in diffs)), float(max(d.mean() for d in diffs)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--farm-usd", required=True)
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--z", type=float, required=True)
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--steps", type=int, default=1, help="step()s before each capture")
    ap.add_argument("--other-x", type=float, default=None,
                    help="a pose showing a DIFFERENT picture (e.g. the unshaded "
                         "control table) — measures the far side of the gap")
    ap.add_argument("--other-y", type=float, default=0.0)
    ap.add_argument("--other-z", type=float, default=0.0)
    args = ap.parse_args(argv)

    import numpy as np

    from solar_twin.world.sim_runtime import SimRuntime

    rt = SimRuntime(
        args.farm_usd,
        camera_robots=["drone2"],
        marker_robots=[],
        headless=True,
        resolution=(640, 480),
    )

    def grab(steps: int):
        rt.step(steps)
        fr = rt.capture("drone2")
        return None if fr is None else np.asarray(fr)[..., :3].astype(np.int16)

    def report(label: str, frames: list) -> None:
        frames = [f for f in frames if f is not None]
        if len(frames) < 2:
            print(f"{label}: fewer than 2 frames captured")
            return
        digests = [_digest(f.astype(np.uint8)) for f in frames]
        thumbs = [_thumb(f.astype(np.uint8)) for f in frames]
        base = frames[0]
        diffs = [np.abs(f - base) for f in frames[1:]]
        print(
            f"{label}\n"
            f"  distinct EXACT digests:  {len(set(digests))}/{len(frames)}  {digests}\n"
            f"  distinct THUMBNAILS:     {len(set(thumbs))}/{len(frames)}  "
            f"(max cell delta {_thumb_delta(thumbs, np)[0]:.3f}, "
            f"mean cell delta {_thumb_delta(thumbs, np)[1]:.4f} LSB)\n"
            f"  vs frame 0: max abs delta {max(int(d.max()) for d in diffs)}, "
            f"mean abs delta {max(float(d.mean()) for d in diffs):.4f}, "
            f"pixels changed {max(float((d.max(axis=2) > 0).mean()) for d in diffs) * 100:.2f}%"
        )

    rt.set_pose("drone2", args.x, args.y, args.z, 0.0)
    report("1. HOLD STILL (renderer alone)", [grab(args.steps) for _ in range(args.n)])

    frames = []
    for _ in range(args.n):
        rt.set_pose("drone2", args.x + 50.0, args.y + 50.0, args.z + 10.0, 0.0)
        rt.step(args.steps)
        rt.set_pose("drone2", args.x, args.y, args.z, 0.0)
        frames.append(grab(args.steps))
    report("2. LEAVE AND RETURN (adds transform round-trip)", frames)

    rt.set_pose("drone2", args.x, args.y, args.z, 0.0)
    report("3. AFTER 10 SETTLING STEPS (does it converge?)",
           [grab(10) for _ in range(args.n)])

    # 4. The OTHER side of the gap: a genuinely different picture. Without this
    # the noise figures above cannot justify any tolerance — a threshold needs
    # both the largest difference that means nothing and the smallest that means
    # something. `--other-*` should name a pose whose panel is in a different
    # shading state (e.g. the unshaded control table).
    if args.other_x is not None:
        base = grab(args.steps)
        rt.set_pose("drone2", args.other_x, args.other_y, args.other_z, 0.0)
        other = grab(args.steps)
        report("4. A DIFFERENT PANEL (what a real difference measures)",
               [base, other])

    rt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
