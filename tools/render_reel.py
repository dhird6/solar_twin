#!/usr/bin/env python3
"""Render the conditions reel — one video, six lighting/weather conditions, labelled.

**Isaac-bound — run under `./python.sh`.**

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/build_reel_stages.py --subset 60 --out-dir assets/reel
    PYTHONPATH=src $ISAAC tools/render_reel.py --stage-dir assets/reel \
        --out assets/conditions_reel.mp4

## Why this is not `plant_tour.py` with more chapters

`plant_tour.render()` renders one stage. The reel needs **six**, because the sun
vector, all 273 tracker angles and the procedural sky shader are baked at build time —
so night is a different USD, not a different camera. This opens each condition's stage
in turn inside **one** `SimulationApp` (app startup is ~30 s; paying it six times is
most of a coffee break) and streams every frame into a single mp4.

Everything else is reused rather than reinvented: `flythrough.interpolate` for eased
camera moves, `tour.annotate`/`tour.card` for the labelled overlay, and
`recorder.RunRecorder(stream_path=...)` so a 2-minute 720p reel never sits in RAM.

## The labels are the point

Every shot carries its chapter's checklist — BUILT / INFERRED / TODO — because a
rendered frame carries no provenance: a teleported drone looks autonomous and an
emissive cell looks thermal. `tests/test_conditions_reel.py` pins the ones that matter
so they cannot quietly go missing.
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

#: End-to-end cost of one finished frame, from `plant_tour.py`'s measurement on this
#: box: render + overlay + streaming encode, not the render alone (0.71 s). Used only
#: to project the runtime up front so a long job says so before you wait for it.
SECONDS_PER_FRAME = 0.92


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the labelled conditions reel.")
    ap.add_argument("--stage-dir", default="assets/reel", help="output of build_reel_stages.py")
    ap.add_argument("--out", default="assets/conditions_reel.mp4")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument(
        "--only", default="", help="comma-separated condition names (for a re-render)"
    )
    ap.add_argument(
        "--no-closing-card",
        action="store_true",
        help="omit the closing \"WHAT IS NOT REAL YET\" card. The PER-SHOT labels stay: "
        "they are what keep an individual frame honest, and dropping them would make a "
        "teleported drone read as autonomous. Use this for an audience that gets the "
        "caveats another way (TASKS.md carries the same list).",
    )
    ap.add_argument(
        "--seconds-scale",
        type=float,
        default=1.0,
        help="scale every chapter's duration. 0.5 halves the render for a quick look.",
    )
    args = ap.parse_args(argv)

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": "RaytracedLighting",
                         "width": args.width, "height": args.height})

    import carb
    import numpy as np
    import omni.replicator.core as rep
    import omni.usd
    from pxr import Gf, Usd, UsdGeom

    from solar_twin.world.conditions_reel import (
        CONDITIONS,
        build_chapters,
        close_keys,
        shot_keys,
    )
    from solar_twin.world.flythrough import interpolate
    from solar_twin.world.recorder import RunRecorder
    from solar_twin.world.tour import annotate, card

    canvas = (args.width, args.height)
    stage_dir = Path(args.stage_dir)
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    conds = [c for c in CONDITIONS if not wanted or c.name in wanted]
    missing = [c.name for c in conds if not (stage_dir / f"{c.name}.usd").exists()]
    if missing:
        raise SystemExit(
            f"no stage for {missing} in {stage_dir}. Build them first:\n"
            f"  PYTHONPATH=src $ISAAC tools/build_reel_stages.py --out-dir {stage_dir}"
        )

    settings = carb.settings.get_settings()
    #: The measured photographic exposure (see docs/ENVIRONMENT.md § Looks). Applied
    #: after EVERY `open_stage`, because the renderer re-initialises per stage and
    #: silently resets these — the bug that made an earlier tonemap attempt a no-op.
    TONEMAP = {
        "/rtx/post/tonemap/fNumber": 9.0,
        "/rtx/post/tonemap/cameraShutter": 50.0,
        "/rtx/post/tonemap/filmIso": 100.0,
        "/rtx/post/histogram/enabled": False,
        "/rtx/reflections/maxReflectionBounces": 3,
    }

    rec = RunRecorder(fps=args.fps, stream_path=args.out)
    n_written = 0
    t_start = time.time()

    # Cards first so the reel opens on its own scope. They cost no render, so the
    # title and the closing "what is not real yet" list are nearly free — which is
    # the whole reason this reel can afford to be honest.
    # Count the plot off the FIRST stage so the layout caption matches what is
    # actually rendered. A 60-table subset captioned "273 tables / 30,016 modules" is
    # the reel claiming to be 5x bigger than it is — caught in a smoke render.
    omni.usd.get_context().open_stage(str(stage_dir / f"{conds[0].name}.usd"))
    for _ in range(60):
        app.update()
    _s = omni.usd.get_context().get_stage()
    # Prim names are `Panel_R258_C000` — UNDERSCORES, not the `R258-C000` form the
    # panel_id uses. Splitting on "-" returned the whole name, so every module counted
    # as its own table and the caption read "6,720 tables / 6,720 modules". One table is
    # one ROW, so the row token is the table key.
    _panels = [p for p in _s.Traverse() if p.GetName().startswith("Panel_")]
    _tables = {n.split("_")[1] for n in (p.GetName() for p in _panels) if "_" in n}
    facts = {"panels": len(_panels), "tables": len(_tables)}
    print(f"  counted off the stage: {facts}", flush=True)

    all_chapters = build_chapters((0.0, 0.0, 1.0, 1.0), tuple(conds), facts=facts)
    title_card, closing_card = all_chapters[0], all_chapters[-1]
    total_ch = len(conds) + 2

    for _ in range(int(round(title_card.seconds * args.fps * args.seconds_scale))):
        rec.add_composed(card(title_card, 1, total_ch, canvas=canvas))
        n_written += 1
    print(f"  title card: {n_written} frames", flush=True)

    for ci, cond in enumerate(conds, start=2):
        usd = stage_dir / f"{cond.name}.usd"
        print(f"\n=== [{ci}/{total_ch}] {cond.name}: {cond.title} ===", flush=True)
        omni.usd.get_context().open_stage(str(usd))
        for _ in range(90):
            app.update()
        for k, v in TONEMAP.items():
            settings.set(k, v)
        stage = omni.usd.get_context().get_stage()

        # Frame the plot that is actually loaded, so a subset build is shot correctly.
        farm = stage.GetPrimAtPath("/World/Farm")
        rng = (
            UsdGeom.BBoxCache(
                Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
            )
            .ComputeWorldBound(farm)
            .ComputeAlignedRange()
        )
        bounds = (rng.GetMin()[0], rng.GetMin()[1], rng.GetMax()[0], rng.GetMax()[1])

        cam_path = "/World/ReelCam"
        cam = UsdGeom.Camera.Define(stage, cam_path)
        cam.CreateHorizontalApertureAttr().Set(36.0)
        # A 2 km ground plane is clipped away by the default far plane.
        cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 20000.0))
        cam_api = UsdGeom.XformCommonAPI(stage.GetPrimAtPath(cam_path))

        rp = rep.create.render_product(cam_path, canvas)
        annot = rep.AnnotatorRegistry.get_annotator("rgb")
        annot.attach([rp])
        for _ in range(12):
            app.update()

        seconds = cond.seconds * args.seconds_scale
        # ⚠ Honour `close_up`. This called `shot_keys` unconditionally, so the flag
        # reached `build_chapters` and the RENDERER ignored it — the fault chapter
        # kept its wide establishing pass while claiming to show a cell-level defect.
        keys = interpolate(
            (close_keys if cond.close_up else shot_keys)(bounds, seconds), args.fps
        )
        chapter = [c for c in all_chapters if c.title == cond.title][0]
        print(
            f"  bounds {tuple(round(v, 1) for v in bounds)} · {len(keys)} frames "
            f"(~{len(keys) * SECONDS_PER_FRAME / 60:.1f} min)",
            flush=True,
        )

        t_ch = time.time()
        for fi, k in enumerate(keys):
            cam.CreateFocalLengthAttr().Set(float(k.focal_mm))
            cam_api.SetTranslate(Gf.Vec3d(float(k.x), float(k.y), float(k.z)))
            # `pitch_deg` 90 = level; `heading_deg` is a compass bearing, so rotateZ is
            # its negation — the convention plant_tour verified on this build.
            cam_api.SetRotate(
                (float(k.pitch_deg), 0.0, -float(k.heading_deg)),
                UsdGeom.XformCommonAPI.RotationOrderXYZ,
            )
            # Turn the turbines so blades (and their shadows) move across the shot.
            for prim in stage.Traverse():
                if prim.GetName() == "Hub":
                    rpm_attr = prim.GetAttribute("st:rpm")
                    rpm = float(rpm_attr.Get()) if rpm_attr and rpm_attr.IsValid() else 11.5
                    ang = (fi * rpm * 6.0 / args.fps) % 360.0
                    UsdGeom.XformCommonAPI(prim).SetRotate(
                        (0.0, ang, 0.0), UsdGeom.XformCommonAPI.RotationOrderXYZ
                    )
            rep.orchestrator.step(rt_subframes=8, pause_timeline=False)
            frame = np.asarray(annot.get_data())
            if frame.size == 0:
                continue
            rec.add_composed(annotate(frame, chapter, ci, total_ch, canvas=canvas))
            n_written += 1
        el = time.time() - t_ch
        print(
            f"  [{cond.name}] {len(keys)} frames in {el:.0f}s "
            f"({el / max(1, len(keys)):.2f} s/frame)",
            flush=True,
        )
        annot.detach()

    if not args.no_closing_card:
        for _ in range(int(round(closing_card.seconds * args.fps * args.seconds_scale))):
            rec.add_composed(card(closing_card, total_ch, total_ch, canvas=canvas))
            n_written += 1
    else:
        print("  closing card OMITTED (--no-closing-card); per-shot labels kept",
              flush=True)

    path = rec.write(args.out)
    mins = (time.time() - t_start) / 60.0
    print(
        f"\nwrote {path}\n  {n_written} frames @ {args.fps} fps "
        f"= {n_written / args.fps:.0f}s of video, rendered in {mins:.1f} min"
    )
    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
