"""Procedural PBR texture maps for the balance-of-plant surfaces (pure, Isaac-free).

Generates tileable albedo / roughness / normal maps at build time for the
surfaces that are *not* part of the fault-signature machinery: ground, roads,
concrete pads, the inverter/transformer housings and the perimeter fence. Written
next to the USD, never committed (`CLAUDE.md`: no large binaries), exactly like
the sky texture.

**Panel glass and frame are deliberately NOT here.** Their look feeds the soiling
bake and the hotspot emissive, which took four stacked fixes to get right — the
dust film's translucency is pre-blended in Python against the cell and frame
*diffuse constants*, and its alpha window (0.72-0.94) was tuned so the bright
aluminium rail is muted along with the cells. Texturing those two materials
changes what lies under the bake and re-opens the "bright frame lines read as a
hotspot" failure. That is a separate, flagged decision (see `SESSIONS.md`).

**The albedo invariant.** Every albedo map is a *modulation* whose mean is forced
to exactly 1.0, applied equally to R, G and B, multiplying the existing `_LOOKS`
diffuse constant. Two consequences, both deliberate:

- **Mean albedo is preserved by construction**, so the ground does not quietly
  get brighter or darker. The 0.30 ground albedo is itself a measured value —
  0.17 read as cold slate across 320 x 647 m and 0.44 measured as a near-white
  blowout that buried the roads in glare.
- **Hue is preserved exactly.** A per-channel modulation would shift the ground's
  R-B balance, which is the precise quantity Session 10c used to catch an
  emissive sky dome lighting the desert floor blue (dome off R-B +16, dome on
  R-B -38). Keeping the multiplier achromatic means that measurement stays a
  test of the *lighting*, not of the texture — the texture cannot alias into it.

So these maps add surface microstructure (and real roughness/normal variation)
without moving either number the previous session pinned down.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Texture resolution. 512 is enough for surfaces seen from >= 0.8 m standoff and
#: keeps build-time generation well under a second per map with numpy.
DEFAULT_SIZE = 512


@dataclass(frozen=True)
class SurfaceSpec:
    """How one balance-of-plant surface varies.

    `albedo_var` is the peak-to-mean fractional swing of the achromatic albedo
    modulation; `rough_mid`/`rough_var` the roughness band; `normal_strength` the
    height-to-normal gain (relief per tile); `octaves` how many noise scales
    stack; `seed` keeps different surfaces from sharing a pattern.
    """

    name: str
    albedo_var: float
    rough_mid: float
    rough_var: float
    normal_strength: float
    octaves: int
    seed: int


#: One entry per material NAME in `farm_builder._LOOKS` that gets textured.
#: Values chosen from what the surface physically is, not from taste:
SURFACES: dict[str, SurfaceSpec] = {
    # Dry desert soil: broad drifts plus fine grain, near-fully diffuse, real relief.
    "ground": SurfaceSpec(
        "ground", albedo_var=0.16, rough_mid=0.95, rough_var=0.05,
        normal_strength=0.05, octaves=5, seed=1301,
    ),
    # Compacted gravel haul road: coarser aggregate, rougher, more relief than soil.
    "road": SurfaceSpec(
        "road", albedo_var=0.20, rough_mid=0.93, rough_var=0.06,
        normal_strength=0.07, octaves=4, seed=2411,
    ),
    # Cast concrete pad: fine pitting, slightly smoother, shallow relief.
    "concrete": SurfaceSpec(
        "concrete", albedo_var=0.08, rough_mid=0.82, rough_var=0.07,
        normal_strength=0.03, octaves=4, seed=3527,
    ),
    # Painted steel inverter cabinet: near-flat panel, faint orange-peel only.
    "equipment": SurfaceSpec(
        "equipment", albedo_var=0.04, rough_mid=0.45, rough_var=0.10,
        normal_strength=0.01, octaves=3, seed=4643,
    ),
    # Dark strut / transformer housing.
    "structure": SurfaceSpec(
        "structure", albedo_var=0.05, rough_mid=0.58, rough_var=0.12,
        normal_strength=0.02, octaves=3, seed=5759,
    ),
    # Galvanised fence steel. Split out from the PANEL frame so the fence can be
    # textured without touching the deferred panel-frame material — the two
    # carried identical values and were one material, which made "texture the
    # fence" and "defer the panel frame" mutually exclusive. Weathered zinc:
    # patchier than paint, smoother than gravel, faint spangle relief.
    "fence_frame": SurfaceSpec(
        "fence_frame", albedo_var=0.07, rough_mid=0.42, rough_var=0.14,
        normal_strength=0.02, octaves=3, seed=6871,
    ),
}

#: The panel-side materials that must NEVER be textured — asserted in tests, not
#: just documented. `panel_frame` feeds the soiling bake's substrate colour and
#: `cell_*` are what the bake blends over; texturing either re-opens the
#: bright-frame-as-hotspot failure that took four fixes to close.
DEFERRED_SURFACES = frozenset({"panel_frame", "cell_healthy", "cell_hotspot"})


def _lattice(size: int, cells: int, seed: int):
    """One octave of tileable value noise, bilinearly upsampled with wrap."""
    import numpy as np

    rng = np.random.default_rng(seed)
    g = rng.random((cells, cells))
    # Wrap by one row/col so the interpolation closes the seam.
    g = np.pad(g, ((0, 1), (0, 1)), mode="wrap")
    ys = np.linspace(0.0, cells, size, endpoint=False)
    xs = np.linspace(0.0, cells, size, endpoint=False)
    y0 = ys.astype(int)
    x0 = xs.astype(int)
    fy = (ys - y0)[:, None]
    fx = (xs - x0)[None, :]
    # Smoothstep: bilinear alone leaves visible lattice creases.
    fy = fy * fy * (3.0 - 2.0 * fy)
    fx = fx * fx * (3.0 - 2.0 * fx)
    g00 = g[np.ix_(y0, x0)]
    g10 = g[np.ix_(y0 + 1, x0)]
    g01 = g[np.ix_(y0, x0 + 1)]
    g11 = g[np.ix_(y0 + 1, x0 + 1)]
    return (
        g00 * (1 - fy) * (1 - fx)
        + g10 * fy * (1 - fx)
        + g01 * (1 - fy) * fx
        + g11 * fy * fx
    )


def fbm(size: int, octaves: int, seed: int, base_cells: int = 4):
    """Tileable fractional Brownian motion in [0, 1], mean ~0.5.

    Octave cell counts double, so every octave divides `size` evenly and the
    result tiles exactly — a non-integer ratio would leave a seam at the wrap.
    """
    import numpy as np

    total = np.zeros((size, size))
    amp, norm, cells = 1.0, 0.0, base_cells
    for o in range(octaves):
        if cells > size:
            break
        total += amp * _lattice(size, cells, seed + 1013 * o)
        norm += amp
        amp *= 0.5
        cells *= 2
    out = total / norm if norm else total
    return out


def height_to_normal(height, strength: float):
    """Tangent-space normal map (RGB in [0,1]) from a height field.

    Gradients are taken with `np.roll`, so they wrap — a non-wrapping gradient
    would put a visible lit/dark seam along two edges of every tile.
    """
    import numpy as np

    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * 0.5
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * 0.5
    nx = -dx * strength * height.shape[1]
    ny = -dy * strength * height.shape[0]
    nz = np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    return np.stack([nx / length, ny / length, nz / length], axis=-1) * 0.5 + 0.5


@dataclass
class TextureSet:
    """Three `uint8` arrays for one surface. `albedo` is a MODULATION, not a
    colour: it is grey, mean 1.0 in float terms, and multiplies the material's
    `diffuseColor` constant in the shader."""

    name: str
    albedo: object  # H x W x 3 uint8, achromatic, mean == 128
    roughness: object  # H x W uint8
    normal: object  # H x W x 3 uint8

    @property
    def albedo_mean(self) -> float:
        """Mean of the modulation in shader units (should be ~1.0)."""
        return float(self.albedo.mean()) / 128.0


def make_texture_set(spec: SurfaceSpec, size: int = DEFAULT_SIZE) -> TextureSet:
    """Build one surface's maps. Pure given `spec` and `size` — the RNG is seeded
    off `spec.seed`, so a rebuild produces byte-identical textures and a stage is
    reproducible from a script + config."""
    import numpy as np

    field = fbm(size, spec.octaves, spec.seed)

    # --- albedo: achromatic modulation, mean forced to exactly 1.0 ---------- #
    mod = 1.0 + spec.albedo_var * (field - field.mean()) / max(
        1e-9, np.abs(field - field.mean()).max()
    )
    mod = mod / mod.mean()  # the invariant, enforced not assumed
    # 128 is shader-unit 1.0. Clipping is what makes the *stored* mean drift from
    # the float mean, so keep the swing well inside the 8-bit range.
    alb = np.clip(np.rint(mod * 128.0), 0, 255).astype(np.uint8)
    albedo = np.stack([alb] * 3, axis=-1)

    # --- roughness: a second, decorrelated field --------------------------- #
    r_field = fbm(size, max(2, spec.octaves - 1), spec.seed + 7919)
    rough = np.clip(
        spec.rough_mid + spec.rough_var * (r_field - 0.5) * 2.0, 0.0, 1.0
    )
    roughness = np.rint(rough * 255.0).astype(np.uint8)

    # --- normal: from the albedo's own height field, so relief and shading agree #
    normal = np.rint(height_to_normal(field, spec.normal_strength) * 255.0).astype(
        np.uint8
    )
    return TextureSet(spec.name, albedo, roughness, normal)


def tinted_albedo(ts: TextureSet, rgb: tuple[float, float, float]):
    """Apply the material's `diffuseColor` constant to the modulation.

    `UsdPreviewSurface` has no multiply node, so the constant cannot be combined
    with a texture in the shader graph — it has to be baked here. Because the
    modulation's mean is exactly 1.0 and achromatic, the result's **per-channel
    mean equals `rgb` exactly**: the textured surface has the same mean albedo and
    the same hue as the flat one it replaces. That is what stops this change from
    moving the ground's measured 0.30 albedo or its R-B balance.
    """
    import numpy as np

    mod = ts.albedo[..., 0].astype(np.float64) / 128.0
    out = np.stack([mod * c for c in rgb], axis=-1)
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def write_texture_set(
    out_dir: str,
    spec: SurfaceSpec,
    size: int = DEFAULT_SIZE,
    rgb: tuple[float, float, float] | None = None,
) -> dict:
    """Render one surface's maps to PNGs. Returns {channel: path}.

    `rgb` is the material's diffuse constant; when given, the albedo map is
    tinted by it so the shader can read it directly as `diffuseColor`.
    """
    from pathlib import Path

    from PIL import Image

    ts = make_texture_set(spec, size)
    if rgb is not None:
        ts = TextureSet(ts.name, tinted_albedo(ts, rgb), ts.roughness, ts.normal)
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    for channel, arr, mode in (
        ("albedo", ts.albedo, "RGB"),
        ("roughness", ts.roughness, "L"),
        ("normal", ts.normal, "RGB"),
    ):
        p = d / f"{spec.name}_{channel}.png"
        Image.fromarray(arr, mode=mode).save(p)
        paths[channel] = str(p)
    return paths


def write_all(
    out_dir: str,
    size: int = DEFAULT_SIZE,
    diffuse: dict[str, tuple[float, float, float]] | None = None,
) -> dict:
    """Generate every textured surface. Returns {surface: {channel: path}}.

    `diffuse` maps surface name -> the material's diffuse constant (i.e.
    `farm_builder._LOOKS[name][0]`), so each albedo map carries the colour the
    flat material had.
    """
    diffuse = diffuse or {}
    return {
        name: write_texture_set(out_dir, spec, size, diffuse.get(name))
        for name, spec in SURFACES.items()
    }
