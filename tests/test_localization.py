"""GNSS/RTK localization — and the verdict-misattribution KPI it exists for."""

from __future__ import annotations

import math

import pytest

from solar_twin.control.localization import (
    HORIZONTAL_SIGMA_M,
    FixMode,
    GnssReceiver,
    ImuSpec,
    misattribution_by_mode,
    misattribution_rate,
)

#: Khavda's real module pitch along the torque tube (from the vendor CAD).
PITCH = 1.14804


# --------------------------------------------------------------------------- #
# ⭐ The finding: at this pitch, RTK-fixed is not optional.
# --------------------------------------------------------------------------- #


def test_rtk_fixed_essentially_never_mislabels_a_panel():
    """2 cm against a 1.148 m pitch — the verdict lands on the right module."""
    assert misattribution_rate(HORIZONTAL_SIGMA_M[FixMode.RTK_FIXED], PITCH) < 0.001


def test_a_float_fix_mislabels_a_quarter_of_verdicts():
    """⭐ Why this module exists. Perception can be perfect and the inspection still
    useless: the fault is real, the diagnosis right, and the crew sent to the wrong
    module. Measured 25.1%."""
    rate = misattribution_rate(HORIZONTAL_SIGMA_M[FixMode.RTK_FLOAT], PITCH)
    assert 0.15 < rate < 0.35, f"expected ~25%, got {rate:.1%}"


def test_plain_gnss_is_worse_than_useless_at_this_pitch():
    assert misattribution_rate(HORIZONTAL_SIGMA_M[FixMode.SINGLE], PITCH) > 0.75


def test_misattribution_is_monotonic_in_fix_quality():
    order = [FixMode.RTK_FIXED, FixMode.RTK_FLOAT, FixMode.DGPS, FixMode.SINGLE]
    rates = [misattribution_rate(HORIZONTAL_SIGMA_M[m], PITCH) for m in order]
    assert rates == sorted(rates), f"a worse fix must not mislabel less: {rates}"


def test_a_wider_pitch_forgives_more_error():
    """The chord across the aisle is 2.278 m — twice the along-tube pitch, so the
    same fix error is roughly half as likely to cross a boundary."""
    tight = misattribution_rate(0.5, PITCH)
    wide = misattribution_rate(0.5, 2.278)
    assert wide < tight


def test_no_fix_mislabels_everything():
    assert misattribution_rate(float("inf"), PITCH) == 1.0


def test_zero_error_never_mislabels():
    assert misattribution_rate(0.0, PITCH) == 0.0


def test_a_nonpositive_pitch_is_refused():
    with pytest.raises(ValueError):
        misattribution_rate(0.5, 0.0)


def test_misattribution_matches_the_analytic_form():
    """Sampled, but it must agree with erfc(pitch / (2*sqrt(2)*sigma))."""
    sigma = 0.5
    expected = math.erfc(PITCH / (2.0 * math.sqrt(2.0) * sigma))
    assert misattribution_rate(sigma, PITCH, samples=60000) == pytest.approx(
        expected, abs=0.01
    )


def test_every_fix_mode_is_scored():
    scored = misattribution_by_mode(PITCH)
    assert set(scored) == set(FixMode)


# --------------------------------------------------------------------------- #
# The receiver model.
# --------------------------------------------------------------------------- #


def test_fixes_are_deterministic_for_a_seed():
    """A KPI measured with localisation error on must still be reproducible."""
    a = GnssReceiver(seed=7).fix((10.0, 20.0, 5.0))
    b = GnssReceiver(seed=7).fix((10.0, 20.0, 5.0))
    assert a.believed == b.believed


def test_different_seeds_give_different_fixes():
    a = GnssReceiver(seed=1).fix((10.0, 20.0, 5.0))
    b = GnssReceiver(seed=2).fix((10.0, 20.0, 5.0))
    assert a.believed != b.believed


def test_a_better_fix_mode_lands_closer_to_truth():
    truth = (100.0, 200.0, 12.0)
    def mean_err(mode):
        r = GnssReceiver(mode=mode, seed=3)
        return sum(r.fix(truth).error_m for _ in range(200)) / 200
    assert mean_err(FixMode.RTK_FIXED) < mean_err(FixMode.RTK_FLOAT) < mean_err(FixMode.SINGLE)


def test_vertical_error_exceeds_horizontal():
    """Satellites are only ever above you, so the vertical geometry is worse."""
    truth = (0.0, 0.0, 0.0)
    r = GnssReceiver(mode=FixMode.SINGLE, seed=5)
    fixes = [r.fix(truth) for _ in range(400)]
    h = sum(math.dist(f.believed[:2], (0.0, 0.0)) for f in fixes) / len(fixes)
    v = sum(abs(f.believed[2]) for f in fixes) / len(fixes)
    assert v > h * 0.9, "vertical should not be better than horizontal"


def test_an_outage_drifts_further_the_longer_it_lasts():
    """⭐ The reason degradation matters more than a single sigma."""
    r = GnssReceiver(mode=FixMode.NONE, seed=11, imu=ImuSpec(drift_m_per_s=0.5))
    early = r.fix((0.0, 0.0, 0.0), dt_s=1.0).sigma_m
    for _ in range(9):
        r.fix((0.0, 0.0, 0.0), dt_s=1.0)
    late = r.fix((0.0, 0.0, 0.0), dt_s=1.0).sigma_m
    assert late > early * 5


def test_a_regained_fix_resets_the_drift():
    r = GnssReceiver(mode=FixMode.NONE, seed=2)
    for _ in range(5):
        r.fix((0.0, 0.0, 0.0))
    r.nominal_mode = FixMode.RTK_FIXED
    assert r.fix((0.0, 0.0, 0.0)).sigma_m == HORIZONTAL_SIGMA_M[FixMode.RTK_FIXED]


def test_degradation_actually_degrades_some_fixes():
    r = GnssReceiver(mode=FixMode.RTK_FIXED, seed=4, degrade_probability=0.5)
    modes = {r.fix((0.0, 0.0, 0.0)).mode for _ in range(100)}
    assert FixMode.RTK_FLOAT in modes and FixMode.RTK_FIXED in modes


def test_no_degradation_by_default():
    r = GnssReceiver(mode=FixMode.RTK_FIXED, seed=4)
    assert {r.fix((0.0, 0.0, 0.0)).mode for _ in range(50)} == {FixMode.RTK_FIXED}


# --------------------------------------------------------------------------- #
# Record shape — the caveat must survive.
# --------------------------------------------------------------------------- #


def test_summary_carries_the_modelled_caveat():
    r = GnssReceiver(seed=1)
    r.fix((0.0, 0.0, 0.0))
    s = r.summary()
    assert "MODELLED" in s["caveat"]
    assert "UNDER-stated" in s["caveat"], (
        "the summary must say real error is time-correlated, so this UNDER-states "
        "consecutive-panel misattribution"
    )


def test_summary_on_an_unused_receiver_does_not_divide_by_zero():
    assert GnssReceiver().summary()["n_fixes"] == 0


def test_summary_serialises():
    import json

    r = GnssReceiver(seed=1, degrade_probability=0.3)
    for _ in range(10):
        r.fix((1.0, 2.0, 3.0))
    json.loads(json.dumps(r.summary()))
    assert r.summary()["n_fixes"] == 10
