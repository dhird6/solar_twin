"""Balance-of-plant geometry — roads, fencing, inverter stations (pure, no Isaac).

A solar farm is not a field of panels. It is panels plus the things that make
them a plant: access roads wide enough for a truck, inverter/transformer skids,
a perimeter fence, a gate. Without them a render has no sense of scale and no
sense of place — every module looks the same size as every other and the eye has
nothing to measure against.

**The honesty rule this module exists to enforce.** The vendor drawing describes
*hardware*: tracker tables and their modules. It does NOT describe roads,
fencing or electrical rooms. So every element here is tagged `DERIVED` (read out
of the real table geometry — a corridor the CAD leaves empty IS a road) or
`INFERRED` (standard plant practice, placed by us). `farm_builder` prints the
split, and nothing inferred may ever be described as surveyed (`NFR-07`).

Pure geometry, unit tested without pxr.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Provenance tags. Kept as plain strings so they can go straight into a USD
#: attribute and be visible in the stage, not just in this file.
DERIVED = "derived"    # read out of the vendor CAD's own table positions
INFERRED = "inferred"  # standard practice, chosen by us — NOT from the drawing

#: An across-row gap at least this wide is a vehicle corridor rather than the
#: normal maintenance aisle between two tracker rows. Khavda's ordinary aisles
#: are 5-6 m; its one internal road is 11 m.
ROAD_MIN_WIDTH_M = 8.0


@dataclass(frozen=True)
class RoadStrip:
    """An axis-aligned road quad in stage-local metres (Z-up world)."""

    x0: float
    y0: float
    x1: float
    y1: float
    provenance: str
    name: str

    @property
    def width_m(self) -> float:
        return min(self.x1 - self.x0, self.y1 - self.y0)


@dataclass(frozen=True)
class Pad:
    """An equipment pad: inverter/transformer skid, or a control room."""

    x: float
    y: float
    width_m: float
    depth_m: float
    provenance: str
    name: str


def table_extent(site) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of the HARDWARE in stage-local metres.

    Note the northing term: a table's stored position is its southern insert
    point and its modules run `length_m` north of it, so the extent is not the
    bounding box of the insert points (that under-reports the site by a full
    table length — 128 m at Khavda).
    """
    xs0 = [t.easting - site.origin_easting for t in site.tables]
    ys0 = [t.northing - site.origin_northing for t in site.tables]
    ys1 = [y + t.length_m for y, t in zip(ys0, site.tables)]
    half = site.module_length_m / 2.0  # chord sticks out either side of the tube
    return (min(xs0) - half, min(ys0), max(xs0) + half, max(ys1))


def derived_roads(site, min_width_m: float = ROAD_MIN_WIDTH_M) -> list[RoadStrip]:
    """North-south vehicle corridors the drawing itself contains.

    Tracker rows are spaced by a regular maintenance aisle; anywhere the spacing
    jumps well past that, the plant has left room for a truck. That is a road,
    and it is `DERIVED` — we are reading the CAD, not decorating it.
    """
    eastings = sorted({t.easting - site.origin_easting for t in site.tables})
    _, min_y, _, max_y = table_extent(site)
    half = site.module_length_m / 2.0
    roads = []
    for a, b in zip(eastings, eastings[1:]):
        gap = b - a
        if gap < min_width_m:
            continue
        # The corridor is the clear span between the two rows' module edges.
        roads.append(
            RoadStrip(
                x0=a + half,
                y0=min_y,
                x1=b - half,
                y1=max_y,
                provenance=DERIVED,
                name=f"road_ns_{len(roads)}",
            )
        )
    return roads


def perimeter_road(
    extent: tuple[float, float, float, float], width_m: float = 8.0, offset_m: float = 7.0
) -> list[RoadStrip]:
    """A ring road around the block. **INFERRED** — every utility-scale plant has
    one (fire access, module delivery, panel washing), but this drawing covers
    one block of one plot and its boundary works are outside the sheet.
    """
    min_x, min_y, max_x, max_y = extent
    x0, y0 = min_x - offset_m - width_m, min_y - offset_m - width_m
    x1, y1 = max_x + offset_m + width_m, max_y + offset_m + width_m
    w = width_m
    return [
        RoadStrip(x0, y0, x1, y0 + w, INFERRED, "road_perimeter_s"),
        RoadStrip(x0, y1 - w, x1, y1, INFERRED, "road_perimeter_n"),
        RoadStrip(x0, y0, x0 + w, y1, INFERRED, "road_perimeter_w"),
        RoadStrip(x1 - w, y0, x1, y1, INFERRED, "road_perimeter_e"),
    ]


def inverter_pads(
    site,
    roads: list[RoadStrip],
    module_watts: float = 600.0,
    mw_per_station: float = 4.0,
    pad_w: float = 12.0,
    pad_d: float = 7.0,
) -> list[Pad]:
    """Inverter/transformer skids along the internal road. **INFERRED.**

    Count follows plant capacity rather than a made-up number: 30,016 modules at
    ~600 W is ~18 MW_dc, and central inverter stations land around 4 MW each, so
    this block wants roughly four or five. They sit on the internal road because
    that is how they are cabled and craned in. ⚠ Module wattage is a class
    estimate, not a datasheet figure for this site.
    """
    if not roads:
        return []
    road = max(roads, key=lambda r: (r.y1 - r.y0) * (r.x1 - r.x0))
    capacity_mw = site.n_modules * module_watts / 1e6
    n = max(1, round(capacity_mw / mw_per_station))
    cx = (road.x0 + road.x1) / 2.0
    span = road.y1 - road.y0
    return [
        Pad(
            x=cx,
            # Evenly spaced along the road, inset from both ends.
            y=road.y0 + span * (i + 0.5) / n,
            width_m=pad_w,
            depth_m=pad_d,
            provenance=INFERRED,
            name=f"inverter_{i:02d}",
        )
        for i in range(n)
    ]


def fence_posts(
    extent: tuple[float, float, float, float], offset_m: float = 3.0, spacing_m: float = 12.0
) -> list[tuple[float, float]]:
    """Post positions for a perimeter fence, walked corner to corner. **INFERRED.**

    Spacing is a real constraint, not decoration: it sets how many posts the
    stage carries. At 12 m a ~1000 m perimeter is ~85 posts, which instance
    cheaply; at 2 m it would be 500 and buy nothing visually.
    """
    min_x, min_y, max_x, max_y = extent
    x0, y0 = min_x - offset_m, min_y - offset_m
    x1, y1 = max_x + offset_m, max_y + offset_m
    pts: list[tuple[float, float]] = []

    def walk(ax, ay, bx, by):
        import math

        length = math.hypot(bx - ax, by - ay)
        # CEIL, not floor: `spacing_m` is a maximum. Rounding down stretches the
        # real gap past what was asked for (a 10.6 m step for a 10 m spacing),
        # which on a fence reads as a missing post.
        n = max(1, math.ceil(length / spacing_m - 1e-9))
        for i in range(n):
            f = i / n
            pts.append((ax + (bx - ax) * f, ay + (by - ay) * f))

    walk(x0, y0, x1, y0)
    walk(x1, y0, x1, y1)
    walk(x1, y1, x0, y1)
    walk(x0, y1, x0, y0)
    return pts


def provenance_summary(items) -> dict[str, int]:
    """How much of what got built is read from the drawing vs. assumed by us.
    `farm_builder` prints this so a viewer of the render is told, every time."""
    out: dict[str, int] = {}
    for it in items:
        out[it.provenance] = out.get(it.provenance, 0) + 1
    return out
