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


def build_quadcopter(stage, path: str, arm: float | None = None, spec=None) -> RobotParts:
    """An inspection quadcopter at `path`, origin at its centre of mass.

    Dimensions come from a `fleet_specs.DroneSpec` — a named real platform —
    rather than from a bare `arm` constant. The old default (`arm=0.34`) built a
    0.96 m motor-to-motor diagonal while the docstring claimed "~0.9 m", and
    neither figure was tied to a machine anyone had chosen. Default is now
    `DJI_M350`: 0.895 m diagonal, 0.533 m props, i.e. the class that actually
    carries a radiometric thermal payload for utility PV inspection.

    `arm` is still accepted so existing callers keep working, and it overrides the
    spec's own geometry when given.

    ⚠ Motors sit on the DIAGONAL of an X-frame, so `diagonal = 2 * arm * sqrt(2)`.
    Treating the diagonal as `2 * arm` builds a machine 41% too large — the exact
    trap `DroneSpec.arm_m` exists to close.

    The camera is mounted by the caller *below* this body so the airframe never
    occludes the nadir view (a bug that previously produced all-black frames).
    """
    from pxr import UsdGeom

    from solar_twin.world.fleet_specs import DJI_M350

    spec = spec or DJI_M350
    arm = float(arm) if arm is not None else spec.arm_m
    rotor_r = spec.rotor_diameter_m / 2.0

    UsdGeom.Xform.Define(stage, path)
    parts = RobotParts()

    # Fuselage: the platform's own body box, longer than wide so heading is
    # readable in frame.
    _box(
        stage,
        f"{path}/Body",
        (spec.body_w_m, spec.body_l_m, spec.body_h_m * 0.45),
        (0.0, 0.0, 0.0),
        _CARBON,
    )
    # Canopy, offset forward (+Y = nose) so orientation is visually unambiguous.
    _box(
        stage,
        f"{path}/Canopy",
        (spec.body_w_m * 0.60, spec.body_l_m * 0.42, spec.body_h_m * 0.32),
        (0.0, spec.body_l_m * 0.26, spec.body_h_m * 0.28),
        _LENS,
    )
    # A hazard-orange tail flash: makes yaw legible in an overview render.
    _box(
        stage,
        f"{path}/Tail",
        (spec.body_w_m * 0.26, spec.body_l_m * 0.30, spec.body_h_m * 0.14),
        (0.0, -spec.body_l_m * 0.50, spec.body_h_m * 0.09),
        _HAZARD,
    )

    for i, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        ax, ay = sx * arm * 0.62, sy * arm * 0.62
        # Arm: a thin boom out to the motor.
        _box(
            stage,
            f"{path}/Arm_{i}",
            (arm * 0.16, arm * 0.95, arm * 0.10),
            (ax * 0.72, ay * 0.72, 0.0),
            _CARBON,
            rotate=(0.0, 0.0, -45.0 if sx * sy > 0 else 45.0),
        )
        mx, my = sx * arm, sy * arm
        _cyl(stage, f"{path}/Motor_{i}", arm * 0.10, arm * 0.16, (mx, my, arm * 0.11), _ALLOY)
        # Rotor disc: a thin cylinder, spun by the runtime, at the platform's real
        # propeller radius. A disc rather than modelled blades because at
        # inspection standoff a spinning disc is what a camera actually resolves,
        # and it costs 1 prim instead of 2 per rotor.
        rotor = f"{path}/Rotor_{i}"
        _cyl(stage, rotor, rotor_r, 0.006, (mx, my, arm * 0.21), _ROTOR)
        parts.rotors.append(rotor)
        # Landing skid under each motor.
        _cyl(stage, f"{path}/Skid_{i}", arm * 0.04, arm * 0.41, (mx, my, -arm * 0.24), _ALLOY)

    # Gimbal yoke below the belly; the caller parents the camera near here.
    gimbal = f"{path}/Gimbal"
    _cyl(stage, gimbal, arm * 0.14, arm * 0.13, (0.0, arm * 0.12, -arm * 0.31), _CARBON)
    parts.gimbal = gimbal
    return parts


def build_ugv(stage, path: str, spec=None) -> RobotParts:
    """A four-wheeled ground rover at `path`, origin at ground level.

    Dimensions come from a `fleet_specs.RoverSpec`; the default is a Husky
    A200-class platform (990 x 670 x 390 mm body, 330 mm wheels) — a real
    mid-size inspection rover, and to within 3 cm what this function already
    built from literals.

    The mast is the platform's *payload*, sized by `spec.mast_height_m` and
    reported separately from body height. That split matters: a rover cannot be
    "0.4-0.5 m tall including sensor mast" when its wheels and deck alone reach
    0.39 m, so body height and total height have to be two numbers rather than
    one contested one.
    """
    from pxr import UsdGeom

    from solar_twin.world.fleet_specs import CLEARPATH_HUSKY

    spec = spec or CLEARPATH_HUSKY
    UsdGeom.Xform.Define(stage, path)
    parts = RobotParts()

    wheel_r = spec.wheel_diameter_m / 2.0
    # The body sits on the wheels: deck centre is one wheel radius up, plus enough
    # clearance that the chassis box does not intersect the tyres.
    chassis_h = spec.body_h_m - wheel_r
    deck_z = wheel_r + chassis_h / 2.0

    _box(stage, f"{path}/Chassis", (spec.body_w_m, spec.body_l_m, chassis_h), (0.0, 0.0, deck_z), _CARBON)
    # Bonnet slope + hazard strip so heading and vehicle-ness are obvious.
    _box(
        stage,
        f"{path}/Bonnet",
        (spec.body_w_m * 0.88, spec.body_l_m * 0.26, chassis_h * 0.50),
        (0.0, spec.body_l_m * 0.39, deck_z + chassis_h * 0.70),
        _ALLOY,
    )
    _box(
        stage,
        f"{path}/Beacon",
        (0.10, 0.10, 0.07),
        (0.0, -spec.body_l_m * 0.33, deck_z + chassis_h * 0.80),
        _HAZARD,
    )

    # Wheels are cylinders about local X (the axle), placed at the corners. Their
    # OUTER faces sit flush with the body sides, because a platform's published
    # width is its overall width — wheels included. Offsetting them by a fraction
    # of the body width instead pushed the measured envelope to 0.844 m against a
    # specified 0.670 m, i.e. 26% too wide: exactly the eyeballed-versus-measured
    # error this module now exists to prevent.
    wheel_t = min(0.12, spec.body_w_m * 0.18)
    for i, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        wheel = f"{path}/Wheel_{i}"
        _cyl(
            stage,
            wheel,
            wheel_r,
            wheel_t,
            (sx * (spec.body_w_m - wheel_t) / 2.0, sy * spec.body_l_m * 0.33, wheel_r),
            _TYRE,
            axis="X",
        )
        parts.wheels.append(wheel)

    # Sensor mast + head: the payload. Stands clear of the panels it drives
    # between, and its height is a spec field rather than a literal.
    mast_h = spec.mast_height_m
    _cyl(
        stage,
        f"{path}/Mast",
        0.035,
        mast_h * 0.83,
        (0.0, -0.10, spec.body_h_m + mast_h * 0.41),
        _ALLOY,
    )
    head = f"{path}/SensorHead"
    head_h = 0.12
    # `total_height_m` is the TOP of the machine, so the head hangs just below it
    # rather than being centred on it — otherwise the measured envelope overshoots
    # the stated height by half a sensor head.
    _box(
        stage,
        head,
        (0.20, 0.12, head_h),
        (0.0, -0.10, spec.body_h_m + mast_h - head_h / 2.0),
        _LENS,
    )
    parts.gimbal = head
    return parts
