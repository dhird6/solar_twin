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
    "panel": {
        "width": 1.0,
        "length": 2.0,
        "height": 0.05,
        "mount_height": 0.75,
        "cell_cols": 6,
        "cell_rows": 10,
    },
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


def test_dust_subgrid_cannot_align_to_the_cell_grid():
    """The dust sub-grid must not be a multiple of the PV cell counts, or the film
    would snap back to cell borders — the exact bug this design replaced (the VLM
    read cell-aligned opaque patches as a two-tone design, not as soiling)."""
    sub_cols, sub_rows = farm_builder._DUST_SUBGRID
    assert sub_cols % 6 != 0, "dust sub-grid columns align to the 6 PV cell columns"
    assert sub_rows % 10 != 0, "dust sub-grid rows align to the 10 PV cell rows"


def test_soiled_panel_dust_film_lets_the_grid_read_through(tmp_path):
    """A soiled panel carries a DustFilm mesh whose per-face colour is BAKED as
    dust-over-substrate. RTX renders UsdPreviewSurface `opacity` as a hard cutout
    on this build, so translucency is baked instead: faces over a blue cell must
    differ from faces over a light frame gap, which is what keeps the cell grid
    visible *through* the dust rather than hidden under a flat sheet."""
    farm = dict(FARM, faults={"rate": 1.0, "states": ["soiled"]})
    out = tmp_path / "soiled.usd"
    farm_builder.build(farm, str(out))
    stage = Usd.Stage.Open(str(out))
    films = [p for p in stage.Traverse() if p.GetName() == "DustFilm"]
    assert films, "expected a DustFilm mesh on a soiled panel"
    for prim in films:
        mesh = UsdGeom.Mesh(prim)
        n_faces = len(mesh.GetFaceVertexCountsAttr().Get())
        assert n_faces > 0, "dust film has no faces"

        primvar = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
        assert primvar, "dust film must carry a displayColor primvar"
        assert primvar.GetInterpolation() == UsdGeom.Tokens.uniform
        colors = primvar.Get()
        assert len(colors) == n_faces, "expected one baked colour per face"

        # The grid must read through: more than one distinct blended colour, and
        # dust must not have flattened everything to a single opaque tone.
        distinct = {tuple(round(v, 4) for v in c) for c in colors}
        assert len(distinct) > 1, (
            "dust film is a single flat colour — the substrate is not showing "
            "through, which is the bug this design replaced"
        )
        purpose = UsdGeom.Imageable(prim).GetPurposeAttr().Get()
        assert purpose in (None, UsdGeom.Tokens.default_)


def test_healthy_panel_has_no_dust_film(tmp_path):
    stage = _build(tmp_path)  # FARM has faults rate 0.0
    assert not [p for p in stage.Traverse() if p.GetName() == "DustFilm"]


def test_shading_occluder_authored_only_when_enabled(tmp_path):
    # Off by default (base FARM has no `shading` block).
    base = _build(tmp_path)
    assert not [p for p in base.Traverse() if p.GetName() == "ShadingBar"]

    # Enabled -> a renderable bar suspended up-sun (+Y) and above the panels.
    farm = dict(
        FARM,
        sun={"elevation_deg": 45.0, "azimuth_deg": 0.0},
        shading={"enabled": True, "height": 2.4, "thickness": 0.18},
    )
    out = tmp_path / "shaded.usd"
    farm_builder.build(farm, str(out))
    stage = Usd.Stage.Open(str(out))
    bars = [p for p in stage.Traverse() if p.GetName() == "ShadingBar"]
    assert len(bars) == 1
    t, _, _, _, _ = UsdGeom.XformCommonAPI(bars[0]).GetXformVectors(Usd.TimeCode.Default())
    assert t[1] > 0.0, "occluder must sit up-sun (+Y) of the row to shadow it"
    assert t[2] > 0.8, "occluder must be above panel height to cast onto the surface"
    purpose = UsdGeom.Imageable(bars[0]).GetPurposeAttr().Get()
    assert purpose in (None, UsdGeom.Tokens.default_), "occluder must be renderable"


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
