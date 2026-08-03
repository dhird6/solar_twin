"""Sortie planning — the robot deciding what it can do and when to turn back.

Isaac-free. These test the DECISION logic against a model of a battery; nothing here
has been flown, so none of it validates real endurance (see `sortie.py`'s header).
"""

from __future__ import annotations

import pytest

from solar_twin.orchestrator.sortie import (
    DEFAULT_RESERVE_FRACTION,
    SortiePlan,
    VehicleEndurance,
    plan_sorties,
    replan,
)
from solar_twin.world.fleet_specs import (
    CLEARPATH_HUSKY,
    DEFAULT_ENDURANCE_DERATE,
    DJI_M350,
    DroneSpec,
)

BASE = (0.0, 0.0, 0.0)


def _v(**kw) -> VehicleEndurance:
    base = dict(
        name="test", usable_endurance_s=600.0, cruise_speed_ms=10.0,
        dwell_s=10.0, reserve_fraction=0.0,
    )
    base.update(kw)
    return VehicleEndurance(**base)


def _line(n: int, spacing: float = 10.0, y: float = 0.0):
    return [(f"P{i:03d}", (spacing * (i + 1), y, 0.0)) for i in range(n)]


# --------------------------------------------------------------------------- #
# The constraint that did not exist before: a robot has a battery.
# --------------------------------------------------------------------------- #


def test_a_vehicle_without_a_published_endurance_is_refused():
    """A planner that assumed a battery would give a confident meaningless answer."""
    naked = DroneSpec(
        name="unknown", diagonal_m=0.9, rotor_diameter_m=0.5,
        body_l_m=0.4, body_w_m=0.4, body_h_m=0.4, mass_kg=6.0,
    )
    with pytest.raises(ValueError, match="max_endurance_s"):
        VehicleEndurance.from_spec(naked)


def test_reserve_is_held_back_from_the_budget():
    v = _v(usable_endurance_s=1000.0, reserve_fraction=0.15)
    assert v.budget_s == pytest.approx(850.0)


def test_derate_and_reserve_are_separate_bites():
    """The derate shrinks the tank; the reserve fences off part of what is left."""
    v = VehicleEndurance.from_spec(DJI_M350, reserve_fraction=0.15)
    assert v.usable_endurance_s == pytest.approx(
        DJI_M350.max_endurance_s * DEFAULT_ENDURANCE_DERATE
    )
    assert v.budget_s == pytest.approx(v.usable_endurance_s * 0.85)
    assert v.budget_s < v.usable_endurance_s < DJI_M350.max_endurance_s


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_invalid_derate_is_refused(bad):
    with pytest.raises(ValueError):
        DJI_M350.usable_endurance_s(bad)


def test_zero_or_negative_speed_is_refused():
    with pytest.raises(ValueError):
        _v(cruise_speed_ms=0.0)


def test_reserve_of_one_would_leave_no_budget_and_is_refused():
    with pytest.raises(ValueError):
        _v(reserve_fraction=1.0)


# --------------------------------------------------------------------------- #
# ⭐ The decision a fixed route cannot express: cost the way home first.
# --------------------------------------------------------------------------- #


def test_a_sortie_always_leaves_enough_to_get_home():
    """Every sortie's own accounting must fit inside the budget it was given."""
    v = _v(usable_endurance_s=300.0)
    plan = plan_sorties(_line(40), v, BASE)
    assert plan.sorties
    for s in plan.sorties:
        assert s.duration_s <= s.budget_s + 1e-9, (
            f"sortie {s.index} plans {s.duration_s:.1f}s against a {s.budget_s:.1f}s budget"
        )


def test_duration_includes_the_return_leg():
    """One target 100 m out at 10 m/s = 10s there + 10s dwell + 10s back."""
    v = _v(dwell_s=10.0, cruise_speed_ms=10.0)
    plan = plan_sorties([("P0", (100.0, 0.0, 0.0))], v, BASE)
    assert plan.sorties[0].duration_s == pytest.approx(30.0)
    assert plan.sorties[0].travel_m == pytest.approx(200.0)


def test_work_beyond_one_battery_becomes_more_sorties():
    v = _v(usable_endurance_s=200.0)
    plan = plan_sorties(_line(30), v, BASE)
    assert len(plan.sorties) > 1
    assert plan.n_planned == 30, "every reachable target must still be planned"


def test_every_target_appears_exactly_once_across_the_plan():
    """The accounting has to close — no duplicates, nothing silently vanished."""
    v = _v(usable_endurance_s=250.0)
    targets = _line(45)
    plan = plan_sorties(targets, v, BASE)
    seen = [t for s in plan.sorties for t in s.target_ids]
    seen += list(plan.unreachable) + list(plan.deferred)
    assert sorted(seen) == sorted(t for t, _ in targets)
    assert len(seen) == len(set(seen)), "a target was planned twice"


# --------------------------------------------------------------------------- #
# Deciding what NOT to do — and the two reasons are not the same.
# --------------------------------------------------------------------------- #


def test_a_target_too_far_to_reach_at_all_is_unreachable_not_deferred():
    """Out-and-back already exceeds the budget: more trips would not help."""
    v = _v(usable_endurance_s=60.0, cruise_speed_ms=10.0)  # 600 m of travel, total
    plan = plan_sorties([("near", (100.0, 0, 0)), ("far", (5000.0, 0, 0))], v, BASE)
    assert "far" in plan.unreachable
    assert "far" not in plan.deferred
    assert plan.n_planned == 1


def test_hitting_the_sortie_cap_defers_rather_than_dropping_silently():
    """A cap must never read as 'that was everything'."""
    v = _v(usable_endurance_s=200.0)
    plan = plan_sorties(_line(40), v, BASE, max_sorties=1)
    assert len(plan.sorties) == 1
    assert plan.deferred, "capped work must be reported as deferred"
    assert not plan.unreachable, "capped work is not a capability limit"


def test_unreachable_and_deferred_are_distinguishable_in_the_record():
    v = _v(usable_endurance_s=60.0)
    plan = plan_sorties(
        [("a", (50.0, 0, 0)), ("b", (60.0, 0, 0)), ("far", (9000.0, 0, 0))],
        v, BASE, max_sorties=1,
    )
    d = plan.to_dict()
    assert d["n_unreachable"] >= 1
    assert set(d) >= {"unreachable", "deferred", "n_sorties", "n_planned"}


def test_an_empty_target_list_plans_nothing_rather_than_failing():
    plan = plan_sorties([], _v(), BASE)
    assert plan.sorties == () and plan.n_planned == 0


# --------------------------------------------------------------------------- #
# Blocked legs — the hook for keep-outs, without pretending to route around them.
# --------------------------------------------------------------------------- #


def test_a_leg_the_predicate_rejects_is_never_planned_through():
    """No detour logic exists, so a blocked target must be reported, not flown."""
    banned = (300.0, 0.0, 0.0)

    def is_clear(a, b):
        return b != banned

    v = _v(usable_endurance_s=600.0)
    plan = plan_sorties(
        [("ok1", (100.0, 0, 0)), ("blocked", banned), ("ok2", (150.0, 0, 0))],
        v, BASE, is_clear=is_clear,
    )
    planned = [t for s in plan.sorties for t in s.target_ids]
    assert "blocked" not in planned
    assert "blocked" in plan.unreachable


def test_a_predicate_that_blocks_everything_terminates():
    """Guards the infinite loop: nothing fits, so nothing must spin forever."""
    plan = plan_sorties(_line(5), _v(), BASE, is_clear=lambda a, b: False)
    assert plan.n_planned == 0
    assert len(plan.unreachable) == 5


# --------------------------------------------------------------------------- #
# ⭐ Re-planning: what makes a discovery able to change the plan.
# --------------------------------------------------------------------------- #


def test_replan_returns_what_still_fits_from_here():
    v = _v(usable_endurance_s=600.0, cruise_speed_ms=10.0, dwell_s=10.0)
    ids, abort = replan(_line(20), v, current_pos=(50.0, 0, 0), charge_remaining_s=120.0)
    assert not abort
    assert 0 < len(ids) < 20, "a partial charge should buy some targets, not all"


def test_replan_aborts_when_even_the_flight_home_is_marginal():
    """Must be distinguishable from 'zero targets happen to fit'."""
    v = _v(cruise_speed_ms=10.0)
    ids, abort = replan(
        _line(5), v, current_pos=(1000.0, 0, 0), charge_remaining_s=50.0
    )
    assert abort is True and ids == []


def test_replan_never_plans_past_the_point_of_no_return():
    v = _v(cruise_speed_ms=10.0, dwell_s=10.0)
    pos = (100.0, 0.0, 0.0)
    charge = 200.0
    ids, abort = replan(_line(30), v, current_pos=pos, charge_remaining_s=charge)
    assert not abort
    # Re-walk the accepted list and confirm the way home still fits at every step.
    spent, cur = 0.0, pos
    lookup = dict(_line(30))
    for tid in ids:
        tpos = lookup[tid]
        spent += v.travel_s(cur, tpos) + v.dwell_s
        cur = tpos
        assert spent + v.travel_s(cur, BASE) <= charge + 1e-9


def test_replan_from_base_with_a_full_charge_matches_a_fresh_first_sortie():
    v = _v(usable_endurance_s=300.0)
    targets = _line(30)
    plan = plan_sorties(targets, v, BASE)
    ids, abort = replan(targets, v, current_pos=BASE, charge_remaining_s=v.budget_s)
    assert not abort
    assert list(plan.sorties[0].target_ids) == ids


# --------------------------------------------------------------------------- #
# The real platforms, and the answer that makes this layer worth having.
# --------------------------------------------------------------------------- #


def test_the_real_drone_cannot_sweep_a_whole_table_in_one_flight():
    """⭐ The finding. A 128 m Khavda table is 112 modules; at 20 s dwell that is
    37 min of dwell alone against an M350's ~28 min planning budget. This is exactly
    the decision that did not exist when routes were fixed — and note it is DWELL
    that limits the drone here, not travel."""
    v = VehicleEndurance.from_spec(DJI_M350, dwell_s=20.0)
    plan = plan_sorties(_line(112, spacing=1.15), v, BASE)
    assert len(plan.sorties) > 1, "112 modules must not fit one M350 flight at 20 s dwell"
    assert plan.n_planned == 112


def test_the_rover_trades_speed_for_endurance():
    """The Husky is 8x slower and lasts 3x longer — so on a dwell-dominated sweep it
    needs fewer trips, which is a real dispatch consideration and not an obvious one."""
    targets = _line(112, spacing=1.15)
    drone = plan_sorties(_line(112, spacing=1.15),
                         VehicleEndurance.from_spec(DJI_M350, dwell_s=20.0), BASE)
    rover = plan_sorties(targets,
                         VehicleEndurance.from_spec(CLEARPATH_HUSKY, dwell_s=20.0), BASE)
    assert len(rover.sorties) < len(drone.sorties)


def test_published_endurance_figures_are_present_for_every_platform():
    """A platform with no battery figure cannot be dispatched — catch it here."""
    for spec in (DJI_M350, CLEARPATH_HUSKY):
        assert spec.max_endurance_s > 0, f"{spec.name} has no endurance"
        assert spec.cruise_speed_ms > 0, f"{spec.name} has no cruise speed"


def test_plan_is_serialisable_for_the_run_record():
    plan = plan_sorties(_line(20), _v(usable_endurance_s=200.0), BASE)
    d = plan.to_dict()
    import json

    json.loads(json.dumps(d))
    assert d["n_sorties"] == len(plan.sorties)
    assert d["sorties"][0]["utilisation"] <= 1.0
