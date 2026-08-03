"""Selecting whole DC blocks — the honest way to make the plant look bigger.

`subset_site` crops a radius of tables: right for "prove the pipeline cheaply", wrong
for a wide shot, because it cuts blocks in half and the plant reads as one ragged
field. `select_blocks` keeps WHOLE surveyed blocks instead.

The property that matters most here is not geometric, it is provenance: every
position must come from the S05b digest. Tiling a copy of BLOCK-02 four times would
look the same in a render and would be an invented plant wearing surveyed
coordinates.
"""

from __future__ import annotations

import pathlib

import pytest

from solar_twin.world.layout_import import (
    TableSpec,
    block_of,
    blocks_in,
    load_site,
    select_blocks,
    subset_site,
)

DIGEST = (
    pathlib.Path(__file__).resolve().parents[1]
    / "configs" / "layouts" / "khavda_s05b_digest.yaml"
)


def _t(e, n, layer="", tid="T0", index=0):
    return TableSpec(
        index=index, table_id=tid, easting=e, northing=n, rot_deg=0.0,
        length_m=64.4, width_m=2.278, modules=56, module_rows=1, layer=layer,
    )


# --------------------------------------------------------------------------- #
# Block identity comes from the survey, never from position
# --------------------------------------------------------------------------- #


def test_block_is_read_from_the_layer_name():
    assert block_of(_t(0, 0, "MMS Table Block-06")) == "Block-06"
    assert block_of(_t(0, 0, "  MMS Table Block-11  ")) == "Block-11"


def test_a_layout_with_no_block_structure_reports_none():
    """BLOCK-02's own DXF is a single block and the procedural farm has none. Neither
    should be silently assigned to an invented block."""
    assert block_of(_t(0, 0, "")) == ""
    assert block_of(_t(0, 0, "MMS Table")) == ""


def test_selecting_on_a_blockless_site_is_a_no_op():
    """Rather than returning an empty stage, which would look like a build failure."""
    from solar_twin.world.layout_import import SiteSpec

    site = SiteSpec(
        crs="EPSG:32642", origin_easting=0.0, origin_northing=0.0,
        module_pitch_m=1.134, module_length_m=1.134, module_width_m=2.278,
        nominal_tilt_deg=0.0, tables=[_t(0, 0), _t(10, 0, index=1)],
    )
    assert len(select_blocks(site, 2).tables) == 2


# --------------------------------------------------------------------------- #
# Against the real digest
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not DIGEST.exists(), reason="S05b digest not present")
class TestRealDigest:
    def test_the_plot_has_its_surveyed_24_blocks(self):
        site = load_site(str(DIGEST))
        assert len(blocks_in(site)) == 24, "the digest's own provenance says 24 blocks"

    def test_blocks_are_ordered_by_position_not_by_name(self):
        """The digest's numbering is not spatially sorted, so 'the first 4 by name'
        would scatter them across a 4.8 km plot instead of giving a neighbourhood."""
        site = load_site(str(DIGEST))
        ordered = blocks_in(site)
        assert ordered != sorted(ordered), (
            "block order should be geographic; if it happens to equal name order the "
            "ordering guarantee is untested"
        )

    def test_selecting_n_blocks_grows_the_plant_monotonically(self):
        site = load_site(str(DIGEST))
        counts = [len(select_blocks(site, n).tables) for n in (1, 2, 3, 4)]
        assert counts == sorted(counts)
        assert len(set(counts)) == 4, "each extra block must add tables"

    def test_four_blocks_is_materially_bigger_than_one(self):
        """The whole point of the config: measured 264 -> 1,065 tables and
        29,232 -> 117,264 modules."""
        site = load_site(str(DIGEST))
        one, four = select_blocks(site, 1), select_blocks(site, 4)
        assert sum(t.modules for t in four.tables) > 3 * sum(t.modules for t in one.tables)

    def test_selection_keeps_WHOLE_blocks(self):
        """The property that separates this from `subset_site`: no block may appear
        partially. A half-cut block is what makes a wide shot look ragged."""
        site = load_site(str(DIGEST))
        full = {}
        for t in site.tables:
            full.setdefault(block_of(t), 0)
            full[block_of(t)] += 1
        sel = select_blocks(site, 4)
        got: dict[str, int] = {}
        for t in sel.tables:
            got.setdefault(block_of(t), 0)
            got[block_of(t)] += 1
        for b, n in got.items():
            assert n == full[b], f"{b} was cut: {n} of {full[b]} tables"

    def test_coordinates_are_never_recomputed(self):
        """A selection must be a CROP of the real plot, so a mission flown on it
        matches full-plot geometry. Recentring would make every panel id refer to
        different ground."""
        site = load_site(str(DIGEST))
        sel = select_blocks(site, 4)
        assert (sel.origin_easting, sel.origin_northing) == (
            site.origin_easting, site.origin_northing
        )
        by_id = {t.table_id: t for t in site.tables}
        for t in sel.tables[:50]:
            src = by_id[t.table_id]
            assert (t.easting, t.northing) == (src.easting, src.northing)
            # `index` becomes the panel's row and therefore its panel_id — if a
            # selection renumbered it, a mission would write verdicts onto the wrong
            # hardware (the exact trap `TableSpec.index` documents).
            assert t.index == src.index

    def test_asking_for_all_or_more_returns_everything(self):
        site = load_site(str(DIGEST))
        assert len(select_blocks(site, 0).tables) == len(site.tables)
        assert len(select_blocks(site, 99).tables) == len(site.tables)

    def test_named_selection_and_a_loud_failure_for_an_unknown_block(self):
        site = load_site(str(DIGEST))
        names = blocks_in(site)[:2]
        assert len(select_blocks(site, 0, names).tables) < len(site.tables)
        with pytest.raises(ValueError, match="unknown block"):
            select_blocks(site, 0, ["Block-99"])

    def test_subset_site_still_crops_a_radius(self):
        """The two selectors must stay distinct — `blocks` for scale, `max_tables` for
        a cheap pipeline check."""
        site = load_site(str(DIGEST))
        assert len(subset_site(site, 20).tables) == 20


@pytest.mark.skipif(not DIGEST.exists(), reason="S05b digest not present")
def test_the_4block_config_selects_four_and_keeps_real_provenance():
    """⭐ The config the owner asked for. Guards the claim in its own header."""
    import yaml

    cfg_path = pathlib.Path(__file__).resolve().parents[1] / "configs" / "farm_khavda_4block.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["layout"]["blocks"] == 4
    assert cfg["layout"]["path"].endswith("khavda_s05b_digest.yaml"), (
        "must come from the surveyed digest, not a tiled copy of BLOCK-02"
    )
    header = cfg_path.read_text().split("layout:")[0].lower()
    assert "real" in header and "tiled" in header, (
        "the header must state that nothing is duplicated — that is the claim"
    )
    assert "second-hand" in header, "…and that the digest is second-hand"
