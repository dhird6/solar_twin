"""farm_builder USD-authoring guards — require pxr, so they SKIP where pxr is
unavailable (aarch64 system Python has no usd-core wheel). They run in x86 CI
and under Isaac Sim's Python.

The regression these exist for: debug/visualization geometry must never be able
to influence a SENSOR frame. The keep-out no-fly spheres were originally authored
as default-purpose prims; being *display*-translucent does not make them
*shadow*-translucent, so two 9-10 m spheres at hub height shaded the whole farm
and halved frame brightness — masking the very fault cells perception has to
find. They are now `purpose = "guide"` (excluded from the default render).
"""

import pytest

pytest.importorskip("pxr")

from pxr import Usd, UsdGeom  # noqa: E402

from solar_twin.world import farm_builder  # noqa: E402

FARM = {
    "seed": 20260721,
    "grid": {"rows": 1, "cols": 3, "row_pitch": 6.0, "col_pitch": 2.2},
    "georef": {"lat0": 33.4484, "lon0": -112.0740, "elev0": 331.0},
    "panel": {"width": 1.0, "length": 2.0, "height": 0.05, "mount_height": 0.75},
    "faults": {"rate": 0.0, "states": []},
    "terrain": {"kind": "heightfield", "amplitude": 0.6, "wavelength": 14.0},
    "turbines": [{"pos": [9.5, -14.0], "hub_height": 18.0, "blade_len": 8.0, "rpm": 12.0}],
}


def _build(tmp_path) -> Usd.Stage:
    out = tmp_path / "farm.usd"
    farm_builder.build(dict(FARM), str(out))
    return Usd.Stage.Open(str(out))


def test_keepout_viz_is_guide_purpose_not_renderable(tmp_path):
    """Regression: the no-fly viz sphere must be guide-purpose so it is excluded
    from the default render and can never shadow/occlude a camera frame."""
    stage = _build(tmp_path)
    viz = [p for p in stage.Traverse() if p.GetName() == "KeepoutViz"]
    assert viz, "expected a KeepoutViz prim for the configured turbine"
    for prim in viz:
        purpose = UsdGeom.Imageable(prim).GetPurposeAttr().Get()
        assert purpose == UsdGeom.Tokens.guide, (
            f"{prim.GetPath()} purpose={purpose!r} — debug viz must be 'guide' or "
            "it will shadow the sensor frames"
        )


def test_turbine_and_panels_are_renderable(tmp_path):
    """The inverse guard: real scene content must NOT be guide-purpose, or the
    turbine would stop casting the blade shadows the false-fault test needs."""
    stage = _build(tmp_path)
    for name in ("Tower", "Nacelle"):
        prims = [p for p in stage.Traverse() if p.GetName() == name]
        assert prims, f"expected a {name} prim"
        for prim in prims:
            purpose = UsdGeom.Imageable(prim).GetPurposeAttr().Get()
            assert purpose in (None, UsdGeom.Tokens.default_), (
                f"{prim.GetPath()} must stay renderable, got purpose={purpose!r}"
            )
