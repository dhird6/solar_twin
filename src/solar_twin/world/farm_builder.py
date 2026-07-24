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
from solar_twin.world.layout import (
    FarmLayout,
    fault_cells,
    soiling_field,
    soiling_mask,
    terrain_height,
)

# Fallback panel-centre height if farm.yaml omits panel.mount_height.
PANEL_MOUNT_HEIGHT = 0.75

# Physically-plausible UsdPreviewSurface looks, keyed by name. A panel is a grid
# of *cells* over a frame — faults recolor only the affected cells (localized),
# so a soiled panel reads as "dusty patch on a PV module", not a beige rectangle.
# (name -> diffuse, emissive, roughness, metallic)
_LOOKS: dict[str, tuple[tuple, tuple, float, float]] = {
    "cell_healthy": ((0.02, 0.04, 0.13), (0.0, 0.0, 0.0), 0.22, 0.35),  # dark-blue glassy PV
    "cell_hotspot": ((0.14, 0.05, 0.03), (2.2, 0.35, 0.0), 0.5, 0.0),   # hot cell glow
    "frame": ((0.62, 0.63, 0.66), (0.0, 0.0, 0.0), 0.3, 0.9),           # aluminium rail
    "ground": ((0.17, 0.14, 0.10), (0.0, 0.0, 0.0), 1.0, 0.0),          # dry earth (kept dark so the sun doesn't blow it out)
    "turbine": ((0.9, 0.9, 0.92), (0.0, 0.0, 0.0), 0.35, 0.0),          # off-white tower/blade
    "structure": ((0.30, 0.30, 0.33), (0.0, 0.0, 0.0), 0.6, 0.4),       # dark strut / occluder
}

# How finely to tessellate the heightfield ground mesh (verts per axis).
_TERRAIN_RES = 48

# Dust-film sub-grid (tiles across width x along length). Deliberately NOT a
# multiple of the PV cell counts (6 x 10) so the film can never align to the cell
# grid — real dust ignores cell boundaries. Resolution must be well ABOVE the cell
# grid (~8 tiles per cell) or the baked substrate quantises into coarse slabs
# instead of resolving the thin frame lines between cells.
_DUST_SUBGRID = (50, 86)
#: Dust colour, blended per-face over whatever lies beneath (cell or frame gap).
#: Must stay DARK and earth-toned. A pale/bright deposit on a dark panel is the
#: textbook *hotspot* signature, and Cosmos Reason duly misread a lighter dust as
#: "a cluster of bright pixels... indicative of a hotspot" (run 20260724T170822).
#: Real soiling is dirt: it DARKENS and mutes the module, it does not brighten it.
_DUST_RGB = (0.26, 0.21, 0.14)
#: Density above which a sub-tile carries dust at all.
_DUST_THRESHOLD = 0.5

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


def _dust_material(stage) -> UsdShade.Material:
    """Dust shader that takes its colour per-face from the mesh's `displayColor`
    primvar.

    We do NOT use UsdPreviewSurface `opacity`: RTX on this build renders it as a
    hard cutout (verified — a 0.55-opacity film still came out fully opaque, with
    or without `opacityThreshold=0`). Instead the translucency is **baked**: each
    face is pre-blended against whatever lies beneath it (blue cell or light
    frame gap), so the cell grid still reads *through* the dust while every
    material stays opaque and renderer-independent."""
    path = "/World/Looks/dust_film"
    mat = UsdShade.Material.Define(stage, path)
    reader = UsdShade.Shader.Define(stage, path + "/ColorReader")
    reader.CreateIdAttr("UsdPrimvarReader_float3")
    reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
    reader.CreateOutput("result", Sdf.ValueTypeNames.Float3)

    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        reader.ConnectableAPI(), "result"
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.97)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def _build_dust_film(stage, panel_path, pw, pl, ph, n_ccol, n_crow, rng, material) -> int:
    """Author the soiling as ONE mesh lying on the panel glass, with the dust
    colour **baked per face** against what sits beneath it.

    Built on a sub-grid independent of the PV cells, so the patch crosses cell
    boundaries with ragged edges and pools toward the panel's lower (-Y) edge —
    how dust settles on a tilted module. Density varies across the drift (heavy in
    the middle, thin at the edges), so the film looks like grime rather than
    paint. Returns the face count."""
    n_cols, n_rows = _DUST_SUBGRID
    field = soiling_field(n_rows, n_cols, rng)
    # Ragged OUTLINE from a jittered threshold; smooth INTERIOR from the raw
    # field — jittering the shading instead makes the film look dithered.
    tiles = sorted(soiling_mask(field, rng, _DUST_THRESHOLD))
    if not tiles:
        return 0
    tw, tl = pw / n_cols, pl / n_rows
    cw, cl = pw / n_ccol, pl / n_crow
    z = ph * 1.02  # just above the cell tops (cells top out at ~ph)
    cell_rgb = _LOOKS["cell_healthy"][0]
    gap_rgb = _LOOKS["frame"][0]
    half_gap = 0.86 / 2  # cells are inset by this fraction; outside it is frame

    pts, counts, idx, colors = [], [], [], []
    for r, c in tiles:
        x0 = -pw / 2 + c * tw
        y0 = -pl / 2 + r * tl  # r=0 is the -Y (lower) edge
        # What is underneath this tile's centre — a cell face, or the frame gap?
        cx, cy = x0 + tw / 2 + pw / 2, y0 + tl / 2 + pl / 2
        fx = (cx % cw) / cw - 0.5
        fy = (cy % cl) / cl - 0.5
        base_rgb = cell_rgb if (abs(fx) < half_gap and abs(fy) < half_gap) else gap_rgb
        # Blend dust over it; heavier drifts are more opaque, thin edges less so.
        # Alpha must stay HIGH. Dust settles on the aluminium frame too, so the
        # bright frame lines must be muted along with the cells. At low alpha the
        # 0.62-albedo frame survives at ~0.54 while the 0.02 cells go dark — that
        # manufactured a grid of BRIGHT LINES inside the patch, which Cosmos Reason
        # read as "a cluster of bright pixels ... characteristic of a hotspot"
        # (runs 20260724T170822 / T171133). The grid should remain faintly visible
        # as low contrast, never as bright highlights.
        a = max(0.72, min(0.94, 0.72 + 0.40 * (field[(r, c)] - _DUST_THRESHOLD)))
        rgb = tuple(base_rgb[i] * (1.0 - a) + _DUST_RGB[i] * a for i in range(3))

        b = len(pts)
        pts.extend(
            [
                Gf.Vec3f(x0, y0, z),
                Gf.Vec3f(x0 + tw, y0, z),
                Gf.Vec3f(x0 + tw, y0 + tl, z),
                Gf.Vec3f(x0, y0 + tl, z),
            ]
        )
        counts.append(4)
        idx.extend([b, b + 1, b + 2, b + 3])
        colors.append(Gf.Vec3f(*rgb))

    mesh = UsdGeom.Mesh.Define(stage, f"{panel_path}/DustFilm")
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateSubdivisionSchemeAttr("none")
    # One colour per face ("uniform" interpolation) — the baked dust-over-substrate.
    cp = mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.uniform)
    cp.Set(colors)
    _bind(mesh.GetPrim(), material)
    return len(counts)


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


def _build_shading_occluder(stage, farm_cfg, layout, material) -> bool:
    """Author a thin bar suspended UP-SUN of the row so its shadow falls as a hard
    line ACROSS the elevated panel surfaces (not the ground).

    The subtlety SC-05 exposed: a distant turbine's shadow sails over the ~0.8 m
    panels onto the ground. To land a shadow ON the tilted panel plane, the
    occluder must sit on the sun side, just above panel height, so the cast ray
    intersects the panel. Placement is derived from the sun angle in `sun:` so it
    stays correct if the elevation changes. This is the reliable, phase-independent
    'shading' stressor for KPI-03 (vs the turbine's intermittent blade shadow)."""
    spec = farm_cfg.get("shading", {}) or {}
    if not spec.get("enabled", False):
        return False
    import math

    sun = farm_cfg.get("sun", {}) or {}
    elev = math.radians(float(sun.get("elevation_deg", 45.0)))
    panel = farm_cfg.get("panel", {})
    h_p = float(panel.get("mount_height", 0.75)) + float(panel.get("height", 0.05))
    # Bar height above ground, and how far up-sun (+Y) it must sit so its shadow
    # lands on the row at panel height: dY = (z_bar - h_p) / tan(elev).
    z_bar = float(spec.get("height", h_p + 1.6))
    dy = (z_bar - h_p) / max(0.2, math.tan(elev))
    thick = float(spec.get("thickness", 0.18))
    yaw = float(spec.get("yaw_deg", 0.0))  # rotate the bar in-plane -> diagonal shadow
    # Span the whole row in X (plus overhang) so every panel gets the shadow line.
    # A yawed bar's ends swing in Y; shorten the span as yaw grows so the ends stay
    # over the row rather than casting their shadow off into the dirt.
    ox = layout.origin[0]
    span_x = (layout.cols * layout.col_pitch + 2.0) * max(0.4, math.cos(math.radians(yaw)))
    cx = ox + (layout.cols - 1) * layout.col_pitch / 2.0
    bar = UsdGeom.Cube.Define(stage, "/World/ShadingBar")
    bar.CreateSizeAttr(1.0)
    api = UsdGeom.XformCommonAPI(bar)
    api.SetTranslate(Gf.Vec3d(cx, layout.origin[1] + dy, z_bar))
    if yaw:
        api.SetRotate((0.0, 0.0, yaw), UsdGeom.XformCommonAPI.RotationOrderXYZ)
    api.SetScale(Gf.Vec3f(span_x, thick, thick))
    _bind(bar.GetPrim(), material)
    print(
        f"  shading occluder: bar at y={dy:.2f} z={z_bar:.2f} yaw={yaw:.0f} "
        f"(sun elev {math.degrees(elev):.0f})"
    )
    return True


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
    sun.CreateAngleAttr(0.53)  # sun's angular diameter -> soft shadow edges
    sun.CreateColorAttr(Gf.Vec3f(1.0, 0.97, 0.9))
    # Sun elevation/azimuth are scenario knobs: a LOW sun casts long turbine-blade
    # shadows across the row (the SC-05 false-fault stressor). elevation_deg maps
    # to -X tilt (90 = overhead/noon, ~14 = near horizon); azimuth_deg to Z spin.
    sun_cfg = farm_cfg.get("sun", {}) or {}
    elev = float(sun_cfg.get("elevation_deg", 50.0))
    azim = float(sun_cfg.get("azimuth_deg", 25.0))
    # Dim a low sun a little (grazing light is less intense) so frames don't blow out.
    sun.CreateIntensityAttr(2400.0 if elev >= 30.0 else 1700.0)
    UsdGeom.XformCommonAPI(sun).SetRotate(
        (-elev, 0.0, azim), UsdGeom.XformCommonAPI.RotationOrderXYZ
    )
    dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
    # Ambient fill (config knob): lowering it deepens shadows toward near-black — a
    # harder KPI-03 stressor where a shadow reads more like a dark defect.
    dome.CreateIntensityAttr(float(sun_cfg.get("ambient", 300.0)))

    # --- shared material set (5 looks, reused across all prims) --------------
    looks = {
        name: _make_material(stage, f"/World/Looks/{name}", diff, emis, rough, metal)
        for name, (diff, emis, rough, metal) in _LOOKS.items()
    }
    dust_mat = _dust_material(stage)

    # --- ground: heightfield mesh sampling the shared terrain function --------
    _build_ground_heightfield(stage, farm_cfg, layout, looks["ground"])

    # --- optional shading occluder (KPI-03 hard-shadow stressor) --------------
    _build_shading_occluder(stage, farm_cfg, layout, looks["structure"])

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
        # Cell-level faults (hotspot) recolor cells; soiling is a film authored
        # after the cells as a translucent overlay that ignores the cell grid.
        bad = fault_cells(state, n_crow, n_ccol, rng)
        fault_look = {pv.PanelState.HOTSPOT: looks["cell_hotspot"]}.get(state)

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

        # Soiling: a translucent dust film over the glass, crossing cell borders.
        if state is pv.PanelState.SOILED:
            _build_dust_film(stage, path, pw, pl, ph, n_ccol, n_crow, rng, dust_mat)

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
    ap.add_argument("farm", nargs="?", help="path to farm.yaml (omit with --scenario)")
    ap.add_argument(
        "--scenario",
        help="build the composed farm from a scenario YAML (sun/faults/turbine "
        "overrides applied) instead of a bare farm.yaml",
    )
    ap.add_argument("--out", default="assets/farm.usd", help="output USD path")
    args = ap.parse_args(argv)
    if args.scenario:
        from solar_twin.scenario import load_scenario

        farm_cfg = load_scenario(args.scenario).farm_cfg
    elif args.farm:
        farm_cfg = _load_farm_cfg(args.farm)
    else:
        ap.error("provide a farm.yaml path, or --scenario")
    build(farm_cfg, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
