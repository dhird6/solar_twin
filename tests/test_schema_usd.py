"""USD adapter tests for the schema — require pxr, so they SKIP where pxr is
unavailable (e.g. aarch64 system Python: usd-core has no wheel). They run in
x86 CI (usd-core) and under Isaac Sim's Python. Guards the create/read/write
roundtrip — including the Int2 grid_index type that a bare tuple gets wrong.
"""

import pytest

pytest.importorskip("pxr")

from pxr import Usd, UsdGeom  # noqa: E402

from solar_twin.schema import pv_module as pv  # noqa: E402


def _stage():
    return Usd.Stage.CreateInMemory()


def test_create_read_roundtrip():
    st = _stage()
    prim = pv.create_panel(
        st, "/World/Panel_R12_C047", "R12-C047", 12, 47, (33.4, -112.0, 331.0)
    )
    rec = pv.read_panel(prim)
    assert rec.panel_id == "R12-C047"
    assert rec.grid_index == (12, 47)  # Int2 must survive as ints
    assert rec.state is pv.PanelState.HEALTHY
    assert rec.geo_position == (33.4, -112.0, 331.0)


def test_grid_id_roundtrips_through_usd():
    """`grid:id` is a contract addition and gets the same round-trip guard `pv:`
    got. Written as a side store during sim it would violate USD-as-source-of-truth,
    so it has to survive the prim."""
    stage = _stage()
    prim = pv.create_panel(
        stage, "/World/Farm/P", "R12-C047", 12, 47, cell_id="G-0012"
    )
    assert prim.GetAttribute(pv.ATTR_GRID_ID).Get() == "G-0012"
    assert pv.read_panel(prim).cell_id == "G-0012"


def test_grid_id_is_absent_when_not_requested():
    """Omitting it must author NO attribute, so a stage built without the grid
    layer is byte-identical to one built before the namespace existed."""
    prim = pv.create_panel(_stage(), "/World/Farm/P", "R12-C047", 12, 47)
    assert not prim.HasAttribute(pv.ATTR_GRID_ID)
    assert pv.read_panel(prim).cell_id == ""


def test_grid_id_matches_across_both_authoring_paths():
    """`author_panel_spec` is the bulk path that actually builds 30k panels; the
    two paths must stay in lockstep on this attribute like every other."""
    from pxr import Sdf

    stage = _stage()
    UsdGeom.Xform.Define(stage, "/World/Farm")
    with Sdf.ChangeBlock():
        pv.author_panel_spec(
            stage.GetRootLayer().GetPrimAtPath("/World/Farm"),
            "P", "R12-C047", 12, 47, None, cell_id="G-0012",
        )
    fast = stage.GetPrimAtPath("/World/Farm/P")
    slow = pv.create_panel(stage, "/World/Farm/Q", "R12-C047", 12, 47, cell_id="G-0012")
    assert (
        fast.GetAttribute(pv.ATTR_GRID_ID).Get()
        == slow.GetAttribute(pv.ATTR_GRID_ID).Get()
        == "G-0012"
    )


def test_grid_index_is_vec2i_not_double():
    st = _stage()
    prim = pv.create_panel(st, "/World/P", "R00-C000", 3, 9)
    attr = prim.GetAttribute(pv.ATTR_GRID_INDEX)
    # Declared type must be int2 (regression: a bare tuple made USD infer int2/
    # double mismatch and raise on Set).
    assert attr.GetTypeName() == "int2"
    assert tuple(attr.Get()) == (3, 9)


def test_write_state_appends_log():
    st = _stage()
    prim = pv.create_panel(st, "/World/P", "R00-C000", 0, 0)
    pv.write_state(prim, pv.PanelState.HOTSPOT, "flagged", "2026-07-21T00:00:00")
    rec = pv.read_panel(prim)
    assert rec.state is pv.PanelState.HOTSPOT
    assert rec.last_inspected == "2026-07-21T00:00:00"
    assert rec.inspection_log == ["2026-07-21T00:00:00 hotspot: flagged"]


def test_stage_up_axis_helpers_available():
    st = _stage()
    UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    assert UsdGeom.GetStageUpAxis(st) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(st) == 1.0


def test_restore_state_rewinds_state_stamp_and_log():
    """`--repeat` correctness at the USD level: after a verdict is written, a
    restore must put the prim back exactly as the builder left it. Anything less
    and the next repeat reads the previous repeat's verdict as ground truth."""
    st = _stage()
    prim = pv.create_panel(st, "/World/P", "R01-C001", 1, 1)
    prim.GetAttribute(pv.ATTR_STATE).Set(pv.PanelState.SOILED.value)  # injected fault
    before = pv.read_panel(prim)

    pv.write_state(prim, pv.PanelState.HOTSPOT, "misread as hot", "2026-07-28T00:00:00")
    mid = pv.read_panel(prim)
    assert mid.state is pv.PanelState.HOTSPOT
    assert len(mid.inspection_log) == 1

    pv.restore_state(prim, before)
    after = pv.read_panel(prim)
    assert after.state is pv.PanelState.SOILED
    assert after.last_inspected == before.last_inspected
    # The log feeds `history` into the perception prompt — a leftover line would
    # change the question the next repeat asks.
    assert list(after.inspection_log) == list(before.inspection_log) == []


def test_restore_state_is_not_a_write_and_leaves_no_trace():
    st = _stage()
    prim = pv.create_panel(st, "/World/P2", "R01-C002", 1, 2)
    pv.write_state(prim, pv.PanelState.CRACK, "cracked", "2026-07-28T00:00:01")
    snap = pv.read_panel(prim)
    pv.restore_state(prim, snap)
    # Restoring a snapshot that already had a log preserves it verbatim — the
    # restore neither appends nor drops entries.
    assert list(pv.read_panel(prim).inspection_log) == list(snap.inspection_log)
    assert len(snap.inspection_log) == 1
