"""Procedural farm layout — pure geometry + seeded fault injection.

Shared logic: `run.py` uses it now (against the fake backend) and
`farm_builder.py` will use the SAME layout to author the USD stage on the Spark,
so the panel grid and the seeded fault picks are identical in sim and in tests.
No Isaac import here — this is pure math and lives in `world/` only because it
is farm-shaped; importing it never drags in pxr.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from solar_twin.control.base import Waypoint
from solar_twin.orchestrator.mission import InspectionTarget
from solar_twin.schema.pv_module import (
    GeoAnchor,
    PanelRecord,
    PanelState,
    coerce_state,
    local_to_geo,
    panel_id,
)


def terrain_height(x: float, y: float, cfg: dict) -> float:
    """Ground elevation (meters) at stage-local (x, y). Pure + deterministic so
    the farm builder (mesh), the panel mounts, and the drone waypoints all agree
    on where the ground is — the whole point of a shared terrain function. `flat`
    (or a missing block) returns 0.0, preserving the old flat-ground behaviour.

    A sum of two orthogonal sines gives smooth, seed-free, gentle undulation
    (no numpy — stays importable in the Isaac-free tests)."""
    spec = cfg.get("terrain", {}) or {}
    if spec.get("kind", "flat") != "heightfield":
        return 0.0
    amp = float(spec.get("amplitude", 0.0))
    wl = float(spec.get("wavelength", 12.0)) or 12.0
    k = 2.0 * math.pi / wl
    return amp * 0.5 * (math.sin(k * x) + math.cos(k * y * 0.75))


def fault_cells(
    state: PanelState, cell_rows: int, cell_cols: int, rng: random.Random
) -> set[tuple[int, int]]:
    """Which (row, col) PV cells carry a *cell-level* fault look.

    Only faults that are genuinely cell-localized in reality belong here: a
    **hotspot** is one overheating cell, so snapping it to the cell grid is
    physically correct. **Soiling is deliberately NOT handled here** — dust is a
    film lying on the glass, so it crosses cell boundaries; see `soiling_tiles`.
    (Rendering soiling as opaque, perfectly cell-aligned rectangles made the VLM
    read it as a two-tone design pattern rather than contamination.)

    Pure + deterministic in `rng` so the sim and any future re-derivation
    (Replicator labels) agree on the mask. Isaac-free — tested without pxr."""
    if cell_rows <= 0 or cell_cols <= 0:
        return set()
    if state is PanelState.HOTSPOT:
        cells = [(r, c) for r in range(cell_rows) for c in range(cell_cols)]
        n = min(rng.randint(1, 2), len(cells))
        return set(rng.sample(cells, k=n))
    return set()


def soiling_field(
    n_rows: int, n_cols: int, rng: random.Random
) -> dict[tuple[int, int], float]:
    """Dust *density* per sub-tile, on a grid **independent of the PV cells** so the
    patch crosses cell boundaries the way real dust does.

    Density = a lower-edge gradient (row 0 is the panel's downhill edge, where dust
    accumulates on a tilted module) + a few soft blobs. **Deliberately smooth — no
    per-tile noise** so callers can shade with it directly; white noise here makes
    the film look dithered/speckled rather than like a continuous layer of dust.
    Edge raggedness comes from jittering the *threshold* instead (`soiling_tiles`).
    Pure and deterministic in `rng`; Isaac-free."""
    field: dict[tuple[int, int], float] = {}
    if n_rows <= 0 or n_cols <= 0:
        return field
    span = max(n_rows, n_cols)
    blobs = [
        (
            rng.uniform(0.0, n_rows * 0.6),  # biased toward the lower edge
            rng.uniform(0.0, n_cols),
            rng.uniform(0.18, 0.38) * span,
        )
        for _ in range(3)
    ]
    for r in range(n_rows):
        grad = 1.0 - (r / max(1, n_rows - 1))  # 1.0 at the lower edge -> 0.0 at top
        for c in range(n_cols):
            density = 0.28 * grad
            for br, bc, radius in blobs:
                d = math.hypot(r - br, c - bc)
                if d < radius:
                    density += 1.0 - d / radius
            field[(r, c)] = density
    return field


def soiling_mask(
    field: dict[tuple[int, int], float], rng: random.Random, threshold: float = 0.5
) -> set[tuple[int, int]]:
    """Which sub-tiles carry dust: threshold the (smooth) field with a jittered
    cut so the drift's OUTLINE is ragged while its interior stays smooth."""
    return {k for k, v in field.items() if v + rng.uniform(-0.15, 0.15) > threshold}


def soiling_tiles(
    n_rows: int, n_cols: int, rng: random.Random, threshold: float = 0.5
) -> set[tuple[int, int]]:
    """Convenience: build the field and mask it in one call (see both above)."""
    return soiling_mask(soiling_field(n_rows, n_cols, rng), rng, threshold)


@dataclass(frozen=True)
class PanelSite:
    panel_id: str
    row: int
    col: int
    position: tuple[float, float, float]  # stage-local meters (Z-up)
    geo_position: tuple[float, float, float]  # (lat, lon, elev)


class FarmLayout:
    """Panel grid + georef derived from a parsed ``farm.yaml`` dict."""

    def __init__(self, farm_cfg: dict):
        self.cfg = farm_cfg
        grid = farm_cfg["grid"]
        self.rows = int(grid["rows"])
        self.cols = int(grid["cols"])
        self.row_pitch = float(grid["row_pitch"])
        self.col_pitch = float(grid["col_pitch"])
        self.origin = tuple(float(v) for v in grid.get("origin", [0.0, 0.0, 0.0]))
        geo = farm_cfg["georef"]
        self.anchor = GeoAnchor(
            lat0=float(geo["lat0"]),
            lon0=float(geo["lon0"]),
            elev0=float(geo.get("elev0", 0.0)),
            heading_deg=float(geo.get("heading_deg", 0.0)),
        )
        self.sites = self._build_sites()

    def _build_sites(self) -> list[PanelSite]:
        ox, oy, oz = self.origin
        sites: list[PanelSite] = []
        for row in range(self.rows):
            for col in range(self.cols):
                x = ox + col * self.col_pitch
                y = oy + row * self.row_pitch
                # Panels stand ON the ground: base z follows the terrain.
                z = oz + terrain_height(x, y, self.cfg)
                sites.append(
                    PanelSite(
                        panel_id=panel_id(row, col),
                        row=row,
                        col=col,
                        position=(x, y, z),
                        geo_position=local_to_geo(x, y, z, self.anchor),
                    )
                )
        return sites

    @property
    def n_panels(self) -> int:
        return len(self.sites)

    def seeded_faults(self) -> dict[str, PanelState]:
        """Seeded pick of which panels are faulted and with what state.

        Deterministic in (seed, rate, states, grid) so a run replays exactly.
        """
        faults_cfg = self.cfg.get("faults", {})
        rate = float(faults_cfg.get("rate", 0.0))
        states = [coerce_state(s) for s in faults_cfg.get("states", [])]
        if rate <= 0.0 or not states:
            return {}
        seed = int(self.cfg.get("seed", 0))
        rng = random.Random(seed)
        n_fault = round(rate * self.n_panels)
        chosen = rng.sample(self.sites, k=min(n_fault, self.n_panels))
        return {site.panel_id: rng.choice(states) for site in chosen}

    def panel_records(self) -> list[PanelRecord]:
        """Panels as records with seeded faults applied (for the fake backend)."""
        faults = self.seeded_faults()
        return [
            PanelRecord(
                panel_id=s.panel_id,
                grid_index=(s.row, s.col),
                state=faults.get(s.panel_id, PanelState.HEALTHY),
                geo_position=s.geo_position,
            )
            for s in self.sites
        ]

    def panel_top_z(self, base_z: float | None = None) -> float:
        """Z of a panel's top face = ground/base z + mount height + half thickness.

        Drone standoffs are measured from *here*, not absolute zero — otherwise the
        close-confirm camera ends up below the panel (it did: confirm=1.0 abs put
        the camera at 0.7 m under a 0.75 m panel). Passing the panel's own base_z
        (which follows the terrain) keeps framing correct over undulating ground.
        `base_z=None` uses the origin (flat-ground convenience for tests)."""
        panel = self.cfg.get("panel", {})
        mount_h = float(panel.get("mount_height", 0.75))
        ph = float(panel.get("height", 0.05))
        base = self.origin[2] if base_z is None else base_z
        return base + mount_h + ph / 2.0

    def inspection_targets(self, mission_cfg: dict) -> list[InspectionTarget]:
        """Waypoints per panel derived from layout + mission kinematics.

        Screen/confirm standoffs are meters *above that panel's top* (terrain-
        relative); the ground bot stays at ground level in front of the panel."""
        kin = mission_cfg.get("kinematics", {})
        screen_z = float(kin.get("screen_standoff", 2.5))
        confirm_z = float(kin.get("confirm_standoff", 0.8))
        approach_offset = self.row_pitch / 2.0
        targets: list[InspectionTarget] = []
        for s in self.sites:
            x, y, z = s.position  # z already follows the terrain
            top = self.panel_top_z(z)
            targets.append(
                InspectionTarget(
                    panel_id=s.panel_id,
                    approach=Waypoint(x, y - approach_offset, z),
                    screen=Waypoint(x, y, top + screen_z),
                    confirm=Waypoint(x, y, top + confirm_z),
                )
            )
        return targets
