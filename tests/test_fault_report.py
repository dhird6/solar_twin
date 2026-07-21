"""FaultReport round-trip (§6.3 payload shared by run record + /mission/fault).

Pure-python, no Isaac — this is the shape `docs/ROS2_CONTRACT.md` locks in.
"""

import json

from solar_twin.schema.pv_module import FaultReport


def _sample() -> FaultReport:
    return FaultReport(
        panel_id="R01-C003",
        fault_type="hotspot",
        confidence=0.87,
        note="ground-truth confirm hotspot",
        timestamp="2026-07-21T00:00:02",
        panel_geo_position=(33.4484, -112.0740, 331.0),
    )


def test_to_dict_shape():
    d = _sample().to_dict()
    assert d == {
        "panel_id": "R01-C003",
        "fault_type": "hotspot",
        "confidence": 0.87,
        "note": "ground-truth confirm hotspot",
        "timestamp": "2026-07-21T00:00:02",
        "panel_geo_position": (33.4484, -112.0740, 331.0),
    }


def test_dict_roundtrip():
    report = _sample()
    restored = FaultReport.from_dict(report.to_dict())
    assert restored == report


def test_json_roundtrip():
    report = _sample()
    raw = json.dumps(report.to_dict())
    restored = FaultReport.from_dict(json.loads(raw))
    # geo_position comes back as a list from JSON; from_dict re-tuples it.
    assert restored == report


def test_geo_position_optional():
    report = FaultReport(
        panel_id="R00-C000",
        fault_type="soiled",
        confidence=0.5,
        note="",
        timestamp="2026-07-21T00:00:00",
    )
    assert report.panel_geo_position is None
    restored = FaultReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert restored == report
