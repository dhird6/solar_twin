"""Ingest of the GatiShakti plot digests — pure-python, no Isaac.

The digest is the first plant data we did NOT derive ourselves, so these tests
pin the two things that make it safe to use: a fail-closed loader, and the
real-position / inferred-geometry split staying visible in the output.
"""

from __future__ import annotations

import json
import math

import pytest

from solar_twin.world.plot_digest import (
    BoxAsset,
    PlotDigest,
    Turbine,
    load,
    parse_rating_mw,
)

# One WTG ~1.6 km from the anchor, one ~8 km away, plus a table and an inverter.
DIGEST = {
    "id": "S05b",
    "perimeters": {
        "plot": [[543000.0, 2664000.0], [548000.0, 2664000.0], [548000.0, 2666000.0]],
        "blocks": {"Block-01": [[543100.0, 2664100.0], [543200.0, 2664200.0]]},
    },
    "assets": [
        {"type": "wtg", "centroid": [543117.0, 2665460.0],
         "properties": {"layername": "5.2MW WTG"}},
        {"type": "wtg", "centroid": [550000.0, 2664033.0],
         "properties": {"layername": "5.2MW WTG"}},
        {"type": "mms-table",
         "bounds": {"sw": [543100.0, 2664100.0], "ne": [543104.0, 2664228.0]},
         "properties": {"layername": "MMS Table Block-07"}},
        {"type": "inverter",
         "bounds": {"sw": [543300.0, 2664300.0], "ne": [543306.0, 2664303.0]},
         "properties": {"layername": "Inverter Block-07"}},
    ],
}
ANCHOR = (542440.651, 2664033.041)          # our real BLOCK-02 anchor
SITE_EXTENT = (1.1, 320.1, 0.6, 646.3)      # our real BLOCK-02 footprint


def _write(tmp_path, obj, name="Plot-X.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_loads_perimeters_turbines_and_boxes(tmp_path):
    d = load(_write(tmp_path, DIGEST))
    assert d.plot_id == "S05b"
    assert d.counts() == {"wtg": 2, "mms-table": 1, "inverter": 1, "blocks": 1}
    assert len(d.plot_perimeter) == 3


def test_provenance_is_digest_not_derived():
    """The chain is DWG -> THEIR script (which we do not hold) -> JSON -> us. It
    must never be labelled `derived` like our own DXF ingest."""
    assert PlotDigest(plot_id="x").provenance == "digest"


def test_rating_is_parsed_from_the_layer_name():
    assert parse_rating_mw("5.2MW WTG") == 5.2
    assert parse_rating_mw("3 MW WTG") == 3.0
    assert parse_rating_mw("WTG") is None


def test_geometry_is_inferred_from_the_rating_and_says_so(tmp_path):
    """The digest carries a centroid and a layer name — no hub height, no rotor.
    Position is real; size is assumed, and the output must keep them apart."""
    d = load(_write(tmp_path, DIGEST))
    cfg = d.turbines_config(*ANCHOR, radius_m=5000.0, site_extent=SITE_EXTENT)
    assert cfg, "the near turbine should be emitted"
    e = cfg[0]
    assert e["provenance"] == "digest"            # position
    assert e["geometry_provenance"] == "INFERRED"  # hub/blade/rpm
    assert e["rating_mw"] == 5.2
    assert e["hub_height"] == 120.0 and e["blade_len"] == 70.0


def test_survey_to_stage_is_a_pure_offset():
    """Both sides are already EPSG:32642 metres, so the transform is subtraction —
    no reprojection, which is the reason these datasets compose at all."""
    t = Turbine(543117.0, 2665460.0, 5.2, "5.2MW WTG")
    x, y = t.stage_pos(*ANCHOR)
    assert x == pytest.approx(676.349, abs=1e-3)
    assert y == pytest.approx(1426.959, abs=1e-3)


def test_distance_is_measured_to_the_footprint_not_the_anchor(tmp_path):
    """Our block is 320 x 647 m: distance-to-anchor overstates the gap to a
    turbine off the far corner by most of a kilometre."""
    d = load(_write(tmp_path, DIGEST))
    to_anchor = d.near_turbines(*ANCHOR, radius_m=99999.0)[0][0]
    to_site = d.near_turbines(*ANCHOR, radius_m=99999.0, site_extent=SITE_EXTENT)[0][0]
    assert to_site < to_anchor
    assert to_anchor == pytest.approx(math.hypot(676.349, 1426.959), abs=1e-2)


def test_far_turbines_are_excluded_by_radius(tmp_path):
    """A turbine 8 km away contributes no wake and no shadow; including it would
    put dead geometry on the stage and a phantom keep-out in the planner."""
    d = load(_write(tmp_path, DIGEST))
    near = d.turbines_config(*ANCHOR, radius_m=5000.0, site_extent=SITE_EXTENT)
    assert len(near) == 1
    assert len(d.turbines_config(*ANCHOR, radius_m=20000.0, site_extent=SITE_EXTENT)) == 2


def test_turbines_are_sorted_nearest_first(tmp_path):
    d = load(_write(tmp_path, DIGEST))
    cfg = d.turbines_config(*ANCHOR, radius_m=99999.0, site_extent=SITE_EXTENT)
    assert cfg[0]["distance_to_site_m"] <= cfg[1]["distance_to_site_m"]


def test_emitted_config_matches_what_keepout_and_siting_consume(tmp_path):
    """A data swap, not a code change: `build_keepouts` must accept it unchanged."""
    from solar_twin.world.keepout import build_keepouts

    d = load(_write(tmp_path, DIGEST))
    farm = {
        "grid": {"rows": 1, "cols": 4, "row_pitch": 6.0, "col_pitch": 2.2},
        "georef": {"lat0": 24.09, "lon0": 69.42},
        "terrain": {"kind": "flat"},
        "turbines": d.turbines_config(*ANCHOR, radius_m=5000.0, site_extent=SITE_EXTENT),
    }
    kos = build_keepouts(farm)
    assert len(kos) == 1
    assert kos[0].hub[2] == pytest.approx(120.0)      # hub height reached it
    assert kos[0].rotor_radius > 70.0                  # blade_len + margin


def test_block_membership_comes_from_the_layer_name(tmp_path):
    d = load(_write(tmp_path, DIGEST))
    tables = [b for b in d.boxes if b.kind == "mms-table"]
    assert tables[0].block == "Block-07"
    assert BoxAsset("idt", 0, 0, 1, 1, "IDT").block is None


def test_box_geometry_is_read_correctly(tmp_path):
    d = load(_write(tmp_path, DIGEST))
    t = [b for b in d.boxes if b.kind == "mms-table"][0]
    w, l = t.size_m
    assert (round(w, 1), round(l, 1)) == (4.0, 128.0)   # a tracker table
    assert t.centroid == (543102.0, 2664164.0)


# --- fail-closed loading --------------------------------------------------- #


def test_a_wrong_crs_is_rejected_rather_than_reprojected(tmp_path):
    bad = {**DIGEST, "crs": "EPSG:4326"}
    with pytest.raises(ValueError, match="CRS"):
        load(_write(tmp_path, bad))


def test_a_digest_with_no_id_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no plot"):
        load(_write(tmp_path, {**DIGEST, "id": ""}))


def test_an_empty_digest_is_rejected_not_silently_loaded(tmp_path):
    """A silently-empty digest surfaces much later as "the plant has no
    turbines", which is indistinguishable from a config mistake."""
    with pytest.raises(ValueError, match="no assets"):
        load(_write(tmp_path, {"id": "Empty", "perimeters": {}, "assets": []}))


def test_the_template_shaped_block_list_is_also_accepted(tmp_path):
    """Plot-Ref.json uses `blocks: [{id, coordinates}]` rather than a dict —
    reading that as zero blocks would be a silent loss."""
    alt = {**DIGEST, "perimeters": {
        "plot": [], "blocks": [{"id": "B1", "coordinates": [[1.0, 2.0]]}]}}
    d = load(_write(tmp_path, alt))
    assert d.block_perimeters == {"B1": [(1.0, 2.0)]}
