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


def derived_ew_roads(site, min_width_m: float = ROAD_MIN_WIDTH_M) -> list[RoadStrip]:
    """East-west corridors the drawing itself contains.

    `derived_roads` reads gaps between *eastings* and so can only ever find
    north-south corridors. A plant also needs cross traffic, and the honest place
    to find it is the same place: a tracker table runs `length_m` north from its
    insert point, so wherever one band of tables ends and the next begins with
    room to spare, the CAD has left an east-west corridor.

    Deliberately DERIVED-only — there is no `inferred_ew_road` counterpart. An
    invented arterial would have to run *through* surveyed tracker tables, which
    does not merely add an assumption but contradicts the drawing. If the CAD
    leaves no cross corridor, the answer is no cross corridor.
    """
    if not site.tables:
        return []
    # Collect each table's north-south span, then look for clear bands between
    # the union of those spans.
    spans = sorted(
        (t.northing - site.origin_northing, t.northing - site.origin_northing + t.length_m)
        for t in site.tables
    )
    merged: list[list[float]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    min_x, _, max_x, _ = table_extent(site)
    roads = []
    for (_, hi), (lo, _) in zip(merged, merged[1:]):
        if lo - hi < min_width_m:
            continue
        roads.append(
            RoadStrip(
                x0=min_x,
                y0=hi,
                x1=max_x,
                y1=lo,
                provenance=DERIVED,
                name=f"road_ew_{len(roads)}",
            )
        )
    return roads


def access_spurs(
    pads: list[Pad], roads: list[RoadStrip], width_m: float = 5.0
) -> list[RoadStrip]:
    """A short spur joining each equipment pad to the nearest road. **INFERRED.**

    Without these the inverter stations sit in the array with no way in, which is
    the giveaway that a site model is decoration rather than a plant: a 4 MW
    central inverter arrives on a low-loader and is craned into place.

    The spur is drawn axis-aligned to the nearest edge of the nearest road, since
    everything else in this module is axis-aligned and a lone diagonal quad would
    have to be a mesh rather than a strip for no visual gain.
    """
    out: list[RoadStrip] = []
    if not roads:
        return out
    half = width_m / 2.0
    for pad in pads:
        best = None
        for road in roads:
            # Distance to this road's rectangle, and which way to travel to reach it.
            dx_w, dx_e = road.x0 - pad.x, pad.x - road.x1
            dy_s, dy_n = road.y0 - pad.y, pad.y - road.y1
            for dist, axis, sign in (
                (dx_w, "x", 1.0),
                (dx_e, "x", -1.0),
                (dy_s, "y", 1.0),
                (dy_n, "y", -1.0),
            ):
                if dist <= 0.0:
                    continue  # the pad already overlaps the road on this axis
                if best is None or dist < best[0]:
                    best = (dist, axis, sign, road)
        if best is None:
            continue  # already on a road; no spur needed
        dist, axis, sign, road = best
        n = len(out)
        if axis == "x":
            x_from = pad.x if sign > 0 else road.x1
            x_to = road.x0 if sign > 0 else pad.x
            out.append(
                RoadStrip(x_from, pad.y - half, x_to, pad.y + half, INFERRED, f"road_spur_{n:02d}")
            )
        else:
            y_from = pad.y if sign > 0 else road.y1
            y_to = road.y0 if sign > 0 else pad.y
            out.append(
                RoadStrip(pad.x - half, y_from, pad.x + half, y_to, INFERRED, f"road_spur_{n:02d}")
            )
    return out


def subdivide_strip(strip: RoadStrip, max_seg_m: float = 25.0) -> list[RoadStrip]:
    """Split a strip along its long axis into segments at most `max_seg_m` long.

    This is what lets a road follow the ground. A road was one flat quad placed
    at the terrain height of its own CENTRE, which is fine on the `flat` terrain
    it was written for and wrong on the real DEM: the block carries 2.2 m of
    relief, so a 647 m perimeter road hung up to ~1 m clear of the grade at one
    end and buried itself at the other. Segments are re-sampled individually, so
    the road tracks the surface instead of cutting through it.

    Segment length is a fidelity/cost knob: GLO-30 is sampled on a 20 m grid, so
    25 m segments are already finer than the terrain data and going smaller buys
    nothing but prims.
    """
    length_y = strip.y1 - strip.y0
    length_x = strip.x1 - strip.x0
    if max(length_x, length_y) <= max_seg_m:
        return [strip]
    import math

    if length_y >= length_x:
        n = max(1, math.ceil(length_y / max_seg_m))
        step = length_y / n
        return [
            RoadStrip(
                strip.x0,
                strip.y0 + i * step,
                strip.x1,
                strip.y0 + (i + 1) * step,
                strip.provenance,
                f"{strip.name}_s{i:03d}",
            )
            for i in range(n)
        ]
    n = max(1, math.ceil(length_x / max_seg_m))
    step = length_x / n
    return [
        RoadStrip(
            strip.x0 + i * step,
            strip.y0,
            strip.x0 + (i + 1) * step,
            strip.y1,
            strip.provenance,
            f"{strip.name}_s{i:03d}",
        )
        for i in range(n)
    ]


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
