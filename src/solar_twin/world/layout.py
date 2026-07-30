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
    cell_for_panel,
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


#: Loaded DEMs, keyed by (sidecar path, site origin). Sampling is called ~30k
#: times per build (once per module) plus once per ground vertex, so the grid is
#: read from disk once, not per call.
_DEM_CACHE: dict = {}


def _dem_for(cfg: dict):
    """The `DemTerrain` for this config, or None when terrain is not DEM-backed."""
    spec = cfg.get("terrain", {}) or {}
    if spec.get("kind") != "dem":
        return None
    path = spec.get("path")
    if not path:
        raise ValueError("terrain.kind: dem requires terrain.path (a dem_fetch.py sidecar)")
    origin_e = float(spec.get("site_origin_easting", 0.0))
    origin_n = float(spec.get("site_origin_northing", 0.0))
    key = (str(path), origin_e, origin_n, spec.get("datum", "hardware_mean"))
    if key not in _DEM_CACHE:
        from solar_twin.world.dem import DemTerrain

        # No bounds needed: dem_fetch.py already cropped the grid to this site
        # plus a margin, so the grid mean IS the site mean. Passing the panel
        # footprint would be circular — the footprint needs terrain to exist.
        _DEM_CACHE[key] = DemTerrain.load(
            str(path), origin_e, origin_n,
            datum=str(spec.get("datum", "hardware_mean")),
        )
    return _DEM_CACHE[key]


def _pad_for(cfg: dict, dem):
    """The `GradedPad` for this config, or None when grading is off.

    Cached like the DEM: fitting samples the terrain on a grid, and
    `terrain_height` is called per module, per waypoint and per ground-mesh
    vertex — refitting there would be a quadratic cost in the hot path.

    ⚠ The footprint comes from `terrain.pad_bounds` when given, else the DEM
    patch's own extent. It deliberately does NOT come from the panel positions:
    those are derived from terrain, so asking terrain to depend on them is
    circular — the same trap `_dem_for` calls out for the datum.
    """
    spec = cfg.get("terrain", {}) or {}
    if not spec.get("graded"):
        return None
    b = spec.get("pad_bounds")
    if b:
        min_x, max_x, min_y, max_y = (float(v) for v in b)
    else:
        # The baked patch is the site plus a margin; shrink it so the pad covers
        # the hardware rather than the margin, and the blend has somewhere to go.
        span_x = (dem.grid.shape[1] - 1) * dem.step_m
        span_y = (dem.grid.shape[0] - 1) * dem.step_m
        off_e = dem.origin_e - dem.site_origin_e
        off_n = dem.origin_n - dem.site_origin_n
        margin = float(spec.get("pad_margin_m", 60.0))
        min_x, max_x = off_e + margin, off_e + span_x - margin
        min_y, max_y = off_n + margin, off_n + span_y - margin
    key = ("pad", id(dem), min_x, max_x, min_y, max_y,
           spec.get("pad_tolerance_m"), spec.get("pad_blend_m"))
    if key not in _DEM_CACHE:
        from solar_twin.world.grading import fit_pad

        pad = fit_pad(dem, min_x, max_x, min_y, max_y,
                      blend_m=float(spec.get("pad_blend_m", 40.0)))
        pad.tolerance_m = float(spec.get("pad_tolerance_m", 0.025))
        _DEM_CACHE[key] = pad
    return _DEM_CACHE[key]


def terrain_height(x: float, y: float, cfg: dict) -> float:
    """Ground elevation (meters) at stage-local (x, y). Pure + deterministic so
    the farm builder (mesh), the panel mounts, and the drone waypoints all agree
    on where the ground is — the whole point of a shared terrain function.

    Three kinds:
    - `flat` (or missing) returns 0.0 — the original behaviour.
    - `heightfield` sums two orthogonal sines: smooth, seed-free, **synthetic**.
      Fine for the procedural test farm, never for a real site.
    - `dem` samples a real Copernicus GLO-30 patch baked by `tools/dem_fetch.py`
      (see `world/dem.py`). Heights are relative to a datum so the plant still
      straddles z=0 rather than sitting at its true 4 m above sea level.

    On top of `dem`, `terrain.graded: true` returns the **engineered civil pad**
    (`world/grading.py`) instead of raw satellite ground. GLO-30 is a pre-grading
    DSM, and building on it makes the worst Khavda row need 0.53 m of pile-height
    variation — a real plant graded that away. Off by default so every previously
    recorded number stays reproducible.
    """
    spec = cfg.get("terrain", {}) or {}
    kind = spec.get("kind", "flat")
    if kind == "dem":
        dem = _dem_for(cfg)
        if dem is None:
            return 0.0
        pad = _pad_for(cfg, dem)
        return pad.height(x, y) if pad is not None else dem.height(x, y)
    if kind != "heightfield":
        return 0.0
    amp = float(spec.get("amplitude", 0.0))
    wl = float(spec.get("wavelength", 12.0)) or 12.0
    k = 2.0 * math.pi / wl
    return amp * 0.5 * (math.sin(k * x) + math.cos(k * y * 0.75))


def terrain_feature_step(cfg: dict) -> float:
    """The finest horizontal detail `terrain_height` actually carries, in metres.

    This is the spacing a ground MESH has to be tessellated at to represent the
    terrain it is drawing. Sampling coarser than this aliases: the drawn surface
    then disagrees with the `terrain_height` that panels and waypoints were
    mounted from, so a panel can clear the terrain function and still be buried by
    the triangle rendered beneath it.

    ⚠ That is not hypothetical. The ground mesh used to be a fixed 48-160 verts
    stretched across a horizon-sized sheet — 40 m spacing over a 14 m-wavelength
    heightfield on the procedural farm (a 3x undersample, measured: panel bottom
    z=0.254 against a drawn ground of 0.412, i.e. **buried by 158 mm**), and 65 m
    over Khavda's 20 m DEM posts. `world/farm_builder` now grades its ground mesh
    off this value.

    A `dem` returns its own post spacing: the mesh interpolates bilinearly between
    posts and so does `DemTerrain.height`, so vertices AT the posts reproduce the
    real surface exactly and anything finer buys nothing.
    """
    spec = cfg.get("terrain", {}) or {}
    kind = spec.get("kind", "flat")
    if kind == "dem":
        dem = _dem_for(cfg)
        if dem is None:
            return 0.0
        # A graded pad is a plane plus a blended residual; the residual is sampled
        # from the DEM, so the DEM's step still bounds the detail.
        return float(dem.step_m)
    if kind == "heightfield":
        # Four samples per hump — enough to carry a sine's peak and trough. Two
        # would alias a hump into a straight line at the wrong height.
        return float(spec.get("wavelength", 12.0) or 12.0) / 4.0
    return 0.0  # flat: no detail to lose at any spacing


def cell_id_for(site, farm_cfg: dict) -> str:
    """`grid:id` — the dispatch cell a panel site rolls up to, or `""` when off.

    **The one place this is derived.** `farm_builder._cell_id_for` delegates here
    and `FarmLayout.panel_records` calls it, so the USD stage and the in-memory
    records the mission ranks can never disagree about which cell a panel is in.
    Two implementations of this would be two conventions, and a join key with two
    conventions is not a join key.

    `(site.row, site.col)` is `(table index, module index)` — set by
    `layout_import.expand_sites`, and the procedural grid's own (row, col) — so
    the cell falls out of the layout's OWN structure. Nothing is invented here:
    no geometric grid is imposed, and the table is used because it is the finest
    unit the vendor DWG actually carries (see `schema.pv_module`'s `grid:`
    namespace note — a cell is a table, and a table is NOT a string).

    Off unless `grid.enabled` is set in `farm.yaml`, so a stage built without it
    is byte-identical to one built before the namespace existed, and a mission
    over such a stage sees `cell_id == ""` on every record.
    """
    g = farm_cfg.get("grid", {}) or {}
    if not g.get("enabled", False):
        return ""
    return cell_for_panel((site.row, site.col), int(g.get("modules_per_cell", 0)))


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
        # A DEM is indexed in survey coordinates, and the anchor that maps stage
        # (0,0) to them lives in the site file. Inject it rather than asking a
        # config to restate it: two copies of a georeference is one too many.
        tspec = self.cfg.get("terrain") or {}
        if tspec.get("kind") == "dem":
            tspec.setdefault("site_origin_easting", site.origin_easting)
            tspec.setdefault("site_origin_northing", site.origin_northing)
            self.cfg["terrain"] = tspec
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
        """Panels as records with seeded faults applied (for the fake backend).

        `cell_id` is stamped from `cell_id_for` — the SAME derivation
        `farm_builder` writes onto the prim as `grid:id` — so the ranker that
        reads these records buckets panels exactly the way the stage does. It was
        left blank here at first and the suspicion-first demo had to set it by
        hand, which is precisely how the mission and the stage would drift apart.

        Empty (and behaviour byte-identical to before) unless `grid.enabled`.
        """
        faults = self.seeded_faults()
        return [
            PanelRecord(
                panel_id=s.panel_id,
                grid_index=(s.row, s.col),
                state=faults.get(s.panel_id, PanelState.HEALTHY),
                geo_position=s.geo_position,
                cell_id=cell_id_for(s, self.cfg),
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

    def route_sites(self, mission_cfg: dict) -> list[PanelSite]:
        """The order the fleet visits panels in.

        `route: linear` (default) walks `self.sites` as laid out — table by table,
        module 0 upward. It is the order every KPI so far was measured in, so it
        stays the default: changing it would make new numbers incomparable with
        old ones.

        `route: serpentine` reverses every second table, so the fleet turns round
        at the end of a row and comes back down the next one. On a real block that
        is the difference between a patrol and a farce: a 128 m table walked
        one-way means a 128 m deadhead back to the start of the next row, every
        row. This is the coverage pattern a real survey flies (and the shape
        `cuOpt` would optimise later, `FR-xx`).

        `stride` samples every Nth module — a coverage sweep rather than a census.
        ⚠ It changes what the run measures: the denominator is the panels VISITED,
        not the panels on site.

        `route: fault_zone` returns a contiguous window of `zone_panels` panels
        centred on a seeded fault, for `ScoutDispatchMission`. It exists because a
        watchable survey and a sparse fault rate are otherwise incompatible: on the
        whole plot at `faults.rate` 5e-4 a 24-panel window has a ~1% chance of
        containing anything to find, so a sweep from panel 0 shows a drone flying
        over healthy glass forever. Centring the window on a fault is the sim
        standing in for the string-level telemetry a real plant uses to pick which
        zone to survey — the drone still has to find the panel visually.

        ⚠⚠ `fault_zone` is NOT a measurement route. It selects on ground truth, so
        its fault prevalence is set by the window, not the site: every rate-style
        KPI (`KPI-01`, `KPI-03`) is meaningless under it. It also ignores `stride`,
        because subsampling can drop the very panel the window was built around.
        """
        route = str(mission_cfg.get("route", "linear"))
        stride = max(1, int(mission_cfg.get("panel_stride", 1)))

        sites = list(self.sites)
        if route == "fault_zone":
            return self._fault_zone_sites(mission_cfg, sites)
        if stride > 1:
            sites = sites[::stride]
        if route != "serpentine":
            return sites

        # Group by table, preserving the order tables first appear, then flip the
        # traversal of alternate tables.
        by_table: dict[int, list[PanelSite]] = {}
        for s in sites:
            by_table.setdefault(s.row, []).append(s)
        out: list[PanelSite] = []
        for i, (_row, group) in enumerate(by_table.items()):
            out.extend(reversed(group) if i % 2 else group)
        return out

    def _fault_zone_sites(self, mission_cfg: dict, sites: list) -> list:
        """A contiguous window of panels centred on the `zone_index`-th seeded fault.

        Contiguous in `self.sites` order (table by table, module 0 upward) rather
        than by euclidean distance, so the window is physically compact and the
        fleet's commutes inside it stay short — which is the whole point of
        surveying a zone instead of a plot.
        """
        zone = max(1, int(mission_cfg.get("zone_panels", 24)))
        which = max(0, int(mission_cfg.get("zone_index", 0)))
        faults = self.seeded_faults()

        fault_positions = [i for i, s in enumerate(sites) if s.panel_id in faults]
        if not fault_positions:
            # Loud, not silent: without this the caller gets a plausible-looking
            # window of healthy panels and concludes the escalation path is broken.
            print(
                f"  [warn] route: fault_zone but this stage has no seeded faults "
                f"(faults.rate is 0?) — falling back to the first {zone} panels; "
                "nothing will escalate",
                flush=True,
            )
            return sites[:zone]

        centre = fault_positions[min(which, len(fault_positions) - 1)]
        start = max(0, min(centre - zone // 2, len(sites) - zone))
        window = sites[start : start + zone]
        in_window = sum(1 for s in window if s.panel_id in faults)
        print(
            f"  [note] route: fault_zone — {len(window)} panels around "
            f"{sites[centre].panel_id} ({in_window} seeded fault"
            f"{'' if in_window == 1 else 's'} inside; "
            f"{len(fault_positions)} on the stage). Not a KPI route.",
            flush=True,
        )
        return window

    def inspection_targets(self, mission_cfg: dict) -> list[InspectionTarget]:
        """Waypoints per panel derived from layout + mission kinematics.

        Screen/confirm standoffs are meters *above that panel's top* (terrain-
        relative); the ground bot stays at ground level in front of the panel."""
        kin = mission_cfg.get("kinematics", {})
        screen_z = float(kin.get("screen_standoff", 2.5))
        confirm_z = float(kin.get("confirm_standoff", 0.8))
        # The survey pass flies higher than the screening pass so the scout sees a
        # panel plus its neighbours; the drop to `screen_z` is what makes the
        # converge beat visible. Defaults to the screening standoff, which makes the
        # scout a no-op change for any config that does not set it.
        scout_z = float(kin.get("scout_standoff", screen_z))
        approach_offset = self.row_pitch / 2.0
        targets: list[InspectionTarget] = []
        for s in self.route_sites(mission_cfg):
            x, y, z = s.position  # z already follows the terrain
            top = self.panel_top_z(z, s)
            targets.append(
                InspectionTarget(
                    panel_id=s.panel_id,
                    approach=Waypoint(x, y - approach_offset, z),
                    screen=Waypoint(x, y, top + screen_z),
                    confirm=Waypoint(x, y, top + confirm_z),
                    scout=Waypoint(x, y, top + scout_z),
                )
            )
        return targets
