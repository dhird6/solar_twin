"""`run.py`'s sortie layer — and above all, that OFF is the identity.

Isaac-free. `run.py` imports without Isaac (its Isaac-bound backends are built
lazily), which is what makes this testable at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from solar_twin.run import _plan_sorties


@dataclass
class _T:
    panel_id: str
    position: tuple


def _targets(n: int = 112):
    return [_T(f"R00-C{i:03d}", (5.0 + i * 1.15, 60.0, 3.0)) for i in range(n)]


# --------------------------------------------------------------------------- #
# ⭐ The acceptance test, same as grid_dispatch's: a disabled layer cannot
# perturb a recorded KPI. Not "an equivalent list" — the identical object.
# --------------------------------------------------------------------------- #


def test_mode_off_returns_the_very_same_list_object():
    targets = _targets()
    out, plan = _plan_sorties({"sorties": {"enabled": True}}, targets, "off")
    assert out is targets
    assert plan is None


def test_absent_config_returns_the_very_same_list_object():
    targets = _targets()
    out, plan = _plan_sorties({}, targets, "report")
    assert out is targets
    assert plan is None


def test_report_mode_does_not_change_which_panels_are_visited():
    """Report is analysis, not enforcement — the mission must be unaffected."""
    targets = _targets()
    out, plan = _plan_sorties({"sorties": {"enabled": True}}, targets, "report")
    assert [t.panel_id for t in out] == [t.panel_id for t in targets]
    assert plan is not None and len(plan.sorties) >= 1


# --------------------------------------------------------------------------- #
# Enforce changes the mission, which is why it is opt-in.
# --------------------------------------------------------------------------- #


def test_enforce_cuts_the_sweep_to_one_sortie():
    targets = _targets()
    out, plan = _plan_sorties({"sorties": {"enabled": True}}, targets, "enforce")
    assert len(out) < len(targets)
    assert [t.panel_id for t in out] == list(plan.sorties[0].target_ids)


def test_enforce_preserves_target_order():
    targets = _targets()
    out, _ = _plan_sorties({"sorties": {"enabled": True}}, targets, "enforce")
    ids = [t.panel_id for t in out]
    assert ids == sorted(ids), "enforce must filter, never reorder"


def test_enforce_on_a_sweep_that_already_fits_changes_nothing():
    targets = _targets(5)
    out, _ = _plan_sorties({"sorties": {"enabled": True}}, targets, "enforce")
    assert len(out) == 5


# --------------------------------------------------------------------------- #
# Config plumbing.
# --------------------------------------------------------------------------- #


def test_always_report_enables_without_enabled():
    targets = _targets(10)
    _, plan = _plan_sorties({"sorties": {"always_report": True}}, targets, "report")
    assert plan is not None


def test_an_unknown_platform_is_refused_with_the_known_list():
    with pytest.raises(ValueError, match="unknown"):
        _plan_sorties(
            {"sorties": {"enabled": True, "platform": "nope"}}, _targets(3), "report"
        )


def test_platform_choice_changes_the_plan():
    """⭐ The rover's 3 h beats the drone's 55 min on a dwell-dominated sweep."""
    targets = _targets()
    _, drone = _plan_sorties({"sorties": {"enabled": True, "platform": "m350"}},
                             targets, "report")
    _, rover = _plan_sorties({"sorties": {"enabled": True, "platform": "husky"}},
                             targets, "report")
    assert len(rover.sorties) < len(drone.sorties)


def test_dwell_dominates_the_drone_sortie():
    """Documents WHY the drone needs two trips: dwell, not travel.

    112 panels x 20 s is 37 min of dwell against ~28 min of budget, while the whole
    table is only ~130 m of flying. Halving dwell must therefore cut the trips —
    if it ever does not, the cost model has drifted to travel-dominated.
    """
    targets = _targets()
    _, slow = _plan_sorties({"sorties": {"enabled": True, "dwell_s": 20.0}},
                            targets, "report")
    _, quick = _plan_sorties({"sorties": {"enabled": True, "dwell_s": 5.0}},
                             targets, "report")
    assert len(quick.sorties) < len(slow.sorties)


def test_a_generous_derate_buys_more_per_sortie():
    targets = _targets()
    _, cautious = _plan_sorties({"sorties": {"enabled": True, "derate": 0.4}},
                                targets, "report")
    _, optimistic = _plan_sorties({"sorties": {"enabled": True, "derate": 0.95}},
                                  targets, "report")
    assert cautious.sorties[0].n_targets < optimistic.sorties[0].n_targets


def test_the_plan_serialises_for_the_run_record():
    import json

    _, plan = _plan_sorties({"sorties": {"enabled": True}}, _targets(20), "report")
    json.loads(json.dumps(plan.to_dict()))
    assert plan.to_dict()["vehicle"] == "dji-m350-class"
