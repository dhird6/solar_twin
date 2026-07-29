"""Turbine articulation USD authoring (`FR-11`) — needs `pxr`, so SKIPS off-Isaac.

Same convention as `test_farm_builder_usd.py`: these run under `./python.sh` on the
Spark and in x86 CI, and skip on aarch64 system Python (no `usd-core` wheel). The
*pure* half of `FR-11` — the rotor-speed conversion — is tested in
`test_siting.py`, deliberately in a separate file, because a module-level
`importorskip` skips the whole module and would have taken the runs-anywhere tests
down with it.

The regression these exist for: the turbine proxy already carried colliders, but
`sim_runtime` *wrote the Hub transform every frame*, so nothing could ever be
pushed by a blade — animation wearing physics' clothes (`RISK-11`). Enabling the
articulation without silencing that write swaps one bug for a worse one: two owners
of a single transform, which reads as a solver problem rather than a double-write.

Every `UsdPhysics` API used here was verified present on this build (Isaac Sim
6.0.1-rc.7 / PhysX 110.1.13) rather than recalled.
"""

from __future__ import annotations

import pytest

from solar_twin.world.siting import rpm_to_deg_per_s

pytest.importorskip("pxr")

from pxr import Usd, UsdPhysics  # noqa: E402

from solar_twin.world import farm_builder  # noqa: E402

SPEC = {"pos": [10.0, 20.0], "hub_height": 120.0, "blade_len": 70.0, "rpm": 12.0}


@pytest.fixture()
def articulated():
    """A turbine proxy with the articulation applied, on an in-memory stage."""
    stage = Usd.Stage.CreateInMemory()
    looks = {"turbine": None}

    # `_build_turbine` binds a material; pass a stage-real one so it does not fail.
    from pxr import UsdShade

    mat = UsdShade.Material.Define(stage, "/World/Looks/turbine")
    looks["turbine"] = mat

    path = "/World/Turbines/turbine_0"
    farm_builder._build_turbine(stage, path, SPEC, 0.0, looks)
    farm_builder._articulate_turbine(stage, path, SPEC)
    return stage, path


def test_the_root_is_an_articulation(articulated):
    stage, path = articulated
    assert stage.GetPrimAtPath(path).HasAPI(UsdPhysics.ArticulationRootAPI)


def test_the_hub_is_a_rigid_body_with_explicit_mass(articulated):
    """Derived-from-collider mass on a long thin paddle is arbitrary, and rotor
    inertia sets how much a gust perturbs the rotor — worth stating."""
    stage, path = articulated
    hub = stage.GetPrimAtPath(path + "/Hub")
    assert hub.HasAPI(UsdPhysics.RigidBodyAPI)
    assert hub.HasAPI(UsdPhysics.MassAPI)
    assert UsdPhysics.MassAPI(hub).GetMassAttr().Get() == pytest.approx(2000.0)


def test_the_tower_stays_static_rather_than_becoming_a_body(articulated):
    """The tower is bolted to the ground. Making it a rigid body would add a link
    for the solver to integrate for no behavioural gain."""
    stage, path = articulated
    tower = stage.GetPrimAtPath(path + "/Tower")
    assert not tower.HasAPI(UsdPhysics.RigidBodyAPI)
    assert tower.HasAPI(UsdPhysics.CollisionAPI)  # still collides


def test_a_revolute_joint_connects_tower_to_hub_about_the_rotor_axis(articulated):
    stage, path = articulated
    joint = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath(path + "/RotorJoint"))
    assert joint
    assert joint.GetAxisAttr().Get() == "Y"  # matches _build_turbine's geometry
    assert joint.GetBody0Rel().GetTargets() == [stage.GetPrimAtPath(path + "/Tower").GetPath()]
    assert joint.GetBody1Rel().GetTargets() == [stage.GetPrimAtPath(path + "/Hub").GetPath()]


def test_the_rotor_is_free_spinning_not_limited(articulated):
    """A revolute joint with limits set would make the rotor an oscillating flap
    rather than something that turns."""
    stage, path = articulated
    joint = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath(path + "/RotorJoint"))
    lower = joint.GetLowerLimitAttr()
    upper = joint.GetUpperLimitAttr()
    # Unauthored (or inf) both count as unlimited; what must NOT happen is a
    # finite pair of limits.
    if lower.HasAuthoredValue() and upper.HasAuthoredValue():
        assert lower.Get() > upper.Get()  # USD's "no limit" encoding


def test_the_drive_is_velocity_controlled_at_the_turbines_rpm(articulated):
    """Zero stiffness + damping = velocity control. Stiffness > 0 would spring the
    rotor back to an *angle*, which is not what a rotor does."""
    stage, path = articulated
    drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath(path + "/RotorJoint"), "angular")
    assert drive
    assert drive.GetStiffnessAttr().Get() == pytest.approx(0.0)
    assert drive.GetDampingAttr().Get() > 0.0
    assert drive.GetTargetVelocityAttr().Get() == pytest.approx(
        rpm_to_deg_per_s(SPEC["rpm"])
    )


def test_the_hub_is_flagged_so_the_runtime_stops_writing_its_transform(articulated):
    """The double-write guard. `sim_runtime` skips hubs carrying this flag; without
    it the kinematic write and the solver fight over one transform every frame."""
    stage, path = articulated
    attr = stage.GetPrimAtPath(path + "/Hub").GetAttribute("st:articulated")
    assert attr and attr.IsValid() and attr.Get() is True


def test_a_plain_proxy_carries_no_articulation_and_no_flag():
    """Articulation is opt-in (`turbines_articulated`). Every KPI run recorded so
    far used the kinematic proxy, so the default must be untouched — a driven rotor
    is a different scene, not a free upgrade to a pinned measurement."""
    stage = Usd.Stage.CreateInMemory()
    from pxr import UsdShade

    looks = {"turbine": UsdShade.Material.Define(stage, "/World/Looks/turbine")}
    path = "/World/Turbines/turbine_0"
    farm_builder._build_turbine(stage, path, SPEC, 0.0, looks)

    assert not stage.GetPrimAtPath(path).HasAPI(UsdPhysics.ArticulationRootAPI)
    assert not stage.GetPrimAtPath(path + "/Hub").HasAPI(UsdPhysics.RigidBodyAPI)
    assert not stage.GetPrimAtPath(path + "/RotorJoint")
    flag = stage.GetPrimAtPath(path + "/Hub").GetAttribute("st:articulated")
    assert not (flag and flag.IsValid())


def test_blades_remain_colliders_on_one_rotor_body(articulated):
    """Three blades as three rigid bodies would need three more joints and cannot
    move relative to each other anyway. One body, three colliders."""
    stage, path = articulated
    for b in range(3):
        blade = stage.GetPrimAtPath(f"{path}/Hub/Blade_{b}")
        assert blade.HasAPI(UsdPhysics.CollisionAPI)
        assert not blade.HasAPI(UsdPhysics.RigidBodyAPI)
