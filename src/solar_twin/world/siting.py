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
    log=None,
) -> list[TurbineSite]:
    """Site `n` turbines around the array by wake-constrained dart-throwing.

    Returns as many as the spacing rule permits — **and logs the shortfall**. A
    site that physically cannot hold the requested count must say so rather than
    quietly returning fewer, which would read as "this is what the wind farm
    looks like" (`NFR-07`, no silent caps).

    `wind_dir_deg` default 250 (WSW) is the Rann of Kutch's summer monsoon
    regime, which is the season that drives Khavda's wind resource. ⚠ It is a
    climatological direction, not a met-mast measurement for this plot.
    """
    import random

    rng = random.Random(seed)
    downwind = downwind_spacing_d * rotor_diameter_m
    crosswind = crosswind_spacing_d * rotor_diameter_m
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

    placed: list[TurbineSite] = []
    darts = 0
    while len(placed) < n and darts < max_darts:
        darts += 1
        x, y = dart()
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
    (see `world/siting.py`). An explicit list still wins when present, so existing
    configs and any KPI run pinned to known turbine positions are unaffected.
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

    from solar_twin.world.site import table_extent

    if layout is None or getattr(layout, "site", None) is None:
        if log:
            log("  [warn] turbine_scatter needs a site layout; keeping explicit turbines")
        return list(explicit)

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
        log=log,
    )
    if log:
        log(
            f"  turbines: scattered {len(sites)} at "
            f"{cfg.get('downwind_spacing_d', 7.0)}D x {cfg.get('crosswind_spacing_d', 4.0)}D "
            f"(wind from {cfg.get('wind_dir_deg', 250.0)} deg), "
            f"lattice_score={lattice_score(sites):.2f} (1.00 = a grid) — INFERRED placement"
        )
    return [s.to_cfg() for s in sites]
