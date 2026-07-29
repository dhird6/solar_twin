"""Run-to-run variance aggregation — pure-python, no Isaac.

The reference case is the real one from SESSIONS.md 2026-07-28: two identical
runs, one panel (R258-C013) diagnosed `soiled` in run A and `hotspot` in run B.
These tests pin how that gets reported — and how the frame digests decide
whether the renderer or the model moved.
"""

from __future__ import annotations

from solar_twin.kpi import variance as V


#: An 8x8 uint8 thumbnail as hex, all cells the same value. `_thumb(100)` and
#: `_thumb(101)` are the SAME picture (1 LSB apart, under THUMB_TOLERANCE);
#: `_thumb(100)` and `_thumb(140)` are different pictures.
def _thumb(value: int) -> str:
    return bytes([value] * 64).hex()


def _panel(pid, injected="healthy", detected="healthy", screen="clean", sha="a" * 16,
           confirm_sha=None, thumb=None, confirm_thumb=None):
    return {
        "panel_id": pid,
        "injected_state": injected,
        "screen_status": screen,
        "escalated": screen == "suspect",
        "detected_state": detected,
        "note": "",
        "screen_frame_sha": sha,
        "confirm_frame_sha": confirm_sha,
        "screen_frame_thumb": _thumb(100) if thumb is None else thumb,
        "confirm_frame_thumb": confirm_thumb,
    }


def _run(panels, ffr=0.0, dr=1.0):
    return {
        "metrics": {
            "false_fault_rate": ffr,
            "detection_rate": dr,
            "faults_detected": sum(1 for p in panels if p["escalated"]),
            "panels_inspected": len(panels),
        },
        "panels": panels,
    }


def test_identical_runs_report_a_stable_spread():
    runs = [_run([_panel("R01-C001"), _panel("R01-C002")]) for _ in range(3)]
    rep = V.summarize(runs)
    assert rep.n_runs == 3
    assert rep.panels_compared == 2
    assert rep.disagreements == []
    assert rep.agreement_rate == 1.0
    spread = rep.metrics["false_fault_rate"]
    assert spread.stable and spread.range == 0.0
    assert "identical across repeats" in spread.quote()


def test_spread_is_quoted_with_a_range_when_it_varies():
    runs = [_run([_panel("R01-C001")], ffr=f) for f in (0.0, 0.02, 0.01)]
    spread = V.summarize(runs).metrics["false_fault_rate"]
    assert not spread.stable
    assert (spread.min, spread.max, spread.median) == (0.0, 0.02, 0.01)
    assert "range" in spread.quote()


def test_identical_frames_but_different_verdicts_blames_the_model():
    """The SESSIONS.md R258-C013 case, with the pixels proven identical."""
    a = _run([_panel("R258-C013", injected="soiled", detected="soiled",
                     screen="suspect", confirm_sha="c" * 16,
                     confirm_thumb=_thumb(100))])
    b = _run([_panel("R258-C013", injected="soiled", detected="hotspot",
                     screen="suspect", confirm_sha="c" * 16,
                     # 1 LSB of renderer noise — the same picture, not a new one.
                     thumb=_thumb(101), confirm_thumb=_thumb(101))])
    rep = V.summarize([a, b])
    assert len(rep.disagreements) == 1
    d = rep.disagreements[0]
    assert d.detected_states == ("soiled", "hotspot")
    assert d.cause == "model"
    assert d.frames_identical is True
    assert rep.causes == {"model": 1}


def test_different_frames_blames_the_renderer():
    a = _run([_panel("R01-C001", detected="healthy", sha="a" * 16, thumb=_thumb(100))])
    b = _run([_panel("R01-C001", detected="soiled", screen="suspect", sha="b" * 16,
                     thumb=_thumb(160))])  # 60 LSB: a different picture
    rep = V.summarize([a, b])
    assert rep.disagreements[0].cause == "render"
    assert rep.disagreements[0].frames_identical is False


def test_missing_digests_are_unknown_not_guessed():
    a = _run([_panel("R01-C001", detected="healthy", sha=None, thumb="")])
    b = _run([_panel("R01-C001", detected="soiled", screen="suspect", sha=None,
                     thumb="")])
    rep = V.summarize([a, b])
    assert rep.disagreements[0].cause == "unknown"
    assert rep.disagreements[0].frames_identical is None


def test_confirm_frame_difference_is_reported_as_both():
    # Same screening frame, but the close pass saw different pixels — the two
    # causes cannot be separated on this evidence, so say so.
    a = _run([_panel("R01-C001", detected="soiled", screen="suspect",
                     confirm_sha="1" * 16, confirm_thumb=_thumb(100))])
    b = _run([_panel("R01-C001", detected="hotspot", screen="suspect",
                     confirm_sha="2" * 16, confirm_thumb=_thumb(160))])
    assert V.summarize([a, b]).disagreements[0].cause == "both"


def test_panels_missing_from_some_repeats_are_flagged_not_averaged():
    a = _run([_panel("R01-C001"), _panel("R01-C002")])
    b = _run([_panel("R01-C001")])
    rep = V.summarize([a, b])
    assert rep.partial_panels == ["R01-C002"]
    assert rep.panels_compared == 1  # the odd panel is excluded, and named


def test_metric_absent_from_one_run_is_not_summarized():
    a = _run([_panel("R01-C001")])
    b = _run([_panel("R01-C001")])
    del b["metrics"]["false_fault_rate"]
    rep = V.summarize([a, b])
    assert "false_fault_rate" not in rep.metrics  # no partial spread
    assert "detection_rate" in rep.metrics


def test_empty_input_is_safe():
    rep = V.summarize([])
    assert rep.n_runs == 0 and rep.agreement_rate == 0.0


def test_report_serializes_and_describes():
    a = _run([_panel("R01-C001", detected="healthy")])
    b = _run([_panel("R01-C001", detected="soiled", screen="suspect")])
    rep = V.summarize([a, b])
    d = rep.to_dict()
    assert d["n_runs"] == 2 and d["disagreements"][0]["panel_id"] == "R01-C001"
    text = rep.describe()
    assert "variance over N=2" in text and "R01-C001" in text


# --- renderer stability, measured even when nothing flips ------------------ #


def test_frame_stability_is_reported_when_all_verdicts_agree():
    """The RISK-24 question. A repeat set with zero flips still says whether the
    renderer produced identical pixels — otherwise renderer determinism is only
    ever observable by accident, when a verdict happens to change."""
    runs = [_run([_panel("R01-C001", sha="a" * 16), _panel("R01-C002", sha="b" * 16)])
            for _ in range(3)]
    rep = V.summarize(runs)
    assert rep.disagreements == []
    assert rep.frames.compared == 2
    assert rep.frames.identical == 2
    assert rep.frames.changed == 0
    assert "renderer stable" in rep.frames.verdict


def test_frame_stability_separates_sampling_noise_from_a_real_difference():
    """The measured reality on this build: bits differ every time, the picture
    does not. Both must be reported, or renderer noise reads as a scene change."""
    a = _run([_panel("R01-C001", sha="a" * 16, thumb=_thumb(100))])
    b = _run([_panel("R01-C001", sha="z" * 16, thumb=_thumb(100))])
    rep = V.summarize([a, b])
    assert rep.frames.changed == 1           # not bit-identical...
    assert rep.frames.same_picture == 1      # ...but the same picture
    assert "sampling noise" in rep.frames.verdict

    c = _run([_panel("R01-C001", sha="q" * 16, thumb=_thumb(180))])
    rep2 = V.summarize([a, c])
    assert rep2.frames.different_picture == 1
    assert "MATERIALLY different" in rep2.frames.verdict


def test_frame_stability_says_unmeasured_without_digests():
    runs = [_run([_panel("R01-C001", sha=None, thumb="")]) for _ in range(2)]
    rep = V.summarize(runs)
    assert rep.frames.unknown == 1
    assert "unmeasured" in rep.frames.verdict


def test_different_picture_panels_are_named():
    """`changed_panels` lists every not-bit-identical panel, which on a
    stochastic renderer is all of them and names nothing. The panels whose
    *picture* moved are the actionable list, so they get their own."""
    a = _run([_panel("R01-C001", thumb=_thumb(100)), _panel("R01-C002", thumb=_thumb(100))])
    b = _run([_panel("R01-C001", thumb=_thumb(100)), _panel("R01-C002", thumb=_thumb(200))])
    rep = V.summarize([a, b])
    assert rep.frames.different_picture_panels == ["R01-C002"]
    assert "R01-C002" in rep.frames.verdict


def test_tolerance_is_the_calibrated_one():
    """Pinned so a future edit to THUMB_TOLERANCE is a deliberate act with a
    measurement behind it, not a quiet drift (see its docstring for the data)."""
    assert V.THUMB_TOLERANCE == 3.0
    # 2.2 LSB was the worst settled same-scene pose measured on this build.
    assert V.thumbnails_differ([_thumb(100), _thumb(102)]) is False
    # 35 LSB was the smallest real difference measured (shaded vs. control).
    assert V.thumbnails_differ([_thumb(100), _thumb(135)]) is True
