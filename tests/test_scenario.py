"""Scenario composition (IF-03) — pure, Isaac-free."""

import textwrap

import pytest

from solar_twin.scenario import Scenario, compose, load_scenario


def test_compose_deep_merges_overrides():
    farm = {"seed": 1, "faults": {"rate": 0.2, "states": ["soiled"]}, "grid": {"cols": 10}}
    mission = {"perception": "ground_truth", "kinematics": {"screen_standoff": 2.5}}
    spec = {
        "seed": 99,
        "farm_overrides": {"faults": {"rate": 0.0}, "sun": {"elevation_deg": 16.0}},
        "mission_overrides": {"perception": "cosmos_reason"},
    }
    farm_out, mission_out = compose(farm, mission, spec)

    # Nested keys merge (states preserved), leaf overridden (rate 0.2 -> 0.0).
    assert farm_out["faults"] == {"rate": 0.0, "states": ["soiled"]}
    assert farm_out["sun"] == {"elevation_deg": 16.0}
    assert farm_out["grid"] == {"cols": 10}  # untouched base
    assert mission_out["perception"] == "cosmos_reason"
    assert mission_out["kinematics"] == {"screen_standoff": 2.5}
    # seed propagates into both for reproducibility.
    assert farm_out["seed"] == 99 and mission_out["seed"] == 99


def test_compose_replaces_lists_wholesale():
    farm = {"turbines": [{"pos": [1, 2]}, {"pos": [3, 4]}]}
    spec = {"farm_overrides": {"turbines": [{"pos": [9, 9]}]}}
    farm_out, _ = compose(farm, {}, spec)
    assert farm_out["turbines"] == [{"pos": [9, 9]}]  # not merged element-wise


def test_load_scenario_composes_from_files(tmp_path):
    (tmp_path / "farm.yaml").write_text(
        textwrap.dedent(
            """
            seed: 1
            grid: {rows: 1, cols: 3, row_pitch: 6.0, col_pitch: 2.2}
            faults: {rate: 0.2, states: [soiled]}
            """
        )
    )
    (tmp_path / "mission.yaml").write_text("perception: ground_truth\n")
    (tmp_path / "scn.yaml").write_text(
        textwrap.dedent(
            """
            name: my_scn
            seed: 7
            extends: {farm: farm.yaml, mission: mission.yaml}
            farm_overrides: {faults: {rate: 0.0}, sun: {elevation_deg: 16.0}}
            mission_overrides: {perception: cosmos_reason}
            kpi_gates: {false_fault_rate_max: 0.05}
            """
        )
    )
    scn = load_scenario(str(tmp_path / "scn.yaml"))
    assert isinstance(scn, Scenario)
    assert scn.name == "my_scn"
    assert scn.farm_cfg["faults"]["rate"] == 0.0
    assert scn.farm_cfg["faults"]["states"] == ["soiled"]  # merged, not lost
    assert scn.farm_cfg["sun"]["elevation_deg"] == 16.0
    assert scn.mission_cfg["perception"] == "cosmos_reason"
    assert scn.farm_cfg["seed"] == 7
    assert scn.kpi_gates == {"false_fault_rate_max": 0.05}


def test_load_scenario_requires_extends(tmp_path):
    (tmp_path / "bad.yaml").write_text("name: x\n")
    with pytest.raises(ValueError, match="extends"):
        load_scenario(str(tmp_path / "bad.yaml"))


def test_repo_false_fault_scenarios_are_valid():
    """Both shipped false-fault scenarios load as all-healthy setups with a gate."""
    for path in (
        "configs/scenarios/sweeping_shadow.yaml",   # SC-05: turbine (faithful, WIP)
        "configs/scenarios/hard_shadow.yaml",       # reliable occluder stressor
    ):
        scn = load_scenario(path)
        assert scn.farm_cfg["faults"]["rate"] == 0.0, path
        assert scn.kpi_gates["false_fault_rate_max"] == 0.05, path
    # SC-05 keeps its turbine; hard_shadow drops it for a bar occluder.
    sc05 = load_scenario("configs/scenarios/sweeping_shadow.yaml")
    hard = load_scenario("configs/scenarios/hard_shadow.yaml")
    assert len(sc05.farm_cfg["turbines"]) >= 1
    assert hard.farm_cfg["turbines"] == [] and hard.farm_cfg["shading"]["enabled"]
