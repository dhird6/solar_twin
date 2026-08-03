"""`wind_disturbance` wired into `run.py` — end to end on the Isaac-free spine.

`world/windfield.py` shipped as a complete, seeded, unit-tested model that **nothing
called**: measured 2026-07-31, it had zero consumers anywhere in `src/`. These tests
exist so that cannot silently become true again, and so the three ways the wiring
could be wrong are pinned rather than assumed:

* off must be the identity, or every KPI recorded before this is unquotable;
* on must actually move the camera AND still let the mission finish (the drift is
  larger than `at_goal`'s tolerance, so a naive wrapper hangs the escalation FSM);
* asking for wind where there is none must complain, not quietly measure calm air.

⚠ What is wired here disturbs the **camera pose**, not the airframe. It is not flight
dynamics and `KPI-05` does not come from it — see `control/wind_drift.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from solar_twin.run import run

#: 3 tables x 4 modules, flat, all healthy. A turbine stands well clear of every
#: waypoint: far enough not to clamp anything, present so `build_keepouts` produces
#: a `SafeControl` and the wrapper-stacking assertion below has something to find.
FARM = {
    "seed": 5,
    "grid": {
        "rows": 3,
        "cols": 4,
        "row_pitch": 6.0,
        "col_pitch": 2.2,
        "origin": [0.0, 0.0, 0.0],
        "enabled": True,
    },
    "panel": {
        "width": 1.0,
        "length": 2.0,
        "height": 0.05,
        "tilt_deg": 20.0,
        "mount_height": 0.75,
    },
    "faults": {"rate": 0.0, "states": []},
    "terrain": {"kind": "flat"},
    "turbines": [{"pos": [300.0, 300.0], "hub_height": 40.0, "blade_len": 20.0}],
}
MISSION = {
    "fleet": {"ground_bot": "bot", "screen_drone": "d1", "confirm_drone": "d2"},
    "kinematics": {"screen_standoff": 2.5, "confirm_standoff": 0.8},
    "escalation": {"screen_suspect_confidence": 0.5},
}

#: A windy-but-flyable day, matching `khavda_windy_hover.yaml`.
WIND = {
    "mean_speed": 12.0,
    "direction_deg": 225.0,
    "speed_variation": 0.35,
    "direction_variation_deg": 15.0,
    "gust_period_s": 4.0,
}


def _farm(wind: dict | None = None) -> dict:
    cfg = {k: v for k, v in FARM.items()}
    if wind is not None:
        cfg["wind"] = dict(wind)
    return cfg


def _mission(**kin) -> dict:
    return {**MISSION, "kinematics": {**MISSION["kinematics"], **kin}}


def _record(tmp_path: Path, farm_cfg: dict, mission_cfg: dict) -> dict:
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={},
        farm_cfg=farm_cfg,
        mission_cfg=mission_cfg,
    )
    return json.loads((out / "results.json").read_text())


# --------------------------------------------------------------------------- #
# Off is the identity
# --------------------------------------------------------------------------- #


def test_off_by_default_leaves_no_trace_in_the_record(tmp_path: Path):
    """⭐ The claim that makes this safe to land. A run that did not ask for wind must
    be indistinguishable from one made before the feature existed."""
    rec = _record(tmp_path, _farm(WIND), _mission())
    assert "wind_disturbance" not in rec


def test_a_calm_run_and_a_wind_free_config_agree_panel_for_panel(tmp_path: Path):
    """Declaring `wind:` in the farm config must not change anything on its own —
    the mission knob is the only switch."""
    with_block = _record(tmp_path, _farm(WIND), _mission())
    without = _record(tmp_path, _farm(None), _mission())
    assert [p["panel_id"] for p in with_block["panels"]] == [
        p["panel_id"] for p in without["panels"]
    ]
    assert with_block["metrics"]["panels_inspected"] == without["metrics"][
        "panels_inspected"
    ]


# --------------------------------------------------------------------------- #
# On, and honest about it
# --------------------------------------------------------------------------- #


def test_on_moves_the_camera_and_records_how_far(tmp_path: Path):
    rec = _record(tmp_path, _farm(WIND), _mission(wind_disturbance=True))
    wd = rec["wind_disturbance"]
    assert wd["max_drift_m"] > 0.0
    assert wd["mean_wind_ms"] == 12.0
    assert wd["speed_variation"] == 0.35


def test_the_record_refuses_to_be_mistaken_for_flight_dynamics(tmp_path: Path):
    """The `NFR-07` guard, in the artefact a reader actually opens. A drift figure
    sitting in a run record with no caveat is how `KPI-05` gets quoted from a pose
    offset that has no mass, thrust or attitude loop behind it."""
    rec = _record(tmp_path, _farm(WIND), _mission(wind_disturbance=True))
    assert "NOT flight dynamics" in rec["wind_disturbance"]["model"]


def test_the_mission_still_completes_every_panel_under_wind(tmp_path: Path):
    """The mission-hang regression, end to end. Drift (~0.2 m) exceeds `at_goal`'s
    0.05 m tolerance, so a wrapper that reported progress against the *commanded*
    waypoint would leave the FSM waiting on a drone that had already arrived."""
    calm = _record(tmp_path, _farm(WIND), _mission())
    windy = _record(tmp_path, _farm(WIND), _mission(wind_disturbance=True))
    assert windy["metrics"]["panels_inspected"] == calm["metrics"]["panels_inspected"]
    assert windy["metrics"]["panels_inspected"] > 0
    assert windy["metrics"]["abstentions"] == 0


def test_drift_scales_with_the_declared_stiffness(tmp_path: Path):
    """`wind_hold_stiffness_n_per_m` is the one INFERRED constant that sets the
    answer, so the config knob must demonstrably reach the model."""
    soft = _record(
        tmp_path,
        _farm(WIND),
        _mission(wind_disturbance=True, wind_hold_stiffness_n_per_m=40.0),
    )
    stiff = _record(
        tmp_path,
        _farm(WIND),
        _mission(wind_disturbance=True, wind_hold_stiffness_n_per_m=160.0),
    )
    assert soft["wind_disturbance"]["max_drift_m"] > stiff["wind_disturbance"][
        "max_drift_m"
    ]


def test_a_lighter_airframe_is_pushed_differently(tmp_path: Path):
    m350 = _record(
        tmp_path, _farm(WIND), _mission(wind_disturbance=True, wind_drone_spec="m350")
    )
    mavic = _record(
        tmp_path, _farm(WIND), _mission(wind_disturbance=True, wind_drone_spec="mavic3t")
    )
    # The Mavic's body box is far smaller, so it presents less area to the wind.
    assert (
        mavic["wind_disturbance"]["max_drift_m"] < m350["wind_disturbance"]["max_drift_m"]
    )


def test_an_unknown_drone_spec_is_refused(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError, match="wind_drone_spec"):
        _record(
            tmp_path,
            _farm(WIND),
            _mission(wind_disturbance=True, wind_drone_spec="quadcopter9000"),
        )


# --------------------------------------------------------------------------- #
# The two traps
# --------------------------------------------------------------------------- #


def test_asking_for_wind_without_a_wind_block_warns_and_stays_calm(tmp_path: Path, capsys):
    """The `NFR-07` failure mode named directly: a scenario that turns the hazard on,
    has no hazard to apply, and reports a number as if it did. It must say so."""
    rec = _record(tmp_path, _farm(None), _mission(wind_disturbance=True))
    assert "wind_disturbance" not in rec
    out = capsys.readouterr().out
    assert "no wind" in out and "NFR-07" in out


def test_the_keepout_record_survives_the_extra_wrapper(tmp_path: Path):
    """The regression for a real bug in this change. `run.py` located the keep-out
    tally with `isinstance(control, SafeControl)`, which silently returns False the
    moment anything wraps it — so turning wind on would have dropped the entire
    keep-out section from the run record. Both must be present together."""
    rec = _record(tmp_path, _farm(WIND), _mission(wind_disturbance=True))
    assert "wind_disturbance" in rec
    assert rec["keepout"]["turbines"] == 1
    assert rec["keepout"]["min_clearance_m"] is not None
