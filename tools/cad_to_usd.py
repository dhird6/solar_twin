#!/usr/bin/env python3
"""Convert a real CAD file to USD (Isaac-bound) — the professional asset route.

**Isaac-bound — run under `./python.sh`.**

## Why this exists

Every surface in this twin was procedurally generated from `UsdGeom.Cube` in our own
code, and it looked like it. The research answer to "what do professionals do
instead" has two halves, and only one of them is a material problem:

* **Materials** — solved separately: MDL/OmniPBR/OmniGlass, see `world/mdl_materials.py`.
* **Geometry** — there is **no NVIDIA SimReady asset for a PV module, tracker, or
  inverter**. Checked: the Isaac 6.0 asset tree has Props, Robots, Environments,
  People, Sensors, and nothing solar. So a real-looking module cannot be downloaded;
  it has to come from CAD, which is how the PV industry works anyway (PVcase is an
  AutoCAD plug-in emitting native DWG; RatedPower exports DWG/DXF/glTF).

## What is actually installed here (verified, not assumed)

This aarch64 Isaac Sim 6.0.1 source build ships the full converter stack:

    omni.kit.converter.cad 209.4.0   bundle
    omni.kit.converter.hoops 510.3.0 STEP/IGES/SLDPRT/CATPart/JT/DWG/DXF/...
    omni.kit.converter.dgn 510.1.5   MicroStation DGN
    omni.kit.converter.jt 509.1.2    Siemens JT
    omni.kit.asset_converter 6.0.1   OBJ/STL/glTF/FBX
    omni.importer.onshape 2.0.3      Onshape
    omni.kit.converter.gsplat 0.1.14 Gaussian splats (the NuRec path)

HOOPS advertises: **step stp iges igs sldprt sldasm catpart catproduct prt asm
x_t x_b jt dwg dxf 3dm ipt iam dgn obj stl glb gltf fbx**.

⚠ **`.dwg`/`.dxf` matters twice over.** It is the format the Khavda vendor drawing
already arrives in — today we parse it for *coordinates* (`tools/layout_from_dxf.py`)
and throw the geometry away — and it is what every PV design tool exports.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/cad_to_usd.py tracker.step --out assets/cad/tracker.usd
    PYTHONPATH=src $ISAAC tools/cad_to_usd.py --list          # what can be converted

⚠ **Converting is not the whole job.** A raw CAD import is not SimReady: it arrives
with CAD units (often mm), no semantic labels, no physics, and frequently a prim per
bolt. Expect to set `metersPerUnit`, decimate, and re-bind materials. This tool
reports the prim count and unit scale so those problems are visible immediately
rather than after the asset is referenced 30,016 times.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

#: What the installed HOOPS converter advertises. Kept here so `--list` can answer
#: without launching Isaac, and so a caller gets a clear refusal rather than a
#: converter error 20 seconds into app startup.
HOOPS_FORMATS = {
    ".step", ".stp", ".iges", ".igs", ".sldprt", ".sldasm", ".catpart",
    ".catproduct", ".prt", ".asm", ".x_t", ".x_b", ".jt", ".dwg", ".dxf",
    ".3dm", ".ipt", ".iam", ".dgn", ".obj", ".stl", ".glb", ".gltf", ".fbx",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert a CAD file to USD.")
    ap.add_argument("cad", nargs="?", help="input CAD file")
    ap.add_argument("--out", default="", help="output .usd (default: alongside input)")
    ap.add_argument(
        "--list", action="store_true", help="print supported formats and exit"
    )
    ap.add_argument(
        "--merge-meshes",
        action="store_true",
        help="ask the converter to merge meshes. A CAD assembly can arrive as one prim "
        "per fastener; merging is usually right for a VISUAL asset and wrong if you "
        "need to articulate it later.",
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=0.0,
        help="metres per input unit (e.g. 0.001 for a millimetre CAD file). 0 = trust "
        "the file. ⚠ Getting this wrong is the single most common CAD-import failure: "
        "a mm asset in a metres stage is 1000x too big and lights wrongly.",
    )
    args = ap.parse_args(argv)

    if args.list:
        print("HOOPS/CAD converter (omni.kit.converter.hoops 510.3.0) accepts:")
        for f in sorted(HOOPS_FORMATS):
            print(f"   {f}")
        print(
            "\nAlso installed: omni.kit.converter.dgn (DGN), omni.kit.converter.jt "
            "(Siemens JT),\n  omni.kit.asset_converter (OBJ/STL/glTF/FBX), "
            "omni.importer.onshape,\n  omni.kit.converter.gsplat (Gaussian splats)."
        )
        print(
            "\nNo NVIDIA SimReady asset exists for a PV module, tracker or inverter — "
            "checked\nagainst the Isaac 6.0 asset tree. CAD is the route for those."
        )
        return 0

    if not args.cad:
        ap.error("give a CAD file, or --list")
    src = Path(args.cad)
    if not src.exists():
        raise SystemExit(f"no such file: {src}")
    if src.suffix.lower() not in HOOPS_FORMATS:
        raise SystemExit(
            f"{src.suffix} is not in the installed converter's format list.\n"
            f"Run --list to see what is accepted."
        )
    out = Path(args.out) if args.out else src.with_suffix(".usd")
    out.parent.mkdir(parents=True, exist_ok=True)

    from isaacsim import SimulationApp

    # Headless: conversion is a batch job and needs no viewport.
    app = SimulationApp({"headless": True})

    import carb
    import omni.kit.app

    # The converter extensions are not in the base python app's enable list.
    mgr = omni.kit.app.get_app().get_extension_manager()
    for ext in (
        "omni.kit.converter.common",
        "omni.kit.converter.hoops_core",
        "omni.kit.converter.hoops",
    ):
        if not mgr.is_extension_enabled(ext):
            mgr.set_extension_enabled_immediate(ext, True)
            print(f"  enabled {ext}", flush=True)
    for _ in range(30):
        app.update()

    try:
        from omni.kit.converter.hoops_core import get_instance
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] HOOPS converter not importable: {exc}", file=sys.stderr)
        app.close()
        return 2

    # Converter options go in as a JSON file (`config_path_to_args`), matching the
    # extension's own `launch_hoops_app.py` entry point.
    opts: dict = {}
    if args.merge_meshes:
        opts["merge_meshes"] = True
    if args.scale > 0.0:
        opts["meters_per_unit"] = args.scale
    cfg = out.parent / f".{out.stem}_converter_opts.json"
    cfg.write_text(json.dumps(opts))

    print(f"converting {src}  ->  {out}", flush=True)

    async def _run():
        from omni.kit.converter.common import config_path_to_args

        conv = get_instance()
        if conv is None:
            return None, "HOOPS converter instance is None (extension not ready)"
        file_format_args = config_path_to_args(str(cfg))
        _, status = await conv.create_converter_task(
            str(src), str(out), file_format_args
        )
        return status, None

    status, err = asyncio.get_event_loop().run_until_complete(_run())
    if err:
        print(f"[FAIL] {err}", file=sys.stderr)
        app.close()
        return 3
    if status is None or getattr(status, "error_code", 1) != 0:
        msg = getattr(status, "error_msg", "unknown")
        code = getattr(status, "error_code", "?")
        print(f"[FAIL] conversion failed: code {code} — {msg}", file=sys.stderr)
        app.close()
        return 4

    print(f"[ok] wrote {out}", flush=True)

    # Report what actually came out. A CAD import that is 40,000 prims of mm-scaled
    # bolts is a trap you want to see NOW, not after referencing it 30,016 times.
    try:
        from pxr import Usd, UsdGeom

        stage = Usd.Stage.Open(str(out))
        prims = list(stage.Traverse())
        mpu = UsdGeom.GetStageMetersPerUnit(stage)
        upaxis = UsdGeom.GetStageUpAxis(stage)
        meshes = [p for p in prims if p.GetTypeName() == "Mesh"]
        print(
            f"  prims={len(prims)}  meshes={len(meshes)}  "
            f"metersPerUnit={mpu}  upAxis={upaxis}"
        )
        if abs(mpu - 1.0) > 1e-9:
            print(
                f"  ⚠ metersPerUnit={mpu}, but this project's stages are metres/Z-up "
                "(CLAUDE.md). Rescale before referencing it into a farm stage."
            )
        if str(upaxis) != "Z":
            print(f"  ⚠ upAxis={upaxis}; farm stages assert Z-up.")
        if len(prims) > 5000:
            print(
                f"  ⚠ {len(prims)} prims is heavy for an asset instanced per module — "
                "decimate or --merge-meshes before use at plant scale."
            )
    except Exception as exc:  # noqa: BLE001 — reporting must not fail the conversion
        print(f"  [warn] could not inspect the result: {exc}")

    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
