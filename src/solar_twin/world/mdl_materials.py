"""MDL-backed materials for the plant — the look layer (Isaac-bound).

## Why this exists

Every stage this project has shipped was painted with flat `UsdPreviewSurface`
colours: 13 constant-diffuse materials for a 30,016-module power plant, no
roughness maps, no normals, no glass. It rendered like coloured plastic, and the
owner's verdict on seeing it in the GUI — "this is not anything like real" — was
correct.

The cause was not effort, it was the wrong shader. **Omniverse RTX renders MDL
natively and only *translates* `UsdPreviewSurface`**, and NVIDIA's own SimReady
material guidance says to author with **OmniPBR** and **OmniGlass**. That also
explains the regression `farm_builder` documents at length: the textured
`UsdPreviewSurface` path rendered the desert near-black (ground R-B +23.5 -> -0.1)
and was bisected to "the diffuse input is being ignored outright". It was ignored
because the translation layer dropped it — not because the maps were wrong.

## Measured on this build, 2026-07-31

This Isaac Sim 6.0.1 source build ships **zero `.mdl` files** and configures no MDL
search path, which is why nobody could switch before. But a render probe settled it:

* `OmniPBR.mdl` / `OmniGlass.mdl` resolve **by bare name** — they are built into the
  RTX renderer rather than shipped as files, so using them needs **no network and no
  asset download**. That is what this module uses.
* A library MDL referenced by **https** (`.../Materials/Base/Metals/Aluminum_Anodized.mdl`)
  also resolved and rendered. Real library looks are therefore available, at the cost
  of a network dependency — kept optional here, see `library_material`.

## The binding contract

An MDL material is *not* a shader `id`. It is `SetSourceAsset(<file>, "mdl")` plus
`SetSourceAssetSubIdentifier(<module fn>, "mdl")`, with the material's **`mdl`**
surface output connected to the shader's `out`. Getting any of those three wrong
yields a silent default-grey surface, which is indistinguishable from a material
that "just looks flat" — the exact failure this module replaces.

## Deliberately NOT changed here

`farm_builder`'s `panel_frame` albedo (0.62) is load-bearing: the soiling film's
baked alpha window was tuned against it, and lowering it once produced a grid of
bright rails that Cosmos Reason read as "characteristic of a hotspot". So the PV
looks in `PLANT_LOOKS` are a *proposal* that must clear a render measurement
(warm ground R-B, no bright frame lines) before becoming any scenario's default.
`farm_builder` keeps them behind `materials: mdl`.
"""

from __future__ import annotations

from typing import Optional

from pxr import Gf, Sdf, UsdShade

#: NVIDIA's public asset root for this Isaac generation. Only needed by
#: `library_material`; the OmniPBR/OmniGlass path below touches no network.
S3_MATERIALS = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
    "/Assets/Isaac/6.0/Isaac/Materials"
)

#: Library MDLs verified to resolve (HTTP 200) and render on this build. The Isaac
#: 6.0 tree is much thinner than full vMaterials — notably there is **no ground,
#: sand or gravel material**, which is why the desert floor is OmniPBR + our own
#: generated maps rather than a library look.
LIBRARY = {
    "aluminum_anodized": ("Base/Metals/Aluminum_Anodized.mdl", "Aluminum_Anodized"),
    "aluminum_anodized_black": (
        "Base/Metals/Aluminum_Anodized_Black.mdl",
        "Aluminum_Anodized_Black",
    ),
    "steel_stainless": ("Base/Metals/Steel_Stainless.mdl", "Steel_Stainless"),
    "asphalt": ("Base/Natural/Asphalt.mdl", "Asphalt"),
    "tinted_glass": ("Base/Glass/Tinted_Glass_R75.mdl", "Tinted_Glass_R75"),
}


def mdl_material(
    stage,
    path: str,
    mdl_file: str,
    subidentifier: str,
    inputs: Optional[dict] = None,
) -> UsdShade.Material:
    """Author an MDL-backed material.

    `inputs` maps an MDL parameter name to `(Sdf.ValueTypeNames.X, value)`. An
    unknown parameter name is silently ignored by the renderer, so a typo here
    degrades the look rather than raising — which is why the probe in
    `tools/material_probe.py` renders each look and prints its pixels.
    """
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + "/Shader")
    shader.SetSourceAsset(Sdf.AssetPath(mdl_file), "mdl")
    shader.SetSourceAssetSubIdentifier(subidentifier, "mdl")
    for name, (vtype, value) in (inputs or {}).items():
        shader.CreateInput(name, vtype).Set(value)
    # The `mdl` render context — NOT the default `surface` output. A material whose
    # only surface output is the universal one is what makes RTX fall back to
    # UsdPreviewSurface translation and lose the MDL entirely.
    mat.CreateSurfaceOutput("mdl").ConnectToSource(shader.ConnectableAPI(), "out")
    return mat


def omni_pbr(stage, path: str, **kw) -> UsdShade.Material:
    """OmniPBR, the general-purpose opaque look. Built into the renderer."""
    return mdl_material(stage, path, "OmniPBR.mdl", "OmniPBR", _pbr_inputs(**kw))


def omni_glass(stage, path: str, **kw) -> UsdShade.Material:
    """OmniGlass, for the module cover glass.

    `thin_walled=True` is NVIDIA's own recommendation for panel glass: a PV cover
    sheet is a few millimetres of float glass, and modelling it as a solid volume
    buys refraction nobody sees while costing real path-tracing time.
    """
    return mdl_material(stage, path, "OmniGlass.mdl", "OmniGlass", _glass_inputs(**kw))


F = Sdf.ValueTypeNames.Float
C3 = Sdf.ValueTypeNames.Color3f
B = Sdf.ValueTypeNames.Bool
A = Sdf.ValueTypeNames.Asset
F2 = Sdf.ValueTypeNames.Float2


def _pbr_inputs(
    diffuse=None,
    roughness: Optional[float] = None,
    metallic: Optional[float] = None,
    emissive=None,
    emissive_intensity: Optional[float] = None,
    diffuse_texture: Optional[str] = None,
    roughness_texture: Optional[str] = None,
    normal_texture: Optional[str] = None,
    texture_scale: Optional[tuple] = None,
    bump_factor: Optional[float] = None,
    project_uvw: Optional[bool] = None,
) -> dict:
    """Map friendly names onto OmniPBR's actual parameter names.

    The indirection is the point: OmniPBR calls diffuse `diffuse_color_constant` and
    roughness `reflection_roughness_constant`, and a caller that guesses
    `diffuseColor` gets a silently default material.
    """
    out: dict = {}
    if diffuse is not None:
        out["diffuse_color_constant"] = (C3, Gf.Vec3f(*diffuse))
    if roughness is not None:
        out["reflection_roughness_constant"] = (F, float(roughness))
    if metallic is not None:
        out["metallic_constant"] = (F, float(metallic))
    if emissive is not None:
        out["enable_emission"] = (B, True)
        out["emissive_color"] = (C3, Gf.Vec3f(*emissive))
        out["emissive_intensity"] = (F, float(emissive_intensity or 1.0))
    if diffuse_texture:
        out["diffuse_texture"] = (A, Sdf.AssetPath(diffuse_texture))
    if roughness_texture:
        out["reflectionroughness_texture"] = (A, Sdf.AssetPath(roughness_texture))
        # Without this the texture is bound and then ignored in favour of the
        # constant — a 0-vs-1 switch that looks exactly like "the map did nothing".
        out["reflection_roughness_texture_influence"] = (F, 1.0)
    if normal_texture:
        out["normalmap_texture"] = (A, Sdf.AssetPath(normal_texture))
    if texture_scale is not None:
        out["texture_scale"] = (F2, Gf.Vec2f(*texture_scale))
    if bump_factor is not None:
        out["bump_factor"] = (F, float(bump_factor))
    if project_uvw is not None:
        out["project_uvw"] = (B, bool(project_uvw))
    return out


def _glass_inputs(
    color=None,
    ior: Optional[float] = None,
    thin_walled: bool = True,
    roughness: Optional[float] = None,
    depth: Optional[float] = None,
) -> dict:
    out: dict = {"thin_walled": (B, bool(thin_walled))}
    if color is not None:
        out["glass_color"] = (C3, Gf.Vec3f(*color))
    if ior is not None:
        out["glass_ior"] = (F, float(ior))
    if roughness is not None:
        out["frosting_roughness"] = (F, float(roughness))
    if depth is not None:
        out["depth"] = (F, float(depth))
    return out


def library_material(stage, path: str, key: str, **kw) -> UsdShade.Material:
    """A real NVIDIA library look, fetched over https.

    ⚠ **Adds a network dependency to rendering.** Kit caches the MDL and its
    textures, but a stage authored this way will not render offline the first time,
    and every stage this project ships is otherwise fully self-contained. Prefer
    `omni_pbr`. Verified: `aluminum_anodized` resolves and renders.
    """
    if key not in LIBRARY:
        raise ValueError(f"unknown library material {key!r}; known: {sorted(LIBRARY)}")
    rel, sub = LIBRARY[key]
    return mdl_material(stage, path, f"{S3_MATERIALS}/{rel}", sub, _pbr_inputs(**kw))


# --------------------------------------------------------------------------- #
# The plant's looks
# --------------------------------------------------------------------------- #

#: MDL replacements for `farm_builder._LOOKS`, keyed identically so the builder can
#: swap one table for the other. Values are kwargs for `omni_pbr` unless the entry
#: names `_glass`.
#:
#: Every roughness/metallic here is a real-surface figure rather than a guess:
#: anodised aluminium is a rough-ish metal (0.35, metallic 1.0), a PV cell under
#: glass is dark and glossy (0.18), dry desert soil is fully rough dielectric
#: (0.95, metallic 0), and asphalt/gravel sits just below that.
PLANT_LOOKS: dict[str, dict] = {
    # Cells sit UNDER the cover glass, so their own specular is low — the gloss you
    # see on a real module is the glass, authored separately as `panel_glass`.
    "cell_healthy": dict(diffuse=(0.016, 0.031, 0.098), roughness=0.30, metallic=0.15),
    "cell_hotspot": dict(
        diffuse=(0.14, 0.05, 0.03),
        roughness=0.5,
        metallic=0.0,
        emissive=(1.0, 0.16, 0.0),
        emissive_intensity=2.2,
    ),
    # ⚠ DARK ANODISED, deliberately NOT the flat path's 0.62 mill-finish — and this is
    # the one place the realism layer overrides a load-bearing value, so here is the
    # reasoning in full.
    #
    # 0.62 + metallic 1.0 was tried first and measured: at grazing incidence under a
    # bright sky the frames render as a hot bright grid over the array, which is
    # exactly the "cluster of bright pixels ... characteristic of a hotspot" that
    # Cosmos Reason misread once before. The flat path needs 0.62 because there the
    # frame IS the whole module slab showing between cells, and the soiling film's
    # alpha window was tuned against it. Here the frame is only a 35 mm perimeter bar,
    # so nothing else depends on its albedo — and black-anodised frames are what a
    # large share of utility modules actually ship with today. Real, and it removes a
    # false-fault mechanism instead of adding one.
    "panel_frame": dict(diffuse=(0.055, 0.055, 0.060), roughness=0.45, metallic=0.85),
    "fence_frame": dict(diffuse=(0.55, 0.56, 0.58), roughness=0.42, metallic=1.0),
    "ground": dict(diffuse=(0.30, 0.25, 0.19), roughness=0.95, metallic=0.0),
    "turbine": dict(diffuse=(0.88, 0.88, 0.90), roughness=0.40, metallic=0.0),
    "structure": dict(diffuse=(0.30, 0.30, 0.33), roughness=0.55, metallic=0.85),
    "road": dict(diffuse=(0.20, 0.19, 0.17), roughness=0.92, metallic=0.0),
    "concrete": dict(diffuse=(0.46, 0.45, 0.43), roughness=0.88, metallic=0.0),
    "equipment": dict(diffuse=(0.55, 0.57, 0.58), roughness=0.45, metallic=0.35),
    # NEW: the torque tube and pile steel that the plant never had at all.
    "galv_steel": dict(diffuse=(0.52, 0.53, 0.55), roughness=0.45, metallic=1.0),
}

#: The module cover glass — the single biggest visual difference, because it is what
#: makes a PV module read as glass-over-silicon instead of painted tile. A real PV
#: cover is low-iron float glass with an AR coat: nearly colourless, IOR ~1.5.
PANEL_GLASS = dict(color=(0.92, 0.95, 0.98), ior=1.5, thin_walled=True, roughness=0.02)


def build_plant_materials(
    stage,
    looks_root: str = "/World/Looks",
    textures: Optional[dict] = None,
) -> dict[str, UsdShade.Material]:
    """Author every plant material as MDL. Returns name -> Material.

    `textures` optionally maps a look name to `{"albedo":..., "roughness":...,
    "normal":..., "tile_m":...}`. Bound through **OmniPBR's** texture inputs, which
    is the half the old path got wrong: the maps were correct and the
    `UsdPreviewSurface` translation discarded them.
    """
    mats: dict[str, UsdShade.Material] = {}
    for name, kw in PLANT_LOOKS.items():
        kw = dict(kw)
        tex = (textures or {}).get(name)
        if tex:
            if tex.get("albedo"):
                kw["diffuse_texture"] = tex["albedo"]
            if tex.get("roughness"):
                kw["roughness_texture"] = tex["roughness"]
            if tex.get("normal"):
                kw["normal_texture"] = tex["normal"]
            if tex.get("tile_m"):
                # OmniPBR's texture_scale is in UV repeats, and `_set_uvs` already
                # lays UVs out in metres — so one repeat per tile is scale 1 and the
                # tiling lives in the UVs, not here. Passed through for the cases
                # where a caller wants extra repeats on top.
                kw["texture_scale"] = (tex["tile_m"], tex["tile_m"])
        mats[name] = omni_pbr(stage, f"{looks_root}/{name}", **kw)
    mats["panel_glass"] = omni_glass(stage, f"{looks_root}/panel_glass", **PANEL_GLASS)
    return mats
