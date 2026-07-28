"""Balance-of-plant geometry (pure, no Isaac). Anchored on the real Khavda CAD."""

import math
from pathlib import Path

import pytest

from solar_twin.world.layout_import import load_site
from solar_twin.world.site import (
    DERIVED,
    INFERRED,
    derived_roads,
    fence_posts,
    inverter_pads,
    perimeter_road,
    provenance_summary,
    table_extent,
)

SITE_PATH = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"


@pytest.fixture(scope="module")
def site():
    if not SITE_PATH.exists():  # pragma: no cover — the file is committed
        pytest.skip("site file missing")
    return load_site(str(SITE_PATH))


def test_table_extent_includes_the_length_each_table_runs_north(site):
    """A table's stored position is its SOUTHERN insert; its modules run 128.58 m
    north of it. Using the insert points alone under-reports the block by a full
    table length, which is how the ground mesh once came out too small."""
    min_x, min_y, max_x, max_y = table_extent(site)
    assert max_x - min_x == pytest.approx(319.0 + 2.278, abs=0.1)   # + half a chord each side
    assert max_y - min_y == pytest.approx(646.9, abs=1.0)           # NOT the 518 m insert span


def test_the_one_real_internal_road_is_found_and_it_is_derived(site):
    """The CAD's aisles are 5-6 m with a single 11 m gap at x=143. That gap is a
    road the drawing contains — it must be found, not invented."""
    roads = derived_roads(site)
    assert len(roads) == 1, [r.name for r in roads]
    road = roads[0]
    assert road.provenance == DERIVED
    assert 143.0 < road.x0 < 155.0
    # Clear span = the 11 m centre spacing minus half a module chord either side.
    assert road.width_m == pytest.approx(11.0 - 2.278, abs=0.01)
    # It runs the full length of the block, so a vehicle can actually use it.
    assert road.y1 - road.y0 == pytest.approx(646.9, abs=1.0)


def test_ordinary_maintenance_aisles_are_not_promoted_to_roads(site):
    """A 5-6 m aisle between two tracker rows is not a road. If the threshold
    ever drops far enough to catch them, the site fills with 57 'roads'."""
    assert derived_roads(site, min_width_m=4.0).__len__() > 50   # the failure mode
    assert len(derived_roads(site)) == 1                          # the guard


def test_perimeter_road_encloses_the_hardware_and_is_flagged_inferred():
    extent = (0.0, 0.0, 100.0, 200.0)
    ring = perimeter_road(extent, width_m=8.0, offset_m=7.0)
    assert len(ring) == 4
    assert {r.provenance for r in ring} == {INFERRED}
    # Every strip lies strictly outside the hardware it rings.
    for r in ring:
        assert r.x0 <= -7.0 or r.x1 >= 107.0 or r.y0 <= -7.0 or r.y1 >= 207.0
    assert all(r.width_m == pytest.approx(8.0) for r in ring)


def test_inverter_count_follows_plant_capacity_not_a_magic_number(site):
    """30,016 modules x ~600 W = ~18 MWdc; ~4 MW per central station -> 4-5."""
    pads = inverter_pads(site, derived_roads(site))
    assert 4 <= len(pads) <= 5, len(pads)
    assert {p.provenance for p in pads} == {INFERRED}
    # A bigger station rating means fewer of them, monotonically.
    assert len(inverter_pads(site, derived_roads(site), mw_per_station=9.0)) == 2


def test_inverters_sit_on_the_internal_road_and_are_spread_along_it(site):
    roads = derived_roads(site)
    pads = inverter_pads(site, roads)
    road = roads[0]
    assert all(road.x0 <= p.x <= road.x1 for p in pads)
    ys = sorted(p.y for p in pads)
    assert ys == [p.y for p in pads]                       # already in order
    assert min(ys) > road.y0 and max(ys) < road.y1         # inset from both ends
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert max(gaps) - min(gaps) < 1e-6                    # evenly spaced


def test_no_inverters_without_a_road_to_put_them_on(site):
    assert inverter_pads(site, []) == []


def test_fence_walks_the_whole_perimeter_at_the_requested_spacing():
    posts = fence_posts((0.0, 0.0, 100.0, 200.0), offset_m=3.0, spacing_m=10.0)
    # Perimeter of the 106 x 206 ring is 624 m -> ~62 posts.
    assert 58 <= len(posts) <= 66, len(posts)
    assert len(set(posts)) == len(posts)                   # no duplicate corners
    xs = [p[0] for p in posts]
    ys = [p[1] for p in posts]
    assert min(xs) == pytest.approx(-3.0) and max(xs) == pytest.approx(103.0)
    assert min(ys) == pytest.approx(-3.0) and max(ys) == pytest.approx(203.0)
    # Consecutive posts never exceed the spacing (a gap would look like a hole).
    for (ax, ay), (bx, by) in zip(posts, posts[1:]):
        assert math.hypot(bx - ax, by - ay) <= 10.0 + 1e-6


def test_provenance_is_reportable(site):
    """The split between 'read from the drawing' and 'assumed by us' has to be
    countable, because the builder states it out loud on every build."""
    items = derived_roads(site) + perimeter_road(table_extent(site))
    assert provenance_summary(items) == {DERIVED: 1, INFERRED: 4}
