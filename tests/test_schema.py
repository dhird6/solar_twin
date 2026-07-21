"""Pure-python contract tests for the PVModule schema (no pxr / no Isaac)."""

import pytest

from solar_twin.schema.pv_module import (
    GeoAnchor,
    PanelRecord,
    PanelState,
    append_inspection,
    coerce_state,
    geo_to_local,
    is_valid_state,
    local_to_geo,
    panel_id,
    panel_path,
)


def test_taxonomy_membership():
    assert is_valid_state("hotspot")
    assert is_valid_state("healthy")
    assert not is_valid_state("on_fire")


def test_coerce_unknown_defaults():
    assert coerce_state("soiled") is PanelState.SOILED
    assert coerce_state("nonsense") is PanelState.UNKNOWN


def test_panel_id_and_path_format():
    assert panel_id(12, 47) == "R12-C047"
    assert panel_id(1, 3) == "R01-C003"
    assert panel_path("/World/Farm", 12, 47) == "/World/Farm/Panel_R12_C047"


def test_append_inspection_is_append_only_and_immutable():
    rec = PanelRecord(panel_id="R01-C001", grid_index=(1, 1))
    assert rec.is_healthy

    r1 = append_inspection(rec, PanelState.HOTSPOT, "flagged", "2026-07-21T00:00:00")
    # Original untouched (append_inspection returns a copy).
    assert rec.inspection_log == []
    assert rec.state is PanelState.HEALTHY
    # New record reflects the change.
    assert r1.state is PanelState.HOTSPOT
    assert not r1.is_healthy
    assert r1.last_inspected == "2026-07-21T00:00:00"
    assert r1.inspection_log == ["2026-07-21T00:00:00 hotspot: flagged"]

    r2 = append_inspection(r1, PanelState.HEALTHY, "cleaned", "2026-07-22T00:00:00")
    assert len(r2.inspection_log) == 2  # append-only history


def test_local_to_geo_origin_is_anchor():
    anchor = GeoAnchor(lat0=33.4484, lon0=-112.0740, elev0=331.0, heading_deg=0.0)
    lat, lon, elev = local_to_geo(0.0, 0.0, 0.0, anchor)
    assert lat == pytest.approx(33.4484)
    assert lon == pytest.approx(-112.0740)
    assert elev == pytest.approx(331.0)


@pytest.mark.parametrize("heading", [0.0, 30.0, 90.0, 215.0])
@pytest.mark.parametrize("pt", [(10.0, 5.0, 2.0), (-40.0, 120.0, -3.0), (0.0, 0.0, 0.0)])
def test_geo_roundtrip(heading, pt):
    anchor = GeoAnchor(lat0=33.4484, lon0=-112.0740, elev0=331.0, heading_deg=heading)
    lat, lon, elev = local_to_geo(*pt, anchor)
    x, y, z = geo_to_local(lat, lon, elev, anchor)
    assert x == pytest.approx(pt[0], abs=1e-4)
    assert y == pytest.approx(pt[1], abs=1e-4)
    assert z == pytest.approx(pt[2], abs=1e-6)


def test_heading_rotates_frame():
    # Local +Y at heading 90 should point due east -> +longitude, ~0 lat change.
    anchor = GeoAnchor(lat0=0.0, lon0=0.0, heading_deg=90.0)
    lat, lon, _ = local_to_geo(0.0, 100.0, 0.0, anchor)
    assert lon > 0.0
    assert lat == pytest.approx(0.0, abs=1e-9)
