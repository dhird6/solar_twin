"""Cinematic flythrough of a built farm stage (Isaac-bound: SimulationApp).

    PYTHONPATH=src ./python.sh -m solar_twin.world.flythrough assets/khavda_full.usd

Writes an mp4 that shows the plant at its real extent: an establishing aerial,
a descent onto the internal access road past the inverter stations, and a low
pass along the tracker rows. This is the "what does the site look like" artifact;
`run.py --video` is the "what is the fleet doing" one.

No mission, no perception, no robots — it only moves a camera, so it costs a
render per frame and nothing else. Camera moves are eased (smoothstep) between
keyframes because a linear ramp starts and stops with a visible jerk that reads
as a dropped frame.

⚠ Cameras look along local **-Z**. `rotateXYZ = (90, 0, 0)` therefore looks north
along +Y; less than 90 pitches down, more pitches up, and `rotateZ = -azimuth`
swings the heading clockwise from north. Verified on this build, not assumed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass
class Key:
    """One camera keyframe: position, aim, lens, and how long to take getting here."""

    x: float
    y: float
    z: float
    pitch_deg: float  # 90 = level with the horizon, <90 looks down
    heading_deg: float  # compass bearing the camera faces, 0 = north
    focal_mm: float = 20.0
    seconds: float = 4.0
    label: str = ""


def _smoothstep(t: float) -> float:
    """Ease in and out. A linear parameter makes every move start and stop with a
    jolt, which on a slow aerial reads as a stutter rather than a camera move."""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def interpolate(keys: list[Key], fps: int) -> list[Key]:
    """Expand keyframes into one `Key` per rendered frame."""
    out: list[Key] = []
    for a, b in zip(keys, keys[1:]):
        n = max(1, int(round(b.seconds * fps)))
        for i in range(n):
            t = _smoothstep(i / n)

            def lerp(p, q, t=t):
                return p + (q - p) * t

            # Heading takes the SHORT way round, or a pan from 350 to 10 degrees
            # spins the camera 340 degrees the wrong way.
            dh = (b.heading_deg - a.heading_deg + 180.0) % 360.0 - 180.0
            out.append(
                Key(
                    x=lerp(a.x, b.x),
                    y=lerp(a.y, b.y),
                    z=lerp(a.z, b.z),
                    pitch_deg=lerp(a.pitch_deg, b.pitch_deg),
                    heading_deg=a.heading_deg + dh * t,
                    focal_mm=lerp(a.focal_mm, b.focal_mm),
                    label=b.label,
                )
            )
    out.append(keys[-1])
    return out


def default_shots(bounds: tuple[float, float, float, float]) -> list[Key]:
    """A three-move tour sized from the stage's own bounds, so it frames a
    10-panel test row and a 273-table block equally well."""
    min_x, min_y, max_x, max_y = bounds
    cx = (min_x + max_x) / 2.0
    span_y = max_y - min_y
    span_x = max_x - min_x
    high = max(120.0, 0.55 * max(span_x, span_y))

    return [
        # 1. Establishing aerial from the south, whole block in frame.
        Key(cx, min_y - span_y * 0.50, high, 58.0, 0.0, 24.0, 0.0, "the block"),
        Key(cx, min_y - span_y * 0.28, high * 0.78, 56.0, 0.0, 24.0, 5.0, "the block"),
        # 2. Descend and swing onto the site.
        Key(cx * 0.75, min_y - span_y * 0.12, high * 0.35, 72.0, 14.0, 20.0, 5.0, "descending"),
        # 3. Onto the internal road, travelling north past the inverters.
        Key(cx, min_y + span_y * 0.06, 7.0, 88.0, 0.0, 22.0, 4.0, "access road"),
        Key(cx, min_y + span_y * 0.55, 6.0, 89.0, 0.0, 22.0, 7.0, "access road"),
        # 4. Rise off the road, then climb out to the north and turn to look back
        #    south over the whole block. Aiming outward here (an earlier version
        #    banked north-west from the western edge) framed empty desert with the
        #    rows in one corner — the last frame should be the plant, not the sand.
        Key(cx, min_y + span_y * 0.70, 26.0, 80.0, 0.0, 20.0, 5.0, "tracker rows"),
        # Altitude stays a FRACTION of the site, like every other move here: an
        # absolute climb that frames a 647 m block would be 150 m over a 22 m
        # test row, looking down at nothing.
        Key(cx, min_y + span_y * 1.05, high * 0.42, 60.0, 180.0, 22.0, 6.0, "the block"),
    ]


def render(
    usd_path: str,
    out_path: str,
    fps: int = 24,
    resolution: tuple[int, int] = (1280, 720),
    caption: str | None = None,
) -> str:
    from solar_twin.world.sim_runtime import SimRuntime

    rt = SimRuntime(
        usd_path,
        camera_robots=[],
        marker_robots=[],
        headless=True,
        resolution=resolution,
        # The tour renders through the OVERVIEW camera, so its render product is
        # what --width/--height have to size. Passing `resolution` alone left
        # every flythrough at a hardcoded 960x540 whatever the flags said.
        overview_resolution=resolution,
        overview_pose=(0.0, 0.0, 100.0),  # re-aimed per frame below
    )
    # pxr only AFTER SimulationApp exists: importing it first leaves Isaac's
    # schema extensions unregistered and the app dies on startup.
    from pxr import Gf, Usd, UsdGeom  # noqa: PLC0415

    stage = rt.stage
    # Frame the tour on the real hardware, not on an assumed origin.
    farm = stage.GetPrimAtPath("/World/Farm")
    bbox = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    ).ComputeWorldBound(farm)
    rng = bbox.ComputeAlignedRange()
    bounds = (rng.GetMin()[0], rng.GetMin()[1], rng.GetMax()[0], rng.GetMax()[1])
    print(f"  farm bounds: {tuple(round(v, 1) for v in bounds)}", flush=True)

    cam_prim = stage.GetPrimAtPath("/World/Overview")
    cam = UsdGeom.Camera(cam_prim)
    api = UsdGeom.XformCommonAPI(cam_prim)
    # Reach: a 2 km ground plane is clipped to nothing by a default far plane.
    cam.CreateClippingRangeAttr().Set(Gf.Vec2f(0.1, 20000.0))
    cam.CreateHorizontalApertureAttr().Set(36.0)

    frames = interpolate(default_shots(bounds), fps)
    print(f"  {len(frames)} frames @ {fps} fps = {len(frames) / fps:.1f}s", flush=True)

    from solar_twin.world.recorder import Caption, RunRecorder

    rec = RunRecorder(fps=fps, max_frames=len(frames) + 8)
    for i, k in enumerate(frames):
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
        rec.caption = Caption(panel_id=k.label, subtitle=caption or "")
        rec.add(fr)
        if i % 40 == 0:
            print(f"    frame {i}/{len(frames)}", flush=True)

    path = rec.write(out_path)
    print(f"wrote flythrough ({len(rec.frames)} frames): {path}", flush=True)
    rt.close()
    return path or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a flythrough of a built farm USD.")
    ap.add_argument("usd", help="path to a stage built by world.farm_builder")
    ap.add_argument("--out", default="assets/flythrough.mp4")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--caption", default=None, help="subtitle burned into every frame")
    args = ap.parse_args(argv)
    render(
        args.usd,
        args.out,
        fps=args.fps,
        resolution=(args.width, args.height),
        caption=args.caption,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
