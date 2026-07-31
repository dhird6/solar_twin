#!/usr/bin/env python3
"""Prove a built stage's ground is REAL DEM terrain, in pixels and in numbers.

**Isaac-bound — run under `./python.sh`, from the repo root** (farm configs carry
relative paths, so CWD must be the repo).

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/terrain_proof.py \\
        --usd assets/khavda_block02_terrain.usd \\
        --farm configs/farm_khavda_block02.yaml --out runs/terrain_block02

Why this exists rather than a screenshot: **Khavda is genuinely almost flat.**
BLOCK-02 carries 2.2 m of relief over 1.1 x 1.4 km — a 0.16% grade. A photograph
of a 0.16% grade looks exactly like a photograph of a plane, so "it looks hilly"
is not available as evidence and anyone claiming it should be disbelieved. What IS
visible is the effect the terrain has on the *hardware*: tracker tables are rigid
beams fitted as straight lines through the grade (`world/dem.fit_line`), so along a
raking shot their tops step up and down against the horizon by the amount the
ground moves under them.

So this writes three artifacts, and says which is a photo and which is a plot:

* `raking_*.png` — RENDERED FRAMES from the sim, long lens, camera at torque-tube
  height looking down the array. The proof is the panel tops departing from a
  straight line, not a visible hill.
* `heightmap.png` — a PLOT, not a photo: the ground mesh's own vertices coloured
  by z, read back out of the built USD. This is what the renderer was handed.
* `terrain_proof.json` — the numbers, so the claim is quotable: mesh z range,
  panel z range, distinct values, and the DEM patch the stage resolved.

⚠ A flat stage still renders a pretty picture. The falsifiable part is
`ground_mesh.z_delta_m`: `terrain.kind: flat` returns 0.0 everywhere, so a flat
build scores an EXACT 0.0 and this tool says FLAT. That is the check — the images
are for looking at, the JSON is for arguing with.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

#: Below this the ground is flat to within float noise and no renderer will show
#: anything. Deliberately tiny: the point is to separate "0.0 by construction"
#: from "a real, small, desert relief", not to assert the relief is impressive.
FLAT_EPSILON_M = 1e-6


def _stats(zs) -> dict:
    import numpy as np

    z = np.asarray(zs, dtype=float)
    return {
        "n": int(z.size),
        "min_m": round(float(z.min()), 4),
        "max_m": round(float(z.max()), 4),
        "delta_m": round(float(z.max() - z.min()), 4),
        "stdev_m": round(float(z.std()), 4),
        "distinct": int(np.unique(np.round(z, 4)).size),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usd", required=True, help="stage built by world.farm_builder")
    ap.add_argument("--farm", required=True, help="the farm.yaml that built it")
    ap.add_argument("--out", default="runs/terrain_proof")
    ap.add_argument(
        "--diff-against",
        default="",
        help="directory of renders from a `terrain.kind: flat` build of the SAME "
        "config. Every shot is differenced against its namesake there, so the "
        "proof is 'these pixels moved because of terrain' rather than 'this looks "
        "hilly' — which a 0.16%% grade never will.",
    )
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import yaml

    farm_cfg = yaml.safe_load(Path(args.farm).read_text())
    terrain = farm_cfg.get("terrain") or {}
    report: dict = {
        "usd": args.usd,
        "farm": args.farm,
        "terrain_spec": {
            k: terrain.get(k) for k in ("kind", "path", "datum", "graded")
        },
    }

    # --- what the LAYOUT computed, before any renderer touched it ------------- #
    from solar_twin.world.layout import FarmLayout, terrain_feature_step

    layout = FarmLayout(farm_cfg)
    report["panels"] = _stats([s.position[2] for s in layout.sites])
    report["terrain_feature_step_m"] = round(float(terrain_feature_step(farm_cfg)), 3)

    # --- open the stage; pxr only AFTER SimulationApp, or Isaac's schema ------ #
    # --- extensions are unregistered and the app dies on startup -------------- #
    from solar_twin.world.sim_runtime import SimRuntime

    rt = SimRuntime(
        args.usd,
        camera_robots=[],
        marker_robots=[],
        headless=True,
        resolution=(args.width, args.height),
        overview_resolution=(args.width, args.height),
        overview_pose=(0.0, 0.0, 100.0),
    )
    from pxr import Gf, Usd, UsdGeom  # noqa: PLC0415

    stage = rt.stage

    # --- what the RENDERER was handed ----------------------------------------- #
    ground = stage.GetPrimAtPath("/World/Ground")
    if not ground or not ground.IsValid():
        raise SystemExit("no /World/Ground on this stage — nothing to prove")
    pts = np.array(UsdGeom.Mesh(ground).GetPointsAttr().Get())
    report["ground_mesh"] = _stats(pts[:, 2])
    report["ground_mesh"]["x_span_m"] = [
        round(float(pts[:, 0].min()), 1),
        round(float(pts[:, 0].max()), 1),
    ]
    report["ground_mesh"]["y_span_m"] = [
        round(float(pts[:, 1].min()), 1),
        round(float(pts[:, 1].max()), 1),
    ]
    is_flat = report["ground_mesh"]["delta_m"] <= FLAT_EPSILON_M
    report["verdict"] = "FLAT" if is_flat else "REAL RELIEF"

    # --- the heightmap PLOT (the data, honestly labelled as a plot) ----------- #
    # Rendered with PIL rather than matplotlib: matplotlib is not in Isaac's
    # bundled Python and must not be installed into it (see docs/ENVIRONMENT.md).
    from PIL import Image, ImageDraw

    farm = stage.GetPrimAtPath("/World/Farm")
    rng = (
        UsdGeom.BBoxCache(
            Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
        )
        .ComputeWorldBound(farm)
        .ComputeAlignedRange()
    )
    bounds = (rng.GetMin()[0], rng.GetMin()[1], rng.GetMax()[0], rng.GetMax()[1])
    report["farm_bounds"] = [round(float(v), 1) for v in bounds]

    # Clip the plot to the hardware footprint plus a margin. The mesh reaches the
    # sky dome kilometres out, and at that scale the plant is four pixels.
    pad = 250.0
    bx0, by0, bx1, by1 = bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad
    sel = (
        (pts[:, 0] >= bx0) & (pts[:, 0] <= bx1) & (pts[:, 1] >= by0) & (pts[:, 1] <= by1)
    )
    near = pts[sel]
    report["ground_mesh_over_footprint"] = _stats(near[:, 2])

    W, H = 900, int(900 * (by1 - by0) / max(1.0, (bx1 - bx0)))
    H = max(200, min(H, 1600))
    img = Image.new("RGB", (W, H), (10, 10, 14))
    dr = ImageDraw.Draw(img)
    zmin, zmax = float(near[:, 2].min()), float(near[:, 2].max())
    zrange = max(1e-9, zmax - zmin)
    # One filled cell per mesh vertex — the mesh is coarse (20 m posts) and that
    # coarseness is the honest picture of what the DEM actually carries.
    cw = max(2.0, W / max(2.0, len(np.unique(np.round(near[:, 0], 1)))))
    ch = max(2.0, H / max(2.0, len(np.unique(np.round(near[:, 1], 1)))))
    for px, py, pz in near:
        u = (px - bx0) / (bx1 - bx0) * W
        v = H - (py - by0) / (by1 - by0) * H
        t = (pz - zmin) / zrange
        # Blue (low) -> sand (high). Chosen so it cannot be mistaken for a render.
        col = (int(40 + 200 * t), int(60 + 130 * t), int(190 - 130 * t))
        dr.rectangle([u - cw / 2, v - ch / 2, u + cw / 2, v + ch / 2], fill=col)
    dr.rectangle(
        [
            (bounds[0] - bx0) / (bx1 - bx0) * W,
            H - (bounds[3] - by0) / (by1 - by0) * H,
            (bounds[2] - bx0) / (bx1 - bx0) * W,
            H - (bounds[1] - by0) / (by1 - by0) * H,
        ],
        outline=(255, 255, 255),
        width=2,
    )
    dr.text(
        (8, 8),
        f"PLOT (not a render): /World/Ground vertices by z\n"
        f"{zmin:.3f} .. {zmax:.3f} m  (delta {zrange:.3f} m)\n"
        f"white box = hardware footprint",
        fill=(255, 255, 255),
    )
    heightmap = out / "heightmap.png"
    img.save(heightmap)
    print(f"  wrote {heightmap}", flush=True)

    # --- the RENDERED raking shots -------------------------------------------- #
    cam_prim = stage.GetPrimAtPath("/World/Overview")
    cam = UsdGeom.Camera(cam_prim)
    api = UsdGeom.XformCommonAPI(cam_prim)
    cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 20000.0))
    cam.CreateHorizontalApertureAttr().Set(36.0)

    cx = (bounds[0] + bounds[2]) / 2.0
    span_y = bounds[3] - bounds[1]
    # ⚠ Read this before judging the images: BLOCK-02 has 2.2 m of relief over
    # 1.4 km — a **0.16% grade**. No camera angle makes that look like a hill,
    # and a shot that seems to is lying. The shots below are chosen to make the
    # terrain's effect *measurable*, not dramatic:
    #
    # * `compressed_*` — a long lens from far off-site. Compressing 647 m of
    #   tables into one frame stacks their tops, so a departure from a straight
    #   line is the terrain and nothing else.
    # * `raking_*` — camera AT torque-tube height, so the tops sit on the horizon.
    #
    # The real proof is `--diff-against` a `kind: flat` build of the same config:
    # identical seed, identical hardware, identical camera, so every pixel that
    # differs differs *because of terrain*.
    shots = [
        ("compressed_north", cx, bounds[1] - 700.0, 4.0, 90.0, 0.0, 200.0),
        ("compressed_south", cx, bounds[3] + 700.0, 4.0, 90.0, 180.0, 200.0),
        ("raking_north", cx, bounds[1] - 40.0, 1.9, 90.0, 0.0, 85.0),
        ("raking_south", cx, bounds[3] + 40.0, 1.9, 90.0, 180.0, 85.0),
        ("raking_low_wide", cx, bounds[1] - 90.0, 3.2, 89.0, 0.0, 35.0),
        ("aerial", cx, (bounds[1] + bounds[3]) / 2.0 - 0.55 * span_y, 190.0, 62.0, 0.0, 24.0),
    ]

    # RTX needs a few frames to build its pipeline; absorb that rather than
    # losing a real shot to it (the same warm-up flythrough.py does).
    for _ in range(8):
        rt.step(1)
        rt.capture_overview()

    from PIL import Image as PILImage

    shot_paths = []
    for name, x, y, z, pitch, heading, focal in shots:
        api.SetTranslate(Gf.Vec3d(float(x), float(y), float(z)))
        api.SetRotate(
            (float(pitch), 0.0, float(-heading)), UsdGeom.XformCommonAPI.RotationOrderXYZ
        )
        cam.GetFocalLengthAttr().Set(float(focal))
        frame = None
        for _ in range(6):  # a dropped frame is a miss, not a result
            rt.step(1)
            frame = rt.capture_overview()
            if frame is not None:
                break
        if frame is None:
            print(f"  [warn] {name}: renderer returned no frame", flush=True)
            continue
        p = out / f"{name}.png"
        PILImage.fromarray(np.asarray(frame)[:, :, :3]).save(p)
        shot_paths.append(str(p))
        print(f"  wrote {p}", flush=True)

    report["renders"] = shot_paths
    report["heightmap"] = str(heightmap)

    # --- the A/B: this stage against a `kind: flat` build of the same config --- #
    # Same seed, same 30,016 modules, same camera. Every pixel that differs
    # differs BECAUSE OF TERRAIN — which is how you show a 0.16% grade to an eye
    # that cannot see one.
    if args.diff_against:
        ref_dir = Path(args.diff_against)
        diffs = {}
        for p in shot_paths:
            name = Path(p).name
            ref = ref_dir / name
            if not ref.exists():
                print(f"  [warn] no reference frame {ref}", flush=True)
                continue
            a = np.asarray(PILImage.open(p).convert("RGB"), dtype=np.int16)
            b = np.asarray(PILImage.open(ref).convert("RGB"), dtype=np.int16)
            if a.shape != b.shape:
                print(f"  [warn] {name}: shape mismatch {a.shape} vs {b.shape}", flush=True)
                continue
            d = np.abs(a - b).max(axis=2)
            changed = float((d > 8).mean())  # 8/255: past encoder + RTX dither
            diffs[name] = {
                "pixels_changed_frac": round(changed, 4),
                "max_abs_diff": int(d.max()),
                "mean_abs_diff": round(float(d.mean()), 3),
            }
            # Amplified so a real but small difference is visible at a glance.
            amp = np.clip(d.astype(np.float32) * 6.0, 0, 255).astype(np.uint8)
            PILImage.fromarray(amp).save(out / f"diff_{name}")
        report["diff_vs_flat"] = diffs
        report["diff_reference"] = str(ref_dir)
        if diffs:
            worst = max(diffs.values(), key=lambda v: v["pixels_changed_frac"])
            report["diff_verdict"] = (
                "TERRAIN CHANGES THE RENDER"
                if worst["pixels_changed_frac"] > 0.01
                else "NO VISIBLE DIFFERENCE — suspect the terrain is not reaching the render"
            )

    (out / "terrain_proof.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    print(
        f"\nVERDICT: {report['verdict']} — ground mesh spans "
        f"{report['ground_mesh']['delta_m']} m over {report['ground_mesh']['n']} verts; "
        f"panels span {report['panels']['delta_m']} m",
        flush=True,
    )
    rt.close()
    return 0 if not is_flat else 1


if __name__ == "__main__":
    raise SystemExit(main())
