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
import math
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
    terrain_feature_step,
    terrain_height,
)
from solar_twin.world import sky as sky_model
from solar_twin.world import textures as tex
from solar_twin.world.siting import rpm_to_deg_per_s

# Fallback panel-centre height if farm.yaml omits panel.mount_height.
PANEL_MOUNT_HEIGHT = 0.75

# Physically-plausible UsdPreviewSurface looks, keyed by name. A panel is a grid
# of *cells* over a frame — faults recolor only the affected cells (localized),
# so a soiled panel reads as "dusty patch on a PV module", not a beige rectangle.
# (name -> diffuse, emissive, roughness, metallic)
_LOOKS: dict[str, tuple[tuple, tuple, float, float]] = {
    "cell_healthy": ((0.02, 0.04, 0.13), (0.0, 0.0, 0.0), 0.22, 0.35),  # dark-blue glassy PV
    "cell_hotspot": ((0.14, 0.05, 0.03), (2.2, 0.35, 0.0), 0.5, 0.0),   # hot cell glow
    # ⚠ THE PANEL FRAME IS DEFERRED — do not texture, do not re-tune. Its 0.62
    # albedo is what the soiling film's baked alpha window (0.72-0.94) was tuned
    # against: at lower alpha this rail survives at ~0.54 while the 0.02 cells go
    # dark, manufacturing a grid of BRIGHT LINES that Cosmos Reason read as "a
    # cluster of bright pixels ... characteristic of a hotspot". Four stacked
    # fixes got this right. Changing it is a flagged decision with a measurement.
    "panel_frame": ((0.62, 0.63, 0.66), (0.0, 0.0, 0.0), 0.3, 0.9),     # aluminium rail
    # Same base look, separate material — SO THE FENCE CAN BE TEXTURED WITHOUT
    # TOUCHING THE PANEL FRAME. These were one material, which put "add PBR to the
    # fence" in direct conflict with "defer the panel frame"; the values are
    # identical on purpose, so the split alone changes nothing visually.
    "fence_frame": ((0.62, 0.63, 0.66), (0.0, 0.0, 0.0), 0.3, 0.9),     # galvanised fence steel
    # Kutch is pale, dusty, sun-bleached ground — NOT the near-black it used to
    # be. The old value was chosen when the ground was a small backdrop behind a
    # 10-panel row and the worry was blowing out the frame; across a 320 x 647 m
    # site it read as cold grey slate and made the whole plant look synthetic.
    # Albedo ~0.3 is where real dry soil sits. 0.44 measured as a near-white
    # blowout at this sun intensity, which buried the roads and fence in glare.
    "ground": ((0.30, 0.25, 0.19), (0.0, 0.0, 0.0), 1.0, 0.0),          # dry desert earth
    "turbine": ((0.9, 0.9, 0.92), (0.0, 0.0, 0.0), 0.35, 0.0),          # off-white tower/blade
    "structure": ((0.30, 0.30, 0.33), (0.0, 0.0, 0.0), 0.6, 0.4),       # dark strut / occluder
    "road": ((0.20, 0.19, 0.17), (0.0, 0.0, 0.0), 0.95, 0.0),           # compacted gravel haul road
    "concrete": ((0.46, 0.45, 0.43), (0.0, 0.0, 0.0), 0.85, 0.0),       # equipment pad
    "equipment": ((0.55, 0.57, 0.58), (0.0, 0.0, 0.0), 0.45, 0.2),      # inverter cabinet
}

# How finely to tessellate the heightfield ground mesh (verts per axis). Now a
# FLOOR on the graded grid rather than the whole story — see `_graded_axis`.
_TERRAIN_RES = 48

#: Hard cap on ground-mesh vertices per axis, so the graded grid cannot blow the
#: render budget. 560 x 560 = 313k verts is the worst case; in practice the real
#: site comes out far below it (measured: 3.7k verts for BLOCK-02 and 68.6k for the
#: whole 4.8 km S05b plot, against 25.6k for the old uniform sheet at every size).
#:
#: ⚠ This is a cap on COST, and it must not silently become a cap on FIDELITY. At
#: 220 the whole plot was forced to 42.2 m spacing against a 20 m DEM — a 2x
#: undersample of the very terrain the panels are mounted from, which is the aliasing
#: `layout.terrain_feature_step` exists to prevent. Ground vertices are cheap next to
#: 680k panel prims, so the cap is set where it stops mattering, and
#: `_build_ground_heightfield` LOGS when it bites instead of quietly smoothing the
#: desert.
_GROUND_MAX_AXIS = 560

#: How fast ground-mesh spacing may grow per step once outside the site. 1.35
#: reaches a 5 km horizon in ~14 verts while keeping adjacent quads similar enough
#: that the expansion does not read as visible banding on a raking shot.
_GROUND_GRADE_GROWTH = 1.35

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


#: World-metres covered by one texture tile, per surface. Sized to the thing:
#: soil drifts read over metres, a cast concrete pad's pitting over centimetres.
#: Too large and the map turns to mush; too small and it visibly repeats.
_UV_TILE_M = {
    "ground": 24.0,
    "road": 8.0,
    "concrete": 4.0,
    "equipment": 2.0,
    "structure": 1.5,
    "fence_frame": 1.5,
}


def _textured_material(
    stage: Usd.Stage,
    path: str,
    name: str,
    tex_paths: dict,
    metallic: float,
    diffuse_from_primvar: bool = False,
    use_normal: bool = True,
) -> UsdShade.Material:
    """A `UsdPreviewSurface` driven by generated albedo / roughness / normal maps.

    `diffuse_from_primvar=True` keeps `diffuseColor` wired to the mesh's
    `displayColor` primvar and applies only the roughness and normal maps. That
    is what the GROUND needs: its per-vertex colour carries both the
    three-octave grading variation and the aerial-perspective fade that melts the
    mesh rim into the horizon haze — replacing it with a flat texture would bring
    back the hard ground edge with void beyond it (Session 10c). So the ground
    gains real microsurface without losing either of those.

    `metallic` stays a constant: none of these surfaces has a metalness *map*, and
    inventing one would be decoration rather than measurement.
    """
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)

    st = UsdShade.Shader.Define(stage, path + "/UvReader")
    st.CreateIdAttr("UsdPrimvarReader_float2")
    st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    st_out = st.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    def _tex(channel: str, out_type, out_name: str):
        t = UsdShade.Shader.Define(stage, f"{path}/{channel.capitalize()}Tex")
        t.CreateIdAttr("UsdUVTexture")
        t.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(tex_paths[channel])
        t.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st_out)
        # Repeat, not clamp: these are tiled across hundreds of metres.
        t.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        t.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
        return t, t.CreateOutput(out_name, out_type)

    if diffuse_from_primvar:
        reader = UsdShade.Shader.Define(stage, path + "/ColorReader")
        reader.CreateIdAttr("UsdPrimvarReader_float3")
        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            reader.ConnectableAPI(), "result"
        )
        reader.CreateOutput("result", Sdf.ValueTypeNames.Float3)
    else:
        _, rgb_out = _tex("albedo", Sdf.ValueTypeNames.Float3, "rgb")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            rgb_out
        )

    _, r_out = _tex("roughness", Sdf.ValueTypeNames.Float, "r")
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(r_out)

    if use_normal:
        _, n_out = _tex("normal", Sdf.ValueTypeNames.Float3, "rgb")
        ntex = UsdShade.Shader(stage.GetPrimAtPath(f"{path}/NormalTex"))
        # A normal map is stored 0..1 but read as -1..1; without this bias/scale the
        # surface is lit as if every normal pointed into the +XYZ octant.
        ntex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2, 2, 2, 1))
        ntex.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1, -1, -1, 0))
        # Normal maps are non-colour data; letting the renderer sRGB-decode them
        # bends the normals.
        ntex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
        shader.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(n_out)

    rtex = UsdShade.Shader(stage.GetPrimAtPath(f"{path}/RoughnessTex"))
    rtex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")

    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def _planar_uvs(pts, tile_m: float):
    """World-space planar UVs (X/Y over `tile_m`) as a vertex-interpolated primvar.

    Planar projection is correct for the surfaces this is used on — ground, roads
    and pads are all near-horizontal — and it means adjacent quads share a
    continuous UV field, so a road built from many subdivided segments does not
    show a texture reset at every seam.
    """
    return [Gf.Vec2f(p[0] / tile_m, p[1] / tile_m) for p in pts]


def _set_uvs(mesh, pts, tile_m: float) -> None:
    pv_api = UsdGeom.PrimvarsAPI(mesh.GetPrim())
    primvar = pv_api.CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
    )
    primvar.Set(_planar_uvs(pts, tile_m))


#: Where instanced panel prototypes live. A USD *class* prim: composed on demand
#: by references, never imaged in its own right.
_PROTO_ROOT = "/__Prototypes"


def _panel_prototype(stage, cache: dict, sx, sy, ph, n_ccol, n_crow, looks) -> str:
    """Define (once per distinct module size) the geometry of a HEALTHY panel and
    return its class-prim path, for healthy panels to reference (`IF-09`).

    This is what makes the real block renderable. Authoring every module's
    geometry inline costs 75 prims each — one Geom plus a `n_ccol x n_crow` cell
    grid — so Khavda's 30,016 modules came to ~2.25M prims, which is why the
    whole site had never been built at once. Referencing one prototype leaves a
    single Xform per panel: ~30k prims for the same plant, with Hydra drawing the
    cells as instances.

    The panel prim itself stays a real, per-panel prim carrying the `pv:` attrs,
    so the USD stage remains the source of truth for panel state and verdict
    writeback is unchanged — only the *geometry* is shared. Prototype roots
    author no transform, so each instance's own translate/rotate still wins.
    """
    key = (round(sx, 6), round(sy, 6), round(ph, 6), n_ccol, n_crow)
    if key in cache:
        return cache[key]

    if not stage.GetPrimAtPath(_PROTO_ROOT):
        stage.CreateClassPrim(_PROTO_ROOT)
    path = f"{_PROTO_ROOT}/Panel_{len(cache)}"
    UsdGeom.Xform.Define(stage, path)

    geom = UsdGeom.Cube.Define(stage, path + "/Geom")
    geom.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(geom).SetScale(Gf.Vec3f(sx, sy, ph))
    _bind(geom.GetPrim(), looks["panel_frame"])

    cells = UsdGeom.Xform.Define(stage, path + "/Cells")  # noqa: F841 — parent scope
    cw, cl = sx / n_ccol, sy / n_crow
    gap = 0.86
    for r in range(n_crow):
        for c in range(n_ccol):
            cell = UsdGeom.Cube.Define(stage, f"{path}/Cells/c_{r}_{c}")
            cell.CreateSizeAttr(1.0)
            capi = UsdGeom.XformCommonAPI(cell)
            capi.SetTranslate(
                Gf.Vec3d(-sx / 2 + (c + 0.5) * cw, -sy / 2 + (r + 0.5) * cl, ph / 2 + ph * 0.25)
            )
            capi.SetScale(Gf.Vec3f(cw * gap, cl * gap, ph * 0.5))
            _bind(cell.GetPrim(), looks["cell_healthy"])

    cache[key] = path
    return path


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


def _articulate_turbine(stage, path: str, spec: dict) -> None:
    """Turn an authored turbine proxy into a real USD articulation (`FR-11`).

    The proxy already carries colliders, but they are **inert**: the runtime spins
    the Hub by writing its transform each frame, which is animation, not dynamics —
    nothing can be pushed by a blade that is teleported through it (`RISK-11`).
    This adds the articulation so the rotor is simulated:

    - `ArticulationRootAPI` on the turbine root,
    - the **Hub** becomes a rigid body (its blade children are already colliders,
      so the rotor is one body with three blade colliders rather than three bodies
      — cheaper, and correct since the blades cannot move relative to each other),
    - a **revolute joint** about the rotor axis (+Y, matching `_build_turbine`'s
      geometry) from the static tower to the hub,
    - an **angular drive** in velocity mode, so the rotor is *driven* at the
      turbine's rpm and can still be resisted, rather than being kinematically
      dragged to a pose.

    The tower and nacelle stay static colliders: they are bolted to the ground, and
    a fixed joint to the world would add a body for the solver to integrate for no
    behavioural gain.

    ⚠ Opt-in (`turbines_articulated: true`). Enabling it means the runtime must
    STOP writing the Hub transform, or the kinematic write fights the solver — see
    `sim_runtime`'s spin loop, which skips articulated hubs. Every KPI run recorded
    so far used the kinematic proxy, so the default is unchanged deliberately.

    All four APIs used here were verified present on this build (Isaac Sim
    6.0.1-rc.7 / PhysX 110.1.13) rather than recalled: `ArticulationRootAPI`,
    `RigidBodyAPI`, `RevoluteJoint`, `DriveAPI`.
    """
    root = stage.GetPrimAtPath(path)
    hub = stage.GetPrimAtPath(path + "/Hub")
    tower = stage.GetPrimAtPath(path + "/Tower")
    if not (root and hub and tower):
        print(f"  [warn] cannot articulate {path}: missing root/Hub/Tower")
        return
    try:
        UsdPhysics.ArticulationRootAPI.Apply(root)

        UsdPhysics.RigidBodyAPI.Apply(hub)
        # Explicit mass: derived-from-collider mass on a long thin paddle is
        # plausible but arbitrary, and rotor inertia sets how much a gust
        # perturbs it — a number worth stating rather than inheriting.
        UsdPhysics.MassAPI.Apply(hub).CreateMassAttr(
            float(spec.get("rotor_mass_kg", 2000.0))
        )

        joint = UsdPhysics.RevoluteJoint.Define(stage, path + "/RotorJoint")
        joint.CreateAxisAttr("Y")  # matches the rotor axis in `_build_turbine`
        joint.CreateBody0Rel().SetTargets([tower.GetPath()])
        joint.CreateBody1Rel().SetTargets([hub.GetPath()])
        # Free-spinning: a revolute joint with no limits set is unlimited, which
        # is what a rotor is. Limits here would make it an oscillating flap.

        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
        drive.CreateTypeAttr("force")
        # Velocity control: zero stiffness (no target *position* to hold), damping
        # supplies the torque that chases target velocity. Stiffness > 0 would make
        # the rotor spring back to an angle.
        drive.CreateStiffnessAttr(0.0)
        drive.CreateDampingAttr(float(spec.get("rotor_damping", 1.0e5)))
        drive.CreateTargetVelocityAttr(rpm_to_deg_per_s(spec.get("rpm", 10.0)))
        drive.CreateMaxForceAttr(float(spec.get("rotor_max_torque", 1.0e7)))

        # Marks the hub for `sim_runtime`, which must not also write its transform.
        hub.CreateAttribute("st:articulated", Sdf.ValueTypeNames.Bool).Set(True)
    except Exception as exc:  # noqa: BLE001 — fall back to the kinematic proxy
        print(f"  [warn] articulation skipped for {path}: {exc}")


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
    gap_rgb = _LOOKS["panel_frame"][0]
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


def _graded_axis(
    lo: float,
    hi: float,
    fine_step: float,
    far_lo: float,
    far_hi: float,
    growth: float = _GROUND_GRADE_GROWTH,
    max_n: int = _GROUND_MAX_AXIS,
) -> list[float]:
    """Vertex coordinates along one ground axis: uniform `fine_step` across the
    near field `[lo, hi]`, then geometrically expanding steps out to the horizon.

    This is the "high-res visual mesh near the hardware, decimated far field"
    split, done as ONE tensor-product grid rather than two meshes — two meshes
    would need their shared edge stitched, and any mismatch there is a visible
    crack in the desert.

    Why it replaced a uniform sheet: the ground has to reach the sky dome (several
    km) while resolving terrain the panels are mounted from (tens of metres). A
    single uniform grid cannot do both inside a sane vertex count, and the old
    compromise resolved neither — 40-65 m spacing, which undersampled every
    terrain source in the project. Grading spends the vertices where geometry
    stands and coarsens where there is nothing to occlude.
    """
    span = max(fine_step, hi - lo)
    # Reserve part of the budget for the two skirts, or a big fine region leaves no
    # vertices to reach the horizon with and the ground stops short of the sky.
    n_fine = min(int(math.ceil(span / fine_step)) + 1, max(4, int(max_n * 0.65)))
    step = span / (n_fine - 1)
    vals = [lo + step * i for i in range(n_fine)]

    for sign, limit in ((-1.0, far_lo), (1.0, far_hi)):
        edge = vals[0] if sign < 0 else vals[-1]
        s, out = step, []
        while (edge - limit) * sign < 0 and len(out) < max_n:
            s *= growth
            edge += sign * s
            out.append(edge)
        if out:
            # Land the outermost vertex exactly on the horizon so terrain meets sky.
            out[-1] = limit
            vals = list(reversed(out)) + vals if sign < 0 else vals + out
    return vals


def ground_extent(farm_cfg, layout, horizon_m: float) -> tuple[float, float, float, float]:
    """The rectangle the ground mesh covers, in stage metres.

    Exported (and computed once) because the ground mesh is the floor everything
    else stands on: anything authored beyond it floats in the void against the sky
    dome. `_build_osm_layer` clips real geography to exactly this box, so terrain
    and geography cannot disagree about where the world ends.
    """
    min_x, min_y, max_x, max_y = layout.bounds()
    extent = max(max_x - min_x, max_y - min_y)
    reach = max(horizon_m, extent / 2.0 + max(60.0, 0.12 * extent) + 30.0)
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    return (cx - reach, cy - reach, cx + reach, cy + reach)


def _build_ground_heightfield(stage, farm_cfg, layout, material, horizon_m: float = 30.0) -> None:
    """A tessellated ground mesh sampling the SAME terrain_height() the panels and
    waypoints use, so the visible ground matches where things are mounted. Flat
    terrain degenerates to a flat mesh (still fine).

    The tessellation is GRADED (`_graded_axis`): fine enough near the hardware to
    reproduce the terrain source exactly, coarsening outward to the sky dome. A
    uniform sheet at these extents aliased the terrain badly enough to bury panels
    in the drawn ground — see `layout.terrain_feature_step`.
    """
    # Size the ground from the ACTUAL panel bounding box, not rows x pitch. An
    # imported CAD site is irregular (varying table lengths, aisles, gaps) and has
    # no meaningful row/col rectangle — deriving the span from the grid left the
    # terrain far too small to cover the farm.
    min_x, min_y, max_x, max_y = layout.bounds()
    extent = max(max_x - min_x, max_y - min_y)
    # The near field: the hardware plus enough room that the grading transition is
    # not right against the fence, where a raking shot would read it as a ridge.
    pad = max(60.0, 0.12 * extent)
    # Reach to the HORIZON, not just past the last panel. A 30 m margin is fine
    # standing in an aisle and wrong from 300 m up: the ground ran out and the
    # camera saw the sky dome's underside as a grey floor beyond the plant, which
    # made a 320 x 647 m site look like a model on a table. `horizon_m` is the
    # sky dome's base radius, so terrain now meets sky wherever you look.
    gx0, gy0, gx1, gy1 = ground_extent(farm_cfg, layout, horizon_m)
    reach = (gx1 - gx0) / 2.0
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0

    # The spacing the terrain source actually justifies. `flat` returns 0 (no
    # detail at any spacing), so fall back to the historic density there.
    fine = terrain_feature_step(farm_cfg)
    if fine <= 0.0:
        fine = max(20.0, 2.0 * reach / _TERRAIN_RES)

    xs = _graded_axis(min_x - pad, max_x + pad, fine, cx - reach, cx + reach)
    ys = _graded_axis(min_y - pad, max_y + pad, fine, cy - reach, cy + reach)
    nx, ny = len(xs), len(ys)
    pts, uvs = [], []
    for y in ys:
        for x in xs:
            pts.append(Gf.Vec3f(x, y, terrain_height(x, y, farm_cfg) - 0.02))
    counts, idx = [], []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b = j * nx + i, j * nx + i + 1
            c, d = (j + 1) * nx + i + 1, (j + 1) * nx + i
            counts.append(4)
            idx.extend([a, b, c, d])
    # What the mesh ACTUALLY resolves over the hardware, measured off the vertices
    # rather than off the requested `fine` — the per-axis cap can coarsen it, and a
    # ground mesh that undersamples its own DEM is how panels end up buried in the
    # drawn surface (`layout.terrain_feature_step`).
    inner = [b - a for a, b in zip(xs, xs[1:]) if min_x <= a <= max_x] or [fine]
    got = max(inner)
    print(
        f"  ground: {nx} x {ny} = {len(pts):,} verts, {got:.1f} m spacing over the "
        f"site, reaching {reach:,.0f} m",
        flush=True,
    )
    if got > fine * 1.2:
        print(
            f"  [warn] ground mesh resolves {got:.1f} m but the terrain source "
            f"carries {fine:.1f} m — capped at {_GROUND_MAX_AXIS} verts/axis, so the "
            "relief is smoothed. Raise the cap if the drape matters here.",
            flush=True,
        )
    mesh = UsdGeom.Mesh.Define(stage, "/World/Ground")
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateSubdivisionSchemeAttr("none")
    _set_uvs(mesh, pts, _UV_TILE_M["ground"])
    # Break up the flat tan sheet. A uniform ground over 320 x 647 m reads as a
    # backdrop, not terrain: with nothing varying, the eye gets no scale cue and
    # the whole plant looks like a model. Low-frequency, deterministic (a seeded
    # hash of position, no numpy), and subtle — this is dust and grading colour
    # variation, NOT invented topography, which stays behind terrain.kind.
    base = _LOOKS["ground"][0]
    haze = _sky_colour(0.0)
    site_r = max(1.0, max(max_x - min_x, max_y - min_y) / 2.0)
    ccx, ccy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    colors = []
    for pt in pts:
        # Three octaves on deliberately incommensurate frequencies. A single
        # sin*cos pair (what this was) beats into visible corduroy stripes across
        # a site this large — regular enough that the eye reads it as a texture
        # bug rather than as ground.
        x, y = pt[0], pt[1]
        n = (
            0.55 * math.sin(x * 0.0163 + 1.7) * math.cos(y * 0.0209 - 0.4)
            + 0.30 * math.sin(x * 0.0571 - y * 0.0433 + 2.1)
            + 0.15 * math.cos(x * 0.1373 + y * 0.1117)
        )
        f = 1.0 + 0.13 * n
        c = [min(1.0, v * f) for v in base]
        # Aerial perspective: fade the ground into the sky's own horizon colour
        # with distance. Without it the mesh ends in a hard edge with void beyond
        # — visible from any altitude, because a real horizon is tens of km away
        # and no finite plane reaches it. This makes the rim melt instead.
        d = math.hypot(x - ccx, y - ccy) / site_r
        t = min(1.0, max(0.0, (d - 2.0) / 6.0)) ** 0.8
        colors.append(Gf.Vec3f(*(a + (b - a) * t for a, b in zip(c, haze))))
    mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(colors)
    _bind(mesh.GetPrim(), material)


def _vertex_colour_material(stage, path: str, roughness: float, emissive: bool):
    """A shader that takes its colour per-vertex/face from the mesh's
    `displayColor` primvar — the same trick the dust film uses, for the same
    reason: it needs a gradient across ONE mesh, and a UsdPreviewSurface can only
    hold a single constant colour.

    `emissive=True` wires the primvar into `emissiveColor` instead of
    `diffuseColor`, so the surface is self-lit. That is what a sky needs: a dome
    lit BY the scene's own sun would be dark on the side facing away from it.
    """
    mat = UsdShade.Material.Define(stage, path)
    reader = UsdShade.Shader.Define(stage, path + "/ColorReader")
    reader.CreateIdAttr("UsdPrimvarReader_float3")
    reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
    reader.CreateOutput("result", Sdf.ValueTypeNames.Float3)

    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    channel = "emissiveColor" if emissive else "diffuseColor"
    shader.CreateInput(channel, Sdf.ValueTypeNames.Color3f).ConnectToSource(
        reader.ConnectableAPI(), "result"
    )
    if emissive:
        # Black base so nothing but the emission shows; the sky must not pick up
        # bounce light from the farm.
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0, 0, 0))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat


def _sky_colour(frac: float) -> tuple[float, float, float]:
    """The legacy gradient ramp, now living in `world/sky.py`.

    Still used for the ground mesh's aerial-perspective haze target, so the rim
    of the ground fades into the same horizon tone the sky renders (Session 10c).
    """
    return sky_model.gradient_colour(frac)


def _write_sky_texture(path: str, sun_elev_deg: float, sun_azim_deg: float, width: int = 1024) -> str:
    """Generate an equirectangular (latlong) sky image and return its path.

    **Why a texture and not a dome of emissive geometry.** The first version of
    this built a self-lit hemisphere. It looked right and was wrong: under
    raytraced lighting an emissive 1.4 km dome is a colossal area light, and it
    lit the desert floor blue — measured, with the dome off the same ground
    rendered R-B +16 (warm), with it on R-B -38 (cold). Two objects were
    disagreeing about the sky, exactly the failure mode the sun-vs-tracker fix
    already dealt with once.

    A `DomeLight` with this texture IS both the visible background and the
    illumination, so they cannot diverge. Generated at build time next to the
    USD, never committed (`CLAUDE.md`: no large binaries).

    The radiance model is now **Preetham** (`world/sky.py`) rather than a
    four-stop ramp, normalised so its hemisphere mean still equals the ramp's.
    That normalisation is the reason this swap cannot move `KPI-03`: the dome IS
    the ambient fill, the fill sets how far shadows fill in, and the shadow
    contrast is the KPI's stimulus. See `world/sky.py` for the measurement.
    """
    return sky_model.write_sky_texture(
        path,
        sun_elev_deg,
        sun_azim_deg,
        ground_rgb=_LOOKS["ground"][0],
        width=width,
    )


def _quad(stage, path, x0, y0, x1, y1, z, material, uv_tile_m: float | None = None):
    """A flat axis-aligned rectangle at height `z` — a road, a pad, an apron.

    `uv_tile_m` authors world-space planar UVs. Planar (not per-quad 0..1) is the
    point: a road is laid as many subdivided segments, and per-quad UVs would
    reset the texture at every segment boundary — a visible ladder of seams down
    the length of every road.
    """
    pts = [
        Gf.Vec3f(x0, y0, z), Gf.Vec3f(x1, y0, z),
        Gf.Vec3f(x1, y1, z), Gf.Vec3f(x0, y1, z),
    ]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr("none")
    if uv_tile_m:
        _set_uvs(mesh, pts, uv_tile_m)
    _bind(mesh.GetPrim(), material)
    return mesh


#: The 6 faces of a unit box as vertex-index quads, with the two in-plane axes
#: each face's UVs run along. Ordered so every face is wound outward.
_BOX_FACES = (
    ((0, 1, 2, 3), (0, 1)),  # -Z, spans X,Y
    ((7, 6, 5, 4), (0, 1)),  # +Z
    ((0, 4, 5, 1), (0, 2)),  # -Y, spans X,Z
    ((3, 2, 6, 7), (0, 2)),  # +Y
    ((0, 3, 7, 4), (1, 2)),  # -X, spans Y,Z
    ((1, 5, 6, 2), (1, 2)),  # +X
)


def _box(stage, path, w, d, h, x, y, z, material, uv_tile_m: float | None = None):
    """An axis-aligned box sitting ON z (not centred on it).

    Without `uv_tile_m` this stays a `UsdGeom.Cube` — byte-identical to before, so
    the instanced fence posts and the OSM building boxes are untouched. With it,
    the box is authored as an explicit `Mesh` carrying **per-face** UVs, because a
    `Cube` has no place to put them and a single planar projection would smear the
    texture down the vertical sides.
    """
    if not uv_tile_m:
        cube = UsdGeom.Cube.Define(stage, path)
        cube.CreateSizeAttr(1.0)
        api = UsdGeom.XformCommonAPI(cube)
        api.SetTranslate(Gf.Vec3d(x, y, z + h / 2.0))
        api.SetScale(Gf.Vec3f(w, d, h))
        _bind(cube.GetPrim(), material)
        return cube

    hw, hd = w / 2.0, d / 2.0
    corners = [
        (x - hw, y - hd, z), (x + hw, y - hd, z),
        (x + hw, y + hd, z), (x - hw, y + hd, z),
        (x - hw, y - hd, z + h), (x + hw, y - hd, z + h),
        (x + hw, y + hd, z + h), (x - hw, y + hd, z + h),
    ]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr([Gf.Vec3f(*c) for c in corners])
    mesh.CreateFaceVertexCountsAttr([4] * 6)
    idx, uvs = [], []
    for verts, (a0, a1) in _BOX_FACES:
        idx.extend(verts)
        for v in verts:
            c = corners[v]
            uvs.append(Gf.Vec2f(c[a0] / uv_tile_m, c[a1] / uv_tile_m))
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateSubdivisionSchemeAttr("none")
    UsdGeom.PrimvarsAPI(mesh.GetPrim()).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
    ).Set(uvs)
    _bind(mesh.GetPrim(), material)
    return mesh


def _build_osm_layer(stage, farm_cfg, layout, looks, ground_box=None) -> dict:
    """Real OpenStreetMap geography around the plant: roads, transmission lines and
    the mapped plant boundaries (`FR-10`/`FR-20`). Returns a tally for the log.

    **This is real third-party data, not our invention and not the vendor CAD's.**
    Baked by `tools/osm_fetch.py` from the official OSM API into the site's own
    EPSG CRS and anchored to the site origin, so an OSM road and a CAD tracker
    table share one frame because both are real survey metres — nothing is scaled
    or fitted to make them agree. Every prim gets `st:provenance = "mapped"`, a
    third value beside `site.py`'s `derived`/`inferred`.

    ⚠ Two honesty constraints, both enforced here rather than left to a comment:

    * Road **widths** are per-class defaults where OSM tags no width, so each
      ribbon records `st:width_source`. The centreline is mapped; the breadth is a
      convention.
    * OSM has **no internal plant roads** for this site, so this layer never
      touches them — `_build_site_works` still owns those as derived/inferred.
      The build log prints both tallies separately so the two cannot blur.

    Terrain-draped per vertex (`osm_features.resample`), because OSM digitises a
    straight desert track as two points kilometres apart and an undraped chord
    floats over the relief the DEM actually has.
    """
    from solar_twin.world.osm_features import (
        MAPPED,
        OSM_POWER_AREAS,
        clip_to_box,
        clip_to_radius,
        load_features,
        resample,
        ribbon,
    )

    cfg = farm_cfg.get("osm", {}) or {}
    path = cfg.get("path")
    if not cfg.get("enabled", bool(path)) or not path:
        return {}
    if not Path(path).exists():
        print(f"  [warn] osm.path {path} not found — no mapped geography authored")
        return {}

    feats = load_features(path)
    min_x, min_y, max_x, max_y = layout.bounds()
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    # The bake covers a padded bbox so one ingest serves any subset; clip so a
    # 20-table patch does not get a 108 km transmission line across its stage.
    radius = float(cfg.get("radius_m", 0.0)) or max(
        2500.0, 2.5 * max(max_x - min_x, max_y - min_y)
    )
    n_raw = len(feats.ways)
    feats = clip_to_radius(feats, cx, cy, radius)
    # Then clip to the GROUND's own extent. `clip_to_radius` keeps whole ways so a
    # road never ends in mid-desert, but that also drags all 108 km of a
    # transmission line onto the stage from one nearby vertex — measured: an OSM
    # layer spanning -23..+31 km with the ground mesh reaching 1.5 km, so 582
    # towers stood in the void. Cutting at the ground edge puts the cut at the
    # horizon, which is where a road should leave frame.
    if ground_box is not None:
        # Inset first: clipping cuts the CENTRELINE, and `ribbon`/curve widths then
        # extrude outward from it, so a road cut exactly on the edge still overhangs
        # the terrain by half its width (measured: 2 m past a 9 km mesh). The
        # allowance is the miter bound `ribbon` uses (half-width / 0.35), so even a
        # corner sitting on the boundary stays on the ground.
        widest = max((w.width_m for w in feats.ways), default=0.0)
        inset = max(3.0, widest / (2.0 * 0.35))
        gx0, gy0, gx1, gy1 = ground_box
        feats = clip_to_box(feats, gx0 + inset, gy0 + inset, gx1 - inset, gy1 - inset)
    if not feats.ways:
        print(f"  [warn] no OSM features within {radius:,.0f} m of the plant")
        return {}

    ground = lambda x, y: terrain_height(x, y, farm_cfg)  # noqa: E731
    root = UsdGeom.Xform.Define(stage, "/World/OSM").GetPrim()
    root.CreateAttribute("st:provenance", Sdf.ValueTypeNames.String).Set(MAPPED)
    root.CreateAttribute("st:source", Sdf.ValueTypeNames.String).Set(feats.source)
    root.CreateAttribute("st:license", Sdf.ValueTypeNames.String).Set(feats.license)

    drape_m = float(cfg.get("drape_step_m", 40.0))
    tally: dict[str, int] = {}

    def _tag(prim, way, **extra) -> None:
        prim.CreateAttribute("st:provenance", Sdf.ValueTypeNames.String).Set(MAPPED)
        prim.CreateAttribute("st:osm_id", Sdf.ValueTypeNames.Int64).Set(way.osm_id)
        prim.CreateAttribute("st:osm_kind", Sdf.ValueTypeNames.String).Set(way.kind)
        for k, v in extra.items():
            prim.CreateAttribute(f"st:{k}", Sdf.ValueTypeNames.String).Set(str(v))

    for i, way in enumerate(feats.roads):
        pts = resample(way.points, drape_m)
        edges = ribbon(pts, way.width_m)
        if not edges:
            continue
        # One mesh per road: a quad strip between the two draped edges. Sampling
        # terrain at each edge vertex (not at the centreline) means the ribbon
        # follows a cross-slope instead of hovering on the downhill side.
        verts, counts, idx = [], [], []
        for (lx, ly), (rx, ry) in edges:
            verts.append(Gf.Vec3f(lx, ly, ground(lx, ly) + 0.04))
            verts.append(Gf.Vec3f(rx, ry, ground(rx, ry) + 0.04))
        for s in range(len(edges) - 1):
            a = 2 * s
            counts.append(4)
            idx.extend([a, a + 1, a + 3, a + 2])
        name = f"road_{i:02d}_{way.kind}"
        mesh = UsdGeom.Mesh.Define(stage, f"/World/OSM/Roads/{name}")
        mesh.CreatePointsAttr(verts)
        mesh.CreateFaceVertexCountsAttr(counts)
        mesh.CreateFaceVertexIndicesAttr(idx)
        mesh.CreateSubdivisionSchemeAttr("none")
        _set_uvs(mesh, verts, _UV_TILE_M["road"])
        _bind(mesh.GetPrim(), looks["road"])
        _tag(
            mesh.GetPrim(), way,
            width_m=f"{way.width_m:.1f}",
            width_source=way.width_source,
            surface=way.surface,
            name=way.name,
        )
        tally["roads"] = tally.get("roads", 0) + 1

    for i, way in enumerate(feats.power):
        pts = resample(way.points, drape_m)
        if way.kind in OSM_POWER_AREAS:
            # A mapped plant/substation boundary is an AREA on the ground, not a
            # conductor. Treating anything non-`line` as a line is a bug this
            # already had: OSM here maps two real substations (`PSS 3`, `KPS 2`)
            # and a generator area, and they were being authored as 14 m overhead
            # cables complete with towers.
            #
            # Filling the area would paint over the ground we actually build on, so
            # it is a ground-following outline, and guide-purpose: an annotation
            # must never occlude or shadow a sensor frame (the exact bug the
            # keep-out spheres caused — see `tests/test_farm_builder_usd.py`).
            curve = UsdGeom.BasisCurves.Define(
                stage, f"/World/OSM/Boundaries/{way.kind}_{i:02d}"
            )
            curve.CreatePointsAttr([Gf.Vec3f(x, y, ground(x, y) + 0.10) for x, y in pts])
            curve.CreateCurveVertexCountsAttr([len(pts)])
            curve.CreateTypeAttr(UsdGeom.Tokens.linear)
            curve.CreateWidthsAttr([3.0] * len(pts))
            curve.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
            curve.CreatePurposeAttr(UsdGeom.Tokens.guide)
            _tag(curve.GetPrim(), way, name=way.name, operator=way.operator)
            tally["boundaries"] = tally.get("boundaries", 0) + 1
            continue
        if way.kind != "line":
            # Some other `power=*` value (pole, portal, ...). Skipping is the honest
            # choice: authoring it as a guessed shape would put invented hardware on
            # the stage under a `mapped` provenance tag, which is the one thing this
            # layer must never do.
            tally["skipped"] = tally.get("skipped", 0) + 1
            continue

        # A transmission line: the conductor at its voltage-class height. Real
        # position and real voltage; the catenary SAG is not modelled, so each
        # span is a straight chord (`NFR-07`).
        h = way.line_height_m
        curve = UsdGeom.BasisCurves.Define(stage, f"/World/OSM/Power/line_{i:02d}")
        curve.CreatePointsAttr([Gf.Vec3f(x, y, ground(x, y) + h) for x, y in pts])
        curve.CreateCurveVertexCountsAttr([len(pts)])
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)
        curve.CreateWidthsAttr([0.9] * len(pts))
        curve.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
        _bind(curve.GetPrim(), looks["structure"])
        _tag(curve.GetPrim(), way, voltage=way.voltage, height_m=f"{h:.0f}")
        tally["power_lines"] = tally.get("power_lines", 0) + 1

        # Towers on the original (un-resampled) OSM vertices: those are the
        # surveyed tower positions, whereas resampled points are interpolation.
        for j, (x, y) in enumerate(way.points):
            _box(
                stage, f"/World/OSM/Power/line_{i:02d}/tower_{j:03d}",
                2.5, 2.5, h, x, y, ground(x, y), looks["turbine"],
            )
        tally["towers"] = tally.get("towers", 0) + len(way.points)

    named = sorted({w.name for w in feats.ways if w.name})
    print(
        f"  OSM (MAPPED, real): {tally} from {n_raw} baked ways, clipped to the "
        f"ground mesh"
        + (f" · named: {', '.join(named[:3])}" if named else ""),
        flush=True,
    )
    if not feats.roads:
        print(
            "    ⚠ no MAPPED roads here — OSM does not map this plant's internal "
            "roads, so every road on the stage is derived/inferred",
            flush=True,
        )
    return tally


def _build_site_works(stage, farm_cfg, layout, looks) -> dict:
    """Roads, fence, inverter stations — the things that make a field of modules
    read as a power plant (`FR-10`). Returns a provenance tally for the caller
    to print.

    Nothing here is invented silently. `site.py` splits every element into
    DERIVED (a corridor the vendor CAD leaves empty really is a road) and
    INFERRED (standard plant practice — the drawing covers hardware only), each
    element carries an `st:provenance` attribute on its prim, and the build log
    states the split. A viewer must never mistake our assumptions for survey.
    """
    from solar_twin.world.site import (
        access_spurs,
        derived_ew_roads,
        derived_roads,
        fence_posts,
        inverter_pads,
        perimeter_road,
        provenance_summary,
        subdivide_strip,
        table_extent,
    )

    cfg = farm_cfg.get("site", {}) or {}
    if not cfg.get("enabled", True) or layout.site is None:
        return {}

    extent = table_extent(layout.site)
    ground = lambda x, y: terrain_height(x, y, farm_cfg)  # noqa: E731
    root = UsdGeom.Xform.Define(stage, "/World/Site").GetPrim()

    # North-south corridors AND east-west cross corridors, both read out of the
    # drawing. There is deliberately no inferred cross road: an invented arterial
    # would have to run through surveyed tracker tables, which does not add an
    # assumption so much as contradict the CAD.
    roads = derived_roads(layout.site) + derived_ew_roads(layout.site)
    if cfg.get("perimeter_road", True):
        roads += perimeter_road(
            extent,
            width_m=float(cfg.get("road_width", 6.0)),
            offset_m=float(cfg.get("road_offset", 7.0)),
        )

    def _lay(strips) -> None:
        """Draw road strips, following the grade.

        Each strip is subdivided along its long axis and every segment sampled at
        its own centre. One flat quad per road was fine on `flat` terrain and
        wrong on the DEM: with 2.2 m of relief across the block, a 647 m
        perimeter road hung ~1 m clear of the ground at one end and buried itself
        at the other.
        """
        seg_m = float(cfg.get("road_segment_m", 25.0))
        for r in strips:
            for s in subdivide_strip(r, max_seg_m=seg_m):
                # 3 cm proud of the grade so the road wins the depth fight with
                # the ground mesh instead of z-fighting with it.
                q = _quad(
                    stage,
                    f"/World/Site/Roads/{s.name}",
                    s.x0, s.y0, s.x1, s.y1,
                    ground((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2) + 0.03,
                    looks["road"],
                    uv_tile_m=_UV_TILE_M["road"],
                )
                q.GetPrim().CreateAttribute("st:provenance", Sdf.ValueTypeNames.String).Set(
                    s.provenance
                )

    _lay(roads)

    pads = []
    if cfg.get("inverters", True):
        pads = inverter_pads(
            layout.site,
            [r for r in roads if r.provenance == "derived"] or roads,
            module_watts=float(cfg.get("module_watts", 600.0)),
            mw_per_station=float(cfg.get("mw_per_station", 4.0)),
        )
        # Access spurs: without them the stations sit in the array with no way in,
        # and a 4 MW central inverter arrives on a low-loader.
        spurs = access_spurs(pads, roads, width_m=float(cfg.get("spur_width", 5.0)))
        _lay(spurs)
        roads = roads + spurs
        for p in pads:
            gz = ground(p.x, p.y)
            base = f"/World/Site/Inverters/{p.name}"
            UsdGeom.Xform.Define(stage, base)
            _quad(
                stage, base + "/Pad",
                p.x - p.width_m / 2, p.y - p.depth_m / 2,
                p.x + p.width_m / 2, p.y + p.depth_m / 2,
                gz + 0.05, looks["concrete"],
                uv_tile_m=_UV_TILE_M["concrete"],
            )
            # Inverter container + the transformer beside it: the pair a real
            # skid carries, and enough mass to give the rows a sense of scale.
            _box(
                stage, base + "/Inverter", 6.0, 2.6, 2.7, p.x - 2.6, p.y, gz + 0.05,
                looks["equipment"], uv_tile_m=_UV_TILE_M["equipment"],
            )
            _box(
                stage, base + "/Transformer", 3.2, 2.6, 2.2, p.x + 3.2, p.y, gz + 0.05,
                looks["structure"], uv_tile_m=_UV_TILE_M["structure"],
            )
            _box(
                stage, base + "/Radiator", 0.5, 2.0, 1.6, p.x + 5.1, p.y, gz + 0.05,
                looks["fence_frame"], uv_tile_m=_UV_TILE_M["fence_frame"],
            )
            UsdGeom.Xform(stage.GetPrimAtPath(base)).GetPrim().CreateAttribute(
                "st:provenance", Sdf.ValueTypeNames.String
            ).Set(p.provenance)

    posts = []
    if cfg.get("fence", True):
        posts = fence_posts(
            extent,
            offset_m=float(cfg.get("fence_offset", 18.0)),
            spacing_m=float(cfg.get("fence_spacing", 12.0)),
        )
        # One prototype post, instanced around the perimeter — same IF-09 trick
        # as the panels, for the same reason.
        proto = f"{_PROTO_ROOT}/FencePost"
        if not stage.GetPrimAtPath(_PROTO_ROOT):
            stage.CreateClassPrim(_PROTO_ROOT)
        if not stage.GetPrimAtPath(proto):
            UsdGeom.Xform.Define(stage, proto)
            # UV'd mesh so the post can carry the fence texture. The prototype is
            # still referenced + instanceable below, so IF-09 is unaffected: one
            # prototype, N instances, regardless of Cube-vs-Mesh.
            _box(
                stage, proto + "/Post", 0.09, 0.09, 2.2, 0.0, 0.0, 0.0,
                looks["fence_frame"], uv_tile_m=_UV_TILE_M["fence_frame"],
            )
        for i, (px, py) in enumerate(posts):
            p = UsdGeom.Xform.Define(stage, f"/World/Site/Fence/post_{i:04d}").GetPrim()
            UsdGeom.XformCommonAPI(p).SetTranslate(Gf.Vec3d(px, py, ground(px, py)))
            p.GetReferences().AddInternalReference(proto)
            p.SetInstanceable(True)
        # Three horizontal wires per span, drawn as one thin box per segment.
        for i, ((ax, ay), (bx, by)) in enumerate(zip(posts, posts[1:] + posts[:1])):
            cx, cy = (ax + bx) / 2, (ay + by) / 2
            length = math.hypot(bx - ax, by - ay)
            yaw = math.degrees(math.atan2(by - ay, bx - ax))
            for k, wz in enumerate((0.7, 1.35, 2.0)):
                wire = UsdGeom.Cube.Define(stage, f"/World/Site/Fence/wire_{i:04d}_{k}")
                wire.CreateSizeAttr(1.0)
                wapi = UsdGeom.XformCommonAPI(wire)
                wapi.SetTranslate(Gf.Vec3d(cx, cy, ground(cx, cy) + wz))
                wapi.SetRotate((0.0, 0.0, yaw), UsdGeom.XformCommonAPI.RotationOrderXYZ)
                wapi.SetScale(Gf.Vec3f(length, 0.02, 0.02))
                # Left as a Cube with no authored UVs: a 2 cm wire spans a tiny
                # fraction of a 1.5 m tile, so any sample of a mean-1.0 modulation
                # is the base colour — texturing it would be indistinguishable.
                # It shares `fence_frame` so the fence stays one look.
                _bind(wire.GetPrim(), looks["fence_frame"])

    tally = provenance_summary(roads + pads)
    root.CreateAttribute("st:provenance_note", Sdf.ValueTypeNames.String).Set(
        "roads/fence/inverters: 'derived' is read from the vendor CAD table "
        "geometry; 'inferred' is standard plant practice placed by solar-twin "
        "and is NOT surveyed (NFR-07)."
    )
    return {"roads": len(roads), "inverters": len(pads), "fence_posts": len(posts), **tally}


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


def _cell_id_for(site, farm_cfg: dict) -> str:
    """`grid:id` for a panel, derived from the layout's OWN table structure.

    `(site.row, site.col)` is `(table index, module index)` — set by
    `layout_import.expand_sites` — so the cell falls out of the CAD's real table
    grouping. Nothing new is invented here: no geometric grid is imposed, and the
    table is used because it is the finest unit the vendor DWG actually carries
    (there is no string map; see `schema.pv_module`'s `grid:` namespace note).

    Off unless `grid.enabled` is set, so a stage built without it is byte-identical
    to one built before the namespace existed.
    """
    g = (farm_cfg.get("grid", {}) or {})
    if not g.get("enabled", False):
        return ""
    return pv.cell_for_panel(
        (site.row, site.col), int(g.get("modules_per_cell", 0))
    )


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
    # `sun.timestamp` (ISO-8601, UTC) overrides the hand-set angles with the REAL
    # solar position for this site and instant, and also drives the tracker angle
    # below — one source, so the light and the panels cannot disagree.
    real_sun = None
    if sun_cfg.get("timestamp"):
        from solar_twin.world.solar import parse_timestamp, solar_position

        when = parse_timestamp(sun_cfg["timestamp"])
        elev, azim = solar_position(layout.anchor.lat0, layout.anchor.lon0, when)
        real_sun = (elev, azim)
        print(
            f"  sun {when.isoformat()}: elevation {elev:.1f}deg azimuth {azim:.1f}deg",
            flush=True,
        )
        if elev <= 0.0:
            print("  [warn] sun BELOW horizon — render will be dark", flush=True)
    # Dim a low sun a little (grazing light is less intense) so frames don't blow out.
    sun.CreateIntensityAttr(2400.0 if elev >= 30.0 else 1700.0)
    # A DistantLight emits along local -Z. With rotation order XYZ,
    #   L = Rz(rz)*Rx(rx)*(0,0,-1) = (-sin rx sin rz, sin rx cos rz, -cos rx)
    # and we need L = -sun = (-cos E sin A, -cos E cos A, -sin E), giving
    #   rx = 90 - elevation, rz = 180 - azimuth (azimuth measured from north).
    # The legacy (-elev, 0, azim) form is kept for configs that set the angles by
    # hand, so SC-05 and friends are unaffected.
    _rot = (90.0 - elev, 0.0, 180.0 - azim) if real_sun else (-elev, 0.0, azim)
    UsdGeom.XformCommonAPI(sun).SetRotate(
        _rot, UsdGeom.XformCommonAPI.RotationOrderXYZ
    )
    dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
    # Ambient fill (config knob): lowering it deepens shadows toward near-black — a
    # harder KPI-03 stressor where a shadow reads more like a dark defect.
    dome.CreateIntensityAttr(float(sun_cfg.get("ambient", 300.0)))
    # ...and the same dome carries the VISIBLE sky, as a generated latlong image.
    # One object is both background and fill, so the render and the lighting agree
    # by construction (see _write_sky_texture for the blue-ground measurement that
    # killed the geometry-dome version).
    if (farm_cfg.get("sky", {}) or {}).get("enabled", True):
        # Named after the STAGE as well as the sun. It used to be
        # `sky_<elev>_<azim>.png` alone, so any two stages sharing a sun timestamp
        # shared one texture file — and building one stage while RENDERING another
        # overwrites the texture the live render is reading. Measured: a full-plot
        # build clobbered `sky_44_80.png` mid-tour and RTX logged
        # `Failed to read texture file ... or file is empty` for the rest of the run.
        # NOT named `tex`: that is the `world.textures` module alias, and rebinding
        # it here shadowed the module for the PBR block below -- `tex.write_all`
        # crashed on a `str`. Caught on the first real render, which is why the
        # render gate exists.
        sky_tex = _write_sky_texture(
            str(
                Path(out).parent
                / f"sky_{Path(out).stem}_{int(round(elev))}_{int(round(azim))}.png"
            ),
            elev,
            azim,
        )
        dome.CreateTextureFileAttr().Set(sky_tex)
        dome.CreateTextureFormatAttr().Set(UsdLux.Tokens.latlong)
        print(f"  sky: generated {sky_tex}", flush=True)

    # --- shared material set (one look per _LOOKS entry, reused everywhere) ---
    looks = {
        name: _make_material(stage, f"/World/Looks/{name}", diff, emis, rough, metal)
        for name, (diff, emis, rough, metal) in _LOOKS.items()
    }
    dust_mat = _dust_material(stage)

    # --- textured PBR for the balance-of-plant surfaces ---------------------- #
    # Generated beside the USD, never committed. Panel glass/frame are pointedly
    # NOT in `textures.SURFACES`: their look feeds the soiling bake and the hotspot
    # emissive, and texturing them re-opens the bright-frame-as-hotspot failure.
    # That is a separate, flagged decision — see SESSIONS.md.
    # ⚠⚠ DEFAULT OFF, and that is a MEASURED regression guard, not a preference.
    # With the textured materials bound, the desert ground renders near-black and
    # achromatic: non-glass mean RGB went (77.6, 68.0, 54.1) -> (1.1, 1.2, 1.2),
    # i.e. R-B +23.5 -> -0.1, on SC-11's control panel. That kills the Session 10c
    # invariant (ground must read WARM, R-B > 0) which exists to catch the sky
    # lighting the desert wrongly -- and it silently weakened KPI-03's stimulus,
    # because a black ground bounces no light up onto the modules (SC-11
    # differential +12.5 -> +10.1 points).
    #
    # Bisected with --pbr (see main()): `albedo` (no normal map) and `primvar`
    # (vertex-colour diffuse) render IDENTICALLY dark, so it is neither the normal
    # map nor the diffuse source -- the diffuse input is being ignored outright.
    # The generated maps are correct (ground albedo R-B +28.04) and every texture
    # path resolves and exists, so the fault is in how this network is consumed,
    # not in the textures. Turn back on with `--pbr on` only once a render
    # measurement puts the ground back above R-B +20.
    pbr_paths = {}
    if (farm_cfg.get("pbr", {}) or {}).get("enabled", False):
        tex_dir = Path(out).parent / f"tex_{Path(out).stem}"
        pbr_paths = tex.write_all(
            str(tex_dir),
            size=int((farm_cfg.get("pbr", {}) or {}).get("size", tex.DEFAULT_SIZE)),
            diffuse={n: _LOOKS[n][0] for n in tex.SURFACES if n in _LOOKS},
        )
        # `mode` exists to bisect the black-ground regression; "on" is the shipped
        # path. Only the GROUND carries a `displayColor` primvar, so "primvar" is a
        # no-op elsewhere and those surfaces keep their albedo map.
        mode = (farm_cfg.get("pbr", {}) or {}).get("mode", "on")
        for name, paths in pbr_paths.items():
            looks[name] = _textured_material(
                stage,
                f"/World/Looks/{name}_pbr",
                name,
                paths,
                metallic=_LOOKS[name][3],
                diffuse_from_primvar=(mode == "primvar" and name == "ground"),
                use_normal=(mode != "albedo"),
            )
        print(
            f"  pbr: textured {len(pbr_paths)} surfaces "
            f"({', '.join(sorted(pbr_paths))}) -> {tex_dir}",
            flush=True,
        )

    # --- ground: reaches the horizon so terrain, not sky, meets the eye -------
    sky_cfg = farm_cfg.get("sky", {}) or {}
    _min_x, _min_y, _max_x, _max_y = layout.bounds()
    horizon_m = float(sky_cfg.get("horizon", 0.0)) or max(
        1500.0, 8.0 * max(_max_x - _min_x, _max_y - _min_y)
    )
    # The ground keeps its VERTEX-COLOUR diffuse and gains only roughness+normal
    # maps. Its displayColor carries the three-octave grading variation AND the
    # aerial-perspective fade into the horizon haze; a flat albedo texture would
    # discard both and bring back the hard mesh rim with void beyond it (10c).
    ground_mat = (
        _textured_material(
            stage, "/World/Looks/ground_pbr", "ground", pbr_paths["ground"],
            metallic=_LOOKS["ground"][3], diffuse_from_primvar=True,
        )
        if "ground" in pbr_paths
        else _vertex_colour_material(
            stage, "/World/Looks/ground_vc", 1.0, emissive=False
        )
    )
    _build_ground_heightfield(
        stage, farm_cfg, layout, ground_mat, horizon_m=horizon_m,
    )

    # --- balance of plant: roads, fence, inverter stations --------------------
    site_stats = _build_site_works(stage, farm_cfg, layout, looks)

    # --- real OSM geography: mapped roads, HV lines, plant boundaries ---------
    # Clipped to the ground mesh's own extent, so nothing real is authored past
    # the terrain it should be standing on.
    _build_osm_layer(
        stage, farm_cfg, layout, looks,
        ground_box=ground_extent(farm_cfg, layout, horizon_m),
    )

    # --- optional shading occluder (KPI-03 hard-shadow stressor) --------------
    _build_shading_occluder(stage, farm_cfg, layout, looks["structure"])

    # HSAT tracker angle. One angle for the whole block: every table shares the
    # same N-S axis and sees the same sun at this instant, so the rows rotate
    # together, as a real site's do. Backtracking (rows limiting rotation at low
    # sun to avoid shading each other) is NOT modelled -- so self-shading here is
    # the worst case, which is the useful case for KPI-03 (`NFR-07`).
    # Computed by `layout`, not here: the drone waypoints are placed above the
    # panel's tilted upper edge, so the builder and `panel_top_z` must use the
    # SAME angle. Two copies of this formula is how the sun and the trackers came
    # to disagree once already.
    tracker_rot = layout.tracker_rotation_deg()
    if tracker_rot is not None:
        print(f"  tracker: {tracker_rot:+.1f}deg about the N-S axis", flush=True)

    pdim = farm_cfg.get("panel", {})
    # NOTE: no global `tilt_deg` here any more — tilt/azimuth are per-site
    # (`PanelSite.tilt_deg` / `.azimuth_deg`), fed by `layout.py` from
    # `panel.tilt_deg` for the procedural grid or per-table for a CAD import.
    mount_h = float(pdim.get("mount_height", PANEL_MOUNT_HEIGHT))
    pw = float(pdim.get("width", 1.0))
    pl = float(pdim.get("length", 2.0))
    ph = float(pdim.get("height", 0.05))
    n_ccol = int(pdim.get("cell_cols", 6))   # cells across width (x)
    n_crow = int(pdim.get("cell_rows", 10))  # cells along length (y)
    seed = int(farm_cfg.get("seed", 0))
    # Instancing is on by default and can be turned off to get the old
    # every-panel-is-unique stage back (useful when diffing a render).
    instancing = bool((farm_cfg.get("render", {}) or {}).get("instancing", True))
    proto_cache: dict = {}

    n_fault = 0
    n_instanced = 0

    # --- fast path: author every HEALTHY instanced panel in ONE ChangeBlock ----
    #
    # This is what makes a whole plot buildable. Going through `UsdStage` per panel
    # is QUADRATIC — every `DefinePrim` recomposes the parent's children, measured
    # at n^2.39 end-to-end, i.e. ~55 hours for S05b's 679,616 modules. Authoring
    # `Sdf.PrimSpec`s into the layer inside one `Sdf.ChangeBlock` defers composition
    # to the end: measured n^1.03 and 60x faster at 32k panels.
    #
    # Faulted panels are deliberately NOT here. Each carries unique geometry (a
    # hotspot recolours specific cells, soiling bakes a per-panel dust film), which
    # is far more code against the Usd API, and at a few percent of the plant they
    # are not what makes the build quadratic. They fall through to the loop below.
    done_fast: set = set()
    if instancing:
        from pxr import Sdf as _Sdf

        # Prototypes must exist BEFORE the ChangeBlock: `_panel_prototype` goes
        # through UsdStage, which cannot compose inside one. There is one per
        # distinct module size, so this is a handful of calls, not N.
        proto_for: dict = {}
        for site in layout.sites:
            if faults.get(site.panel_id, pv.PanelState.HEALTHY) is not pv.PanelState.HEALTHY:
                continue
            key = (site.size_x_m or pw, site.size_y_m or pl)
            if key not in proto_for:
                proto_for[key] = _panel_prototype(
                    stage, proto_cache, key[0], key[1], ph, n_ccol, n_crow, looks
                )

        # `/World/Farm` has to exist as a real spec before children can be added to
        # it. It used to be created implicitly, as an ancestor of the first
        # `Xform.Define(".../Panel_R00_C000")`, so at this point the layer has no
        # spec at that path and `Sdf.PrimSpec` raises "parent prim is NULL".
        UsdGeom.Xform.Define(stage, "/World/Farm")
        layer = stage.GetRootLayer()
        farm_spec = layer.GetPrimAtPath("/World/Farm")
        with _Sdf.ChangeBlock():
            for site in layout.sites:
                if faults.get(site.panel_id, pv.PanelState.HEALTHY) is not pv.PanelState.HEALTHY:
                    continue
                x, y, gz = site.position
                rot = (
                    (site.tilt_deg, 0.0, site.azimuth_deg)
                    if tracker_rot is None
                    else (0.0, tracker_rot, site.azimuth_deg)
                )
                spec = pv.author_panel_spec(
                    farm_spec,
                    pv.panel_path("", site.row, site.col).lstrip("/"),
                    site.panel_id, site.row, site.col, site.geo_position,
                    cell_id=_cell_id_for(site, farm_cfg),
                )
                # Same ops XformCommonAPI would author, in the same order, so the
                # resulting prim is identical to the `create_panel` path's.
                _Sdf.AttributeSpec(
                    spec, "xformOp:translate", _Sdf.ValueTypeNames.Double3
                ).default = Gf.Vec3d(x, y, gz + mount_h)
                _Sdf.AttributeSpec(
                    spec, "xformOp:rotateXYZ", _Sdf.ValueTypeNames.Float3
                ).default = Gf.Vec3f(*(float(v) for v in rot))
                _Sdf.AttributeSpec(
                    spec, "xformOpOrder", _Sdf.ValueTypeNames.TokenArray
                ).default = ["xformOp:translate", "xformOp:rotateXYZ"]
                spec.referenceList.prependedItems.append(
                    _Sdf.Reference(primPath=proto_for[(site.size_x_m or pw, site.size_y_m or pl)])
                )
                spec.instanceable = True
                # The semantic label, authored as specs for the same reason. This is
                # what `UsdSemantics.LabelsAPI` writes: the schema in apiSchemas plus
                # the labels attribute in its instance namespace.
                spec.SetInfo(
                    "apiSchemas",
                    _Sdf.TokenListOp.CreateExplicit(["SemanticsLabelsAPI:class"]),
                )
                _Sdf.AttributeSpec(
                    spec, "semantics:labels:class", _Sdf.ValueTypeNames.TokenArray
                ).default = ["panel", pv.PanelState.HEALTHY.value]
                done_fast.add(site.panel_id)
        n_instanced += len(done_fast)
        if done_fast:
            print(
                f"  authored {len(done_fast):,} healthy panels via Sdf specs in one "
                f"ChangeBlock ({len(proto_for)} prototype(s))",
                flush=True,
            )

    for site in layout.sites:
        if site.panel_id in done_fast:
            continue  # fully authored in the batched pass above
        path = pv.panel_path("/World/Farm", site.row, site.col)
        prim = pv.create_panel(
            stage, path, site.panel_id, site.row, site.col, site.geo_position,
            cell_id=_cell_id_for(site, farm_cfg),
        )
        # Place + orient the panel Xform (Z-up). Tilt is about the row axis (X);
        # azimuth is the mounting structure's plan rotation about Z.
        # site.position.z already follows the terrain, so panels sit on the grade.
        #
        # Both come from the SITE, not from a single global config value: a real
        # multi-block plant has different orientations per block, and a tracker
        # site has no fixed tilt at all. `layout.py` fills these from `panel.tilt_deg`
        # for the procedural farm, so the grid path is unchanged.
        # ⚠ For an HSAT (tracker) site `site.tilt_deg` is only the nominal/stowed
        # angle — the real angle sweeps with the sun. Authoring it as a static tilt
        # is an approximation and must be declared as one (`NFR-07`), not mistaken
        # for validated geometry.
        x, y, gz = site.position
        api = UsdGeom.XformCommonAPI(prim)
        api.SetTranslate(Gf.Vec3d(x, y, gz + mount_h))
        if tracker_rot is None:
            # Fixed-tilt site: tilt is about the row axis (X).
            api.SetRotate(
                (site.tilt_deg, 0.0, site.azimuth_deg),
                UsdGeom.XformCommonAPI.RotationOrderXYZ,
            )
        else:
            # HSAT: the torque tube runs NORTH-SOUTH (+Y), so tracking rotation is
            # about Y. Rotating about X would tilt panels along the tube, which this
            # hardware physically cannot do.
            api.SetRotate(
                (0.0, tracker_rot, site.azimuth_deg),
                UsdGeom.XformCommonAPI.RotationOrderXYZ,
            )

        # Module extent comes from the SITE when it knows (a CAD import carries
        # real hardware dimensions); `panel.width/length` is the procedural
        # fallback. See PanelSite.size_x_m for why this is not one global pair.
        sx = site.size_x_m or pw
        sy = site.size_y_m or pl

        # Seeded fault on the source of truth; localized to a set of cells.
        state = faults.get(site.panel_id, pv.PanelState.HEALTHY)
        if state is not pv.PanelState.HEALTHY:
            prim.GetAttribute(pv.ATTR_STATE).Set(state.value)
            n_fault += 1

        # NOTE: there is deliberately no healthy-and-instanced branch here any more.
        # The overwhelming majority of a real plant is healthy and identical, so it
        # shares one prototype (`IF-09`) — and ALL of those are authored in the
        # batched Sdf pass above, because doing it one prim at a time through
        # UsdStage is what made the build quadratic. Anything reaching this loop is
        # either faulted or on a stage with `render.instancing: false`. Keeping a
        # second copy of the instancing logic here would be two implementations of
        # one contract, free to drift.

        # --- unique geometry: faulted panels (and the procedural farm) --------
        geom = UsdGeom.Cube.Define(stage, path + "/Geom")
        geom.CreateSizeAttr(1.0)
        UsdGeom.XformCommonAPI(geom).SetScale(Gf.Vec3f(sx, sy, ph))
        _bind(geom.GetPrim(), looks["panel_frame"])

        rng = random.Random(f"{seed}:{site.panel_id}")
        # Cell-level faults (hotspot) recolor cells; soiling is a film authored
        # after the cells as a translucent overlay that ignores the cell grid.
        bad = fault_cells(state, n_crow, n_ccol, rng)
        fault_look = {pv.PanelState.HOTSPOT: looks["cell_hotspot"]}.get(state)

        # --- cell grid on the top face: each cell a thin inset tile ----------
        cells = UsdGeom.Xform.Define(stage, path + "/Cells")  # noqa: F841
        cw, cl = sx / n_ccol, sy / n_crow
        gap = 0.86  # tile shrink -> dark grid lines between cells
        for r in range(n_crow):
            for c in range(n_ccol):
                cx = -sx / 2 + (c + 0.5) * cw
                cy = -sy / 2 + (r + 0.5) * cl
                cell = UsdGeom.Cube.Define(stage, f"{path}/Cells/c_{r}_{c}")
                cell.CreateSizeAttr(1.0)
                capi = UsdGeom.XformCommonAPI(cell)
                capi.SetTranslate(Gf.Vec3d(cx, cy, ph / 2 + ph * 0.25))
                capi.SetScale(Gf.Vec3f(cw * gap, cl * gap, ph * 0.5))
                look = fault_look if (fault_look and (r, c) in bad) else looks["cell_healthy"]
                _bind(cell.GetPrim(), look)

        # Soiling: a translucent dust film over the glass, crossing cell borders.
        if state is pv.PanelState.SOILED:
            _build_dust_film(stage, path, sx, sy, ph, n_ccol, n_crow, rng, dust_mat)

        _label(prim, "panel", state.value)

    # --- wind turbines: proxies with a spin-able Hub (runtime turns the blades) -
    from solar_twin.world.siting import resolve_turbines

    turbines = resolve_turbines(farm_cfg, layout, log=print)
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

        # FR-11: opt-in real articulation. Off by default because every KPI run
        # recorded so far used the kinematic proxy, and a driven rotor is a
        # different scene — not a free upgrade to a pinned measurement.
        if bool(farm_cfg.get("turbines_articulated", False)):
            _articulate_turbine(stage, tpath, spec)

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
    n_prims = sum(1 for _ in stage.Traverse())
    faults_note = (
        f"faults={ {pid: s.value for pid, s in faults.items()} }"
        if len(faults) <= 30
        else f"{len(faults)} faults seeded (list suppressed)"
    )
    site_line = f"  site works: {site_stats or 'none'}"
    if site_stats:
        # Said on every build, deliberately: 'inferred' elements are our standard
        # practice assumptions, not survey (NFR-07, world/site.py).
        site_line += "\n    [derived = read from the vendor CAD · inferred = our assumption, NOT surveyed]"
    print(
        f"built {layout.n_panels} panels ({n_fault} faulted), "
        f"{len(turbines)} turbines, terrain={farm_cfg.get('terrain', {}).get('kind', 'flat')} -> {out}\n"
        f"  {n_instanced} panels instanced from {len(proto_cache)} prototype(s), "
        f"{n_prims} prims on stage\n"
        f"{site_line}\n"
        f"  up-axis=Z meters=1.0 seed={farm_cfg.get('seed')} {faults_note}"
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
    ap.add_argument(
        "--subset",
        type=int,
        default=0,
        help="layout.kind=file only: author just N tracker tables, as a COMPACT "
        "patch around the site's south-west corner (0 = all). Use this to prove "
        "the pipeline before the full block, which is ~2.2M prims. ⚠ pass the "
        "SAME --subset to solar_twin.run, or the mission will target panels the "
        "stage lacks.",
    )
    ap.add_argument(
        "--pbr",
        choices=("on", "off", "albedo", "primvar"),
        default="",
        help="override the config's `pbr.enabled` so the textured-PBR layer can be "
        "A/B'd against the flat materials WITHOUT editing a config. `off` = flat "
        "materials only; `albedo` = albedo+roughness but NO normal map; `primvar` = "
        "keep the ground's vertex-colour diffuse (roughness+normal only). Added to "
        "bisect a measured regression: with the full layer on, the desert ground "
        "rendered near-black and achromatic (R-B +23.5 -> -0.1) even though the "
        "GENERATED albedo map is correct (R-B +28.04) -- so the fault is in how the "
        "maps are bound, not in how they are made, and that needs one knob to isolate.",
    )
    args = ap.parse_args(argv)
    if args.scenario:
        from solar_twin.scenario import load_scenario

        farm_cfg = load_scenario(args.scenario).farm_cfg
    elif args.farm:
        farm_cfg = _load_farm_cfg(args.farm)
    else:
        ap.error("provide a farm.yaml path, or --scenario")
    if args.subset:
        layout_cfg = dict(farm_cfg.get("layout") or {})
        if layout_cfg.get("kind") != "file":
            ap.error("--subset only applies to layout.kind: file")
        layout_cfg["max_tables"] = args.subset
        farm_cfg = {**farm_cfg, "layout": layout_cfg}
    if args.pbr:
        pbr_cfg = dict(farm_cfg.get("pbr") or {})
        pbr_cfg["enabled"] = args.pbr != "off"
        pbr_cfg["mode"] = args.pbr
        farm_cfg = {**farm_cfg, "pbr": pbr_cfg}
    build(farm_cfg, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
