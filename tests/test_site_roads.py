"""Road network: derived cross corridors, access spurs, terrain-following
subdivision (pure, no Isaac).

`tests/test_site.py` covers the pre-existing derived/perimeter roads, fence and
inverter pads. This file covers what the road network gained.
"""

from dataclasses import dataclass

import pytest

from solar_twin.world.site import (
    DERIVED,
    INFERRED,
    Pad,
    RoadStrip,
    access_spurs,
    derived_ew_roads,
    subdivide_strip,
)


@dataclass
class _Table:
    easting: float
    northing: float
    length_m: float


@dataclass
class _Site:
    tables: list
    origin_easting: float = 0.0
    origin_northing: float = 0.0
    module_length_m: float = 2.278
    n_modules: int = 0


def _site(rows):
    """rows: (easting, northing, length) in stage-local metres."""
    return _Site(tables=[_Table(e, n, ln) for e, n, ln in rows])


# ------------------------------------------------------- derived E-W corridors
def test_a_clear_band_between_table_rows_is_an_east_west_road():
    """Tables run north from their insert point; where one band ends and the next
    begins with room for a truck, the CAD has left a cross corridor."""
    # Site kept realistically WIDE (300 m): `RoadStrip.width_m` is the min of both
    # spans, so on a toy 10 m-wide site it would report the site's width rather
    # than the corridor's — a fixture artefact, not a code one.
    site = _site([(0.0, 0.0, 100.0), (300.0, 0.0, 100.0), (0.0, 120.0, 100.0), (300.0, 120.0, 100.0)])
    roads = derived_ew_roads(site, min_width_m=8.0)
    assert len(roads) == 1
    r = roads[0]
    assert r.provenance == DERIVED
    assert (r.y0, r.y1) == pytest.approx((100.0, 120.0))
    assert r.width_m == pytest.approx(20.0)


def test_an_ordinary_gap_is_not_a_road():
    site = _site([(0.0, 0.0, 100.0), (0.0, 103.0, 100.0)])
    assert derived_ew_roads(site, min_width_m=8.0) == []


def test_overlapping_table_bands_are_merged_before_looking_for_gaps():
    """Rows of different lengths overlap; without merging, a long table beside a
    short one would fake a corridor that the long table actually occupies."""
    site = _site([(0.0, 0.0, 200.0), (10.0, 0.0, 60.0), (10.0, 80.0, 60.0)])
    assert derived_ew_roads(site, min_width_m=8.0) == []


def test_ew_roads_span_the_hardware_width():
    site = _site([(0.0, 0.0, 50.0), (300.0, 0.0, 50.0), (0.0, 70.0, 50.0), (300.0, 70.0, 50.0)])
    r = derived_ew_roads(site, min_width_m=8.0)[0]
    # Includes the module chord overhang either side, like `table_extent` does.
    assert r.x0 < 0.0 and r.x1 > 300.0


def test_no_tables_is_no_roads_not_a_crash():
    assert derived_ew_roads(_site([])) == []


# ---------------------------------------------------------------- access spurs
def test_each_pad_gets_a_spur_to_the_nearest_road():
    """Without these the inverter stations sit in the array with no way in, which
    is the giveaway that a site model is decoration rather than a plant."""
    road = RoadStrip(100.0, 0.0, 111.0, 600.0, DERIVED, "road_ns_0")
    pads = [Pad(60.0, 100.0, 12.0, 7.0, INFERRED, "inverter_00")]
    spurs = access_spurs(pads, [road], width_m=5.0)
    assert len(spurs) == 1
    s = spurs[0]
    assert s.provenance == INFERRED           # ours, not the drawing's
    assert s.x0 == pytest.approx(60.0)        # starts at the pad
    assert s.x1 == pytest.approx(100.0)       # ends at the road edge
    assert (s.y1 - s.y0) == pytest.approx(5.0)


def test_a_spur_reaches_the_road_from_either_side():
    road = RoadStrip(100.0, 0.0, 111.0, 600.0, DERIVED, "r")
    east = access_spurs([Pad(160.0, 50.0, 12.0, 7.0, INFERRED, "p")], [road])[0]
    assert east.x0 == pytest.approx(111.0) and east.x1 == pytest.approx(160.0)


def test_a_pad_already_on_the_road_needs_no_spur():
    road = RoadStrip(100.0, 0.0, 111.0, 600.0, DERIVED, "r")
    assert access_spurs([Pad(105.0, 300.0, 12.0, 7.0, INFERRED, "p")], [road]) == []


def test_spurs_pick_the_closest_of_several_roads():
    near = RoadStrip(70.0, 0.0, 76.0, 600.0, INFERRED, "near")
    far = RoadStrip(400.0, 0.0, 406.0, 600.0, INFERRED, "far")
    s = access_spurs([Pad(60.0, 10.0, 12.0, 7.0, INFERRED, "p")], [far, near])[0]
    assert s.x1 == pytest.approx(70.0)


def test_no_roads_means_no_spurs():
    assert access_spurs([Pad(1.0, 1.0, 2.0, 2.0, INFERRED, "p")], []) == []


# --------------------------------------------------- terrain-following subdivision
def test_a_long_road_is_split_into_segments():
    """A road used to be ONE flat quad at the height of its own centre. On 2.2 m
    of real relief that hangs the ends off the grade; segments re-sample."""
    strip = RoadStrip(0.0, 0.0, 8.0, 647.0, INFERRED, "road_perimeter_w")
    segs = subdivide_strip(strip, max_seg_m=25.0)
    assert len(segs) == 26                      # ceil(647/25)
    assert segs[0].y0 == pytest.approx(0.0)
    assert segs[-1].y1 == pytest.approx(647.0)  # covers the strip exactly
    # Contiguous, no gaps or overlaps.
    for a, b in zip(segs, segs[1:]):
        assert a.y1 == pytest.approx(b.y0)
    # Width and provenance survive the split.
    assert all(s.x0 == 0.0 and s.x1 == 8.0 for s in segs)
    assert all(s.provenance == INFERRED for s in segs)


def test_subdivision_follows_the_long_axis_whichever_it_is():
    ew = subdivide_strip(RoadStrip(0.0, 0.0, 321.0, 8.0, DERIVED, "road_ew_0"), max_seg_m=25.0)
    assert len(ew) == 13
    for a, b in zip(ew, ew[1:]):
        assert a.x1 == pytest.approx(b.x0)
    assert all(s.y0 == 0.0 and s.y1 == 8.0 for s in ew)


def test_a_short_strip_is_left_alone():
    """No point paying prims for a spur shorter than one segment."""
    strip = RoadStrip(0.0, 0.0, 5.0, 20.0, INFERRED, "road_spur_00")
    assert subdivide_strip(strip, max_seg_m=25.0) == [strip]


def test_segment_names_are_unique_so_prim_paths_do_not_collide():
    segs = subdivide_strip(RoadStrip(0.0, 0.0, 8.0, 647.0, INFERRED, "r"), max_seg_m=25.0)
    assert len({s.name for s in segs}) == len(segs)
