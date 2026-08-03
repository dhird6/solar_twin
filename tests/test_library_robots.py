"""The library-rover registry — Isaac-free half.

The Isaac-bound half (does the asset actually resolve and articulate) cannot run
here; `tools/probe_library_robots.py` is that check and its measurements are what
`LIBRARY_ROVERS` records. These tests guard the registry's *claims* so a URL or a
dimension cannot rot silently.
"""

from __future__ import annotations

import pytest

from solar_twin.world.robot_builder import ISAAC_ASSET_ROOT, LIBRARY_ROVERS


def test_every_entry_has_a_url_and_measured_dimensions():
    for name, entry in LIBRARY_ROVERS.items():
        assert entry["url"].startswith(ISAAC_ASSET_ROOT), f"{name} points off-root"
        assert entry["url"].endswith(".usd"), f"{name} is not a USD"
        lwh = entry["measured_lwh_m"]
        assert len(lwh) == 3 and all(0.05 < v < 5.0 for v in lwh), (
            f"{name} has implausible dimensions {lwh} — these are MEASURED metres, "
            "so a value outside this range means a unit error crept in"
        )


def test_the_asset_root_is_pinned_to_a_version():
    """Unpinned, a bucket reorganisation would silently change what we simulate."""
    assert "/Isaac/6.0" in ISAAC_ASSET_ROOT


def test_nova_carter_names_its_driven_wheels_and_not_its_casters():
    """Carter has passive caster wheels too; driving those would be wrong."""
    wheels = LIBRARY_ROVERS["nova_carter"]["wheel_prims"]
    assert set(wheels) == {"wheel_left", "wheel_right"}
    assert not any("caster" in w for w in wheels)


def test_the_registry_does_not_claim_to_ship_a_husky():
    """⭐ The trap this guards.

    `fleet_specs.CLEARPATH_HUSKY` is what our rover spec and its 3 h battery come
    from, and the library does NOT have a Husky. If someone later adds a `husky`
    key pointing at a Jackal or Dingo asset, the twin would render one machine while
    the sortie planner spends another's endurance — so the absence is asserted.
    """
    assert "husky" not in LIBRARY_ROVERS
    for name, entry in LIBRARY_ROVERS.items():
        assert "husky" not in entry["url"].lower(), (
            f"{name} points at a Husky asset; if NVIDIA has added one, update "
            "fleet_specs in the SAME change or the spec and the mesh disagree"
        )


def test_library_rovers_are_all_smaller_than_our_husky_spec_or_documented():
    """Sanity: these are real sub-metre inspection robots, not vehicles."""
    for name, entry in LIBRARY_ROVERS.items():
        length = max(entry["measured_lwh_m"])
        assert length < 1.5, f"{name} is {length} m — too large for a row-inspection bot"


def test_unknown_platform_raises_rather_than_falling_back(monkeypatch):
    """A silent fallback would mean a run reporting nova_carter while rendering a box."""
    from solar_twin.world import robot_builder

    with pytest.raises(ValueError, match="unknown library rover"):
        robot_builder.build_ugv_library(None, "/World/X", "definitely_not_a_robot")


def test_walk_visits_every_descendant():
    """`_discover_wheels` depends on this, and a shallow walk would find nothing
    on assets that nest their links (Jackal does: front_left_wheel_link)."""
    from solar_twin.world.robot_builder import _walk

    class _P:
        def __init__(self, name, kids=()):
            self._n, self._k = name, list(kids)

        def GetName(self):
            return self._n

        def GetChildren(self):
            return self._k

    tree = _P("root", [_P("a", [_P("a1"), _P("a2")]), _P("b")])
    assert sorted(p.GetName() for p in _walk(tree)) == ["a", "a1", "a2", "b", "root"]
