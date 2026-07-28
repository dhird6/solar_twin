"""The layout audit's geometry checks (pure, no Isaac, no PDF).

`tools/audit_layout.py` answers "is this layout the whole drawing?". The PDF
cross-check needs `pymupdf` and a drawing, so it is exercised by running the tool;
these tests cover the part that decides whether a table is *ambiguous* — the thing
the audit must list rather than approximate.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from audit_layout import audit_tables  # noqa: E402

CHORD = 2.278


def _t(tid, e, n, length=128.58, modules=112, width=CHORD):
    return {
        "id": tid,
        "e": e,
        "n": n,
        "length_m": length,
        "modules": modules,
        "width_m": width,
        "rot_deg": 0.0,
        "layer": "Interior HSAT (1x112)",
    }


def test_a_clean_block_reports_no_issues():
    """The real Khavda ingest is clean: 273 tables, no overlaps, no gaps in the
    dimension data. The audit must not manufacture findings."""
    tables = [_t(f"T{i:04d}", 0.0 + 5.5 * i, 0.0) for i in range(20)]
    issues = audit_tables(tables, CHORD)
    assert all(len(v) == 0 for v in issues.values()), issues


def test_overlapping_tables_are_listed_as_a_pair_not_silently_merged():
    """Two tables sharing ground is a drawing question, not something to average."""
    tables = [_t("A", 100.0, 0.0), _t("B", 101.0, 50.0)]  # 1 m apart, chord 2.278
    issues = audit_tables(tables, CHORD)
    assert len(issues["overlapping"]) == 1
    ov = issues["overlapping"][0]
    assert {ov["a"], ov["b"]} == {"A", "B"}
    assert ov["overlap_x_m"] > 0 and ov["overlap_y_m"] > 0


def test_tables_that_only_touch_end_to_end_do_not_count_as_overlapping():
    """Khavda's table bands abut with 1.0 m end gaps; a shared edge is not a clash,
    and flagging it would bury the real findings in noise."""
    tables = [_t("A", 0.0, 0.0, length=100.0), _t("B", 0.0, 100.0, modules=87, length=99.88)]
    assert audit_tables(tables, CHORD)["overlapping"] == []


def test_a_table_missing_dimension_data_is_listed_not_guessed():
    bad = _t("NODIM", 0.0, 0.0)
    bad["length_m"] = None
    issues = audit_tables([bad, _t("OK", 50.0, 0.0)], CHORD)
    assert issues["missing_dims"] == ["NODIM"]
    # ...and it is excluded from the overlap test rather than crashing it.
    assert issues["overlapping"] == []


def test_a_table_missing_its_module_count_is_listed():
    bad = _t("NOMOD", 0.0, 0.0)
    bad["modules"] = 0
    assert audit_tables([bad], CHORD)["missing_dims"] == ["NOMOD"]


def test_length_and_module_count_must_agree_on_the_pitch():
    """A 112-module table is 128.58 m because each module steps 1.148 m. If the two
    disagree, one of them was guessed — which is exactly what this must surface."""
    liar = _t("LIAR", 0.0, 0.0, length=128.58, modules=56)  # implies a 2.30 m pitch
    issues = audit_tables([liar], CHORD)
    assert len(issues["pitch_mismatch"]) == 1
    assert issues["pitch_mismatch"][0]["id"] == "LIAR"
    assert issues["pitch_mismatch"][0]["pitch_m"] == pytest.approx(2.296, abs=0.01)


def test_the_real_table_variants_all_pass_the_pitch_check():
    """The three HSAT lengths in this block: 1x112, 1x84, 1x56."""
    tables = [
        _t("A", 0.0, 0.0, length=128.58, modules=112),
        _t("B", 20.0, 0.0, length=96.44, modules=84),
        _t("C", 40.0, 0.0, length=64.40, modules=56),
    ]
    assert audit_tables(tables, CHORD)["pitch_mismatch"] == []


def test_duplicate_ids_and_positions_are_reported_separately():
    tables = [_t("DUP", 0.0, 0.0), _t("DUP", 500.0, 0.0), _t("X", 900.0, 0.0), _t("Y", 900.0, 0.0)]
    issues = audit_tables(tables, CHORD)
    assert issues["duplicate_id"] == ["DUP"]
    assert issues["duplicate_position"] == [(900.0, 0.0)]
