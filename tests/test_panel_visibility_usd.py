"""Panel-visibility guards: a built stage must actually SHOW its modules.

Requires pxr, so these SKIP where pxr is unavailable (aarch64 system Python has
no usd-core wheel). They run in x86 CI and under Isaac Sim's Python.

The regression these exist for
------------------------------
`assets/khavda_s05b_tour.mp4` (Session 13b) rendered a plant with essentially no
visible modules. Every panel-authoring suspect was checked against the stage and
came back CLEAN — the instanced prototype carried its full 75 prims at
`purpose=default` with `cell_healthy`/`frame` bound, all 1,904 panels were
instanceable, visible, default-purpose, and cleared the ground mesh by 1.95-2.60
m. The panels were fine; the CAMERA never looked at them, because the 20-table
subset was a 1738 x 161 m smear (see `layout_import.subset_site`) and the shot
list pulled back for the long axis while travelling along the short one (see
`flythrough.footprint_frame`).

So the guards here come in two halves, and the second is the one that would have
caught it:

* **Stage half** — panels are visible, material-bound, default-purpose, and above
  ground. These were already true, and are asserted so a prototype-level mistake
  (which drops every instance at once) cannot pass silently.
* **Framing half** — the camera path must put the footprint in shot. Lives in
  `test_flythrough.py` because it needs no pxr.
"""

import bisect

import pytest

pytest.importorskip("pxr")

from pxr import Usd, UsdGeom, UsdShade  # noqa: E402

from solar_twin.world import farm_builder  # noqa: E402

FARM = {
    "seed": 20260729,
    "grid": {"rows": 2, "cols": 4, "row_pitch": 6.0, "col_pitch": 2.2},
    "georef": {"lat0": 24.0915, "lon0": 69.4205, "elev0": 0.0},
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
    "turbines": [],
}

#: Every purpose a renderer draws in a normal (non-debug) pass.
_RENDERED = (None, UsdGeom.Tokens.default_, UsdGeom.Tokens.render)


def _build(tmp_path, **over) -> Usd.Stage:
    farm = dict(FARM)
    farm.update(over)
    out = tmp_path / "vis.usd"
    farm_builder.build(farm, str(out))
    return Usd.Stage.Open(str(out))


def _panels(stage) -> list:
    farm = stage.GetPrimAtPath("/World/Farm")
    assert farm, "stage has no /World/Farm"
    panels = list(farm.GetChildren())
    assert panels, "stage has no panels under /World/Farm"
    return panels


@pytest.mark.parametrize("instancing", [True, False])
def test_every_panel_is_visible_and_default_purpose(tmp_path, instancing):
    """No panel may be invisible or excluded from the default render pass.

    `purpose="guide"` is the specific trap: this project authors keep-out viz
    spheres as guides on purpose, and the same call on panel geometry would drop
    every module from the render while leaving the prims, the `pv:` attributes and
    the bounding boxes all looking correct.
    """
    stage = _build(tmp_path, render={"instancing": instancing})
    for prim in _panels(stage):
        img = UsdGeom.Imageable(prim)
        assert img.ComputeVisibility() == UsdGeom.Tokens.inherited, (
            f"{prim.GetPath()} is invisible"
        )
        assert img.ComputePurpose() in _RENDERED, (
            f"{prim.GetPath()} purpose={img.ComputePurpose()!r} — panel geometry "
            "must never be guide/proxy purpose or it leaves the default render"
        )


@pytest.mark.parametrize("instancing", [True, False])
def test_every_panel_has_drawable_geometry_in_the_default_pass(tmp_path, instancing):
    """A panel must enclose real volume when the renderer looks at the DEFAULT
    purpose only.

    Computed against `default` alone rather than all purposes on purpose: a
    guide-purposed cell grid still yields a bbox under an all-purpose cache, so
    an all-purpose assert would pass on exactly the stage this guards against.
    """
    stage = _build(tmp_path, render={"instancing": instancing})
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    for prim in _panels(stage):
        rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        assert not rng.IsEmpty(), (
            f"{prim.GetPath()} has NO default-purpose geometry — it will render "
            "as nothing"
        )
        size = rng.GetSize()
        assert min(size[0], size[1]) > 0.1 and size[2] > 0.0, (
            f"{prim.GetPath()} is degenerate: {size}"
        )


@pytest.mark.parametrize("instancing", [True, False])
def test_panel_glass_and_frame_stay_material_bound(tmp_path, instancing):
    """Cells must resolve to a material, or a module renders as unlit default grey
    — geometrically correct and visually not a PV panel.

    Instance proxies are traversed explicitly: with `IF-09` instancing the cells
    live under a class prim and a plain `Traverse()` does not descend into them,
    which is how a prototype-level unbind would go unnoticed.
    """
    stage = _build(tmp_path, render={"instancing": instancing})
    checked = 0
    for prim in _panels(stage):
        for desc in Usd.PrimRange(
            prim, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)
        ):
            if desc.GetTypeName() != "Cube":
                continue
            # ComputeBoundMaterial returns (material, bindingRel) on this build.
            mat = UsdShade.MaterialBindingAPI(desc).ComputeBoundMaterial()[0]
            assert mat and mat.GetPrim().IsValid(), (
                f"{desc.GetPath()} has no bound material — it renders as "
                "untextured default grey, not as glass"
            )
            checked += 1
    assert checked >= len(_panels(stage)), "found no panel Cube geometry to check"


def test_instanced_prototype_carries_the_whole_module(tmp_path):
    """The prototype is the single point of failure for every instance at once, so
    it is asserted directly rather than only through its instances."""
    stage = _build(tmp_path, render={"instancing": True})
    proto = stage.GetPrimAtPath(f"{farm_builder._PROTO_ROOT}/Panel_0")
    assert proto, "instancing is on but no panel prototype was authored"

    kids = {
        p.GetName(): p
        for p in Usd.PrimRange(proto, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate))
    }
    assert "Geom" in kids, "prototype has no frame Geom"
    n_cells = FARM["panel"]["cell_cols"] * FARM["panel"]["cell_rows"]
    cells = [n for n in kids if n.startswith("c_")]
    assert len(cells) == n_cells, f"prototype has {len(cells)} cells, expected {n_cells}"

    for prim in kids.values():
        img = UsdGeom.Imageable(prim)
        assert img.GetPurposeAttr().Get() in _RENDERED, (
            f"{prim.GetPath()} purpose={img.GetPurposeAttr().Get()!r} — a "
            "guide-purposed PROTOTYPE silently drops every instance of it"
        )
        assert img.GetVisibilityAttr().Get() != UsdGeom.Tokens.invisible

    # Every panel must actually reference it, or instancing quietly authored an
    # empty Xform per module.
    for prim in _panels(stage):
        assert prim.IsInstanceable() and prim.HasAuthoredReferences(), (
            f"{prim.GetPath()} is not an instance of the prototype"
        )


@pytest.mark.parametrize("instancing", [True, False])
def test_panels_clear_the_ground_mesh_under_them(tmp_path, instancing):
    """Panels must stand ABOVE the rendered ground, not inside or below it.

    Checked against the ground MESH's own interpolated surface rather than
    `terrain_height`: the mesh is a coarse tessellation of that function (here one
    vertex per ~175 m across a 27 km sheet on the real site), so the two agree
    only at vertices. A panel can clear `terrain_height` and still be buried by
    the triangle actually drawn beneath it — which reads as "not visible" exactly
    like a purpose or binding bug does.
    """
    stage = _build(tmp_path, render={"instancing": instancing})
    ground = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Ground"))
    assert ground, "stage has no /World/Ground"
    pts = ground.GetPointsAttr().Get()
    xs = sorted({round(p[0], 4) for p in pts})
    ys = sorted({round(p[1], 4) for p in pts})
    nx, ny = len(xs), len(ys)
    assert nx * ny == len(pts), "ground is not a tensor-product grid; update this test"

    def _cell(vals: list[float], v: float) -> tuple[int, float]:
        """Index of the cell containing `v` plus the fraction across it. Spacing is
        NON-uniform: the ground mesh is graded (fine over the site, coarsening to
        the horizon), so this bisects rather than dividing by a step."""
        i = min(max(bisect.bisect_right(vals, v) - 1, 0), len(vals) - 2)
        width = vals[i + 1] - vals[i]
        return i, 0.0 if width <= 0 else min(max((v - vals[i]) / width, 0.0), 1.0)

    def ground_z(x: float, y: float) -> float:
        i, tx = _cell(xs, x)
        j, ty = _cell(ys, y)
        z = [pts[(j + b) * nx + i + a][2] for b in (0, 1) for a in (0, 1)]
        return (z[0] * (1 - tx) + z[1] * tx) * (1 - ty) + (z[2] * (1 - tx) + z[3] * tx) * ty

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    for prim in _panels(stage):
        rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        lo, hi = rng.GetMin(), rng.GetMax()
        gz = ground_z((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0)
        assert lo[2] > gz, (
            f"{prim.GetPath()} bottom z={lo[2]:.3f} is at or below the ground mesh "
            f"({gz:.3f}) — it is buried or z-fighting, not visible"
        )


# --------------------------------------------------------------------------- #
# The batched Sdf authoring path.
#
# `farm_builder` authors every healthy instanced panel as `Sdf.PrimSpec`s inside one
# `Sdf.ChangeBlock`, because going through UsdStage per panel is QUADRATIC — every
# `DefinePrim` recomposes the parent's children. Measured: n^2.39 end to end, which
# put S05b's 679,616 modules at ~55 HOURS. Batched it is n^1.03 and the full plot
# builds in 176 s.
#
# The cost is a SECOND authoring path for one contract, so these tests exist to stop
# the two drifting. `pv.author_panel_spec` must stay in lockstep with
# `pv.create_panel`.
# --------------------------------------------------------------------------- #

_PV_ATTRS = (
    "pv:panel_id", "pv:grid_index", "pv:state", "pv:iv_yield",
    "pv:rul_days", "pv:last_inspected", "pv:inspection_log", "pv:geo_position",
)


def test_author_panel_spec_matches_create_panel_field_for_field(tmp_path):
    """The two authoring paths must produce indistinguishable prims — same
    attributes, same VALUES, and same USD types.

    Types are asserted explicitly because that is the failure that hides: a bare
    tuple makes USD infer a double vector and silently mismatch the declared Int2 /
    Double3, which reads back fine in Python and breaks a consumer that asks for the
    declared type.
    """
    from pxr import Sdf

    from solar_twin.schema import pv_module as pv

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/Slow")
    UsdGeom.Xform.Define(stage, "/Fast")
    geo = (24.0915, 69.4205, 4.2)

    pv.create_panel(stage, "/Slow/Panel_R01_C002", "R01-C002", 1, 2, geo)
    layer = stage.GetRootLayer()
    with Sdf.ChangeBlock():
        pv.author_panel_spec(
            layer.GetPrimAtPath("/Fast"), "Panel_R01_C002", "R01-C002", 1, 2, geo
        )

    slow = stage.GetPrimAtPath("/Slow/Panel_R01_C002")
    fast = stage.GetPrimAtPath("/Fast/Panel_R01_C002")
    assert fast and fast.IsValid(), "the Sdf spec did not compose into a prim"
    assert fast.GetTypeName() == slow.GetTypeName() == "Xform"

    for name in _PV_ATTRS:
        a, b = slow.GetAttribute(name), fast.GetAttribute(name)
        assert bool(a) == bool(b), f"{name}: present on one path only"
        if not a:
            continue
        assert a.Get() == b.Get(), f"{name}: {a.Get()!r} != {b.Get()!r}"
        assert a.GetTypeName() == b.GetTypeName(), (
            f"{name}: type {a.GetTypeName()} != {b.GetTypeName()}"
        )

    # And no EXTRA pv: attribute on either side — a field added to one path only is
    # the drift these tests exist to catch.
    def _pv_names(prim):
        return {a.GetName() for a in prim.GetAttributes() if a.GetName().startswith("pv:")}

    assert _pv_names(slow) == _pv_names(fast)


def test_batched_and_unbatched_builds_agree_on_every_panel(tmp_path):
    """End to end: the shipped builder (batched) against one with instancing off.

    The `pv:` contract, the fault assignment and the panel transforms must be
    identical — only the GEOMETRY representation differs (shared prototype vs
    per-panel cells), which is `IF-09` working as intended.
    """
    farm = dict(FARM, faults={"rate": 0.4, "states": ["hotspot", "soiled"]})
    fast_path, slow_path = tmp_path / "fast.usd", tmp_path / "slow.usd"
    farm_builder.build(dict(farm), str(fast_path))
    farm_builder.build(dict(farm, render={"instancing": False}), str(slow_path))

    fast = Usd.Stage.Open(str(fast_path))
    slow = Usd.Stage.Open(str(slow_path))
    fp = {p.GetName(): p for p in fast.GetPrimAtPath("/World/Farm").GetChildren()}
    sp = {p.GetName(): p for p in slow.GetPrimAtPath("/World/Farm").GetChildren()}
    assert set(fp) == set(sp), f"panel sets differ: {sorted(set(fp) ^ set(sp))}"
    assert fp, "no panels built"

    for name, a in fp.items():
        b = sp[name]
        for attr in _PV_ATTRS:
            va, vb = a.GetAttribute(attr), b.GetAttribute(attr)
            assert bool(va) == bool(vb), f"{name}/{attr} present on one stage only"
            if va:
                assert va.Get() == vb.Get(), f"{name}/{attr} differs"
        # Transforms must match exactly — the batched path writes xformOp specs by
        # hand instead of going through XformCommonAPI, so this is the assertion
        # that the hand-authored ops mean the same thing.
        ta = UsdGeom.XformCommonAPI(a).GetXformVectors(Usd.TimeCode.Default())
        tb = UsdGeom.XformCommonAPI(b).GetXformVectors(Usd.TimeCode.Default())
        for i, lbl in enumerate(("translate", "rotate", "scale", "pivot", "rotOrder")):
            assert str(ta[i]) == str(tb[i]), f"{name} xform {lbl}: {ta[i]} != {tb[i]}"


def test_batched_panels_are_instanced_labelled_and_carry_geometry(tmp_path):
    """A healthy panel off the fast path must be a real instance of the prototype,
    resolve renderable geometry, and keep its semantic label.

    The label matters beyond tidiness: it is what Replicator and the confirm-drone
    read, and the batched path writes `SemanticsLabelsAPI` as raw specs rather than
    through `UsdSemantics.LabelsAPI`, so it could silently stop being applied.
    """
    stage = _build(tmp_path)  # FARM has faults rate 0.0 -> every panel is healthy
    panels = _panels(stage)
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    for prim in panels:
        assert prim.IsInstanceable(), f"{prim.GetPath()} is not instanceable"
        assert prim.HasAuthoredReferences(), f"{prim.GetPath()} has no prototype ref"
        assert not cache.ComputeWorldBound(prim).ComputeAlignedRange().IsEmpty()
        labels = prim.GetAttribute("semantics:labels:class")
        assert labels and labels.Get(), f"{prim.GetPath()} lost its semantic label"
        assert "panel" in list(labels.Get())
