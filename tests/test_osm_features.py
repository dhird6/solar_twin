"""Real OSM feature geometry (pure, no Isaac, no pyproj, no network).

`world/osm_features.py` reads what `tools/osm_fetch.py` baked — already projected
into the site CRS and anchored to the site origin — so everything here is plain
metre geometry and runs off the Spark (`NFR-01`).
"""

import math
from pathlib import Path

import pytest

from solar_twin.world.osm_features import (
    LAYER_POWER,
    LAYER_ROADS,
    MAPPED,
    clip_to_radius,
    load_features,
    parse_features,
    resample,
    ribbon,
)

#: A hand-built bake: one straight road, one bent road, a 765 kV line, a boundary.
DOC = {
    "crs": "EPSG:32642",
    "origin": {"easting": 543161.072, "northing": 2664232.304},
    "source": "OpenStreetMap via the official API 0.6 /map call",
    "license": "ODbL 1.0 (c) OpenStreetMap contributors",
    "ways": [
        {
            "osm_id": 1,
            "layer": "roads",
            "kind": "track",
            "points": [[0.0, 0.0], [1000.0, 0.0]],
            "length_m": 1000.0,
            "width_m": 3.5,
            "width_source": "class_default",
            "surface": "unpaved",
        },
        {
            "osm_id": 2,
            "layer": "roads",
            "kind": "tertiary",
            "points": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]],
            "length_m": 200.0,
            "width_m": 6.5,
            "width_source": "class_default",
            "name": "Khavda-Dhordo Gorewali",
        },
        {
            "osm_id": 3,
            "layer": "power",
            "kind": "line",
            "points": [[0.0, 500.0], [2000.0, 500.0]],
            "length_m": 2000.0,
            "voltage": "765000",
        },
        {
            "osm_id": 4,
            "layer": "power",
            "kind": "plant",
            "points": [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0], [0.0, 0.0]],
            "length_m": 170.0,
            "closed": True,
            "name": "Khavda Renewable Energy Park",
            "operator": "Adani Green",
        },
        # Dropped: fewer than two usable points (a way clipped by the bbox).
        {"osm_id": 5, "layer": "roads", "kind": "track", "points": [[0.0, 0.0]]},
    ],
}


def test_parse_keeps_layers_and_drops_degenerate_ways():
    f = parse_features(DOC)
    assert len(f.ways) == 4, "a 1-point way is not geometry and must be dropped"
    assert len(f.by_layer(LAYER_ROADS)) == 2
    assert len(f.by_layer(LAYER_POWER)) == 2
    assert f.crs == "EPSG:32642"
    assert all(w.provenance == MAPPED for w in f.ways), (
        "OSM geometry is MAPPED — never derived (vendor CAD) or inferred (ours)"
    )


def test_road_width_records_whether_it_was_actually_mapped():
    """The centreline is real; the breadth is usually a class default. A render
    that cannot tell the two apart would overclaim the ingest (`NFR-07`)."""
    f = parse_features(DOC)
    for road in f.roads:
        assert road.width_m > 0.0
        assert road.width_source in ("class_default", "osm_tag")


def test_line_height_comes_from_the_voltage_class():
    f = parse_features(DOC)
    line = next(w for w in f.power if w.kind == "line")
    assert line.line_height_m == pytest.approx(45.0), "765 kV is a 45 m class"
    # An untagged voltage must not silently become a 765 kV tower.
    from solar_twin.world.osm_features import OsmWay

    bare = OsmWay(9, LAYER_POWER, "line", ((0, 0), (1, 0)), 1.0)
    assert bare.line_height_m == pytest.approx(14.0)
    assert OsmWay(9, LAYER_POWER, "line", ((0, 0), (1, 0)), 1.0,
                  voltage="220000").line_height_m == pytest.approx(28.0)


def test_clip_keeps_whole_ways_within_the_radius():
    """Whole ways are kept or dropped — splitting one leaves a road ending in
    mid-desert, which reads as broken geometry rather than as a clip."""
    f = parse_features(DOC)
    near = clip_to_radius(f, 0.0, 0.0, 100.0)
    ids = {w.osm_id for w in near.ways}
    assert 1 in ids and 2 in ids and 4 in ids   # all have a vertex at/near origin
    assert 3 not in ids                          # the HV line is 500 m north
    # A kept way keeps ALL its vertices, including ones outside the radius.
    road = next(w for w in near.ways if w.osm_id == 1)
    assert road.points[-1][0] == pytest.approx(1000.0)
    assert near.crs == f.crs and near.source == f.source


def test_resample_bounds_every_segment():
    """OSM digitises a straight desert track as two points kilometres apart; a
    drape samples terrain per vertex, so segments must be bounded first."""
    pts = resample([(0.0, 0.0), (1000.0, 0.0)], 40.0)
    assert len(pts) == 26
    assert max(math.dist(a, b) for a, b in zip(pts, pts[1:])) <= 40.0 + 1e-9
    assert pts[0] == (0.0, 0.0) and pts[-1] == (1000.0, 0.0), "endpoints must be exact"
    # A no-op step must not corrupt the line.
    assert resample([(0.0, 0.0), (5.0, 0.0)], 0.0) == [(0.0, 0.0), (5.0, 0.0)]


def test_ribbon_holds_its_width_along_a_straight_run():
    edges = ribbon([(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)], 6.0)
    assert len(edges) == 3
    for left, right in edges:
        assert math.dist(left, right) == pytest.approx(6.0)
        assert left[1] == pytest.approx(-3.0) and right[1] == pytest.approx(3.0)


def test_ribbon_does_not_pinch_on_a_bend():
    """Offsetting by a single segment normal narrows the ribbon through a corner;
    mitering the averaged normal is what keeps a road the same width all along."""
    edges = ribbon([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)], 8.0)
    widths = [math.dist(left, right) for left, right in edges]
    assert min(widths) >= 8.0 - 1e-6, f"ribbon pinched to {min(widths):.2f} m"
    assert max(widths) <= 8.0 / 0.35 + 1e-6, "miter is unbounded on a sharp corner"


def test_ribbon_survives_a_duplicated_vertex():
    """A zero-length segment has no normal. OSM ways really do contain repeated
    nodes, and a NaN vertex silently corrupts the whole mesh."""
    edges = ribbon([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0), (20.0, 0.0)], 4.0)
    assert len(edges) == 4
    for left, right in edges:
        assert all(math.isfinite(v) for v in (*left, *right))


def test_ribbon_and_resample_reject_nonsense_without_raising():
    assert ribbon([(0.0, 0.0)], 5.0) == []
    assert ribbon([(0.0, 0.0), (1.0, 0.0)], 0.0) == []


# --------------------------------------------------------------------------- #
# The real bake, if it is checked in.
# --------------------------------------------------------------------------- #
OSM_YAML = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_s05b_osm.yaml"


@pytest.mark.skipif(not OSM_YAML.exists(), reason="OSM bake not present")
def test_the_real_khavda_bake_carries_real_named_features():
    """Guards the ingest itself: this must be Khavda's actual mapped geography,
    with the same CRS and origin as the layout it registers against."""
    f = load_features(str(OSM_YAML))
    assert f.crs == "EPSG:32642"
    assert "OpenStreetMap" in f.source and "ODbL" in f.license

    from solar_twin.world.layout_import import load_site

    site = load_site(str(OSM_YAML.parent / "khavda_s05b_digest.yaml"))
    assert f.origin_easting == pytest.approx(site.origin_easting), (
        "OSM bake and CAD layout must share one anchor or they cannot compose"
    )
    assert f.origin_northing == pytest.approx(site.origin_northing)

    names = {w.name for w in f.ways if w.name}
    assert "Khavda Renewable Energy Park" in names, (
        f"expected the mapped park boundary; got {sorted(names)}"
    )
    assert any(w.kind == "line" and w.line_height_m >= 45.0 for w in f.power), (
        "expected the mapped 765 kV transmission line"
    )
    assert f.roads, "expected at least the mapped tracks"


# --------------------------------------------------------------------------- #
# Clipping to the ground mesh.
#
# `clip_to_radius` keeps whole ways so a road never ends in mid-desert — but that
# also drags all 108 km of a transmission line onto the stage from one nearby
# vertex. Measured on the S05b subset: an OSM layer spanning -23..+31 km east and
# -70 km north while the ground mesh reached 1.5 km, leaving 582 towers standing
# in the void against the sky dome. Clipping to the ground's extent puts the cut
# at the horizon, which is where a road should leave frame.
# --------------------------------------------------------------------------- #

from solar_twin.world.osm_features import clip_polyline_to_box, clip_to_box  # noqa: E402

BOX = (0.0, 0.0, 100.0, 100.0)


def test_clip_cuts_exactly_on_the_boundary():
    runs = clip_polyline_to_box([(-50.0, 50.0), (150.0, 50.0)], *BOX)
    assert len(runs) == 1
    assert runs[0][0] == pytest.approx((0.0, 50.0))
    assert runs[0][-1] == pytest.approx((100.0, 50.0))


def test_clip_keeps_a_fully_interior_line_untouched():
    line = [(10.0, 10.0), (50.0, 50.0), (90.0, 20.0)]
    runs = clip_polyline_to_box(line, *BOX)
    assert len(runs) == 1
    assert [tuple(p) for p in runs[0]] == [pytest.approx(p) for p in line]


def test_clip_drops_a_line_entirely_outside():
    assert clip_polyline_to_box([(200.0, 200.0), (300.0, 300.0)], *BOX) == []
    assert clip_polyline_to_box([(-10.0, 50.0), (-5.0, 50.0)], *BOX) == []


def test_clip_splits_a_line_that_leaves_and_re_enters():
    """A road that dips out of the box and back must become TWO runs, not one
    straight shortcut across the gap."""
    runs = clip_polyline_to_box(
        [(10.0, 50.0), (50.0, -50.0), (90.0, 50.0)], *BOX
    )
    assert len(runs) == 2, f"expected two runs, got {len(runs)}"
    for run in runs:
        assert all(0.0 - 1e-6 <= p[1] <= 100.0 + 1e-6 for p in run)


def test_every_clipped_vertex_lies_inside_the_box():
    """The invariant that matters: nothing survives outside the ground mesh."""
    f = parse_features(DOC)
    clipped = clip_to_box(f, -10.0, -10.0, 60.0, 60.0)
    assert clipped.ways, "expected some geometry to survive"
    for way in clipped.ways:
        for x, y in way.points:
            assert -10.0 - 1e-6 <= x <= 60.0 + 1e-6, f"{way.osm_id} vertex x={x}"
            assert -10.0 - 1e-6 <= y <= 60.0 + 1e-6, f"{way.osm_id} vertex y={y}"
    # Metadata must survive the clip, or a road loses the width/provenance that
    # says how much of it is real.
    for way in clipped.ways:
        assert way.provenance == MAPPED
        assert way.osm_id in {w.osm_id for w in f.ways}
    # And the length must be recomputed, not carried over from the full way.
    road = next(w for w in clipped.ways if w.osm_id == 1)
    assert road.length_m < 1000.0, "clipped way still reports its original length"


def test_clipping_the_real_bake_fits_inside_a_ground_mesh():
    """End to end on real data: the S05b bake clipped to a ground extent must not
    leave a single vertex outside it, and must not clip away everything."""
    if not OSM_YAML.exists():  # pragma: no cover
        pytest.skip("OSM bake not present")
    f = load_features(str(OSM_YAML))
    raw_x = [p[0] for w in f.ways for p in w.points]
    # The measured `--subset 200` ground mesh: reach 4,616 m about the footprint
    # centre. See `test_a_small_subset_may_legitimately_have_no_mapped_geography`
    # for why a SMALLER subset is expected to clip to nothing.
    box = (-4327.0, -4407.0, 4905.0, 4825.0)
    assert min(raw_x) < box[0], "the raw bake should extend beyond the ground"
    clipped = clip_to_box(f, *box)
    assert clipped.ways, "the real bake must leave something inside the ground mesh"
    assert clipped.roads, "expected the mapped tracks to survive at this extent"
    for way in clipped.ways:
        for x, y in way.points:
            assert box[0] - 1e-6 <= x <= box[2] + 1e-6
            assert box[1] - 1e-6 <= y <= box[3] + 1e-6


def test_a_small_subset_may_legitimately_have_no_mapped_geography():
    """Measured, and recorded so it is never mistaken for a broken ingest.

    The nearest mapped way to the `--subset 20` patch is ~1.5 km outside its ground
    mesh, so that stage carries NO mapped geography at all. That is the honest
    answer for a 115 x 160 m crop of a 4.8 km plot — OSM maps the region, not the
    aisles — and `farm_builder` warns rather than implying mapped roads are
    present. The threshold is real: `--subset 50` picks up 2 roads and
    `--subset 200` picks up 3 roads and 6 power ways.
    """
    if not OSM_YAML.exists():  # pragma: no cover
        pytest.skip("OSM bake not present")
    f = load_features(str(OSM_YAML))
    subset20_ground = (1335.0, -1420.0, 4335.0, 1580.0)
    assert clip_to_box(f, *subset20_ground).ways == [], (
        "if this now finds features, the ingest or the ground extent changed — "
        "update the numbers in the docstring rather than deleting the test"
    )


def test_every_closed_power_area_in_the_real_bake_is_classified_as_an_area():
    """`farm_builder` splits `power=*` ways into ground AREAS (drawn as outlines)
    and conductor LINES (drawn overhead, with towers). Anything closed that is not
    in the area set gets authored as a cable strung around its own perimeter.

    That bug was live: the real bake contains two mapped substations (`PSS 3`,
    `KPS 2`) and a generator area alongside the two plant boundaries, and all three
    were being authored as 14 m overhead lines. They happen to be clipped away at
    `--subset 200`, so the stage did not show it — which is exactly why this is a
    test on the BAKE rather than on a built stage.
    """
    if not OSM_YAML.exists():  # pragma: no cover
        pytest.skip("OSM bake not present")
    from solar_twin.world.osm_features import OSM_POWER_AREAS

    f = load_features(str(OSM_YAML))
    closed_kinds = {w.kind for w in f.power if w.closed}
    assert closed_kinds, "expected some closed power rings in the real bake"
    missing = closed_kinds - set(OSM_POWER_AREAS)
    assert not missing, (
        f"closed power ring kind(s) {sorted(missing)} are not classified as ground "
        "areas, so they would be authored as overhead conductors with towers"
    )
    # And a conductor must NOT be in the area set, or real lines lose their height.
    assert "line" not in OSM_POWER_AREAS
