"""Real OpenStreetMap features in the site's frame (pure python — no Isaac, no geo).

`tools/osm_fetch.py` fetches OSM for a site's bounding box, projects it into the
site's own CRS and anchors it to the site origin. This reads that bake and turns
polylines into renderable geometry: road ribbons, transmission lines, plant
boundary outlines — each **draped onto the terrain** so it sits on the ground
rather than through it.

The split is the same one `world/dem.py` uses against `tools/dem_fetch.py`:
network access and `pyproj` are ingest-time concerns, so nothing here imports
either. This module needs `yaml` and the standard library, which is what lets it
be unit tested off the Spark (`NFR-01`).

Provenance — the reason this module is separate from `world/site.py`
-------------------------------------------------------------------
`site.py` deals in `DERIVED` (read out of the vendor CAD) and `INFERRED` (our
own assumption). Neither fits real third-party mapping, so this adds a third
tag, `MAPPED`. Keeping it distinct is the whole point: a render can then say
which lines are the real regional road network and which are the plant furniture
we placed ourselves.

⚠ **Two things here are NOT mapped, and must not be described as if they were.**

1. **Road WIDTH.** OSM out here tags a centreline and almost never a width, so
   ribbons are extruded at a per-class default (`width_source: class_default` in
   the bake). The line's *position* is real; its *breadth* is a convention.
2. **Internal plant roads.** Measured over the real footprint: the OSM bbox
   contains **no internal access roads at all** — the plant's own roads are
   private and unmapped. They stay `derived`/`inferred` from `site.py`. What this
   module adds is the landscape the plant sits in, not the plant's own furniture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

#: Provenance tag for real third-party mapped geometry. Deliberately a different
#: string from `site.DERIVED`/`site.INFERRED` so a stage can be audited for which
#: of the three any given prim came from.
MAPPED = "mapped"

#: Layers `osm_fetch.py` files ways under.
LAYER_ROADS = "roads"
LAYER_POWER = "power"
LAYER_WATER = "water"

#: Transmission-line render heights by voltage (volts -> metres). Real tower
#: heights for the class; the conductor sag between towers is NOT modelled, so a
#: span is a straight chord — an approximation, stated (`NFR-07`).
_LINE_HEIGHT_M: tuple[tuple[float, float], ...] = (
    (765_000, 45.0),
    (400_000, 35.0),
    (220_000, 28.0),
    (132_000, 22.0),
    (0, 14.0),
)


@dataclass(frozen=True)
class OsmWay:
    """One OSM way, in stage-local metres (Z-up world, +X east, +Y north)."""

    osm_id: int
    layer: str
    kind: str
    points: tuple[tuple[float, float], ...]
    length_m: float
    closed: bool = False
    name: str = ""
    width_m: float = 0.0
    width_source: str = ""
    surface: str = ""
    voltage: str = ""
    operator: str = ""
    provenance: str = MAPPED

    @property
    def line_height_m(self) -> float:
        """Conductor height for a `power=line`, from its voltage class."""
        try:
            v = float(str(self.voltage).split(";")[0])
        except (TypeError, ValueError):
            v = 0.0
        for threshold, height in _LINE_HEIGHT_M:
            if v >= threshold:
                return height
        return _LINE_HEIGHT_M[-1][1]


@dataclass
class OsmFeatures:
    """A parsed `osm_fetch.py` bake."""

    ways: list[OsmWay] = field(default_factory=list)
    crs: str = ""
    origin_easting: float = 0.0
    origin_northing: float = 0.0
    source: str = ""
    license: str = ""

    def by_layer(self, layer: str) -> list[OsmWay]:
        return [w for w in self.ways if w.layer == layer]

    @property
    def roads(self) -> list[OsmWay]:
        return self.by_layer(LAYER_ROADS)

    @property
    def power(self) -> list[OsmWay]:
        return self.by_layer(LAYER_POWER)


def parse_features(doc: dict) -> OsmFeatures:
    """Parse the dict form of an `osm_fetch.py` YAML."""
    ways: list[OsmWay] = []
    for w in doc.get("ways") or []:
        pts = tuple((float(p[0]), float(p[1])) for p in w.get("points") or [])
        if len(pts) < 2:
            continue
        ways.append(
            OsmWay(
                osm_id=int(w.get("osm_id", 0)),
                layer=str(w.get("layer", "")),
                kind=str(w.get("kind", "")),
                points=pts,
                length_m=float(w.get("length_m", 0.0)),
                closed=bool(w.get("closed", False)),
                name=str(w.get("name", "") or ""),
                width_m=float(w.get("width_m", 0.0) or 0.0),
                width_source=str(w.get("width_source", "") or ""),
                surface=str(w.get("surface", "") or ""),
                voltage=str(w.get("voltage", "") or ""),
                operator=str(w.get("operator", "") or ""),
            )
        )
    origin = doc.get("origin") or {}
    return OsmFeatures(
        ways=ways,
        crs=str(doc.get("crs", "")),
        origin_easting=float(origin.get("easting", 0.0)),
        origin_northing=float(origin.get("northing", 0.0)),
        source=str(doc.get("source", "")),
        license=str(doc.get("license", "")),
    )


def load_features(path: str) -> OsmFeatures:
    """Read an `osm_fetch.py` YAML bake."""
    import yaml  # noqa: PLC0415 — lazy so importing this module needs no yaml

    with open(path) as f:
        return parse_features(yaml.safe_load(f))


def clip_to_radius(features: OsmFeatures, cx: float, cy: float, radius_m: float) -> OsmFeatures:
    """Keep only ways with a vertex within `radius_m` of (cx, cy).

    The bake covers a padded bbox so one ingest serves any subset of the plot;
    a 20-table patch does not want a 108 km transmission line crossing its stage.
    Whole ways are kept or dropped — splitting one would leave a road ending in
    mid-desert, which reads as broken geometry rather than as a clip.
    """
    keep = [
        w
        for w in features.ways
        if any(math.dist(p, (cx, cy)) <= radius_m for p in w.points)
    ]
    return OsmFeatures(
        ways=keep,
        crs=features.crs,
        origin_easting=features.origin_easting,
        origin_northing=features.origin_northing,
        source=features.source,
        license=features.license,
    )


def clip_polyline_to_box(points, x0: float, y0: float, x1: float, y1: float):
    """Split a polyline into the runs of it that lie inside an axis-aligned box.

    Returns a list of polylines (each >= 2 points), with the crossing points
    inserted exactly on the boundary, so a way that leaves the box ends ON the
    edge rather than at its last interior vertex.

    ⚠ **This is why `clip_to_radius` alone is not enough, and the mismatch was
    measured.** `clip_to_radius` keeps or drops WHOLE ways so a road never ends in
    mid-desert — but a 108 km transmission line with one vertex near the plant then
    brings all 108 km of itself. On the S05b subset that authored an OSM layer
    spanning **-23 to +31 km east and -70 km north while the ground mesh reached
    1.5 km**, leaving 582 towers standing in the void beyond the terrain, floating
    against the sky dome. Clipping to the ground's own extent puts the cut AT the
    horizon, where a road running out of frame is exactly what it should look like.
    """
    pts = [tuple(p) for p in points]
    if len(pts) < 2:
        return []

    def inside(p) -> bool:
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    def crossing(a, b):
        """Parameter t in (0, 1] where segment a->b last enters/leaves the box.

        Liang-Barsky: intersect the segment against the four half-planes.
        """
        dx, dy = b[0] - a[0], b[1] - a[1]
        t0, t1 = 0.0, 1.0
        for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
            if abs(p) < 1e-12:
                if q < 0:
                    return None  # parallel and outside
                continue
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return None
        return t0, t1

    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for a, b in zip(pts, pts[1:]):
        hit = crossing(a, b)
        if hit is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        t0, t1 = hit
        pa = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        pb = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        if not current:
            current = [pa]
        elif math.dist(current[-1], pa) > 1e-6:
            # Re-entered the box somewhere else: the previous run is finished.
            if len(current) >= 2:
                runs.append(current)
            current = [pa]
        current.append(pb)
        if t1 < 1.0 - 1e-9:          # left the box before reaching b
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)
    return runs


def clip_to_box(features: OsmFeatures, x0: float, y0: float, x1: float, y1: float) -> OsmFeatures:
    """Clip every way to an axis-aligned box, splitting ways that leave and
    re-enter it. A way clipped to nothing is dropped."""
    out: list[OsmWay] = []
    for way in features.ways:
        for run in clip_polyline_to_box(way.points, x0, y0, x1, y1):
            length = sum(math.dist(a, b) for a, b in zip(run, run[1:]))
            out.append(
                replace(
                    way,
                    points=tuple(run),
                    length_m=round(length, 1),
                    # A clipped ring is no longer closed unless it survived whole.
                    closed=way.closed and math.dist(run[0], run[-1]) < 1.0,
                )
            )
    return OsmFeatures(
        ways=out,
        crs=features.crs,
        origin_easting=features.origin_easting,
        origin_northing=features.origin_northing,
        source=features.source,
        license=features.license,
    )


def resample(points, max_step_m: float):
    """Insert vertices so no segment is longer than `max_step_m`.

    Needed because a road is draped by sampling terrain **per vertex**, and OSM
    digitises a straight desert track as two points kilometres apart. Without
    this a ribbon spans the relief as a chord and floats metres above a dip or
    disappears into a rise — the same class of bug as an undersampled ground
    mesh (see `layout.terrain_feature_step`).
    """
    if max_step_m <= 0 or len(points) < 2:
        return [tuple(p) for p in points]
    out: list[tuple[float, float]] = [tuple(points[0])]
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        n = max(1, int(math.ceil(seg / max_step_m)))
        for i in range(1, n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def ribbon(points, width_m: float):
    """Turn a centreline into a flat ribbon: `[(left, right), ...]` per vertex.

    Offsets use the **average of the two adjoining segment normals**, so the
    ribbon keeps a constant width through a bend instead of pinching on the
    inside of the corner. Degenerate (zero-length) segments are skipped rather
    than producing a NaN normal.
    """
    pts = [tuple(p) for p in points]
    if len(pts) < 2 or width_m <= 0:
        return []
    half = width_m / 2.0

    def normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy)
        return None if n < 1e-9 else (-dy / n, dx / n)

    seg_normals = []
    for a, b in zip(pts, pts[1:]):
        seg_normals.append(normal(a, b))
    # Carry the last valid normal forward/backward over degenerate segments.
    for i, nrm in enumerate(seg_normals):
        if nrm is None:
            seg_normals[i] = next(
                (m for m in seg_normals[i:] if m is not None),
                next((m for m in reversed(seg_normals[:i]) if m is not None), (1.0, 0.0)),
            )

    out = []
    for i, p in enumerate(pts):
        before = seg_normals[i - 1] if i > 0 else seg_normals[0]
        after = seg_normals[i] if i < len(seg_normals) else seg_normals[-1]
        nx, ny = before[0] + after[0], before[1] + after[1]
        mag = math.hypot(nx, ny)
        if mag < 1e-9:                       # a perfect hairpin: fall back to one side
            nx, ny, mag = after[0], after[1], 1.0
        nx, ny = nx / mag, ny / mag
        # Miter correction: on a bend the averaged normal is shorter than the
        # segment normals, so scale it back out to hold the width.
        cos_half = max(0.35, (before[0] * nx + before[1] * ny))
        d = half / cos_half
        out.append(((p[0] - nx * d, p[1] - ny * d), (p[0] + nx * d, p[1] + ny * d)))
    return out
