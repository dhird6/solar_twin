"""Wind-turbine siting — wake-constrained scatter, not a lattice (pure, no Isaac).

The turbines were a hand-written list of five positions in
`configs/farm_khavda_block02.yaml`, and it read exactly like what it was: two
columns at fixed eastings, evenly spaced. Real wind farms are not laid out that
way, and the reason is physical rather than aesthetic — a turbine standing in
another's wake loses energy and gains fatigue load, so siting is driven by
spacing rules referred to the **prevailing wind**, plus setbacks from whatever
else is on the site.

So spacing here is an **ellipse, not a circle**. Industry practice is roughly
5-9 rotor diameters along the prevailing wind and 3-5 across it; a circular
Poisson-disk radius cannot express that, and would either waste the site
(circle = the downwind figure) or allow illegal wake overlap (circle = the
crosswind figure). `min_spacing_ellipse` rotates the constraint into wind
coordinates and tests there.

Sampling is dart-throwing with rejection against every accepted point. Bridson's
algorithm is faster but assumes an isotropic radius, which is the one thing this
problem does not have. At the scale that matters here (a handful to a few dozen
machines) the naive test is microseconds and the anisotropy is worth more than
the speed.

**Seeded.** The scatter takes the farm seed, so a build is reproducible: the same
config gives the same field, which is what lets a KPI number be compared across
runs at all. Unseeded turbine positions would silently move the shadows between
builds.

**Two placements, and they are different plants.** `perimeter` rings the machines
around the panel field; `interspersed` stands them *within* the footprint, in the
clearings between DC blocks. Khavda is a genuinely co-located wind+solar park —
the land is shared, not adjacent — so `interspersed` is the truer layout, and it
is what the multi-block S05b configs use.

⚠ It is also the placement that puts blade shadows on modules, which is precisely
what `ARRAY_SETBACK_D` was introduced to prevent. That is a trade, taken
knowingly: a turbine-shadow KPI measured on an interspersed stage is a property of
that stage and does not transfer from (or to) a `perimeter` one.

⚠ **Whether it fits is a property of the LAYOUT, not of the config.** Measured
2026-07-30 with `largest_interior_clearance`: the 24-block S05b plot has interior
clearings of >=500 m, while single-block BLOCK-02's largest is **25 m** against
the **84 m** a 140 m rotor needs. Inside one DC block the gaps are maintenance
aisles; the clearings a hybrid park actually uses are between blocks. So
`interspersed` on BLOCK-02 sites nothing and says why — it does not quietly fall
back to a ring and let the stage claim a layout it does not have.

⚠ **Clearance alone does NOT give you "among the panels", and the first version of
this placement proved it.** Sampling the hull uniformly and rejecting on clearance
put **1 of 8** machines with panels on all four sides, and one with no panel
within 800 m — all of them legally inside the footprint, all of them reading as a
wind farm parked *beside* a solar farm. The cause is that a plot's hull is mostly
void and a 980 x 560 m wake ellipse lets darts survive best in the biggest holes.
The fix is `interior_candidates`, which enumerates the buildable lattice under a
clearance rule AND an *enclosure* rule (panels in >=3 of 4 quadrants within 4D),
then samples that. Same plot, after: **7 of 7 among the blocks**, nearest table
86-476 m. Enumerating the land also makes "is there room?" answerable directly
(971 candidate positions) instead of only by failing to place anything.

⚠ Placement remains `INFERRED` (`NFR-07`). Khavda is a real hybrid wind+solar
park, but the vendor DWG for BLOCK-02 carries DC block hardware only — it says
nothing about turbines. A more *plausible* scatter is not more *surveyed*, and
this module must never be cited as evidence of where Adani's turbines are.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Rotor-diameter multiples for turbine-to-turbine spacing. Mid-range of normal
#: utility practice; both are configurable per site.
DOWNWIND_SPACING_D = 7.0
CROSSWIND_SPACING_D = 4.0

#: Keep turbines this many rotor diameters clear of the panel field. A turbine
#: inside or hard against the array throws blade shadows across modules, and any
#: KPI-03 false-fault number measured against that is an artefact of OUR
#: placement rather than a property of the plant (Session 10d).
ARRAY_SETBACK_D = 1.5

#: The two ways a hybrid park can be laid out, and they are genuinely different
#: plants rather than two renderings of one.
#:
#: `perimeter` rings the machines around the panel field (`ARRAY_SETBACK_D`), which
#: is what this module did originally and what every recorded KPI was measured on.
#: `interspersed` stands them *within* the footprint, in the clearings between DC
#: blocks — which is how a real co-located wind+solar park like Khavda is built,
#: because the land is shared rather than adjacent.
PLACEMENT_PERIMETER = "perimeter"
PLACEMENT_INTERSPERSED = "interspersed"

#: Minimum clear distance from a turbine's tower axis to any table, in rotor
#: diameters, when placing INSIDE the array. 0.5D is not a style choice: it is the
#: blade tip. A rotor of diameter D sweeps a circle of radius D/2 about the tower,
#: so anything closer than 0.5D has blades passing directly over modules. The
#: default adds margin on top of that.
BLADE_TIP_CLEARANCE_D = 0.5
TABLE_CLEARANCE_D = 0.6

#: How far out an interspersed turbine looks for panels, and in how many of the
#: four quadrants around it they must appear.
#:
#: This exists because clearance alone does not mean what it looks like it means.
#: "Inside the array" is a statement about the bounding box, and a real plot's
#: bounding box is mostly air — the S05b hull is 4,841 x 1,975 m holding 24
#: blocks. Sampling it uniformly under a 980 x 560 m wake ellipse drifts straight
#: into the biggest voids, because that is where darts survive: measured
#: 2026-07-30, plain uniform sampling put **1 of 8** machines with panels on all
#: four sides, and one with no panel within 800 m. Every one passed the clearance
#: rule. They were legally inside the footprint and read as a wind farm parked
#: next to a solar farm, which is the arrangement this placement exists to avoid.
ENCLOSURE_RADIUS_D = 4.0
MIN_OCCUPIED_QUADRANTS = 3

#: Lattice pitch the buildable interior is enumerated on. Shared by
#: `interior_candidates` and the jitter that de-quantises its output, because a
#: jitter of anything other than half this pitch either leaves visible grid
#: banding or pushes points outside the cell that was actually tested.
_CANDIDATE_STEP_M = 25.0


def rpm_to_deg_per_s(rpm: float) -> float:
    """Rotor rpm -> degrees per second. One revolution is 360 deg per 60 s.

    The single conversion for rotor speed, because there are now two consumers
    that must agree: `farm_builder._articulate_turbine` feeds it to a USD angular
    drive's `targetVelocity` (deg/s), and `sim_runtime`'s kinematic spin loop needs
    deg per *update*, which is this divided by the update rate. Those had drifted
    apart as two literals — a hub that visually spins at one rate and is driven at
    another is the kind of thing nobody notices until a blade-shadow KPI does.
    """
    return float(rpm) * 6.0


@dataclass(frozen=True)
class TurbineSite:
    """One sited machine, in stage-local metres."""

    x: float
    y: float
    hub_height_m: float
    rotor_diameter_m: float
    rpm: float

    @property
    def blade_len_m(self) -> float:
        return self.rotor_diameter_m / 2.0

    @property
    def tip_height_m(self) -> float:
        return self.hub_height_m + self.blade_len_m

    def to_cfg(self) -> dict:
        """The shape `farm_builder`/`keepout` already consume, so scattering
        turbines needs no change in either: `build_keepouts` reads the same
        `turbines:` list and therefore recomputes the no-fly volumes from these
        positions automatically."""
        return {
            "pos": [round(self.x, 3), round(self.y, 3)],
            "hub_height": self.hub_height_m,
            "blade_len": round(self.blade_len_m, 3),
            "rpm": round(self.rpm, 2),
        }


def min_spacing_ellipse(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    wind_dir_deg: float,
    downwind_m: float,
    crosswind_m: float,
) -> bool:
    """True when `b` is far enough from `a` under the anisotropic wake rule.

    `wind_dir_deg` is the compass bearing the wind blows FROM (met convention:
    270 = a westerly). The separation vector is rotated into wind coordinates and
    normalised by each axis, so the test is "outside the ellipse", i.e.
    `(along/downwind)^2 + (across/crosswind)^2 >= 1`.
    """
    # Unit vector pointing downwind: wind FROM 270 travels towards the east.
    theta = math.radians(wind_dir_deg)
    # Bearing -> (x=east, y=north): a bearing b points at (sin b, cos b). The
    # downwind direction is the reciprocal of where the wind comes from.
    dwx, dwy = -math.sin(theta), -math.cos(theta)
    dx, dy = bx - ax, by - ay
    along = dx * dwx + dy * dwy
    across = -dx * dwy + dy * dwx  # perpendicular, right-hand
    return (along / downwind_m) ** 2 + (across / crosswind_m) ** 2 >= 1.0


def buildable_ring(
    extent: tuple[float, float, float, float],
    setback_m: float,
    depth_m: float,
) -> list[tuple[float, float, float, float]]:
    """The zone turbines may stand in: outside the panel field by `setback_m`,
    and no further out than `depth_m` beyond that.

    Returned as four axis-aligned rectangles (west, east, south, north) rather
    than one polygon with a hole, because rejection sampling wants to draw
    uniformly from the allowed area and a rectangle list makes that exact —
    sampling the outer box and rejecting the middle biases nothing but wastes
    most darts once the array is large.
    """
    min_x, min_y, max_x, max_y = extent
    x0, y0 = min_x - setback_m, min_y - setback_m
    x1, y1 = max_x + setback_m, max_y + setback_m
    return [
        (x0 - depth_m, y0 - depth_m, x0, y1 + depth_m),  # west
        (x1, y0 - depth_m, x1 + depth_m, y1 + depth_m),  # east
        (x0, y0 - depth_m, x1, y0),                      # south
        (x0, y1, x1, y1 + depth_m),                      # north
    ]


class _RectIndex:
    """Point-in-any-rectangle over many rectangles, via a uniform grid.

    Exists purely for scale. The interspersed placement asks "is this dart clear
    of every table?" up to `max_darts` times, and the full S05b plot has 6,213
    tables — a linear scan is 124M rectangle tests and turns a build step into a
    coffee break. Each rectangle is registered in every grid cell it touches, so a
    query only tests the handful sharing the dart's cell.
    """

    def __init__(self, rects: list[tuple[float, float, float, float]], cell_m: float):
        self.cell = max(float(cell_m), 1e-6)
        self.buckets: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        for r in rects:
            x0, y0, x1, y1 = r
            for i in range(int(math.floor(x0 / self.cell)), int(math.floor(x1 / self.cell)) + 1):
                for j in range(int(math.floor(y0 / self.cell)), int(math.floor(y1 / self.cell)) + 1):
                    self.buckets.setdefault((i, j), []).append(r)

    def hits(self, x: float, y: float) -> bool:
        cell = self.buckets.get(
            (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))
        )
        if not cell:
            return False
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in cell)


def table_blocker(
    footprints: list[tuple[float, float, float, float]], clearance_m: float
):
    """A `blocked(x, y)` predicate: True where a turbine would stand too close to
    a table.

    Each table rectangle is inflated by `clearance_m` and the test is "inside any
    inflated rectangle". That is a rectangular clearance envelope rather than a
    Euclidean one, which is **conservative at the corners** (a dart diagonally off
    a table corner is rejected out to `clearance_m * sqrt(2)`). Erring toward more
    clearance is the right way to err when the quantity being cleared is a
    70 m blade sweeping over glass.
    """
    inflated = [
        (x0 - clearance_m, y0 - clearance_m, x1 + clearance_m, y1 + clearance_m)
        for x0, y0, x1, y1 in footprints
    ]
    index = _RectIndex(inflated, cell_m=max(clearance_m, 1.0))
    return index.hits


class _Occupancy:
    """Which coarse cells hold panels, with an integral image over them.

    The enclosure test asks "are there panels in this quadrant?" a few times per
    candidate point, over thousands of candidates. Scanning the table list for
    each would be O(candidates x tables); a prefix sum answers any axis-aligned
    rectangle in four lookups regardless of how many tables are in it.
    """

    def __init__(self, extent, footprints, step_m: float):
        self.step = max(float(step_m), 1e-6)
        self.min_x, self.min_y, max_x, max_y = extent
        self.nx = max(1, int(math.ceil((max_x - self.min_x) / self.step)))
        self.ny = max(1, int(math.ceil((max_y - self.min_y) / self.step)))
        grid = [[0] * (self.nx + 1) for _ in range(self.ny + 1)]
        for x0, y0, x1, y1 in footprints:
            i0 = max(0, min(self.nx - 1, int((x0 - self.min_x) / self.step)))
            i1 = max(0, min(self.nx - 1, int((x1 - self.min_x) / self.step)))
            j0 = max(0, min(self.ny - 1, int((y0 - self.min_y) / self.step)))
            j1 = max(0, min(self.ny - 1, int((y1 - self.min_y) / self.step)))
            for j in range(j0, j1 + 1):
                row = grid[j]
                for i in range(i0, i1 + 1):
                    row[i] = 1
        # Integral image: sums[j][i] = count over cells strictly left of i, below j.
        self.sums = [[0] * (self.nx + 1) for _ in range(self.ny + 1)]
        for j in range(self.ny):
            run = 0
            for i in range(self.nx):
                run += grid[j][i]
                self.sums[j + 1][i + 1] = self.sums[j][i + 1] + run

    def _count(self, i0: int, j0: int, i1: int, j1: int) -> int:
        i0 = max(0, min(self.nx, i0)); i1 = max(0, min(self.nx, i1))
        j0 = max(0, min(self.ny, j0)); j1 = max(0, min(self.ny, j1))
        if i1 <= i0 or j1 <= j0:
            return 0
        s = self.sums
        return s[j1][i1] - s[j0][i1] - s[j1][i0] + s[j0][i0]

    def any_in(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        return self._count(
            int(math.floor((x0 - self.min_x) / self.step)),
            int(math.floor((y0 - self.min_y) / self.step)),
            int(math.ceil((x1 - self.min_x) / self.step)),
            int(math.ceil((y1 - self.min_y) / self.step)),
        ) > 0

    def occupied_quadrants(self, x: float, y: float, radius_m: float) -> int:
        """How many of the four quadrants around `(x, y)` contain panels."""
        return sum(
            self.any_in(*q)
            for q in (
                (x, y, x + radius_m, y + radius_m),
                (x - radius_m, y, x, y + radius_m),
                (x - radius_m, y - radius_m, x, y),
                (x, y - radius_m, x + radius_m, y),
            )
        )


def interior_candidates(
    extent: tuple[float, float, float, float],
    footprints: list[tuple[float, float, float, float]],
    clearance_m: float,
    enclosure_radius_m: float,
    min_quadrants: int = MIN_OCCUPIED_QUADRANTS,
    step_m: float = _CANDIDATE_STEP_M,
) -> list[tuple[float, float]]:
    """Every point on a `step_m` lattice that a turbine may legally stand on:
    clear of the panels, and *surrounded* by them.

    Enumerating the buildable land once is both faster and more honest than
    rejection-sampling it. Faster because the clearance and enclosure tests run
    per lattice node instead of per dart; more honest because `len()` of the
    result is the direct answer to "does this layout have room for turbines among
    its blocks?" — a question the old dart loop could only answer by failing.
    """
    if not footprints:
        return []
    blocked = table_blocker(footprints, clearance_m)
    occ = _Occupancy(extent, footprints, step_m=max(step_m, 10.0))
    min_x, min_y, max_x, max_y = extent
    out = []
    nx = int((max_x - min_x) / step_m)
    ny = int((max_y - min_y) / step_m)
    for i in range(nx + 1):
        x = min_x + i * step_m
        for j in range(ny + 1):
            y = min_y + j * step_m
            if blocked(x, y):
                continue
            if occ.occupied_quadrants(x, y, enclosure_radius_m) < min_quadrants:
                continue
            out.append((x, y))
    return out


def largest_interior_clearance(
    extent: tuple[float, float, float, float],
    footprints: list[tuple[float, float, float, float]],
    step_m: float = 10.0,
) -> float:
    """The biggest clear-radius a turbine could find inside the array, in metres.

    A diagnostic, not part of the build: when `interspersed` sites fewer machines
    than asked, this answers the only useful follow-up question — *how much room
    is actually in there?* — with a number instead of a shrug. Sampled on a grid
    of `step_m`, so it is a lower bound; the true clearing is at most `step_m`
    larger.

    ⚠ **The result is capped at the largest radius on the ladder below.** A plot
    with genuinely open land returns that cap, not its true clearing — read it as
    "at least this much". The cap is deliberate: this is called to explain a
    shortfall, where the interesting numbers are the small ones.
    """
    if not footprints:
        return math.inf
    min_x, min_y, max_x, max_y = extent
    best = 0.0
    # Bisecting per point would be exact but slow; testing a decreasing ladder of
    # radii against the same index is enough to report a scale.
    nx = max(1, int((max_x - min_x) / step_m))
    ny = max(1, int((max_y - min_y) / step_m))
    radii = [500.0, 350.0, 250.0, 200.0, 140.0, 100.0, 70.0, 50.0, 35.0, 25.0, 15.0, 10.0, 5.0]
    for r in radii:
        if r <= best:
            break
        blocked = table_blocker(footprints, r)
        for i in range(nx + 1):
            x = min_x + i * step_m
            for j in range(ny + 1):
                y = min_y + j * step_m
                if not blocked(x, y):
                    best = r
                    break
            if best >= r:
                break
        if best >= r:
            break
    return best


def scatter_turbines(
    extent: tuple[float, float, float, float],
    n: int,
    seed: int,
    hub_height_m: float = 120.0,
    rotor_diameter_m: float = 140.0,
    wind_dir_deg: float = 250.0,
    downwind_spacing_d: float = DOWNWIND_SPACING_D,
    crosswind_spacing_d: float = CROSSWIND_SPACING_D,
    array_setback_d: float = ARRAY_SETBACK_D,
    ring_depth_d: float = 6.0,
    rpm_range: tuple[float, float] = (10.0, 13.0),
    max_darts: int = 20000,
    placement: str = PLACEMENT_PERIMETER,
    footprints: list[tuple[float, float, float, float]] | None = None,
    table_clearance_d: float = TABLE_CLEARANCE_D,
    enclosure_radius_d: float = ENCLOSURE_RADIUS_D,
    min_occupied_quadrants: int = MIN_OCCUPIED_QUADRANTS,
    log=None,
) -> list[TurbineSite]:
    """Site `n` turbines by wake-constrained dart-throwing — around the array
    (`perimeter`) or among the DC blocks inside it (`interspersed`).

    Returns as many as the spacing rule permits — **and logs the shortfall**. A
    site that physically cannot hold the requested count must say so rather than
    quietly returning fewer, which would read as "this is what the wind farm
    looks like" (`NFR-07`, no silent caps).

    `wind_dir_deg` default 250 (WSW) is the Rann of Kutch's summer monsoon
    regime, which is the season that drives Khavda's wind resource. ⚠ It is a
    climatological direction, not a met-mast measurement for this plot.

    ⚠ **`interspersed` puts blade shadows on modules, deliberately.** That is what
    `ARRAY_SETBACK_D` was introduced to prevent, so the trade is explicit: this
    layout is truer to a co-located park and it makes any turbine-shadow KPI
    (notably KPI-03's false-fault rate) a property of *this* placement. Numbers
    measured on a `perimeter` stage do not carry over — re-measure, don't reuse.

    The clearance rule is a hard physical one, not a preference: `table_clearance_d`
    is validated against `BLADE_TIP_CLEARANCE_D`, because below half a rotor
    diameter the blades sweep over the panels themselves. The *enclosure* rule
    (`enclosure_radius_d`, `min_occupied_quadrants`) is what makes the placement
    mean what its name says — see `ENCLOSURE_RADIUS_D`.
    """
    import random

    rng = random.Random(seed)
    downwind = downwind_spacing_d * rotor_diameter_m
    crosswind = crosswind_spacing_d * rotor_diameter_m
    candidates: list[tuple[float, float]] | None = None
    blocked = None
    if placement == PLACEMENT_INTERSPERSED:
        if table_clearance_d < BLADE_TIP_CLEARANCE_D:
            raise ValueError(
                f"table_clearance_d={table_clearance_d} is inside the rotor sweep "
                f"({BLADE_TIP_CLEARANCE_D}D = the blade tip): the blades would pass "
                f"over the modules. Raise it, or use placement={PLACEMENT_PERIMETER!r}."
            )
        if not footprints:
            if log:
                log(
                    f"  [warn] placement={PLACEMENT_INTERSPERSED} needs table "
                    f"footprints; falling back to {PLACEMENT_PERIMETER}"
                )
            placement = PLACEMENT_PERIMETER
        else:
            # Enumerate the buildable land instead of sampling the hull and hoping.
            # A plot's hull is mostly void, and darts survive best in the void.
            blocked = table_blocker(footprints, table_clearance_d * rotor_diameter_m)
            candidates = interior_candidates(
                extent,
                footprints,
                clearance_m=table_clearance_d * rotor_diameter_m,
                enclosure_radius_m=enclosure_radius_d * rotor_diameter_m,
                min_quadrants=min_occupied_quadrants,
            )
            if not candidates:
                if log is not None:
                    clear = largest_interior_clearance(extent, footprints)
                    need = table_clearance_d * rotor_diameter_m
                    log(
                        f"  [warn] sited 0/{n} turbines — this layout has no room "
                        f"BETWEEN its blocks. A machine needs {need:.0f} m clear of "
                        f"any table ({table_clearance_d:.2f}D) with panels on "
                        f"{min_occupied_quadrants} of 4 sides within "
                        f"{enclosure_radius_d * rotor_diameter_m:.0f} m; the largest "
                        f"clearing here is about {clear:.0f} m. The vendor CAD's "
                        f"table positions are fixed, so the options are a bigger plot "
                        f"with real inter-block corridors, a smaller rotor, or "
                        f"placement={PLACEMENT_PERIMETER!r}."
                    )
                return []
    if candidates is None:
        zones = buildable_ring(
            extent, array_setback_d * rotor_diameter_m, ring_depth_d * rotor_diameter_m
        )
        # Draw each dart from a zone chosen in proportion to its area, so the field
        # is uniform over the buildable land rather than over the zone list.
        areas = [max(0.0, (x1 - x0)) * max(0.0, (y1 - y0)) for x0, y0, x1, y1 in zones]
        total = sum(areas)
        if total <= 0.0:
            return []

        def dart() -> tuple[float, float]:
            t = rng.random() * total
            for (x0, y0, x1, y1), a in zip(zones, areas):
                if t < a:
                    return rng.uniform(x0, x1), rng.uniform(y0, y1)
                t -= a
            x0, y0, x1, y1 = zones[-1]
            return rng.uniform(x0, x1), rng.uniform(y0, y1)
    else:
        # Draw from the enumerated buildable lattice, jittered inside its own cell
        # so the field is not quantised onto a 25 m grid — which would hand
        # `lattice_score` the very regularity this module exists to avoid.
        jitter = _CANDIDATE_STEP_M / 2.0

        def dart() -> tuple[float, float]:
            x, y = candidates[rng.randrange(len(candidates))]
            return x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter)

    placed: list[TurbineSite] = []
    darts = 0
    while len(placed) < n and darts < max_darts:
        darts += 1
        x, y = dart()
        if blocked is not None and blocked(x, y):
            continue
        if any(
            not min_spacing_ellipse(p.x, p.y, x, y, wind_dir_deg, downwind, crosswind)
            for p in placed
        ):
            continue
        placed.append(
            TurbineSite(
                x=x,
                y=y,
                hub_height_m=hub_height_m,
                rotor_diameter_m=rotor_diameter_m,
                rpm=rng.uniform(*rpm_range),
            )
        )

    if len(placed) < n and log is not None:
        if candidates is not None:
            log(
                f"  [warn] sited {len(placed)}/{n} turbines in {darts} darts — the "
                f"land between the blocks is buildable ({len(candidates)} candidate "
                f"positions) but the WAKE rule will not fit more into it at "
                f"{downwind_spacing_d:.0f}D x {crosswind_spacing_d:.0f}D "
                f"({downwind:.0f} x {crosswind:.0f} m). Lower the spacing, or accept "
                f"that this plot holds {len(placed)}."
            )
        else:
            log(
                f"  [warn] sited {len(placed)}/{n} turbines in {darts} darts — the "
                f"buildable ring cannot hold more at {downwind_spacing_d:.0f}D x "
                f"{crosswind_spacing_d:.0f}D spacing ({downwind:.0f} x {crosswind:.0f} m). "
                f"Widen site.turbine_ring_depth_d or lower the spacing to fit them."
            )
    return placed


def lattice_score(sites: list[TurbineSite]) -> float:
    """How grid-like a field is, in [0, 1] — 1.0 means a perfect lattice.

    Exists to make "not a grid" testable instead of a matter of opinion. A
    lattice repeats a small number of coordinate values, so the score is the
    fraction of turbines that share an x or a y with another turbine (within a
    rotor radius). The old hand-written field scores 1.0: every one of its five
    machines shares an easting with another.
    """
    if len(sites) < 2:
        return 0.0
    shared = 0
    for i, a in enumerate(sites):
        tol = a.rotor_diameter_m / 2.0
        for j, b in enumerate(sites):
            if i == j:
                continue
            if abs(a.x - b.x) < tol or abs(a.y - b.y) < tol:
                shared += 1
                break
    return shared / len(sites)


def resolve_turbines(farm_cfg: dict, layout, log=None) -> list[dict]:
    """The turbine list to build: an explicit `turbines:` list, or a scatter.

    Isaac-free on purpose, and exported rather than inlined, because
    `keepout.build_keepouts` has to agree with the geometry exactly — it reads the
    same list shape, so a scattered field gets its no-fly volumes recomputed with
    no change in that module. If this returned something `keepout` could not read,
    the visible spheres and the enforced volumes would drift apart.

    `turbine_scatter.enabled` replaces the hand-written positions, which were two
    columns at fixed eastings — a lattice, which is not how wind farms are sited
    (see `world/siting.py`).

    ⚠ **Precedence, stated exactly, because the obvious reading is wrong:**
    `turbine_scatter.enabled` is checked FIRST and wins. An explicit list does *not*
    override it — it only takes effect when the scatter is off (or when there is no
    site layout to scatter within). In particular **`turbines: []` does not mean
    "no turbines"** while the scatter is enabled; it means "no explicit positions",
    and the scatter then supplies its own. Measured 2026-07-29: a scenario setting
    `farm_overrides.turbines: []` and documenting itself as turbine-free built four
    of them. To get a genuinely turbine-free stage, set
    `turbine_scatter.enabled: false`.
    """
    explicit = farm_cfg.get("turbines", []) or []
    cfg = (farm_cfg.get("turbine_scatter") or {}) if isinstance(farm_cfg, dict) else {}
    if not cfg.get("enabled"):
        return list(explicit)
    if explicit and log:
        log(
            f"  [warn] turbine_scatter.enabled overrides {len(explicit)} explicit "
            f"turbine position(s) in the config"
        )

    from solar_twin.world.site import table_extent, table_footprints

    if layout is None or getattr(layout, "site", None) is None:
        if log:
            log("  [warn] turbine_scatter needs a site layout; keeping explicit turbines")
        return list(explicit)

    placement = str(cfg.get("placement", PLACEMENT_PERIMETER))
    if placement not in (PLACEMENT_PERIMETER, PLACEMENT_INTERSPERSED):
        raise ValueError(
            f"turbine_scatter.placement={placement!r} is not one of "
            f"{PLACEMENT_PERIMETER!r} / {PLACEMENT_INTERSPERSED!r}"
        )
    # Only paid for when it is used: the interspersed rule is the one that needs
    # per-table geometry, and building it is O(tables) on a 6,213-table plot.
    footprints = (
        table_footprints(layout.site) if placement == PLACEMENT_INTERSPERSED else None
    )

    sites = scatter_turbines(
        table_extent(layout.site),
        n=int(cfg.get("count", 5)),
        # Falls back to the FARM seed, so the field moves only when the farm does.
        seed=int(cfg.get("seed", farm_cfg.get("seed", 0))),
        hub_height_m=float(cfg.get("hub_height", 120.0)),
        rotor_diameter_m=float(cfg.get("rotor_diameter", 140.0)),
        wind_dir_deg=float(cfg.get("wind_dir_deg", 250.0)),
        downwind_spacing_d=float(cfg.get("downwind_spacing_d", 7.0)),
        crosswind_spacing_d=float(cfg.get("crosswind_spacing_d", 4.0)),
        array_setback_d=float(cfg.get("array_setback_d", 1.5)),
        ring_depth_d=float(cfg.get("ring_depth_d", 6.0)),
        placement=placement,
        footprints=footprints,
        table_clearance_d=float(cfg.get("table_clearance_d", TABLE_CLEARANCE_D)),
        log=log,
    )
    if log:
        where = (
            "among the DC blocks (blades over open ground, not modules)"
            if placement == PLACEMENT_INTERSPERSED
            else "ringing the array"
        )
        log(
            f"  turbines: scattered {len(sites)} {where} at "
            f"{cfg.get('downwind_spacing_d', 7.0)}D x {cfg.get('crosswind_spacing_d', 4.0)}D "
            f"(wind from {cfg.get('wind_dir_deg', 250.0)} deg), "
            f"lattice_score={lattice_score(sites):.2f} (1.00 = a grid) — INFERRED placement"
        )
    return [s.to_cfg() for s in sites]
