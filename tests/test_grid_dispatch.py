"""Suspicion-first dispatch + SIMULATED SCADA ranking — pure, no Isaac, no GPU.

The load-bearing test here is `test_disabled_reproduces_layout_order_exactly`:
the whole design claim is that this layer is upstream of the FSM and that turning
it off restores current behaviour. That has to be asserted, not asserted-in-prose.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from solar_twin.kpi import simulated_scada as scada
from solar_twin.orchestrator import grid_dispatch as gd
from solar_twin.schema.pv_module import PanelRecord, PanelState, cell_for_panel, grid_id


# --- minimal stand-ins for InspectionTarget / Waypoint -------------------- #
@dataclass
class _WP:
    position: tuple[float, float, float]


@dataclass
class _Target:
    panel_id: str
    approach: _WP


def _rec(pid, table, module, state=PanelState.HEALTHY, iv=1.0):
    return PanelRecord(
        panel_id=pid,
        grid_index=(table, module),
        state=state,
        iv_yield=iv,
        cell_id=cell_for_panel((table, module)),
    )


def _farm(n_tables=3, per_table=4, faults=None):
    """n_tables x per_table panels; `faults` maps panel_id -> PanelState."""
    faults = faults or {}
    recs, targets = {}, []
    for t in range(n_tables):
        for m in range(per_table):
            pid = f"R{t:02d}-C{m:03d}"
            recs[pid] = _rec(pid, t, m, faults.get(pid, PanelState.HEALTHY))
            targets.append(_Target(pid, _WP((float(t * 10), float(m), 0.0))))
    return recs, targets


# --------------------------------------------------------------------------- #
# The grid:id contract
# --------------------------------------------------------------------------- #


class TestGridId:
    def test_cell_is_the_table_by_default(self):
        """A cell is a TABLE. Subdividing would invent electrical topology the
        vendor CAD does not carry."""
        assert cell_for_panel((258, 0)) == cell_for_panel((258, 111)) == "G-0258"

    def test_different_tables_are_different_cells(self):
        assert cell_for_panel((258, 0)) != cell_for_panel((243, 0))

    def test_subdivision_is_opt_in_and_reserved_for_a_real_string_map(self):
        assert cell_for_panel((258, 0), modules_per_cell=28) == "G-0258-01"
        assert cell_for_panel((258, 27), modules_per_cell=28) == "G-0258-01"
        assert cell_for_panel((258, 28), modules_per_cell=28) == "G-0258-02"

    def test_grid_id_is_zero_padded_and_sortable(self):
        assert grid_id(7) == "G-0007"
        assert sorted([grid_id(10), grid_id(9)]) == ["G-0009", "G-0010"]


# --------------------------------------------------------------------------- #
# SIMULATED SCADA
# --------------------------------------------------------------------------- #


class TestSimulatedScada:
    def test_every_score_is_labelled_simulated(self):
        """The label is the point — it must be impossible to read a score without
        seeing that it is not a measurement."""
        recs, _ = _farm()
        for s in scada.rank_cells_simulated(recs.values()):
            assert s.scada_source == "simulated" and s.is_simulated

    def test_summary_carries_the_caveat_at_the_top_level(self):
        recs, _ = _farm()
        out = scada.summary(scada.rank_cells_simulated(recs.values()))
        assert out["simulated"] is True
        assert out["scada_source"] == "simulated"
        assert "circular" in out["caveat"].lower()

    def test_all_healthy_farm_has_zero_anomaly(self):
        recs, _ = _farm()
        scores = scada.rank_cells_simulated(recs.values())
        assert scores and all(s.anomaly == 0.0 for s in scores)

    def test_a_faulted_cell_outranks_a_healthy_one(self):
        recs, _ = _farm(faults={"R01-C000": PanelState.STRING_DROPOUT})
        scores = scada.rank_cells_simulated(recs.values())
        assert scores[0].cell_id == "G-0001"
        assert scores[0].anomaly > 0.0

    def test_worse_fault_gives_a_bigger_anomaly(self):
        mild = scada.rank_cells_simulated(
            _farm(faults={"R00-C000": PanelState.SOILED})[0].values()
        )[0]
        severe = scada.rank_cells_simulated(
            _farm(faults={"R00-C000": PanelState.STRING_DROPOUT})[0].values()
        )[0]
        assert severe.anomaly > mild.anomaly

    def test_degraded_iv_yield_shows_up_without_any_fault_state(self):
        recs, _ = _farm()
        recs["R00-C000"] = _rec("R00-C000", 0, 0, PanelState.HEALTHY, iv=0.5)
        assert scada.rank_cells_simulated(recs.values())[0].anomaly > 0.0

    def test_unknown_state_does_not_manufacture_suspicion(self):
        """An un-inspected panel is not evidence of a fault."""
        recs, _ = _farm(faults={"R00-C000": PanelState.UNKNOWN})
        assert all(s.anomaly == 0.0 for s in scada.rank_cells_simulated(recs.values()))

    def test_panels_without_a_cell_are_skipped_not_bucketed(self):
        recs, _ = _farm()
        recs["orphan"] = PanelRecord(panel_id="orphan", grid_index=(9, 9), cell_id="")
        scores = scada.rank_cells_simulated(recs.values())
        assert "" not in {s.cell_id for s in scores}
        assert sum(s.n_panels for s in scores) == 12

    def test_ranking_is_deterministic(self):
        recs, _ = _farm(faults={"R01-C000": PanelState.SOILED})
        a = [s.cell_id for s in scada.rank_cells_simulated(recs.values())]
        b = [s.cell_id for s in scada.rank_cells_simulated(recs.values())]
        assert a == b

    def test_min_anomaly_filters(self):
        recs, _ = _farm(faults={"R00-C000": PanelState.SOILED})
        assert scada.rank_cells_simulated(recs.values(), min_anomaly=0.5) == []


class TestPosterior:
    def test_a_cleared_cell_stops_outranking_an_uninspected_one(self):
        """The posterior is what stops the fleet re-visiting a cell it just
        cleared while its PR is still depressed.

        Two suspect cells, so there is genuinely something to be out-ranked BY —
        with every other cell at zero anomaly the comparison would be vacuous and
        would pass on tie-breaking alone.
        """
        recs, _ = _farm(
            faults={
                "R00-C000": PanelState.STRING_DROPOUT,  # worst -> ranks first
                "R01-C000": PanelState.SOILED,          # milder
            }
        )
        scores = scada.rank_cells_simulated(recs.values())
        assert scores[0].cell_id == "G-0000"
        cell_of = {p: r.cell_id for p, r in recs.items()}
        cleared = {p: "healthy" for p in recs if recs[p].cell_id == "G-0000"}
        after = scada.apply_verdicts(scores, cleared, cell_of)
        assert after[0].cell_id == "G-0001"  # the un-inspected suspect now leads
        assert next(s for s in after if s.cell_id == "G-0000").posterior == 0.0

    def test_a_confirmed_fault_keeps_the_cell_ranked(self):
        recs, _ = _farm(faults={"R00-C000": PanelState.SOILED})
        scores = scada.rank_cells_simulated(recs.values())
        cell_of = {p: r.cell_id for p, r in recs.items()}
        verdicts = {p: ("soiled" if p == "R00-C000" else "healthy")
                    for p in recs if recs[p].cell_id == "G-0000"}
        after = scada.apply_verdicts(scores, verdicts, cell_of)
        assert next(s for s in after if s.cell_id == "G-0000").posterior == pytest.approx(0.25)

    def test_abstention_is_inspected_but_not_faulty(self):
        """`unknown` is a lost answer, not a finding — rewarding it with dispatch
        priority would reward plumbing failures."""
        recs, _ = _farm(faults={"R00-C000": PanelState.SOILED})
        scores = scada.rank_cells_simulated(recs.values())
        cell_of = {p: r.cell_id for p, r in recs.items()}
        verdicts = {p: "unknown" for p in recs if recs[p].cell_id == "G-0000"}
        after = scada.apply_verdicts(scores, verdicts, cell_of)
        assert next(s for s in after if s.cell_id == "G-0000").posterior == 0.0

    def test_unexplained_pr_is_flagged_as_a_finding(self):
        """A fully-inspected, fault-free cell whose PR is still depressed is
        pointing at soiling or shading — a finding, not a scoring term.

        Uses a fault severe enough to clear `PR_UNEXPLAINED_MIN` rather than
        lowering the threshold to fit: one soiled module in four is a 1.75% cell
        deficit, just under the 2% floor, and moving the floor to pass a test would
        be tuning an already-uncalibratable number.
        """
        recs, _ = _farm(faults={"R00-C000": PanelState.STRING_DROPOUT})
        scores = scada.rank_cells_simulated(recs.values())
        cell_of = {p: r.cell_id for p, r in recs.items()}
        cleared = {p: "healthy" for p in recs if recs[p].cell_id == "G-0000"}
        after = scada.apply_verdicts(scores, cleared, cell_of)
        assert next(s for s in after if s.cell_id == "G-0000").pr_unexplained


# --------------------------------------------------------------------------- #
# THE ACCEPTANCE TEST
# --------------------------------------------------------------------------- #


class TestDisabledIsTheIdentity:
    def test_disabled_reproduces_layout_order_exactly(self):
        """⭐ The design claim, as an assertion.

        "Turning it off must reproduce current behaviour byte-for-byte." Same list
        contents, same order, same object — and no plan constructed.
        """
        recs, targets = _farm(faults={"R02-C000": PanelState.STRING_DROPOUT})
        before = [t.panel_id for t in targets]
        out, res = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=False))
        assert out is targets              # the identity, not a copy
        assert [t.panel_id for t in out] == before
        assert res.enabled is False
        assert res.plan is None
        assert res.n_targets_in == res.n_targets_out == len(before)

    def test_disabled_records_scada_source_none(self):
        """A disabled run must not be ambiguous about whether its ordering came
        from a simulated prior."""
        recs, targets = _farm()
        _, res = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=False))
        assert res.to_dict()["scada_source"] == "none"

    def test_default_config_is_disabled(self):
        """Off by default, so every previously recorded number stays reproducible."""
        assert gd.DispatchConfig().enabled is False
        assert gd.DispatchConfig.from_mission_cfg({}).enabled is False
        assert gd.DispatchConfig.from_mission_cfg({"grid_dispatch": {}}).enabled is False


class TestEnabledOrdering:
    def test_enabled_puts_the_suspect_cell_first(self):
        recs, targets = _farm(faults={"R02-C000": PanelState.STRING_DROPOUT})
        out, res = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=True))
        assert res.enabled and res.plan is not None
        assert recs[out[0].panel_id].cell_id == "G-0002"

    def test_enabled_never_drops_a_panel(self):
        recs, targets = _farm(faults={"R01-C000": PanelState.SOILED})
        out, res = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=True))
        assert sorted(t.panel_id for t in out) == sorted(t.panel_id for t in targets)
        assert res.n_targets_out == res.n_targets_in

    def test_panel_order_within_a_cell_stays_the_layout_s(self):
        recs, targets = _farm(faults={"R01-C000": PanelState.SOILED})
        out, _ = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=True))
        got = [t.panel_id for t in out if recs[t.panel_id].cell_id == "G-0001"]
        want = [t.panel_id for t in targets if recs[t.panel_id].cell_id == "G-0001"]
        assert got == want

    def test_orphan_panels_keep_relative_order_at_the_end(self):
        recs, targets = _farm()
        recs["R00-C000"] = PanelRecord(panel_id="R00-C000", grid_index=(0, 0), cell_id="")
        out, _ = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=True))
        assert out[-1].panel_id == "R00-C000"

    def test_falls_back_to_layout_order_when_no_cells_exist(self):
        """A stage built before the grid namespace must still run."""
        recs, targets = _farm()
        for r in recs.values():
            r.cell_id = ""
        out, res = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=True))
        assert out is targets
        assert "fell back to layout order" in res.reason


class TestRealWaypointShape:
    """⚠ Regression: the stand-in above invented `Waypoint.position`.

    `control.base.Waypoint` is flat `x/y/z/yaw`, so every test in this file
    passed while `order_targets` raised `AttributeError` on real mission targets.
    Found by running the layer against the real 560-panel Khavda block. These
    tests use the REAL dataclass — a hand-rolled double cannot catch a contract
    mismatch with the thing it is doubling.
    """

    def _real_targets(self, recs):
        from solar_twin.control.base import Waypoint

        @dataclass
        class T:
            panel_id: str
            approach: Waypoint

        return [
            T(pid, Waypoint(x=float(r.grid_index[0] * 10), y=float(r.grid_index[1]), z=1.0))
            for pid, r in recs.items()
        ]

    def test_order_targets_accepts_the_real_waypoint(self):
        recs, _ = _farm(faults={"R02-C000": PanelState.STRING_DROPOUT})
        targets = self._real_targets(recs)
        out, res = gd.order_targets(targets, recs, gd.DispatchConfig(enabled=True))
        assert res.plan is not None and res.plan.travel_m > 0
        assert recs[out[0].panel_id].cell_id == "G-0002"

    def test_wp_xy_reads_flat_x_y(self):
        from solar_twin.control.base import Waypoint

        assert gd._wp_xy(Waypoint(x=3.0, y=4.0, z=9.0)) == (3.0, 4.0)

    def test_wp_xy_still_tolerates_a_position_sequence(self):
        assert gd._wp_xy(_WP((3.0, 4.0, 9.0))) == (3.0, 4.0)

    def test_wp_xy_handles_none_and_unknown_shapes(self):
        assert gd._wp_xy(None) is None
        assert gd._wp_xy(object()) is None


class TestRoutePlan:
    def _cells(self):
        recs, targets = _farm(faults={"R02-C000": PanelState.STRING_DROPOUT})
        return scada.rank_cells_simulated(recs.values()), recs, targets

    def test_solver_is_the_stub_because_cuopt_is_not_installed(self):
        """Provenance: a greedy result must never be labelled cuopt."""
        cells, recs, targets = self._cells()
        plan = gd.plan_route(cells, gd._centroids_from_targets(targets, recs))
        assert plan.solver == "greedy-stub"

    def test_requesting_cuopt_explicitly_refuses_to_fall_back(self):
        cells, recs, targets = self._cells()
        with pytest.raises(RuntimeError, match="false provenance"):
            gd.plan_route(
                cells, gd._centroids_from_targets(targets, recs), solver="cuopt"
            )

    def test_kpi09_is_suspicion_per_metre_not_per_battery_hour(self):
        cells, recs, targets = self._cells()
        plan = gd.plan_route(cells, gd._centroids_from_targets(targets, recs))
        assert plan.travel_m > 0
        assert plan.suspicion_per_m == pytest.approx(plan.suspicion / plan.travel_m)

    def test_zero_travel_does_not_divide_by_zero(self):
        cells, recs, _ = self._cells()
        one = {cells[0].cell_id: (0.0, 0.0)}
        plan = gd.plan_route([cells[0]], one, start=(0.0, 0.0))
        assert plan.travel_m == 0.0 and plan.suspicion_per_m == 0.0

    def test_budget_names_what_it_dropped(self):
        """Silent truncation reads as 'covered everything'."""
        cells, recs, targets = self._cells()
        plan = gd.plan_route(
            cells, gd._centroids_from_targets(targets, recs), max_cells=1
        )
        assert len(plan.cell_order) == 1
        assert len(plan.dropped) == 2

    def test_both_escalation_arms_exist_and_neither_is_forced(self):
        """Ground-first vs drone-first is an assumption to measure."""
        cells, recs, targets = self._cells()
        cen = gd._centroids_from_targets(targets, recs)
        for arm in gd.ESCALATION_ARMS:
            assert gd.plan_route(cells, cen, escalation_arm=arm).escalation_arm == arm
        with pytest.raises(ValueError, match="assumption to measure"):
            gd.plan_route(cells, cen, escalation_arm="nonsense")

    def test_plan_is_deterministic(self):
        cells, recs, targets = self._cells()
        cen = gd._centroids_from_targets(targets, recs)
        assert (
            gd.plan_route(cells, cen).cell_order == gd.plan_route(cells, cen).cell_order
        )
