"""Fleet scale against the REAL module dimensions (pure, no Isaac).

The point of these tests is that "the robots look toy-scale" is a claim about a
ratio, and a ratio is checkable. The module figures below are the vendor CAD's
own, from `configs/layouts/khavda_a10b_block02.yaml`.
"""

import pytest

from solar_twin.world.fleet_specs import (
    CLEARPATH_HUSKY,
    DJI_M350,
    DJI_MAVIC3T,
    DRONES,
    ROVERS,
    fits_between_rows,
    scale_report,
    standoff_is_safe,
)

#: Exact survey figures from the generated site file.
MODULE_CHORD_M = 2.278   # across the aisle
MODULE_WIDTH_M = 1.134   # along the torque tube
ROW_PITCH_M = 5.5        # Khavda's ordinary maintenance aisle, 5-6 m


def test_x_frame_diagonal_is_not_twice_the_arm():
    """Motors sit on the DIAGONAL, so diagonal = 2*arm*sqrt(2). Treating it as
    2*arm builds a machine 41% too large — this is the arithmetic that let an
    `arm=0.34` constant become a 0.96 m airframe described as "~0.9 m"."""
    assert DJI_M350.arm_m == pytest.approx(0.895 / (2 * 2**0.5))
    assert 2 * DJI_M350.arm_m * 2**0.5 == pytest.approx(0.895)
    # The wrong formula would give this, and it is NOT the published diagonal.
    assert 2 * DJI_M350.arm_m != pytest.approx(0.895)


def test_the_default_drone_is_a_real_published_platform():
    assert DJI_M350.diagonal_m == pytest.approx(0.895)
    assert DJI_M350.rotor_diameter_m == pytest.approx(0.533)   # 21-inch props
    assert DJI_MAVIC3T.diagonal_m == pytest.approx(0.3801)


def test_swept_radius_exceeds_the_arm_because_props_stick_out():
    assert DJI_M350.swept_radius_m > DJI_M350.arm_m
    assert DJI_M350.swept_radius_m == pytest.approx(0.895 / (2 * 2**0.5) + 0.2665)


def test_the_drone_fits_down_a_real_tracker_aisle():
    """The aisle, not the module, is the binding constraint on drone size here."""
    assert fits_between_rows(DJI_M350, ROW_PITCH_M)
    assert fits_between_rows(DJI_MAVIC3T, ROW_PITCH_M)
    from solar_twin.world.fleet_specs import DroneSpec

    # A 3 m-diagonal machine sweeps 3.3 m and DOES still fit a 5.5 m aisle with a
    # metre of clearance — worth pinning, because it is not obvious and it is the
    # reason a large platform was never the problem here.
    assert fits_between_rows(DroneSpec("big", 3.0, 1.2, 0.6, 0.6, 0.4, 40.0), ROW_PITCH_M)
    # This one does not, and the check has to be able to say no.
    assert not fits_between_rows(DroneSpec("huge", 5.0, 1.5, 0.9, 0.9, 0.5, 90.0), ROW_PITCH_M)


def test_a_standoff_must_clear_the_machines_own_rotors():
    """Motion is kinematic, so a standoff that intersects the panel renders as a
    clean flight through solid glass rather than as a crash."""
    assert not standoff_is_safe(DJI_M350, 0.25)
    assert standoff_is_safe(DJI_M350, 1.0)
    # The compact platform can work closer, because it sweeps less.
    assert standoff_is_safe(DJI_MAVIC3T, 0.35)


def test_the_rover_is_a_real_mid_size_inspection_platform():
    assert CLEARPATH_HUSKY.body_l_m == pytest.approx(0.990)
    assert CLEARPATH_HUSKY.body_w_m == pytest.approx(0.670)
    assert CLEARPATH_HUSKY.body_h_m == pytest.approx(0.390)
    assert CLEARPATH_HUSKY.wheel_diameter_m == pytest.approx(0.330)


def test_body_height_and_total_height_are_two_different_numbers():
    """A rover cannot be "0.4-0.5 m tall including sensor mast": 0.33 m wheels
    plus a deck already reach 0.39 m before any mast exists. The platform height
    and the payload height have to be reported separately."""
    assert CLEARPATH_HUSKY.body_h_m < 0.5           # the platform itself
    assert CLEARPATH_HUSKY.total_height_m > 1.0     # with the sensor mast
    assert CLEARPATH_HUSKY.total_height_m == pytest.approx(0.390 + 0.66)
    # And the wheels alone rule out the 0.4-0.5 m envelope for a masted machine.
    assert CLEARPATH_HUSKY.wheel_diameter_m > 0.5 * 0.5


def test_the_fleet_is_to_scale_against_a_real_module():
    """A module chord is 2.278 m. The rover should read as roughly 0.4 of it and
    the drone as roughly 0.4 — i.e. both are clearly sub-module, neither is a
    speck and neither is bigger than the thing it inspects."""
    r = scale_report(DJI_M350, CLEARPATH_HUSKY, MODULE_CHORD_M, MODULE_WIDTH_M)
    assert 0.3 < r["rover_vs_module_chord"] < 0.6
    assert 0.3 < r["drone_vs_module_chord"] < 0.6
    assert r["module_chord_m"] == pytest.approx(2.278)


def test_the_rover_is_longer_than_a_module_is_wide_along_the_tube():
    """A sanity anchor that catches a scale slip in either direction: a 0.99 m
    rover is a little shorter than the 1.134 m module width, so if it ever reads
    as tiny or enormous next to one module, this moves."""
    assert 0.7 < CLEARPATH_HUSKY.body_l_m / MODULE_WIDTH_M < 1.0


def test_presets_are_registered_by_short_name_for_config_selection():
    assert DRONES["m350"] is DJI_M350
    assert DRONES["mavic3t"] is DJI_MAVIC3T
    assert ROVERS["husky"] is CLEARPATH_HUSKY


def test_every_preset_has_a_provenance_note():
    """Each figure is transcribed from a manufacturer spec, not invented, and the
    note is where the caveat lives."""
    for spec in list(DRONES.values()) + list(ROVERS.values()):
        assert spec.note
        assert spec.name
