#!/usr/bin/env python3
"""Render one wide establishing pass over a stage, with a labelled card (Isaac-bound).

**Isaac-bound — run under `./python.sh`.**

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/render_establisher.py \
        --usd assets/khavda_4block.usd --out assets/establisher.mp4

## Why this exists separately from the mission film

`render_day_mission.py` shoots a `scout_dispatch` mission per time of day, and a
mission only ever visits ~10 panels — so on a big stage most of the plant is backdrop
and the extra geometry buys nothing but render time. Measured: the day-cycle film was
shot on ONE block at 120 tables precisely because six live missions × ~10× the
geometry is hours, not minutes.

This is the other half of that trade: **one camera pass, no fleet, no perception**, on
the largest stage that exists. It costs a couple of hundred frames rather than six
missions, and spliced in front of the film it gives the scale the mission chapters
cannot show. `render_day_mission.py --assemble-only --prepend` does the splice without
re-rendering a single mission frame.

Nothing here is new machinery: `flythrough.interpolate` for the eased camera,
`tour.annotate`/`card` for the labels, `recorder.RunRecorder` for streaming.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usd", required=True)
    ap.add_argument("--out", default="assets/establisher.mp4")
    ap.add_argument("--seconds", type=float, default=14.0, help="pass duration")
    ap.add_argument("--card-seconds", type=float, default=4.0)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--title", default="THE PLANT")
    ap.add_argument("--subtitle", default="")
    args = ap.parse_args(argv)

    usd = Path(args.usd)
    if not usd.exists():
        raise SystemExit(f"no such stage: {usd}")

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": "RaytracedLighting",
                         "width": args.width, "height": args.height})

    import carb
    import numpy as np
    import omni.replicator.core as rep
    import omni.usd
    from pxr import Gf, Usd, UsdGeom

    from solar_twin.world.flythrough import Key, interpolate
    from solar_twin.world.recorder import RunRecorder
    from solar_twin.world.tour import BUILT, INFERRED, Chapter, Item, annotate, card

    canvas = (args.width, args.height)
    omni.usd.get_context().open_stage(str(usd))
    for _ in range(120):
        app.update()
    stage = omni.usd.get_context().get_stage()

    # The measured photographic exposure, applied AFTER open_stage or the renderer
    # resets it (see docs/ENVIRONMENT.md § Looks).
    settings = carb.settings.get_settings()
    for k, v in {
        "/rtx/post/tonemap/fNumber": 9.0,
        "/rtx/post/tonemap/cameraShutter": 50.0,
        "/rtx/post/tonemap/filmIso": 100.0,
        "/rtx/post/histogram/enabled": False,
        "/rtx/reflections/maxReflectionBounces": 3,
    }.items():
        settings.set(k, v)

    farm = stage.GetPrimAtPath("/World/Farm")
    rng = (
        UsdGeom.BBoxCache(
            Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
        )
        .ComputeWorldBound(farm)
        .ComputeAlignedRange()
    )
    lo, hi = rng.GetMin(), rng.GetMax()
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    span = max(hi[0] - lo[0], hi[1] - lo[1])
    panels = sum(1 for p in stage.Traverse() if p.GetName().startswith("Panel_"))
    tables = len({
        n.split("_")[1] for n in
        (p.GetName() for p in stage.Traverse() if p.GetName().startswith("Panel_"))
        if "_" in n
    })
    print(f"stage {usd.name}: {panels:,} modules / {tables:,} tables, "
          f"{hi[0] - lo[0]:.0f} x {hi[1] - lo[1]:.0f} m", flush=True)

    # High, slow, and pulling back — the shot whose whole job is scale. Derived from
    # the bbox so it frames whatever stage it is handed.
    third = args.seconds / 3.0
    keys = interpolate([
        Key(cx, lo[1] - span * 0.30, span * 0.20, 66.0, 0.0, 20.0, third, "in"),
        Key(cx, cy - span * 0.30, span * 0.34, 60.0, 0.0, 20.0, third, "rise"),
        Key(cx, lo[1] - span * 0.62, span * 0.52, 56.0, 0.0, 20.0, third, "pull back"),
    ], args.fps)

    cam_path = "/World/EstablisherCam"
    cam = UsdGeom.Camera.Define(stage, cam_path)
    cam.CreateHorizontalApertureAttr().Set(36.0)
    cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 40000.0))
    cam_api = UsdGeom.XformCommonAPI(stage.GetPrimAtPath(cam_path))

    rp = rep.create.render_product(cam_path, canvas)
    annot = rep.AnnotatorRegistry.get_annotator("rgb")
    annot.attach([rp])
    for _ in range(12):
        app.update()

    sub = args.subtitle or (
        f"{panels:,} modules across {tables:,} tracker tables · "
        f"{hi[0] - lo[0]:.0f} x {hi[1] - lo[1]:.0f} m"
    )
    chapter = Chapter(
        title=args.title,
        subtitle=sub,
        items=[
            Item("layout", BUILT,
                 f"{tables:,} tables / {panels:,} modules, real surveyed positions "
                 "(GatiShakti S05b digest, EPSG:32642)"),
            Item("blocks", BUILT, "whole DC blocks — nothing tiled, mirrored or offset"),
            Item("provenance", INFERRED,
                 "the digest is SECOND-HAND: vendor DWG -> their script (not held) -> JSON"),
            Item("terrain", BUILT, "Copernicus GLO-30 DEM under the whole plot"),
        ],
        seconds=args.card_seconds,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rec = RunRecorder(fps=args.fps, stream_path=str(out))
    n = 0
    for _ in range(int(round(args.card_seconds * args.fps))):
        rec.add_composed(card(chapter, 1, 1, canvas=canvas))
        n += 1

    t0 = time.time()
    for fi, k in enumerate(keys):
        cam.CreateFocalLengthAttr().Set(float(k.focal_mm))
        cam_api.SetTranslate(Gf.Vec3d(float(k.x), float(k.y), float(k.z)))
        cam_api.SetRotate(
            (float(k.pitch_deg), 0.0, -float(k.heading_deg)),
            UsdGeom.XformCommonAPI.RotationOrderXYZ,
        )
        # Keep the turbines turning so the wide shot is not a still life.
        for prim in stage.Traverse():
            if prim.GetName() == "Hub":
                rpm_attr = prim.GetAttribute("st:rpm")
                rpm = float(rpm_attr.Get()) if rpm_attr and rpm_attr.IsValid() else 11.5
                UsdGeom.XformCommonAPI(prim).SetRotate(
                    (0.0, (fi * rpm * 6.0 / args.fps) % 360.0, 0.0),
                    UsdGeom.XformCommonAPI.RotationOrderXYZ,
                )
        rep.orchestrator.step(rt_subframes=8, pause_timeline=False)
        frame = np.asarray(annot.get_data())
        if frame.size == 0:
            continue
        rec.add_composed(annotate(frame, chapter, 1, 1, canvas=canvas))
        n += 1

    path = rec.write(str(out))
    print(f"wrote {path}  ({n} frames @ {args.fps} fps = {n / args.fps:.0f}s, "
          f"{(time.time() - t0) / 60:.1f} min)")
    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
