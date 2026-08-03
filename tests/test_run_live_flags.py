"""CLI plumbing for watching a run live (--gui / --livestream / --live).

Isaac-free: `run.run` is stubbed, so nothing here launches a SimulationApp. What
is under test is the mapping from flags to `sim_opts`, because that mapping is
what decides whether a measurement run silently inherits interpolated motion.
"""

import solar_twin.run as run_mod


def _sim_opts(argv: list[str], monkeypatch) -> dict:
    """Run `main(argv)` with the mission stubbed out; return the sim_opts it built."""
    captured: dict = {}

    def fake_run(farm, mission, backend, runs_dir, sim_opts=None, **kw):
        captured.update(sim_opts or {})
        return None

    monkeypatch.setattr(run_mod, "run", fake_run)
    assert run_mod.main(["farm.yaml", "mission.yaml", *argv]) == 0
    return captured


def test_defaults_are_headless_teleport(monkeypatch):
    """The KPI path: no window, no stream, no interpolation."""
    opts = _sim_opts([], monkeypatch)
    assert opts["headless"] is True
    assert opts["livestream"] is False
    assert opts["live"] is False
    assert opts["video"] is False


def test_gui_opens_a_window_but_does_not_imply_motion(monkeypatch):
    """--gui alone still teleports — interpolation is a separate, costed choice."""
    opts = _sim_opts(["--gui"], monkeypatch)
    assert opts["headless"] is False
    assert opts["live"] is False


def test_livestream_stays_headless(monkeypatch):
    """WebRTC streaming is headless-with-UI, not a local window: asking for a
    window instead would fail on a box with no display."""
    opts = _sim_opts(["--livestream"], monkeypatch)
    assert opts["livestream"] is True
    assert opts["headless"] is True


def test_live_no_longer_requires_video(monkeypatch):
    """The regression this fixes: interpolated motion used to be reachable only
    via --video, so a GUI run showed robots popping between waypoints."""
    opts = _sim_opts(["--gui", "--live"], monkeypatch)
    assert opts["live"] is True
    assert opts["video"] is False


def test_video_and_live_are_independent(monkeypatch):
    opts = _sim_opts(["--video"], monkeypatch)
    assert opts["video"] is True
    assert opts["live"] is False


# --------------------------------------------------------------------------- #
# --hold: stay open after the mission so the plant can be inspected by hand
# --------------------------------------------------------------------------- #


def test_hold_is_off_by_default(monkeypatch):
    """A measurement run must never sit waiting for someone to close a window —
    that would hang CI and every unattended `--repeat` sweep."""
    assert _sim_opts([], monkeypatch)["hold"] is False


def test_hold_reaches_sim_opts(monkeypatch):
    assert _sim_opts(["--gui", "--hold"], monkeypatch)["hold"] is True


def test_hold_composes_with_livestream(monkeypatch):
    """Holding a WebRTC stream open is as useful as holding a window — the stream
    otherwise dies with the mission, which is why runs had to be padded with extra
    panels just to leave time to connect."""
    opts = _sim_opts(["--livestream", "--hold"], monkeypatch)
    assert opts["hold"] is True and opts["livestream"] is True and opts["headless"] is True


def test_hold_headless_without_a_stream_warns_and_does_not_block(tmp_path, capsys):
    """The trap: `--hold` alone leaves `headless=True` with no window and no stream,
    so there is nothing to hold open. It must say so and return, not wait forever."""
    import json

    from solar_twin.run import run

    farm = {
        "seed": 1,
        "grid": {"rows": 1, "cols": 2, "row_pitch": 6.0, "col_pitch": 2.2,
                 "origin": [0.0, 0.0, 0.0]},
        "panel": {"width": 1.0, "length": 2.0, "height": 0.05, "tilt_deg": 20.0,
                  "mount_height": 0.75},
        "faults": {"rate": 0.0, "states": []},
        "terrain": {"kind": "flat"},
    }
    mission = {
        "fleet": {"ground_bot": "bot", "screen_drone": "d1", "confirm_drone": "d2"},
        "kinematics": {"screen_standoff": 2.5, "confirm_standoff": 0.8},
        "escalation": {"screen_suspect_confidence": 0.5},
    }
    out = run(
        farm_path="",
        mission_path="",
        backend_name="fake",
        runs_dir=str(tmp_path),
        sim_opts={"hold": True, "headless": True},
        farm_cfg=farm,
        mission_cfg=mission,
    )
    # The run still completed and wrote its record — holding is never load-bearing.
    assert json.loads((out / "results.json").read_text())["metrics"]["panels_inspected"] > 0
    assert "hold" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------- #
# --physics: step PhysX during the mission
# --------------------------------------------------------------------------- #


def test_physics_is_off_by_default(monkeypatch):
    """⭐ Every KPI on record was measured with physics inert — SimRuntime.step() only
    spun rotors and drew a frame. Turning it on changes what the world does, so the
    default must stay off or historic runs stop being reproducible."""
    assert _sim_opts([], monkeypatch)["physics"] is False


def test_physics_flag_reaches_sim_opts(monkeypatch):
    assert _sim_opts(["--physics"], monkeypatch)["physics"] is True


def test_tonemap_is_off_by_default(monkeypatch):
    """A pixel-scored KPI must not move because of an exposure setting."""
    assert _sim_opts([], monkeypatch)["tonemap"] is False
    assert _sim_opts(["--tonemap"], monkeypatch)["tonemap"] is True


def test_physics_composes_with_the_watching_flags(monkeypatch):
    opts = _sim_opts(["--gui", "--live", "--physics", "--tonemap"], monkeypatch)
    assert opts["physics"] and opts["tonemap"] and opts["live"]
    assert opts["headless"] is False
