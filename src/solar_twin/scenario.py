"""Scenario composition (IF-03) — pure-python, Isaac-free.

A scenario is a small YAML that COMPOSES the static site (`farm.yaml`) and the
fleet/perception wiring (`mission.yaml`) and layers a *dynamic hazard axis* on top
(sun angle, turbine spin, later wind/dust/birds) plus `kpi_gates`. It never forks
the base configs — it `extends` them and deep-merges overrides, so a scenario is a
seeded, reproducible variant, not a copy (`FR-16`, `NFR-02`).

    scn = load_scenario("configs/scenarios/sweeping_shadow.yaml")
    build(scn.farm_cfg, "assets/scn.usd")      # farm_builder consumes the dict
    run(..., farm_cfg=scn.farm_cfg, mission_cfg=scn.mission_cfg)

No Isaac import here (golden rule): pure dict composition, unit-tested without pxr.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (override wins). Nested dicts
    merge key-by-key; every other value (incl. lists) is replaced wholesale."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: str) -> dict:
    import yaml

    with open(path) as f:
        return yaml.safe_load(f) or {}


def _resolve(ref: str, scenario_path: Path) -> str:
    """Resolve an `extends` reference: try it verbatim (repo-relative, how run.py
    is invoked), then relative to the scenario file's directory."""
    if Path(ref).exists():
        return ref
    sibling = scenario_path.parent / ref
    if sibling.exists():
        return str(sibling)
    return ref  # let the open() error name the missing path


@dataclass
class Scenario:
    name: str
    farm_cfg: dict
    mission_cfg: dict
    kpi_gates: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


def compose(
    farm_cfg: dict, mission_cfg: dict, spec: dict
) -> tuple[dict, dict]:
    """Apply a scenario `spec`'s override blocks to base farm/mission dicts.

    `farm_overrides` deep-merges onto the farm (sun, faults, turbines, terrain);
    `mission_overrides` onto the mission (perception, kinematics). A top-level
    `seed` propagates into both so the variant stays reproducible."""
    farm = _deep_merge(farm_cfg, spec.get("farm_overrides", {}))
    mission = _deep_merge(mission_cfg, spec.get("mission_overrides", {}))
    if "seed" in spec:
        farm["seed"] = spec["seed"]
        mission["seed"] = spec["seed"]
    return farm, mission


def load_scenario(path: str) -> Scenario:
    spec = _load_yaml(path)
    ext = spec.get("extends", {})
    if not isinstance(ext, dict) or "farm" not in ext or "mission" not in ext:
        raise ValueError(
            f"{path}: scenario must set `extends: {{farm: ..., mission: ...}}`"
        )
    p = Path(path)
    farm_cfg = _load_yaml(_resolve(ext["farm"], p))
    mission_cfg = _load_yaml(_resolve(ext["mission"], p))
    farm, mission = compose(farm_cfg, mission_cfg, spec)
    return Scenario(
        name=spec.get("name", p.stem),
        farm_cfg=farm,
        mission_cfg=mission,
        kpi_gates=spec.get("kpi_gates", {}),
        meta={k: v for k, v in spec.items() if k not in ("extends",)},
    )
