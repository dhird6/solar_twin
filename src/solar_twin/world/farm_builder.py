"""Procedural USD farm builder (Isaac-bound: pxr).

    PYTHONPATH=src ./python.sh -m solar_twin.world.farm_builder configs/farm.yaml [--out assets/farm.usd]

Authors a USD stage from `farm.yaml`, reusing `world/layout.py` so the grid and
the *seeded* faults are identical to the fake-backend run. Each panel is a
`PVModule` Xform (pv: attributes stamped via `schema/pv_module.py`, the source of
truth) with a box mesh; faulted panels get a distinct emissive material
signature + a USD semantic label the confirm-drone / Replicator can read later.

This only *authors* USD — it needs pxr but NOT a running SimulationApp, so it is
fast. Runs under Isaac Sim's Python (`./python.sh`); pxr is unavailable in the
aarch64 system Python. ⚠ pxr/UsdShade/UsdSemantics APIs verified against the
installed OpenUSD 0.25.5 / Isaac Sim 6.0.1 build.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

from solar_twin.schema import pv_module as pv
from solar_twin.world.layout import FarmLayout

# Mount height of the panel centre above the ground (meters), for visibility.
PANEL_MOUNT_HEIGHT = 0.75

# state -> (diffuseColor, emissiveColor). Emissive red = the hotspot signature.
_STATE_LOOK: dict[pv.PanelState, tuple[tuple, tuple]] = {
    pv.PanelState.HEALTHY: ((0.04, 0.05, 0.18), (0.0, 0.0, 0.0)),
    pv.PanelState.SOILED: ((0.38, 0.33, 0.24), (0.0, 0.0, 0.0)),
    pv.PanelState.HOTSPOT: ((0.10, 0.06, 0.06), (1.6, 0.12, 0.0)),
}


def _load_farm_cfg(path: str) -> dict:
    import yaml  # Isaac's bundled Python ships pyyaml.

    with open(path) as f:
        return yaml.safe_load(f)


def _make_material(stage: Usd.Stage, path: str, diffuse, emissive) -> UsdShade.Material:
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*diffuse)
    )
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*emissive)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.4)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.1)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def _label(prim, *labels: str) -> None:
    """Best-effort USD semantic labels (taxonomy 'class'). Non-fatal if the
    UsdSemantics schema surface differs on another build."""
    try:
        from pxr import UsdSemantics

        api = UsdSemantics.LabelsAPI.Apply(prim, "class")
        api.CreateLabelsAttr(list(labels))
    except Exception as exc:  # noqa: BLE001 — labels are a nice-to-have
        print(f"  [warn] semantic label skipped for {prim.GetPath()}: {exc}")


def build(farm_cfg: dict, out_path: str) -> str:
    layout = FarmLayout(farm_cfg)
    faults = layout.seeded_faults()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Regenerate a fresh stage every build (never hand-edit generated USD).
    if out.exists():
        out.unlink()
    stage = Usd.Stage.CreateNew(str(out))

    # --- coordinate contract: Z-up, meters. Assert it (§6.2). ---------------
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0

    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    # --- lighting + ground for context (not the farm itself) ----------------
    dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
    dome.CreateIntensityAttr(800.0)
    ground = UsdGeom.Cube.Define(stage, "/World/Ground")
    ground.CreateSizeAttr(1.0)
    span_x = max(2.0, layout.cols * layout.col_pitch + 4.0)
    span_y = max(2.0, layout.rows * layout.row_pitch + 4.0)
    UsdGeom.XformCommonAPI(ground).SetTranslate(
        Gf.Vec3d(span_x / 2 - layout.col_pitch, span_y / 2 - layout.row_pitch, -0.05)
    )
    UsdGeom.XformCommonAPI(ground).SetScale(Gf.Vec3f(span_x, span_y, 0.1))

    # --- one material per Slice-0 state -------------------------------------
    materials = {
        state: _make_material(stage, f"/World/Looks/mat_{state.value}", diff, emis)
        for state, (diff, emis) in _STATE_LOOK.items()
    }

    tilt_deg = float(farm_cfg.get("panel", {}).get("tilt_deg", 20.0))
    pdim = farm_cfg.get("panel", {})
    pw = float(pdim.get("width", 1.0))
    pl = float(pdim.get("length", 2.0))
    ph = float(pdim.get("height", 0.05))

    n_fault = 0
    for site in layout.sites:
        path = pv.panel_path("/World/Farm", site.row, site.col)
        prim = pv.create_panel(
            stage, path, site.panel_id, site.row, site.col, site.geo_position
        )
        # Place + tilt the panel Xform (Z-up: tilt about the row axis = X).
        x, y, _ = site.position
        api = UsdGeom.XformCommonAPI(prim)
        api.SetTranslate(Gf.Vec3d(x, y, PANEL_MOUNT_HEIGHT))
        api.SetRotate((tilt_deg, 0.0, 0.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)

        # Box geometry as a child (unit cube scaled to panel dims).
        geom = UsdGeom.Cube.Define(stage, path + "/Geom")
        geom.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(geom).SetScale(Gf.Vec3f(pw, pl, ph))

        # Seeded fault: set state directly on the source of truth (no log line —
        # injection is ground-truth setup, not an inspection) + a look + a label.
        state = faults.get(site.panel_id, pv.PanelState.HEALTHY)
        if state is not pv.PanelState.HEALTHY:
            prim.GetAttribute(pv.ATTR_STATE).Set(state.value)
            n_fault += 1
        mat = materials.get(state, materials[pv.PanelState.HEALTHY])
        UsdShade.MaterialBindingAPI.Apply(geom.GetPrim())
        UsdShade.MaterialBindingAPI(geom.GetPrim()).Bind(mat)
        _label(prim, "panel", state.value)

    stage.GetRootLayer().Save()
    print(
        f"built {layout.n_panels} panels ({n_fault} faulted) -> {out}\n"
        f"  up-axis=Z meters=1.0 seed={farm_cfg.get('seed')} "
        f"faults={ {pid: s.value for pid, s in faults.items()} }"
    )
    return str(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the procedural USD farm.")
    ap.add_argument("farm", help="path to farm.yaml")
    ap.add_argument("--out", default="assets/farm.usd", help="output USD path")
    args = ap.parse_args(argv)
    build(_load_farm_cfg(args.farm), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
