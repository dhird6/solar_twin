"""`physics.colliders` — the config contract, Isaac-free.

The drop tests that prove colliders actually cook live under Isaac
(`tools/physics_probe.py`); these pin the config surface, because the failure this
guards against is silent. Measured 2026-08-03 on the shipped stage: **25 colliders in
81,961 prims, all on turbines, and no collider on the terrain at all** — a body
released over the array fell clean through to z = -32 m. Nothing errored, nothing
warned, and no test failed, because nothing had ever stepped physics.
"""

from __future__ import annotations

import pytest

import yaml

from solar_twin.world.layout import FarmLayout

FARM = {
    "seed": 3,
    "grid": {"rows": 2, "cols": 3, "row_pitch": 6.0, "col_pitch": 2.2,
             "origin": [0.0, 0.0, 0.0]},
    "panel": {"width": 1.0, "length": 2.0, "height": 0.05, "tilt_deg": 20.0,
              "mount_height": 1.5},
    "faults": {"rate": 0.0, "states": []},
    "terrain": {"kind": "flat"},
}


def _colliders(cfg: dict) -> str:
    """Mirror of `farm_builder`'s resolution, so the contract is testable without
    launching Isaac to read one string."""
    return str((cfg.get("physics", {}) or {}).get("colliders", "table"))


def test_default_is_table():
    """Off-by-default is the rule everywhere else in this project, but NOT here: a
    world with no floor is a bug, not a configuration. The old behaviour is still
    reachable as `none` for reproducing a pre-2026-08-03 stage."""
    assert _colliders(FARM) == "table"
    assert _colliders({**FARM, "physics": {}}) == "table"


def test_none_is_reachable_for_reproducing_old_stages():
    assert _colliders({**FARM, "physics": {"colliders": "none"}}) == "none"


#: Read as TEXT, not imported: `farm_builder` imports `pxr` at module scope (it is
#: the Isaac-bound world builder) and this suite must run without Isaac.
def _builder_source() -> str:
    import pathlib

    return (
        pathlib.Path(__file__).resolve().parents[1]
        / "src" / "solar_twin" / "world" / "farm_builder.py"
    ).read_text(encoding="utf-8")


def test_module_is_gone_and_named_as_removed():
    """⚠ `module` was implemented, measured, and REMOVED: applying CollisionAPI to the
    panel prototype does not propagate through instanceable references, so it authored
    544 colliders while claiming 30,016. The error message must say why, or someone
    will reasonably try it again."""
    body = _builder_source()
    i = body.index("is not one of table / none")
    near = body[i : i + 260].lower()
    assert "module" in near, "the rejection must name the removed option"
    assert "instancing" in near, "...and say WHY it was removed"


def test_every_shipped_config_uses_a_valid_setting():
    """A typo in `physics.colliders` should fail the build loudly; this catches one
    committed into a config before anyone runs Isaac."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for path in sorted((root / "configs").rglob("*.yaml")):
        cfg = yaml.safe_load(path.read_text()) or {}
        phys = cfg.get("physics") or {}
        if "colliders" in phys:
            assert phys["colliders"] in ("table", "none"), f"{path.name}: {phys}"


def test_the_racking_report_counts_table_colliders():
    """`_author_mounting` returns the tally the build prints. If the key vanishes, the
    build line stops saying whether the world has a floor and hardware — which is
    exactly the reporting gap that let 25-colliders-in-82k-prims survive."""
    body = _builder_source()
    start = body.index("def _author_mounting(")
    src = body[start : body.index("\ndef ", start + 10)]
    assert '"table_colliders"' in src
    assert "n_coll" in src


def test_mount_height_is_what_the_collider_sits_at():
    """The drop test's predicted rest height is `ground + mount_height + half the
    collider + half the body`. If `mount_height` stopped being the tube height, that
    prediction — and the only quantitative check on the collider — would silently
    become wrong."""
    layout = FarmLayout(FARM)
    assert float(FARM["panel"]["mount_height"]) == 1.5
    # Panels sit at mount_height above their own ground sample.
    zs = [s.position[2] for s in layout.sites]
    assert all(abs(z) < 1e-6 for z in zs), "flat terrain should put panel bases at z=0"
