#!/usr/bin/env python3
"""Render every plant material on a sphere and print its pixels (Isaac-bound).

**Isaac-bound — run under `./python.sh`.**

An MDL parameter name that OmniPBR does not recognise is *silently ignored*, and a
material whose surface output is wired to the wrong render context falls back to a
default grey. Both failures look identical to "the material is just flat", which is
the exact confusion that let 13 constant-colour looks ship for months. So every look
gets rendered and measured, and the numbers are the acceptance test.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/material_probe.py --out runs/materials

Reading the output: `spread` is max-min over the sphere's own pixels. A **flat**
material has a low spread (diffuse shading only); a real PBR material has a high
one, because it carries a specular highlight and reflects the environment. That,
not the mean, is what separates "plastic" from "metal" — and it is the number that
would have caught the old look immediately.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render and measure every plant material.")
    ap.add_argument("--out", default="", help="directory for the PNG + JSON")
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--height", type=int, default=340)
    ap.add_argument(
        "--flat",
        action="store_true",
        help="render the OLD constant-colour UsdPreviewSurface look instead of MDL, "
        "for an A/B. Two runs, not two rows: overlapping rows in one frame made it "
        "impossible to tell which sphere was which.",
    )
    ap.add_argument("--sun", type=float, default=700.0, help="DistantLight intensity")
    ap.add_argument("--dome", type=float, default=180.0, help="DomeLight intensity")
    args = ap.parse_args(argv)

    from isaacsim import SimulationApp

    app = SimulationApp(
        {"headless": True, "renderer": "RaytracedLighting",
         "width": args.width, "height": args.height}
    )

    import numpy as np
    import omni.replicator.core as rep
    import omni.usd
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

    from solar_twin.world.mdl_materials import PLANT_LOOKS, omni_glass, omni_pbr

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))

    # ⚠ Light level is part of what is under test. At sun 3000 + dome 900 every
    # material in this scene saturated to pale grey and the probe reported "has
    # specular range" for all twelve — measured. Without exposure control a bright
    # key light destroys albedo differences, which is its own finding: see
    # `--exposure` and the tonemapping work. These values keep the brightest
    # surface (0.62 frame) below clipping so albedo is actually comparable.
    sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
    sun.CreateIntensityAttr(args.sun)
    sun.CreateAngleAttr(0.53)
    UsdGeom.XformCommonAPI(sun).SetRotate((-50.0, 0.0, 20.0))
    UsdLux.DomeLight.Define(stage, "/World/Dome").CreateIntensityAttr(args.dome)

    ground = UsdGeom.Mesh.Define(stage, "/World/Ground")
    ground.CreatePointsAttr(
        [Gf.Vec3f(-120, -30, 0), Gf.Vec3f(120, -30, 0),
         Gf.Vec3f(120, 40, 0), Gf.Vec3f(-120, 40, 0)]
    )
    ground.CreateFaceVertexCountsAttr([4])
    ground.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    ground.CreateSubdivisionSchemeAttr("none")

    names = list(PLANT_LOOKS) + ["panel_glass"]
    R = 2.0
    PITCH = 5.2
    x0 = -(len(names) - 1) * PITCH / 2.0

    def flat_material(path: str, kw: dict):
        """The OLD look: constant-colour UsdPreviewSurface, for the A/B."""
        mat = UsdShade.Material.Define(stage, path)
        sh = UsdShade.Shader.Define(stage, path + "/Shader")
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*kw.get("diffuse", (0.5, 0.5, 0.5)))
        )
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(
            float(kw.get("roughness", 0.5))
        )
        sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(
            float(kw.get("metallic", 0.0))
        )
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        return mat

    tag = "flat" if args.flat else "mdl"
    for i, name in enumerate(names):
        sph = UsdGeom.Sphere.Define(stage, f"/World/S_{tag}_{i}")
        sph.CreateRadiusAttr(R)
        UsdGeom.XformCommonAPI(sph).SetTranslate(Gf.Vec3d(x0 + i * PITCH, 0.0, R + 0.4))
        look = f"/World/Looks/{tag}_{name}"
        if tag == "flat":
            mat = flat_material(look, PLANT_LOOKS.get(name, {}))
        elif name == "panel_glass":
            from solar_twin.world.mdl_materials import PANEL_GLASS

            mat = omni_glass(stage, look, **PANEL_GLASS)
        else:
            mat = omni_pbr(stage, look, **PLANT_LOOKS[name])
        UsdShade.MaterialBindingAPI(sph.GetPrim()).Bind(mat)

    # Frame the row properly. The previous distance put the OUTER spheres outside the
    # frustum entirely, so every "sample" landed on background — which is why the
    # probe read a 0.016-albedo PV cell as 174/255. Derived, not guessed:
    # tan(hfov/2) = (aperture/2)/focal, so the half-width in world units at distance d
    # is `TAN_HALF * d`, and it must exceed the outermost sphere plus its radius.
    FOCAL, APERTURE = 20.0, 24.0
    TAN_HALF = (APERTURE / 2.0) / FOCAL
    half_row = abs(x0) + R
    dist = (half_row * 1.08) / TAN_HALF  # 8% margin so nothing clips the edge
    cam = UsdGeom.Camera.Define(stage, "/World/Cam")
    cam.CreateFocalLengthAttr(FOCAL)
    cam.CreateHorizontalApertureAttr(APERTURE)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.1, 10000.0))
    UsdGeom.XformCommonAPI(cam).SetTranslate(Gf.Vec3d(0.0, -dist, R * 1.6))
    UsdGeom.XformCommonAPI(cam).SetRotate(
        (88.0, 0.0, 0.0), UsdGeom.XformCommonAPI.RotationOrderXYZ
    )

    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    tmp = (out or Path(".")) / "material_probe.usda"
    stage.Export(str(tmp))

    omni.usd.get_context().open_stage(str(tmp))
    for _ in range(140):
        app.update()
    rp = rep.create.render_product("/World/Cam", (args.width, args.height))
    annot = rep.AnnotatorRegistry.get_annotator("rgb")
    annot.attach([rp])
    for _ in range(20):
        app.update()
    for _ in range(4):
        rep.orchestrator.step(rt_subframes=24, pause_timeline=False)
    img = np.asarray(annot.get_data())
    if img.size == 0:
        print("EMPTY FRAME — render product produced nothing", file=sys.stderr)
        app.close()
        return 1
    rgb = img[..., :3].astype(float)

    h, w = rgb.shape[:2]
    print(f"\n{'material':18} {'R':>6} {'G':>6} {'B':>6} {'spread':>7}  reading")
    report = {}
    for i, name in enumerate(names):
        # Project the sphere centre through the camera's own pinhole: the half-width
        # at `dist` is TAN_HALF*dist, so x_px = w/2 * (1 + x_world / (TAN_HALF*dist)).
        wx = x0 + i * PITCH
        px = int(0.5 * w * (1.0 + wx / (TAN_HALF * dist)))
        # Sample a box INSIDE the sphere: its projected radius in pixels, halved, so
        # the window cannot include background or the ground shadow.
        rad_px = R / (TAN_HALF * dist) * 0.5 * w
        half = max(4, int(rad_px * 0.45))
        cy = int(h * 0.45)
        band = rgb[max(0, cy - half):cy + half, max(0, px - half):min(w, px + half)]
        if band.size == 0:
            continue
        lum = band.mean(axis=2)
        r, g, b = band[..., 0].mean(), band[..., 1].mean(), band[..., 2].mean()
        spread = float(lum.max() - lum.min())
        flat = spread < 40.0
        report[name] = {
            "rgb": [round(r, 1), round(g, 1), round(b, 1)],
            "spread": round(spread, 1),
            "looks_flat": flat,
        }
        print(
            f"{name:18} {r:6.1f} {g:6.1f} {b:6.1f} {spread:7.1f}  "
            + ("⚠ FLAT — no specular/reflection" if flat else "has specular range")
        )

    if out:
        import imageio.v3 as iio

        iio.imwrite(str(out / "materials.png"), img[..., :3].astype(np.uint8))
        (out / "materials.json").write_text(json.dumps(report, indent=2))
        print(f"\nwrote {out / 'materials.png'} and materials.json")

    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
