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


#: Fallback tilt when a config omits `panel.tilt_deg`. Shared by `_build_sites`
#: and `panel_top_z` so a site's authored angle and the waypoint above it can
#: never come from two different defaults.
DEFAULT_TILT_DEG = 20.0

#: Memoisation sentinel — `None` is a real answer from `tracker_rotation_deg`
#: ("this site is fixed-tilt"), so it cannot double as "not computed yet".
_UNSET = object()


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
    #: Plan rotation of the panel's mounting structure, degrees about +Z. Zero for
    #: the procedural grid; per-table for a CAD-imported site, where different
    #: blocks can face different ways.
    azimuth_deg: float = 0.0
    #: Panel tilt, degrees. For a FIXED-tilt site this is the real tilt. For a
    #: TRACKER site it is only the nominal/stowed angle — the true angle is
    #: dynamic (sun-following), so a consumer that treats this as ground truth on
    #: a tracker site is wrong (`NFR-07`).
    tilt_deg: float = 0.0
    #: Module extent along stage +X and +Y, metres, BEFORE `azimuth_deg` is
    #: applied. Zero means "fall back to the config's `panel.width` /
    #: `panel.length`" (the procedural grid).
    #:
    #: This is per-site and not a single config pair because the two layout
    #: sources put the module's long edge on DIFFERENT stage axes: the procedural
    #: farm's rows run along +X (so the long edge is +Y), while a CAD table's
    #: torque tube runs along +Y (so the long edge is +X). A hand-written global
    #: pair silently transposed the real Khavda module — 2.278 m of chord became
    #: 1.134 m and 112 modules overlapped 2:1 along their own tube.
    size_x_m: float = 0.0
    size_y_m: float = 0.0


class FarmLayout:
    """Panel sites + georef derived from a parsed ``farm.yaml`` dict.

    Two sources, one output. ``layout.kind`` selects between them and defaults to
    ``grid`` so every existing config keeps working untouched:

    - ``grid`` — the procedural seeded farm (`grid:` block).
    - ``file`` — a real, CAD-derived site expanded from ``layout.path``
      (`IF-08`); see `world/layout_import.py`.

    Downstream code only ever reads ``self.sites``, so nothing below this class
    needs to know which source was used.
    """

    def __init__(self, farm_cfg: dict):
        self.cfg = farm_cfg
        layout_cfg = farm_cfg.get("layout", {}) or {}
        self.kind = str(layout_cfg.get("kind", "grid"))
        geo = farm_cfg.get("georef", {}) or {}
        self.anchor = GeoAnchor(
            lat0=float(geo.get("lat0", 0.0)),
            lon0=float(geo.get("lon0", 0.0)),
            elev0=float(geo.get("elev0", 0.0)),
            heading_deg=float(geo.get("heading_deg", 0.0)),
        )
        self.site = None
        self._tracker_rot = _UNSET

        if self.kind == "file":
            self._init_from_file(
                str(layout_cfg["path"]),
                max_tables=int(layout_cfg.get("max_tables", 0) or 0),
            )
            return

        grid = farm_cfg["grid"]
        self.rows = int(grid["rows"])
        self.cols = int(grid["cols"])
        self.row_pitch = float(grid["row_pitch"])
        self.col_pitch = float(grid["col_pitch"])
        self.origin = tuple(float(v) for v in grid.get("origin", [0.0, 0.0, 0.0]))
        self.sites = self._build_sites()

    def _init_from_file(self, path: str, max_tables: int = 0) -> None:
        """Expand a CAD-derived site file into per-module panel sites.

        `max_tables > 0` renders only a contiguous southern band of the site —
        essential while the full 273-table block is ~2.2M USD prims (`IF-09`).
        Panel coordinates are unchanged by subsetting, so a subset is a genuine
        crop of the real site rather than a different one.
        """
        from solar_twin.world.layout_import import expand_sites, load_site, subset_site

        site = load_site(path)
        if max_tables:
            site = subset_site(site, max_tables)
        self.site = site
        self.origin = (0.0, 0.0, 0.0)  # stage origin == site file's `origin` anchor
        # Grid-shaped attributes still have consumers (`inspection_targets`'s
        # approach offset, the builder's ground extent). Derive honest analogues:
        # a "row" is a table, a "col" is a module along its torque tube, and the
        # across-row pitch is the aisle a drone actually flies down.
        self.rows = len(site.tables)
        self.cols = max((t.modules_per_row for t in site.tables), default=0)
        self.row_pitch = site.column_pitch_m() or 6.0
        self.col_pitch = site.module_pitch_m or 1.0
        self.sites = expand_sites(
            site,
            terrain_z=lambda x, y: terrain_height(x, y, self.cfg),
            panel_site_cls=PanelSite,
            panel_id_fn=panel_id,
        )

    def bounds(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) over all panel sites, stage-local metres.

        The builder must size the ground from this, not from ``rows × pitch`` — an
        imported site is irregular and has no meaningful row/col rectangle.
        """
        xs = [s.position[0] for s in self.sites]
        ys = [s.position[1] for s in self.sites]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def _build_sites(self) -> list[PanelSite]:
        ox, oy, oz = self.origin
        # The procedural farm is fixed-tilt and uniformly oriented, so the single
        # `panel.tilt_deg` is correct here — but it belongs ON the site, not read
        # separately by the builder, so imported per-table tilt/azimuth flows
        # through the same field instead of a second code path.
        pcfg = self.cfg.get("panel", {}) or {}
        tilt = float(pcfg.get("tilt_deg", DEFAULT_TILT_DEG))
        # Procedural rows run along +X, so the module's long edge lies along +Y —
        # exactly the config's own `width` (x) / `length` (y) convention.
        sx, sy = float(pcfg.get("width", 1.0)), float(pcfg.get("length", 2.0))
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
                        azimuth_deg=0.0,
                        tilt_deg=tilt,
                        size_x_m=sx,
                        size_y_m=sy,
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

    def tracker_rotation_deg(self) -> float | None:
        """The HSAT rotation this stage is authored at, or `None` for a fixed-tilt
        site. **The single source for the tracker angle** — `farm_builder` authors
        the panels from it and `panel_top_z` places the drone above them from it,
        so the geometry and the waypoints cannot silently disagree (they already
        did once for the sun light vs the tracker).

        A site is on trackers when it came from a CAD site file AND the config
        pins a real instant (`sun.timestamp`); a hand-set elevation/azimuth pair
        is the legacy scenario path and leaves panels at their nominal tilt.
        """
        if self._tracker_rot is not _UNSET:
            return self._tracker_rot
        sun_cfg = self.cfg.get("sun", {}) or {}
        rot: float | None = None
        if sun_cfg.get("timestamp") and self.site is not None:
            from solar_twin.world.solar import (
                DEFAULT_MAX_ROTATION_DEG,
                parse_timestamp,
                solar_position,
                tracker_rotation_deg,
            )

            elev, azim = solar_position(
                self.anchor.lat0, self.anchor.lon0, parse_timestamp(sun_cfg["timestamp"])
            )
            rot = tracker_rotation_deg(
                elev,
                azim,
                axis_azimuth_deg=0.0,  # Khavda torque tubes run north-south
                max_rotation_deg=float(
                    sun_cfg.get("tracker_max_rotation_deg", DEFAULT_MAX_ROTATION_DEG)
                ),
            )
        self._tracker_rot = rot
        return rot

    def panel_top_z(self, base_z: float | None = None, site: PanelSite | None = None) -> float:
        """Z of the HIGHEST point of a panel = base z + mount height + how far the
        tilted module rises above its pivot.

        Drone standoffs are measured from *here*, not absolute zero — otherwise the
        close-confirm camera ends up below the panel (it did: confirm=1.0 abs put
        the camera at 0.7 m under a 0.75 m panel). Passing the panel's own base_z
        (which follows the terrain) keeps framing correct over undulating ground.
        `base_z=None` uses the origin (flat-ground convenience for tests).

        ⚠ Tilt is NOT ignorable. A tracker at its 60° stop lifts the upper edge of
        a 2.278 m module 0.99 m above the torque tube; treating the module as flat
        (mount height + half thickness) put the 0.8 m confirm camera *below* that
        edge, inside the row, looking at the panel's underside.
        """
        panel = self.cfg.get("panel", {})
        mount_h = float(panel.get("mount_height", 0.75))
        ph = float(panel.get("height", 0.05))
        tilt = math.radians(abs(self.authored_tilt_deg(site)))
        # Which horizontal extent swings depends on the rotation axis: a tracker
        # turns about +Y (the torque tube), so its chord is the module's X extent;
        # a fixed-tilt row tilts about +X, so its chord is the Y extent.
        if self.tracker_rotation_deg() is not None:
            chord = (site.size_x_m if site else 0.0) or float(panel.get("width", 1.0))
        else:
            chord = (site.size_y_m if site else 0.0) or float(panel.get("length", 2.0))
        rise = chord / 2.0 * math.sin(tilt) + ph / 2.0 * math.cos(tilt)
        base = self.origin[2] if base_z is None else base_z
        return base + mount_h + rise

    def authored_tilt_deg(self, site: PanelSite | None = None) -> float:
        """The tilt the stage is actually authored at for `site`: the live tracker
        angle on a tracker site, else the site's own (fixed) tilt."""
        rot = self.tracker_rotation_deg()
        if rot is not None:
            return rot
        if site is not None:
            return site.tilt_deg
        return float((self.cfg.get("panel", {}) or {}).get("tilt_deg", DEFAULT_TILT_DEG))

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
            top = self.panel_top_z(z, s)
            targets.append(
                InspectionTarget(
                    panel_id=s.panel_id,
                    approach=Waypoint(x, y - approach_offset, z),
                    screen=Waypoint(x, y, top + screen_z),
                    confirm=Waypoint(x, y, top + confirm_z),
                )
            )
        return targets
