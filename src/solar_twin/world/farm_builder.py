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
import random
import sys
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

from solar_twin.schema import pv_module as pv
from solar_twin.world.layout import FarmLayout, fault_cells, terrain_height

# Fallback panel-centre height if farm.yaml omits panel.mount_height.
PANEL_MOUNT_HEIGHT = 0.75

# Physically-plausible UsdPreviewSurface looks, keyed by name. A panel is a grid
# of *cells* over a frame — faults recolor only the affected cells (localized),
# so a soiled panel reads as "dusty patch on a PV module", not a beige rectangle.
# (name -> diffuse, emissive, roughness, metallic)
_LOOKS: dict[str, tuple[tuple, tuple, float, float]] = {
    "cell_healthy": ((0.02, 0.04, 0.13), (0.0, 0.0, 0.0), 0.22, 0.35),  # dark-blue glassy PV
    "cell_soiled": ((0.46, 0.39, 0.28), (0.0, 0.0, 0.0), 0.9, 0.0),     # dust film
    "cell_hotspot": ((0.14, 0.05, 0.03), (2.2, 0.35, 0.0), 0.5, 0.0),   # hot cell glow
    "frame": ((0.62, 0.63, 0.66), (0.0, 0.0, 0.0), 0.3, 0.9),           # aluminium rail
    "ground": ((0.17, 0.14, 0.10), (0.0, 0.0, 0.0), 1.0, 0.0),          # dry earth (kept dark so the sun doesn't blow it out)
    "turbine": ((0.9, 0.9, 0.92), (0.0, 0.0, 0.0), 0.35, 0.0),          # off-white tower/blade
}

# How finely to tessellate the heightfield ground mesh (verts per axis).
_TERRAIN_RES = 48

# Rotor keep-out margin (m) — MUST match keepout.build_keepouts' rotor_margin so
# the translucent no-fly sphere we author here shows the SAME volume the planner
# enforces (world/keepout.py). Keep the two in sync.
_ROTOR_MARGIN = 2.0


def _load_farm_cfg(path: str) -> dict:
    import yaml  # Isaac's bundled Python ships pyyaml.

    with open(path) as f:
        return yaml.safe_load(f)


def _make_material(
    stage: Usd.Stage, path: str, diffuse, emissive, roughness: float, metallic: float
) -> UsdShade.Material:
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*diffuse))
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emissive))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def _bind(prim, material: UsdShade.Material) -> None:
    UsdShade.MaterialBindingAPI.Apply(prim)
    UsdShade.MaterialBindingAPI(prim).Bind(material)


def _add_collision(prim) -> None:
    """Give a prim a PhysX collider (approximated from its geom). Static/inert
    under kinematic teleport; it starts mattering once the drone has rigid-body
    dynamics (Pegasus/PX4). Non-fatal if the schema differs on another build."""
    try:
        UsdPhysics.CollisionAPI.Apply(prim)
    except Exception as exc:  # noqa: BLE001 — colliders are additive, not critical
        print(f"  [warn] collider skipped for {prim.GetPath()}: {exc}")


def _keepout_viz_material(stage) -> UsdShade.Material:
    """A translucent red material for the no-fly sphere (so you can SEE it)."""
    path = "/World/Looks/keepout_viz"
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.9, 0.1, 0.1))
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.12)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def _build_ground_heightfield(stage, farm_cfg, layout, material) -> None:
    """A tessellated ground mesh sampling the SAME terrain_height() the panels and
    waypoints use, so the visible ground matches where things are mounted. Flat
    terrain degenerates to a flat mesh (still fine)."""
    ox, oy, _ = layout.origin
    span_x = max(6.0, layout.cols * layout.col_pitch + 30.0)
    span_y = max(6.0, layout.rows * layout.row_pitch + 40.0)
    x0 = ox + layout.cols * layout.col_pitch / 2 - span_x / 2
    y0 = oy + layout.rows * layout.row_pitch / 2 - span_y / 2
    n = _TERRAIN_RES
    pts, uvs = [], []
    for j in range(n):
        for i in range(n):
            x = x0 + span_x * i / (n - 1)
            y = y0 + span_y * j / (n - 1)
            pts.append(Gf.Vec3f(x, y, terrain_height(x, y, farm_cfg) - 0.02))
    counts, idx = [], []
    for j in range(n - 1):
        for i in range(n - 1):
            a, b = j * n + i, j * n + i + 1
            c, d = (j + 1) * n + i + 1, (j + 1) * n + i
            counts.append(4)
            idx.extend([a, b, c, d])
    mesh = UsdGeom.Mesh.Define(stage, "/World/Ground")
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateSubdivisionSchemeAttr("none")
    _bind(mesh.GetPrim(), material)


def _build_turbine(stage, path, spec, ground_z, looks) -> str:
    """A wind-turbine proxy: tower + nacelle + a 3-blade rotor on a Hub Xform.
    Returns the Hub prim path so the runtime can spin it. The rotor axis is +Y
    (rotor faces along the row), blades splay in the local XZ plane."""
    hub_h = float(spec.get("hub_height", 18.0))
    blade_len = float(spec.get("blade_len", 8.0))
    x, y = spec["pos"]
    root = UsdGeom.Xform.Define(stage, path)
    UsdGeom.XformCommonAPI(root).SetTranslate(Gf.Vec3d(float(x), float(y), float(ground_z)))

    tower = UsdGeom.Cylinder.Define(stage, path + "/Tower")
    tower.CreateAxisAttr("Z")
    tower.CreateHeightAttr(hub_h)
    tower.CreateRadiusAttr(0.6)
    UsdGeom.XformCommonAPI(tower).SetTranslate(Gf.Vec3d(0, 0, hub_h / 2))
    _bind(tower.GetPrim(), looks["turbine"])
    _add_collision(tower.GetPrim())

    nac = UsdGeom.Cube.Define(stage, path + "/Nacelle")
    nac.CreateSizeAttr(1.0)
    napi = UsdGeom.XformCommonAPI(nac)
    napi.SetTranslate(Gf.Vec3d(0, 0, hub_h))
    napi.SetScale(Gf.Vec3f(1.2, 2.6, 1.2))
    _bind(nac.GetPrim(), looks["turbine"])
    _add_collision(nac.GetPrim())

    # Hub: the prim the runtime rotates (about +Y). Blades parented under it.
    hub = UsdGeom.Xform.Define(stage, path + "/Hub")
    UsdGeom.XformCommonAPI(hub).SetTranslate(Gf.Vec3d(0, -1.4, hub_h))
    for b in range(3):
        blade = UsdGeom.Cube.Define(stage, f"{path}/Hub/Blade_{b}")
        blade.CreateSizeAttr(1.0)
        bapi = UsdGeom.XformCommonAPI(blade)
        ang = 120.0 * b
        bapi.SetRotate((0.0, ang, 0.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)
        # Offset the blade outward along its (rotated) local +Z via translate then
        # scale to a long thin paddle.
        import math as _m

        r = blade_len / 2
        bapi.SetTranslate(
            Gf.Vec3d(r * _m.sin(_m.radians(ang)), 0.0, r * _m.cos(_m.radians(ang)))
        )
        bapi.SetScale(Gf.Vec3f(0.35, 0.12, blade_len))
        _bind(blade.GetPrim(), looks["turbine"])
        _add_collision(blade.GetPrim())
    return path + "/Hub"


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

    # --- lighting: a directional sun (relief/shadows) + dome ambient fill ----
    sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
    sun.CreateIntensityAttr(2400.0)
    sun.CreateAngleAttr(0.53)  # sun's angular diameter -> soft shadow edges
    sun.CreateColorAttr(Gf.Vec3f(1.0, 0.97, 0.9))
    # Point it down from the south-west (elevation ~50°): tilt about X, spin about Z.
    UsdGeom.XformCommonAPI(sun).SetRotate(
        (-50.0, 0.0, 25.0), UsdGeom.XformCommonAPI.RotationOrderXYZ
    )
    dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
    dome.CreateIntensityAttr(300.0)  # ambient fill only; sun does the modelling

    # --- shared material set (5 looks, reused across all prims) --------------
    looks = {
        name: _make_material(stage, f"/World/Looks/{name}", diff, emis, rough, metal)
        for name, (diff, emis, rough, metal) in _LOOKS.items()
    }

    # --- ground: heightfield mesh sampling the shared terrain function --------
    _build_ground_heightfield(stage, farm_cfg, layout, looks["ground"])

    pdim = farm_cfg.get("panel", {})
    tilt_deg = float(pdim.get("tilt_deg", 20.0))
    mount_h = float(pdim.get("mount_height", PANEL_MOUNT_HEIGHT))
    pw = float(pdim.get("width", 1.0))
    pl = float(pdim.get("length", 2.0))
    ph = float(pdim.get("height", 0.05))
    n_ccol = int(pdim.get("cell_cols", 6))   # cells across width (x)
    n_crow = int(pdim.get("cell_rows", 10))  # cells along length (y)
    seed = int(farm_cfg.get("seed", 0))

    n_fault = 0
    for site in layout.sites:
        path = pv.panel_path("/World/Farm", site.row, site.col)
        prim = pv.create_panel(
            stage, path, site.panel_id, site.row, site.col, site.geo_position
        )
        # Place + tilt the panel Xform (Z-up: tilt about the row axis = X).
        # site.position.z already follows the terrain, so panels sit on the grade.
        x, y, gz = site.position
        api = UsdGeom.XformCommonAPI(prim)
        api.SetTranslate(Gf.Vec3d(x, y, gz + mount_h))
        api.SetRotate((tilt_deg, 0.0, 0.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)

        # Substrate / backsheet + aluminium frame look (the dark grid lines
        # between cells show through as the module frame).
        geom = UsdGeom.Cube.Define(stage, path + "/Geom")
        geom.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(geom).SetScale(Gf.Vec3f(pw, pl, ph))
        _bind(geom.GetPrim(), looks["frame"])

        # Seeded fault on the source of truth; localized to a set of cells.
        state = faults.get(site.panel_id, pv.PanelState.HEALTHY)
        if state is not pv.PanelState.HEALTHY:
            prim.GetAttribute(pv.ATTR_STATE).Set(state.value)
            n_fault += 1
        rng = random.Random(f"{seed}:{site.panel_id}")
        bad = fault_cells(state, n_crow, n_ccol, rng)
        fault_look = {
            pv.PanelState.SOILED: looks["cell_soiled"],
            pv.PanelState.HOTSPOT: looks["cell_hotspot"],
        }.get(state)

        # --- cell grid on the top face: each cell a thin inset tile ----------
        cells = UsdGeom.Xform.Define(stage, path + "/Cells")
        cw, cl = pw / n_ccol, pl / n_crow
        gap = 0.86  # tile shrink -> dark grid lines between cells
        for r in range(n_crow):
            for c in range(n_ccol):
                cx = -pw / 2 + (c + 0.5) * cw
                cy = -pl / 2 + (r + 0.5) * cl
                cell = UsdGeom.Cube.Define(stage, f"{path}/Cells/c_{r}_{c}")
                cell.CreateSizeAttr(1.0)
                capi = UsdGeom.XformCommonAPI(cell)
                capi.SetTranslate(Gf.Vec3d(cx, cy, ph / 2 + ph * 0.25))
                capi.SetScale(Gf.Vec3f(cw * gap, cl * gap, ph * 0.5))
                look = fault_look if (fault_look and (r, c) in bad) else looks["cell_healthy"]
                _bind(cell.GetPrim(), look)

        _label(prim, "panel", state.value)

    # --- wind turbines: proxies with a spin-able Hub (runtime turns the blades) -
    turbines = farm_cfg.get("turbines", []) or []
    if turbines:
        # A physics scene so the authored colliders are meaningful once a dynamic
        # (Pegasus/PX4) drone is stepped against them. Inert under kinematic teleport.
        scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
        viz_mat = _keepout_viz_material(stage)
    UsdGeom.Xform.Define(stage, "/World/Turbines")
    for i, spec in enumerate(turbines):
        tx, ty = float(spec["pos"][0]), float(spec["pos"][1])
        gz = terrain_height(tx, ty, farm_cfg)
        tpath = f"/World/Turbines/turbine_{i}"
        hub = _build_turbine(stage, tpath, spec, gz, looks)
        _label(stage.GetPrimAtPath(tpath), "wind_turbine")
        # Record the per-turbine rpm on the Hub so the runtime knows how fast to spin.
        stage.GetPrimAtPath(hub).CreateAttribute(
            "st:rpm", Sdf.ValueTypeNames.Float
        ).Set(float(spec.get("rpm", 10.0)))

        # Translucent no-fly sphere: the SAME rotor keep-out volume the planner
        # enforces (world/keepout.py), made visible. Centred on the rotor hub.
        hub_h = float(spec.get("hub_height", 18.0))
        radius = float(spec.get("blade_len", 8.0)) + _ROTOR_MARGIN
        viz = UsdGeom.Sphere.Define(stage, f"{tpath}/KeepoutViz")
        viz.CreateRadiusAttr(radius)
        # Relative to the turbine root (already at tx,ty,gz): lift to the hub.
        UsdGeom.XformCommonAPI(viz).SetTranslate(Gf.Vec3d(0.0, 0.0, hub_h))
        viz.CreateDisplayOpacityAttr([0.12])
        _bind(viz.GetPrim(), viz_mat)
        # purpose=guide keeps this DEBUG-ONLY: guides are excluded from the
        # default render, so the sphere can never darken/occlude a sensor frame.
        # (It did: as a default-purpose prim these 9-10 m spheres cast shadows
        # over the whole farm and halved frame brightness, masking fault cells.)
        viz.CreatePurposeAttr(UsdGeom.Tokens.guide)

    stage.GetRootLayer().Save()
    print(
        f"built {layout.n_panels} panels ({n_fault} faulted), "
        f"{len(turbines)} turbines, terrain={farm_cfg.get('terrain', {}).get('kind', 'flat')} -> {out}\n"
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
