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
