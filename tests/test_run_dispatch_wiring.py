"""`grid_dispatch` wired into `run.py` — end to end on the Isaac-free spine.

Session 16 shipped `order_targets` as a *tested library nothing called*, and
`layout.panel_records()` did not stamp `cell_id`, so the one demo that exercised
the layer had to set the join key by hand. Both gaps are closed here, and the
thing that must be pinned is the same thing the library's own acceptance test
pins, only one level up: **with the ranker off, a run is what it is today.**

⚠⚠ Every ranking asserted below comes from SIMULATED SCADA — derived from the
twin's own `pv:state`/`pv:iv_yield`, i.e. from the ground truth the mission is
sent out to discover. These tests prove the *plumbing* orders panels as
specified. They are not, and cannot be, evidence that suspicion-first dispatch
beats a sweep on real hardware.
"""

from __future__ import annotations

import json
from pathlib import Path

import solar_twin.run as run_mod
from solar_twin.run import run
from solar_twin.world.layout import FarmLayout

#: 3 tables x 4 modules with the grid layer ON. Seed 5 + rate 1/12 puts the one
#: seeded fault at R02-C001, i.e. in cell `G-0002` — the LAST cell in layout
#: order. That is the point: a ranker that works must pull it to the front, and a
#: fault in `G-0000` would pass on layout order alone and prove nothing.
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
    "panel": {"width": 1.0, "length": 2.0, "height": 0.05, "tilt_deg": 20.0,
              "mount_height": 0.75},
    "faults": {"rate": 1 / 12, "states": ["hotspot"]},
    "terrain": {"kind": "flat"},
}
MISSION = {
    "fleet": {"ground_bot": "bot", "screen_drone": "d1", "confirm_drone": "d2"},
    "kinematics": {"screen_standoff": 2.5, "confirm_standoff": 0.8},
    "escalation": {"screen_suspect_confidence": 0.5},
}

SUSPECT_CELL = "G-0002"


def _farm(**grid_overrides) -> dict:
    grid = {**FARM["grid"], **grid_overrides}
    return {**FARM, "grid": grid}


def _mission(**dispatch) -> dict:
    return {**MISSION, "grid_dispatch": dispatch} if dispatch else dict(MISSION)


def _record(tmp_path: Path, farm_cfg: dict, mission_cfg: dict, **sim_opts) -> dict:
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts=sim_opts,
        farm_cfg=farm_cfg,
        mission_cfg=mission_cfg,
    )
    return json.loads((out / "results.json").read_text())


def _layout_order(farm_cfg: dict, mission_cfg: dict) -> list[str]:
    """The panel order a run would sweep with no prioritisation at all."""
    layout = FarmLayout(farm_cfg)
    return [t.panel_id for t in layout.inspection_targets(mission_cfg)]


# --------------------------------------------------------------------------- #
# THE ACCEPTANCE PROPERTY, one level up from the library's own
# --------------------------------------------------------------------------- #


class TestDisabledRunIsUnchanged:
    def test_disabled_run_sweeps_in_layout_order(self, tmp_path: Path):
        """⭐ The claim that makes this safe to land: ranker off == today.

        `order_targets` returns `targets` itself when disabled; this asserts the
        property survives the wiring, so every KPI on record stays reproducible.
        """
        rec = _record(tmp_path, _farm(), _mission())
        got = [p["panel_id"] for p in rec["panels"]]
        assert got == _layout_order(_farm(), _mission())
        assert got[0] == "R00-C000"

    def test_disabled_by_default_even_with_the_grid_layer_built(self, tmp_path: Path):
        """A stage carrying `grid:id` must not silently start re-ordering runs."""
        rec = _record(tmp_path, _farm(), _mission())
        assert rec["dispatch"]["enabled"] is False

    def test_disabled_record_says_no_scada_was_consulted(self, tmp_path: Path):
        """No run record may be ambiguous about whether a simulated prior chose
        the visit order — including the runs where nothing did."""
        d = _record(tmp_path, _farm(), _mission())["dispatch"]
        assert d["scada_source"] == "none"
        assert d["plan"] is None
        assert "caveat" not in d  # nothing to caveat: no ranking happened

    def test_disabled_run_builds_no_plan_and_keeps_every_panel(self, tmp_path: Path):
        d = _record(tmp_path, _farm(), _mission())["dispatch"]
        assert d["n_targets_in"] == d["n_targets_out"] == 12


# --------------------------------------------------------------------------- #
# Enabled: suspicion-first ordering (gap 2)
# --------------------------------------------------------------------------- #


class TestEnabledOrdering:
    def test_enabled_sweeps_the_suspect_cell_first(self, tmp_path: Path):
        rec = _record(tmp_path, _farm(), _mission(enabled=True))
        order = [p["panel_id"] for p in rec["panels"]]
        assert order != _layout_order(_farm(), _mission())
        cells = {r.panel_id: r.cell_id for r in FarmLayout(_farm()).panel_records()}
        assert cells[order[0]] == SUSPECT_CELL

    def test_enabled_never_drops_a_panel(self, tmp_path: Path):
        """Re-prioritising is not sub-sampling: the sweep still covers the site."""
        rec = _record(tmp_path, _farm(), _mission(enabled=True))
        got = sorted(p["panel_id"] for p in rec["panels"])
        assert got == sorted(_layout_order(_farm(), _mission()))

    def test_enabled_record_states_the_prior_is_circular(self, tmp_path: Path):
        """⚠⚠ The label `simulated` is too easy to read as a plumbing detail, so
        a run whose order came from the prior spells out why it proves nothing."""
        d = _record(tmp_path, _farm(), _mission(enabled=True))["dispatch"]
        assert d["scada_source"] == "simulated"
        assert "circular" in d["caveat"].lower()
        assert "SIMULATED" in d["caveat"]

    def test_enabled_record_carries_the_plan_and_its_solver(self, tmp_path: Path):
        """Provenance: a greedy walk must never be readable as a cuOpt result."""
        plan = _record(tmp_path, _farm(), _mission(enabled=True))["dispatch"]["plan"]
        assert plan["solver"] == "greedy-stub"
        assert plan["cell_order"][0] == SUSPECT_CELL
        assert plan["suspicion_per_m"] >= 0.0

    def test_enabled_record_lists_the_scored_cells(self, tmp_path: Path):
        cells = _record(tmp_path, _farm(), _mission(enabled=True))["dispatch"]["cells"]
        assert {c["cell_id"] for c in cells} == {"G-0000", "G-0001", SUSPECT_CELL}
        assert all(c["scada_source"] == "simulated" for c in cells)

    def test_a_stage_without_the_grid_layer_falls_back_to_layout_order(
        self, tmp_path: Path
    ):
        """Asking for the ranker on a stage built before `grid:id` must still
        run — and must say out loud that it fell back, not quietly sweep."""
        farm = _farm(enabled=False)
        rec = _record(tmp_path, farm, _mission(enabled=True))
        assert [p["panel_id"] for p in rec["panels"]] == _layout_order(farm, _mission())
        assert "fell back to layout order" in rec["dispatch"]["reason"]


# --------------------------------------------------------------------------- #
# Budgets — the half that makes KPI-09 comparable at all
# --------------------------------------------------------------------------- #


class TestBudgets:
    def test_max_cells_names_what_it_dropped(self, tmp_path: Path):
        """Silent truncation reads as 'we covered everything'."""
        plan = _record(
            tmp_path, _farm(), _mission(enabled=True, max_cells=1)
        )["dispatch"]["plan"]
        assert plan["cell_order"] == [SUSPECT_CELL]
        assert sorted(plan["dropped"]) == ["G-0000", "G-0001"]

    def test_max_panels_bites_after_the_ranking_not_before(self, tmp_path: Path):
        """`docs/specs/06` requires KPI-09's ranker-ON and ranker-OFF arms to be
        compared at the same seed AND the same panel budget. So the budget is
        spent on the ranked order: a 4-panel budget must buy the 4 panels of the
        suspect cell, not the first 4 panels of the layout."""
        rec = _record(tmp_path, _farm(), _mission(enabled=True), max_panels=4)
        cells = {r.panel_id: r.cell_id for r in FarmLayout(_farm()).panel_records()}
        got = [p["panel_id"] for p in rec["panels"]]
        assert len(got) == 4
        assert {cells[p] for p in got} == {SUSPECT_CELL}

    def test_max_panels_with_the_ranker_off_is_still_the_first_n(self, tmp_path: Path):
        """The other half of the same guarantee: off changes nothing, including
        how the budget interacts with it."""
        rec = _record(tmp_path, _farm(), _mission(), max_panels=4)
        assert [p["panel_id"] for p in rec["panels"]] == _layout_order(
            _farm(), _mission()
        )[:4]


# --------------------------------------------------------------------------- #
# CLI gate — flags map into mission_cfg, and absence writes nothing
# --------------------------------------------------------------------------- #


def _mission_cfg_from_cli(argv: list[str], monkeypatch, base: dict | None = None):
    """Run `main(argv)` with `run` and the YAML loader stubbed; return mission_cfg."""
    captured: dict = {}

    def fake_run(farm, mission, backend, runs_dir, sim_opts=None, **kw):
        captured["mission_cfg"] = kw.get("mission_cfg")
        return None

    monkeypatch.setattr(run_mod, "run", fake_run)
    monkeypatch.setattr(run_mod, "_load_yaml", lambda path: dict(base or MISSION))
    assert run_mod.main(["farm.yaml", "mission.yaml", *argv]) == 0
    return captured["mission_cfg"]


class TestCliGate:
    def test_no_flag_leaves_the_mission_config_untouched(self, monkeypatch):
        """The mission config is copied verbatim into the run dir, so a run that
        never asked for the layer must not gain a `grid_dispatch` key."""
        assert _mission_cfg_from_cli([], monkeypatch) is None

    def test_grid_dispatch_flag_turns_the_layer_on(self, monkeypatch):
        cfg = _mission_cfg_from_cli(["--grid-dispatch"], monkeypatch)
        assert cfg["grid_dispatch"]["enabled"] is True

    def test_flag_merges_onto_the_mission_s_own_block(self, monkeypatch):
        """A mission that pins `min_anomaly`/`escalation_arm` keeps them when the
        flag flips the layer on — the flag is an override, not a replacement."""
        base = {**MISSION, "grid_dispatch": {"min_anomaly": 0.3,
                                             "escalation_arm": "drone_first"}}
        cfg = _mission_cfg_from_cli(["--grid-dispatch"], monkeypatch, base)
        assert cfg["grid_dispatch"] == {
            "min_anomaly": 0.3, "escalation_arm": "drone_first", "enabled": True
        }

    def test_max_cells_flag_sets_the_budget(self, monkeypatch):
        cfg = _mission_cfg_from_cli(
            ["--grid-dispatch", "--dispatch-max-cells", "2"], monkeypatch
        )
        assert cfg["grid_dispatch"] == {"enabled": True, "max_cells": 2}

    def test_a_budget_without_the_ranker_is_called_out(self, monkeypatch, capsys):
        """A cell budget with the ranker off does nothing; saying so beats
        letting someone believe they ran a budgeted arm."""
        _mission_cfg_from_cli(["--dispatch-max-cells", "2"], monkeypatch)
        assert "the ranker is off" in capsys.readouterr().out
