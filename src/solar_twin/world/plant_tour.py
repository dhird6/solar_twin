"""Render the annotated status tour of a built plant (Isaac-bound).

    PYTHONPATH=src ./python.sh -m solar_twin.world.plant_tour assets/khavda_full.usd \
        --layout configs/layouts/khavda_a10b_block02.yaml \
        --dem assets/dem/khavda_block02.yaml \
        --out assets/plant_status_tour.mp4 --budget-minutes 25

Three video artifacts now exist and they answer three different questions:
  * `flythrough.py`  — what does the site look like?
  * `run.py --video`  — what did the fleet do on this run?
  * this             — **which parts of the twin are real, and what is missing?**

Every number burned into the overlay is read off the stage or off a generated
sidecar, never typed in: if the build changes, the caption changes with it. That
is the whole reason this is a script and not a slide deck.

⚠ Camera convention (verified on this 6.0.1 build, not assumed): cameras look
along local -Z, `rotateXYZ=(90,0,0)` looks north along +Y, less than 90 pitches
down, and `rotateZ=-azimuth` swings the heading clockwise from north.
"""

from __future__ import annotations

import argparse
import time

#: Measured on this box at 960x540 AND at 1280x720: a frame costs ~0.71 s and the
#: cost does NOT rise at ground level, which is what Session 10d assumed. Used
#: only to project the render time up front so `--budget-minutes` can hold.
SECONDS_PER_FRAME = 0.71


def stage_facts(stage, layout_path: str | None = None, dem_path: str | None = None) -> dict:
    """Read the tour's captions off the built stage.

    Counted rather than configured: the overlay claims "30,016 modules", and the
    only defensible source for that claim is the prims themselves.
    """
    from pxr import Usd

    facts: dict = {}
    farm = stage.GetPrimAtPath("/World/Farm")
    panels = instanced = hotspot = soiled = 0
    zs: list[float] = []
    if farm and farm.IsValid():
        from pxr import UsdGeom

        for prim in Usd.PrimRange(farm):
            attr = prim.GetAttribute("pv:state")
            if not (attr and attr.IsValid()):
                continue
            panels += 1
            state = attr.Get()
            if state == "hotspot":
                hotspot += 1
            elif state == "soiled":
                soiled += 1
            if prim.IsInstanceable():
                instanced += 1
            t, _, _, _, _ = UsdGeom.XformCommonAPI(prim).GetXformVectors(Usd.TimeCode.Default())
            zs.append(t[2])
    facts.update(panels=panels, instanced=instanced, hotspot=hotspot, soiled=soiled)
    facts["prims"] = sum(1 for _ in Usd.PrimRange(stage.GetPseudoRoot()))
    if zs:
        # The z SPREAD across the block is the visible consequence of the DEM, so
        # it is worth stating next to the DEM's own relief figure.
        facts["panel_z_span_m"] = max(zs) - min(zs)

    def _count(path: str) -> int:
        prim = stage.GetPrimAtPath(path)
        return len(prim.GetChildren()) if prim and prim.IsValid() else 0

    facts["turbines"] = _count("/World/Turbines")
    facts["inverters"] = _count("/World/Site/Inverters")
    facts["roads"] = _count("/World/Site/Roads")

    # Where the turbines actually stand, so the tour can aim at one instead of
    # guessing a heading and framing hazed sky.
    turbines_root = stage.GetPrimAtPath("/World/Turbines")
    if turbines_root and turbines_root.IsValid():
        from pxr import UsdGeom as _UsdGeom

        xy = []
        for prim in turbines_root.GetChildren():
            t, _, _, _, _ = _UsdGeom.XformCommonAPI(prim).GetXformVectors(Usd.TimeCode.Default())
            xy.append((float(t[0]), float(t[1])))
        facts["turbine_xy"] = xy
        # How TALL the machine is, measured off the stage. Deliberately named
        # `tip`, not `hub`: the Hub prim's bound includes its blade children, so
        # this is the blade-tip height (~190 m here, i.e. 120 m hub + 70 m blade).
        # Calling it the hub height would have put the camera's aim 70 m too high.
        for prim in Usd.PrimRange(turbines_root):
            if prim.GetName() == "Hub":
                bbox = _UsdGeom.BBoxCache(
                    Usd.TimeCode.Default(), [_UsdGeom.Tokens.default_, _UsdGeom.Tokens.render]
                ).ComputeWorldBound(prim)
                facts["turbine_tip_m"] = float(bbox.ComputeAlignedRange().GetMax()[2])
                break

    inverters_root = stage.GetPrimAtPath("/World/Site/Inverters")
    if inverters_root and inverters_root.IsValid():
        from pxr import UsdGeom as _UsdGeom

        cache = _UsdGeom.BBoxCache(
            Usd.TimeCode.Default(), [_UsdGeom.Tokens.default_, _UsdGeom.Tokens.render]
        )
        inv = []
        for prim in inverters_root.GetChildren():
            # A station is a small group of prims, so its BOUND centre is where a
            # camera should aim — its Xform origin can sit off to one corner.
            rng_i = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if rng_i.IsEmpty():
                continue
            mid = rng_i.GetMidpoint()
            inv.append((float(mid[0]), float(mid[1])))
        if inv:
            facts["inverter_xy"] = inv

    # Sidecars: generated files, so reading them is reading the build, not a guess.
    if layout_path:
        try:
            import yaml

            with open(layout_path) as fh:
                doc = yaml.safe_load(fh) or {}
            prov = doc.get("provenance") or {}
            facts["tables"] = int(prov.get("tables") or 0)
        except Exception as exc:  # noqa: BLE001 — a caption must not kill the render
            print(f"  [warn] could not read layout sidecar: {exc}", flush=True)
    if dem_path:
        try:
            import yaml

            with open(dem_path) as fh:
                doc = yaml.safe_load(fh) or {}
            lo, hi = doc.get("elev_min_m"), doc.get("elev_max_m")
            if lo is not None and hi is not None:
                facts["relief_m"] = float(hi) - float(lo)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] could not read DEM sidecar: {exc}", flush=True)
    return facts


def _panel_positions(stage, limit: int = 400) -> list[tuple[float, float, float]]:
    """World positions of a run of modules along ONE tracker table, near the
    middle of the block — the row the fleet chapter flies.

    Picked from the stage rather than from the layout config so the fleet is
    guaranteed to fly past hardware that actually exists in this build.
    """
    from pxr import Usd, UsdGeom

    farm = stage.GetPrimAtPath("/World/Farm")
    if not (farm and farm.IsValid()):
        return []
    by_row: dict[int, list[tuple[float, float, float]]] = {}
    for prim in Usd.PrimRange(farm):
        idx = prim.GetAttribute("pv:grid_index")
        if not (idx and idx.IsValid()):
            continue
        gi = idx.Get()
        if gi is None:
            continue
        row = int(gi[0])
        t, _, _, _, _ = UsdGeom.XformCommonAPI(prim).GetXformVectors(Usd.TimeCode.Default())
        by_row.setdefault(row, []).append((float(t[0]), float(t[1]), float(t[2])))
    if not by_row:
        return []
    rows = sorted(by_row)
    mid = rows[len(rows) // 2]
    run = sorted(by_row[mid], key=lambda p: p[1])
    return run[:limit]


def render(
    usd_path: str,
    out_path: str,
    fps: int = 20,
    resolution: tuple[int, int] = (1280, 720),
    budget_seconds: float | None = 25 * 60,
    layout_path: str | None = None,
    dem_path: str | None = None,
    fleet: tuple[str, str] | None = ("drone1", "ground_bot"),
) -> str:
    from solar_twin.world.flythrough import interpolate
    from solar_twin.world.recorder import RunRecorder
    from solar_twin.world.sim_runtime import SimRuntime
    from solar_twin.world.tour import annotate, build_chapters, card, render_frames, scale_to_budget

    drone_id, bot_id = fleet if fleet else (None, None)
    t_start = time.time()
    rt = SimRuntime(
        usd_path,
        camera_robots=[drone_id] if drone_id else [],
        marker_robots=[bot_id] if bot_id else [],
        headless=True,
        resolution=(640, 480),  # the drone camera / inspection frame size
        overview_pose=(0.0, 0.0, 200.0),  # re-aimed every frame below
        overview_resolution=resolution,
    )
    # pxr only AFTER SimulationApp exists, or Isaac's schema extensions are
    # unregistered and the app dies on startup.
    from pxr import Gf, Usd, UsdGeom  # noqa: PLC0415

    stage = rt.stage
    farm = stage.GetPrimAtPath("/World/Farm")
    rng = (
        UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
        .ComputeWorldBound(farm)
        .ComputeAlignedRange()
    )
    bounds = (rng.GetMin()[0], rng.GetMin()[1], rng.GetMax()[0], rng.GetMax()[1])
    print(f"  farm bounds: {tuple(round(v, 1) for v in bounds)}", flush=True)

    facts = stage_facts(stage, layout_path, dem_path)
    print(f"  facts off the stage: {facts}", flush=True)

    cam_prim = stage.GetPrimAtPath("/World/Overview")
    cam = UsdGeom.Camera(cam_prim)
    api = UsdGeom.XformCommonAPI(cam_prim)
    # A 2 km ground plane is clipped to nothing by a default far plane.
    cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 20000.0))
    cam.CreateHorizontalApertureAttr().Set(36.0)

    chapters = build_chapters(bounds, facts)
    chapters = scale_to_budget(chapters, fps, SECONDS_PER_FRAME, budget_seconds)
    n_render = render_frames(chapters, fps)
    n_card = sum(int(round(c.seconds * fps)) for c in chapters if not (c.keys or c.fleet))
    print(
        f"  {len(chapters)} chapters · {n_render} rendered + {n_card} card frames "
        f"= {(n_render + n_card) / fps:.0f}s of video @ {fps} fps",
        flush=True,
    )

    # Streamed, not buffered: a 100 s tour at 720p is ~2,000 frames = ~5.5 GB
    # of RAM, on a box already holding a 75k-prim stage in the same memory.
    rec = RunRecorder(fps=fps, stream_path=out_path)
    total = len(chapters)
    row = _panel_positions(stage) if any(c.fleet for c in chapters) else []

    for ci, ch in enumerate(chapters, start=1):
        n = int(round(ch.seconds * fps))
        t_ch = time.time()

        # --- a card: drawn, not rendered. Free, and readable. ---------------
        if not ch.keys and not ch.fleet:
            img = card(ch, ci, total, canvas=resolution, footer=f"solar-twin · {usd_path}")
            for _ in range(n):
                rec.add_composed(img)
            print(f"  [{ci}/{total}] card '{ch.title}' — {n} frames (no render)", flush=True)
            continue

        # --- the fleet chapter: chase the drone down a real tracker row -----
        if ch.fleet:
            if not row or drone_id is None:
                print(f"  [warn] [{ci}/{total}] no fleet/row available; skipping", flush=True)
                continue
            # Fly the length of the sampled row in exactly n frames. The drone is
            # held one standoff above the module and the ground bot tracks along
            # the aisle beside it, which is the real division of labour: the bot
            # carries the payload, the drone takes the picture.
            x0, y0, z0 = row[0]
            x1, y1, z1 = row[-1]
            for i in range(n):
                t = i / max(1, n - 1)
                x = x0 + (x1 - x0) * t
                y = y0 + (y1 - y0) * t
                z = z0 + (z1 - z0) * t
                rt.set_pose(drone_id, x, y, z + 3.0, 0.0)
                if bot_id:
                    rt.set_pose(bot_id, x - 3.0, y, z - 1.2, 0.0)
                rt.chase(drone_id, back=11.0, up=6.5)
                rt.step(1)
                main, inset = rt.capture_pair(drone_id)
                if main is None:
                    continue
                rec.add_composed(annotate(main, ch, ci, total, inset=inset, canvas=resolution))
                if i and i % 40 == 0:
                    print(f"      fleet frame {i}/{n}", flush=True)
            print(
                f"  [{ci}/{total}] '{ch.title}' — {n} frames in {time.time() - t_ch:.0f}s",
                flush=True,
            )
            continue

        # --- a scripted camera chapter -------------------------------------
        # Distribute the chapter's frames over its own keyframes: `interpolate`
        # works in per-key seconds, so scale those to the (possibly budget-cut)
        # chapter length rather than re-deriving the path.
        span = sum(k.seconds for k in ch.keys[1:]) or 1.0
        keys = [ch.keys[0]] + [
            type(k)(
                x=k.x,
                y=k.y,
                z=k.z,
                pitch_deg=k.pitch_deg,
                heading_deg=k.heading_deg,
                focal_mm=k.focal_mm,
                seconds=k.seconds * ch.seconds / span,
                label=k.label,
            )
            for k in ch.keys[1:]
        ]
        for i, k in enumerate(interpolate(keys, fps)):
            api.SetTranslate(Gf.Vec3d(float(k.x), float(k.y), float(k.z)))
            api.SetRotate(
                (float(k.pitch_deg), 0.0, float(-k.heading_deg)),
                UsdGeom.XformCommonAPI.RotationOrderXYZ,
            )
            cam.GetFocalLengthAttr().Set(float(k.focal_mm))
            rt.step(1)
            fr = rt.capture_overview()
            if fr is None:
                continue
            rec.add_composed(annotate(fr, ch, ci, total, canvas=resolution))
            if i and i % 40 == 0:
                print(f"      frame {i}/{n}", flush=True)
        print(
            f"  [{ci}/{total}] '{ch.title}' — {n} frames in {time.time() - t_ch:.0f}s",
            flush=True,
        )

    path = rec.write(out_path)
    print(
        f"wrote status tour ({len(rec)} frames @ {fps} fps = "
        f"{len(rec) / fps:.0f}s) in {(time.time() - t_start) / 60:.1f} min: {path}",
        flush=True,
    )
    rt.close()
    return path or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the annotated status tour of a built plant.")
    ap.add_argument("usd", help="stage built by world.farm_builder")
    ap.add_argument("--out", default="assets/plant_status_tour.mp4")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument(
        "--budget-minutes",
        type=float,
        default=25.0,
        help="wall-clock render budget; the tour is shortened (loudly) to fit. 0 = no cap",
    )
    ap.add_argument("--layout", default=None, help="generated site yaml, for the table count")
    ap.add_argument("--dem", default=None, help="DEM sidecar yaml, for the relief figure")
    ap.add_argument("--no-fleet", action="store_true", help="skip the chase chapter (no robots)")
    args = ap.parse_args(argv)
    render(
        args.usd,
        args.out,
        fps=args.fps,
        resolution=(args.width, args.height),
        budget_seconds=(args.budget_minutes * 60.0) if args.budget_minutes > 0 else None,
        layout_path=args.layout,
        dem_path=args.dem,
        fleet=None if args.no_fleet else ("drone1", "ground_bot"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
