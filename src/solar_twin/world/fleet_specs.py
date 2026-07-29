"""Real-world dimensions for the fleet, and the checks that keep them honest.

Pure data + arithmetic, no Isaac — so `robot_builder.py` builds geometry FROM a
spec instead of from literals scattered through its body, and the scale can be
asserted against the real module dimensions in a test that runs off the Spark.

**Why a named reference machine rather than a plausible size.** "Looks about
right next to a panel" is not checkable, and it was how the drone ended up at a
size nobody had chosen: the airframe was built from an `arm=0.34` constant, which
makes a 0.96 m motor-to-motor diagonal — an M350-class machine — while the
docstring called it "~0.9 m" and nothing tied either number to a real product.
Each preset here carries published figures for an actual platform, so the
question "is this the right size?" becomes "is this the right machine?", which
someone can answer.

⚠ Figures are manufacturer-published overall dimensions, transcribed. They are
NOT a substitute for a datasheet at integration time, and the rover is a
*class* choice rather than a procurement decision.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DroneSpec:
    """A multirotor, by its published overall dimensions.

    `diagonal_m` is motor-to-motor (the figure manufacturers quote and the one
    that matters for fitting between tracker rows); `rotor_diameter_m` is the
    propeller, which is what actually sets the machine's swept footprint and so
    the clearance it needs from a module.
    """

    name: str
    diagonal_m: float
    rotor_diameter_m: float
    body_l_m: float
    body_w_m: float
    body_h_m: float
    mass_kg: float
    note: str = ""

    @property
    def arm_m(self) -> float:
        """Motor-to-centre distance. Motors sit on the diagonal, so the diagonal
        is `2 * arm * sqrt(2)` for an X-frame — NOT `2 * arm`, which is the
        error that would make the airframe 41% too big."""
        return self.diagonal_m / (2.0 * 2.0**0.5)

    @property
    def swept_radius_m(self) -> float:
        """Half-width of the whole spinning machine: arm plus a rotor radius.
        This, not the body, is what must clear a panel."""
        return self.arm_m + self.rotor_diameter_m / 2.0


@dataclass(frozen=True)
class RoverSpec:
    """A ground robot. Body dimensions are the platform's own; the mast is a
    payload choice and is therefore carried separately — see `total_height_m`.
    """

    name: str
    body_l_m: float
    body_w_m: float
    body_h_m: float
    wheel_diameter_m: float
    mast_height_m: float
    mass_kg: float
    note: str = ""

    @property
    def total_height_m(self) -> float:
        """Body plus mast plus sensor head. Reported separately from `body_h_m`
        because a spec of "0.4-0.5 m tall including sensor mast" is not
        satisfiable: 0.33 m wheels and a deck already reach 0.39 m before any
        mast exists. Body height and payload height are two numbers, and
        conflating them is what makes the requirement look self-contradictory."""
        return self.body_h_m + self.mast_height_m


#: Utility-scale solar O&M flies this class: a large multirotor that can carry a
#: radiometric thermal payload (DJI H30T / Zenmuse XT-class) for IR module
#: inspection. Diagonal 895 mm, 21-inch props.
DJI_M350 = DroneSpec(
    name="dji-m350-class",
    diagonal_m=0.895,
    rotor_diameter_m=0.533,
    body_l_m=0.43,
    body_w_m=0.42,
    body_h_m=0.43,
    mass_kg=6.47,
    note="carries a radiometric thermal payload — the standard utility PV IR platform",
)

#: A small folding platform. Included because it is a real alternative and the
#: honest answer to "0.4-0.6 m diagonal" — but note it carries a much lighter
#: thermal sensor, so choosing it is a payload decision, not just a size one.
DJI_MAVIC3T = DroneSpec(
    name="dji-mavic3t-class",
    diagonal_m=0.3801,
    rotor_diameter_m=0.239,
    body_l_m=0.221,
    body_w_m=0.0965,
    body_h_m=0.0906,
    mass_kg=0.92,
    note="compact; lighter non-radiometric-class thermal sensor than an M350 payload",
)

#: Mid-size differential-drive research/inspection rover. Real published figures
#: (990 x 670 x 390 mm, 330 mm wheels) and squarely the "mid-size inspection
#: rover" class — it is also, to within 3 cm, what the previous hand-built
#: geometry already was.
CLEARPATH_HUSKY = RoverSpec(
    name="husky-a200-class",
    body_l_m=0.990,
    body_w_m=0.670,
    body_h_m=0.390,
    wheel_diameter_m=0.330,
    mast_height_m=0.66,
    mass_kg=50.0,
    note="mast height is a payload choice, not a platform dimension",
)

DRONES = {"m350": DJI_M350, "mavic3t": DJI_MAVIC3T}
ROVERS = {"husky": CLEARPATH_HUSKY}


def fits_between_rows(spec: DroneSpec, row_pitch_m: float, clearance_m: float = 0.5) -> bool:
    """Can this machine fly down the aisle between two tracker rows?

    The aisle, not the module, is the binding constraint on drone size at this
    site: Khavda's rows are pitched ~5-6 m apart, so an M350 fits easily, but the
    check has to exist because a larger platform would not and the failure would
    show up as a collision nobody modelled (motion is kinematic — see
    `robot_builder`'s scope note — so nothing would stop it).
    """
    return 2.0 * spec.swept_radius_m + 2.0 * clearance_m <= row_pitch_m


def standoff_is_safe(spec: DroneSpec, standoff_m: float, margin_m: float = 0.15) -> bool:
    """True when a nadir inspection standoff clears the machine's own rotors.

    A standoff is measured from the PANEL to the drone origin, and the drone is
    not a point: an M350 sweeps 0.72 m in radius. Hovering 0.5 m over a module
    with rotors 0.27 m below the origin is a strike, and because the fleet is
    kinematic it would render as a clean flight through solid glass.
    """
    return standoff_m >= spec.body_h_m / 2.0 + spec.rotor_diameter_m / 2.0 + margin_m


def scale_report(
    drone: DroneSpec, rover: RoverSpec, module_chord_m: float, module_width_m: float
) -> dict:
    """Fleet size expressed as a fraction of a real module, for the build log.

    The point of printing this is that "the robots look toy-scale" is a claim
    about a ratio, and the ratio is knowable: a 0.99 m rover beside a 2.278 m
    module chord should read as roughly 0.43 of it. If a future change breaks the
    scale, this line moves.
    """
    return {
        "drone": drone.name,
        "drone_diagonal_m": round(drone.diagonal_m, 3),
        "drone_vs_module_chord": round(drone.diagonal_m / module_chord_m, 3),
        "rover": rover.name,
        "rover_length_m": round(rover.body_l_m, 3),
        "rover_vs_module_chord": round(rover.body_l_m / module_chord_m, 3),
        "rover_total_height_m": round(rover.total_height_m, 3),
        "module_chord_m": round(module_chord_m, 3),
        "module_width_m": round(module_width_m, 3),
    }
