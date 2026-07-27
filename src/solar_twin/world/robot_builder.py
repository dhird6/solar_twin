"""Recognisable robot geometry for the fleet (Isaac-bound: pxr).

Replaces the placeholder marker cubes with a quadcopter and a ground rover that
actually read as vehicles in a rendered frame. Authored procedurally, so it stays
reproducible from a script + config with no GUI step and no asset download — this
box has no Isaac robot asset pack and no configured asset root.

Scope, stated plainly (`NFR-07`): this is **appearance and articulation, not
dynamics**. Rotors spin and wheels roll as visual proxies driven by distance
travelled; there is no thrust, no drag, no suspension and no collision response.
Real flight dynamics is `FR-06` (Pegasus/PX4), which is a separate piece of work
and currently blocked on Pegasus v5.1.0 vs the installed Isaac 6.0.1.

Returned prim paths are the articulated parts the runtime animates each tick:
`rotors` spin continuously while airborne, `wheels` roll with ground distance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Muted, physically plausible colours. displayColor keeps this material-free so it
# renders identically whether or not the stage carries a material library.
_CARBON = (0.09, 0.09, 0.10)
_ALLOY = (0.62, 0.63, 0.66)
_ROTOR = (0.16, 0.16, 0.18)
_HAZARD = (0.85, 0.42, 0.05)
_LENS = (0.03, 0.05, 0.09)
_TYRE = (0.06, 0.06, 0.07)


@dataclass
class RobotParts:
    """Articulated sub-prims the runtime animates. Paths, not prim handles, so the
    runtime can resolve them lazily and this module stays import-light."""

    rotors: list[str] = field(default_factory=list)
    wheels: list[str] = field(default_factory=list)
    #: Yaw-able sensor head (ground bot) or gimbal (drone), if present.
    gimbal: str | None = None


def _box(stage, path, size, translate, color, rotate=None):
    from pxr import Gf, UsdGeom

    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    api = UsdGeom.XformCommonAPI(cube)
    api.SetTranslate(Gf.Vec3d(*translate))
    api.SetScale(Gf.Vec3f(*size))
    if rotate is not None:
        api.SetRotate(rotate, UsdGeom.XformCommonAPI.RotationOrderXYZ)
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    return cube


def _cyl(stage, path, radius, height, translate, color, axis="Z", rotate=None):
    from pxr import Gf, UsdGeom

    cyl = UsdGeom.Cylinder.Define(stage, path)
    cyl.CreateRadiusAttr(float(radius))
    cyl.CreateHeightAttr(float(height))
    cyl.CreateAxisAttr(axis)
    api = UsdGeom.XformCommonAPI(cyl)
    api.SetTranslate(Gf.Vec3d(*translate))
    if rotate is not None:
        api.SetRotate(rotate, UsdGeom.XformCommonAPI.RotationOrderXYZ)
    cyl.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    return cyl


def build_quadcopter(stage, path: str, arm: float = 0.34) -> RobotParts:
    """An inspection quadcopter at `path`, origin at its centre of mass.

    `arm` is the motor-to-centre distance, so overall span is ~2*arm plus prop
    diameter — 0.34 m gives a ~0.9 m machine, the right class for close panel
    inspection. The camera is mounted by the caller *below* this body so the
    airframe never occludes the nadir view (a bug that previously produced
    all-black frames).
    """
    from pxr import UsdGeom

    UsdGeom.Xform.Define(stage, path)
    parts = RobotParts()

    # Fuselage: a flattened body, longer than wide so heading is readable in frame.
    _box(stage, f"{path}/Body", (0.26, 0.34, 0.10), (0.0, 0.0, 0.0), _CARBON)
    # Canopy, offset forward (+Y = nose) so orientation is visually unambiguous.
    _box(stage, f"{path}/Canopy", (0.16, 0.14, 0.07), (0.0, 0.09, 0.06), _LENS)
    # A hazard-orange tail flash: makes yaw legible in an overview render.
    _box(stage, f"{path}/Tail", (0.07, 0.10, 0.03), (0.0, -0.17, 0.02), _HAZARD)

    for i, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        ax, ay = sx * arm * 0.62, sy * arm * 0.62
        # Arm: a thin boom out to the motor.
        _box(
            stage,
            f"{path}/Arm_{i}",
            (0.055, arm * 0.95, 0.035),
            (ax * 0.72, ay * 0.72, 0.0),
            _CARBON,
            rotate=(0.0, 0.0, -45.0 if sx * sy > 0 else 45.0),
        )
        mx, my = sx * arm, sy * arm
        _cyl(stage, f"{path}/Motor_{i}", 0.032, 0.05, (mx, my, 0.035), _ALLOY)
        # Rotor disc: a thin cylinder, spun by the runtime. A disc rather than
        # modelled blades because at inspection standoff a spinning disc is what a
        # camera actually resolves, and it costs 1 prim instead of 2 per rotor.
        rotor = f"{path}/Rotor_{i}"
        _cyl(stage, rotor, 0.115, 0.006, (mx, my, 0.068), _ROTOR)
        parts.rotors.append(rotor)
        # Landing skid under each motor.
        _cyl(stage, f"{path}/Skid_{i}", 0.012, 0.13, (mx, my, -0.075), _ALLOY)

    # Gimbal yoke below the belly; the caller parents the camera near here.
    gimbal = f"{path}/Gimbal"
    _cyl(stage, gimbal, 0.045, 0.04, (0.0, 0.04, -0.10), _CARBON)
    parts.gimbal = gimbal
    return parts


def build_ugv(stage, path: str) -> RobotParts:
    """A four-wheeled ground rover at `path`, origin at ground level.

    Sized like a real inspection UGV (~1.0 x 0.7 m, 0.35 m wheels) so it is to
    scale against a 2.278 m module and reads correctly in an overview render.
    """
    from pxr import UsdGeom

    UsdGeom.Xform.Define(stage, path)
    parts = RobotParts()

    wheel_r = 0.17
    deck_z = wheel_r + 0.12

    _box(stage, f"{path}/Chassis", (0.66, 1.02, 0.20), (0.0, 0.0, deck_z), _CARBON)
    # Bonnet slope + hazard strip so heading and vehicle-ness are obvious.
    _box(stage, f"{path}/Bonnet", (0.58, 0.26, 0.10), (0.0, 0.40, deck_z + 0.14), _ALLOY)
    _box(stage, f"{path}/Beacon", (0.10, 0.10, 0.07), (0.0, -0.34, deck_z + 0.16), _HAZARD)

    for i, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        wheel = f"{path}/Wheel_{i}"
        # Wheels are cylinders about local X (the axle), placed at the corners.
        _cyl(
            stage,
            wheel,
            wheel_r,
            0.12,
            (sx * 0.36, sy * 0.34, wheel_r),
            _TYRE,
            axis="X",
        )
        parts.wheels.append(wheel)

    # Sensor mast + head: gives the bot a plausible payload and a place to look
    # from, and stands clear of the panels it drives between.
    _cyl(stage, f"{path}/Mast", 0.035, 0.55, (0.0, -0.10, deck_z + 0.38), _ALLOY)
    head = f"{path}/SensorHead"
    _box(stage, head, (0.20, 0.12, 0.12), (0.0, -0.10, deck_z + 0.70), _LENS)
    parts.gimbal = head
    return parts
