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
