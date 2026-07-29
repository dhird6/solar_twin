"""The --repeat path end to end on the Isaac-free spine.

Two things are easy to get wrong here and both are silent, so they are pinned:

1. **State rewind.** The first mission writes its verdict onto the panel. Without
   a rewind, repeat 2 reads that verdict as ground truth and every
   `injected_state` in the record — hence every detection rate — is fiction.
2. **Frame fingerprints.** They are what tells a renderer difference from a model
   flip, so a perception that answers differently on the same picture must be
   attributed to the model — and, because the real renderer is stochastic
   (`RISK-24`), sub-LSB noise must NOT count as a different picture.
"""

from __future__ import annotations

import json
from pathlib import Path

from solar_twin.kpi import variance as V
from solar_twin.orchestrator.fake_backend import FakeSimBackend
from solar_twin.perception.base import Diagnosis, Perception, Verdict, frame_digest
from solar_twin.run import run
from solar_twin.world.layout import FarmLayout

FARM = {
    "seed": 7,
    "grid": {"rows": 1, "cols": 6, "row_pitch": 6.0, "col_pitch": 2.2,
             "origin": [0.0, 0.0, 0.0]},
    "panel": {"width": 1.0, "length": 2.0, "height": 0.05, "tilt_deg": 20.0,
              "mount_height": 0.75},
    "faults": {"rate": 0.34, "states": ["hotspot", "soiled"]},
    "terrain": {"kind": "flat"},
}
MISSION = {
    "fleet": {"ground_bot": "bot", "screen_drone": "d1", "confirm_drone": "d2"},
    "kinematics": {"screen_standoff": 2.5, "confirm_standoff": 0.8},
    "escalation": {"screen_suspect_confidence": 0.5},
}


class FlipFlopPerception(Perception):
    """Deterministic stand-in for a non-deterministic VLM: it alternates its
    diagnosis between calls, exactly the failure mode `--repeat` exists to
    quantify."""

    #: Three answers, cycled — so the second repeat lands on a different one
    #: even though the world, the seed and the frames are identical.
    ANSWERS = ("hotspot", "soiled", "crack")

    def __init__(self):
        self.calls = 0

    def assess(self, frame, context) -> Verdict:
        suspect = context["true_state"] != "healthy"
        return Verdict("suspect" if suspect else "clean", 0.9, "stub")

    def diagnose(self, frame, context) -> Diagnosis:
        answer = self.ANSWERS[self.calls % len(self.ANSWERS)]
        self.calls += 1
        return Diagnosis(answer, 0.8, "flip")


def test_fake_backend_rewinds_panel_state_and_log():
    layout = FarmLayout(FARM)
    backend = FakeSimBackend(layout.panel_records())
    ids = [s.panel_id for s in layout.sites]
    snap = backend.snapshot_panels(ids)
    before = backend.panel(ids[0]).state

    backend.write_panel(ids[0], list(snap.values())[0].state, "note", "t0")
    from solar_twin.schema.pv_module import PanelState

    backend.write_panel(ids[0], PanelState.CRACK, "verdict", "t1")
    assert backend.panel(ids[0]).state == PanelState.CRACK
    assert backend.panel(ids[0]).inspection_log  # log grew

    backend.restore_panels(snap)
    assert backend.panel(ids[0]).state == before
    # The log matters as much as the state: it feeds `history` into the
    # perception prompt, so a leftover entry changes the next repeat's question.
    assert backend.panel(ids[0]).inspection_log == []


def test_repeat_writes_per_repeat_records_and_variance(tmp_path: Path):
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={"repeat": 3},
        farm_cfg=FARM,
        mission_cfg=MISSION,
        scenario_name="unit",
    )
    for i in (1, 2, 3):
        assert (out / f"repeat_{i:02d}" / "results.json").exists()
    assert not (out / "results.json").exists()  # no repeat is "the" result
    var = json.loads((out / "variance.json").read_text())
    assert var["n_runs"] == 3
    assert var["metrics"]["detection_rate"]["n"] == 3
    summary = json.loads((out / "summary.json").read_text())
    assert summary["repeats"] == 3


def test_repeat_ground_truth_is_not_polluted_by_the_previous_run(tmp_path: Path):
    """The whole point of the rewind: `injected_state` must be identical across
    repeats, and the ground-truth stub must therefore score 1.0 every time."""
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={"repeat": 2},
        farm_cfg=FARM,
        mission_cfg=MISSION,
    )
    recs = [
        json.loads((out / f"repeat_{i:02d}" / "results.json").read_text())
        for i in (1, 2)
    ]
    inj = [
        {p["panel_id"]: p["injected_state"] for p in r["panels"]} for r in recs
    ]
    assert inj[0] == inj[1]
    assert any(v != "healthy" for v in inj[0].values())  # faults really were seeded
    assert all(r["metrics"]["detection_rate"] == 1.0 for r in recs)


def test_single_run_layout_is_unchanged(tmp_path: Path):
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={},
        farm_cfg=FARM,
        mission_cfg=MISSION,
    )
    assert (out / "results.json").exists()
    assert not (out / "variance.json").exists()
    rec = json.loads((out / "results.json").read_text())
    assert rec["repeats"] == 1
    assert rec["perception"]["name"] == "ground_truth"


def test_gate_report_is_written_and_judged(tmp_path: Path):
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={},
        farm_cfg=FARM,
        mission_cfg=MISSION,
        kpi_gates={"detection_rate_min": 1.0, "false_fault_rate_max": 0.0},
    )
    gates = json.loads((out / "gates.json").read_text())
    assert gates["passed"] is True
    assert gates["declared"] == 2


def test_breached_gate_is_reported_as_failed(tmp_path: Path):
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={},
        farm_cfg=FARM,
        mission_cfg=MISSION,
        kpi_gates={"detection_rate_min": 1.1},  # unreachable on purpose
    )
    assert json.loads((out / "gates.json").read_text())["passed"] is False


def test_nondeterministic_perception_shows_up_as_a_model_flip(tmp_path: Path):
    """Same seeded world, a perception that changes its mind: the variance
    report must catch it rather than the KPI quietly moving."""
    layout = FarmLayout(FARM)
    backend = FakeSimBackend(layout.panel_records())
    from solar_twin.orchestrator.mission import Fleet, Mission

    fleet = Fleet("bot", "d1", "d2")
    perception = FlipFlopPerception()
    mission = Mission(backend, backend, perception, fleet)
    targets = layout.inspection_targets(MISSION)
    ids = [t.panel_id for t in targets]

    runs = []
    snap = backend.snapshot_panels(ids)
    for i in range(2):
        if i:
            backend.restore_panels(snap)
        res = mission.run(targets)
        runs.append(
            {
                "metrics": {
                    "detection_rate": res.detection_rate,
                    "false_fault_rate": res.false_fault_rate,
                },
                "panels": [
                    {
                        "panel_id": r.panel_id,
                        "injected_state": r.injected_state,
                        "screen_status": r.screen_status,
                        "escalated": r.escalated,
                        "detected_state": r.detected_state,
                        # The fake world has no pixels; force identical frames so
                        # this asserts the attribution logic, not the fake.
                        "screen_frame_sha": "f" * 16,
                        "confirm_frame_sha": "f" * 16 if r.escalated else None,
                        "screen_frame_thumb": bytes([100] * 64).hex(),
                        "confirm_frame_thumb": (
                            bytes([100] * 64).hex() if r.escalated else None
                        ),
                    }
                    for r in res.results
                ],
            }
        )
    rep = V.summarize(runs)
    assert rep.disagreements, "a flip-flopping perception must not look stable"
    assert {d.cause for d in rep.disagreements} == {"model"}
    assert not rep.metrics["detection_rate"].stable


# --- frame digests -------------------------------------------------------- #


def test_frame_digest_is_stable_and_content_sensitive():
    import numpy as np

    a = np.zeros((4, 4, 3), dtype=np.uint8)
    b = a.copy()
    b[0, 0, 0] = 1
    assert frame_digest(a) == frame_digest(a.copy())
    assert frame_digest(a) != frame_digest(b)
    assert len(frame_digest(a)) == 16


def test_frame_digest_tolerates_no_frame_and_junk():
    assert frame_digest(None) is None
    assert frame_digest(object()) is None  # never raises into the mission


def test_mission_records_the_digest_of_the_frame_it_judged():
    """A frameless backend records None — honestly absent, not a fake value."""
    layout = FarmLayout(FARM)
    backend = FakeSimBackend(layout.panel_records())
    from solar_twin.orchestrator.mission import Fleet, Mission
    from solar_twin.perception.ground_truth import GroundTruthPerception

    mission = Mission(backend, backend, GroundTruthPerception(), Fleet("bot", "d1", "d2"))
    res = mission.run(layout.inspection_targets(MISSION))
    assert all(r.screen_frame_sha is None for r in res.results)


# --- frame thumbnails (the noise-tolerant half of the attribution) --------- #


def test_thumbnail_is_stable_under_sub_lsb_noise_but_not_under_a_real_change():
    """Why a thumbnail exists at all: the renderer's measured noise (~0.9/255 per
    pixel) must not read as a different picture, while a shadow must."""
    import numpy as np

    from solar_twin.kpi.variance import thumbnails_differ
    from solar_twin.perception.base import frame_thumbnail

    rng = np.random.default_rng(0)
    base = np.full((480, 640, 3), 120, dtype=np.uint8)
    # Renderer-like noise: +/-1 LSB on about half the pixels (measured profile).
    noisy = np.clip(
        base.astype(np.int16) + rng.integers(-1, 2, base.shape), 0, 255
    ).astype(np.uint8)
    shaded = base.copy()
    shaded[:, :320] = 60  # half the frame in shadow

    assert thumbnails_differ([frame_thumbnail(base), frame_thumbnail(noisy)]) is False
    assert thumbnails_differ([frame_thumbnail(base), frame_thumbnail(shaded)]) is True


def test_thumbnail_encodes_a_fixed_size_grid_and_tolerates_junk():
    import numpy as np

    from solar_twin.perception.base import frame_thumbnail

    t = frame_thumbnail(np.zeros((480, 640, 3), dtype=np.uint8))
    assert len(t) == 8 * 8 * 2  # 64 uint8 cells, hex
    assert frame_thumbnail(None) is None
    assert frame_thumbnail(np.zeros((4, 4, 3), dtype=np.uint8)) is None  # too small
    assert frame_thumbnail(object()) is None


def test_thumbnails_differ_says_cannot_tell_rather_than_guessing():
    from solar_twin.kpi.variance import thumbnails_differ

    good = bytes([10] * 64).hex()
    assert thumbnails_differ([good, None]) is None      # a missing thumbnail
    assert thumbnails_differ([good, "nothex"]) is None  # malformed
    assert thumbnails_differ([good, bytes([10] * 16).hex()]) is None  # size mismatch
    assert thumbnails_differ([]) is None
