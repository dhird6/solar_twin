"""The Isaac-free golden rule, as an executable gate rather than a convention.

CLAUDE.md says: keep every `import omni`/`isaacsim`/`pxr` inside `world/`,
`transport/sim_native.py`, `transport/ros2_bridge.py` — and never in
`orchestrator/`, `perception/base.py`, or `transport/base.py`. Until now nothing
enforced that; the rule lived only in a markdown file, so a single stray
top-level import could have broken Isaac-free tests and CI silently.

**Why this walks the AST instead of grepping.** The project's real discipline is
*where in the file* the import sits: `schema/pv_module.py` imports `pxr` inside
each USD function body on purpose (see its module docstring), so the pure-python
panel contract above it stays testable on a bare interpreter. A grep for
`import pxr` flags that file as a violation; it is the opposite — it is the
pattern to copy. Only imports that execute at module import time count, so this
inspects direct children of `Module.body` and nothing nested inside a function,
a class, a `try:` guard, or an `if TYPE_CHECKING:` block.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = SRC / "solar_twin"

# Import roots that only exist under Isaac Sim's bundled Python on this Spark.
ISAAC_ROOTS = {"omni", "isaacsim", "pxr", "carb"}

# The only places a *module-level* Isaac import is allowed (CLAUDE.md). Paths are
# relative to `src/solar_twin`, POSIX-style.
ISAAC_ALLOWED = (
    "world/",  # the whole Isaac-bound half
    "transport/sim_native.py",
    "transport/ros2_bridge.py",
)


def _modules() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(PKG).as_posix()


def _module_level_imports(path: Path) -> list[tuple[str, int]]:
    """Roots imported when this module is imported — top-level statements only."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in tree.body:  # direct children only: nested imports are lazy
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


def _is_allowed(rel: str) -> bool:
    return any(
        rel.startswith(p) if p.endswith("/") else rel == p for p in ISAAC_ALLOWED
    )


def test_no_module_level_isaac_import_outside_the_allowed_files():
    violations: list[str] = []
    for path in _modules():
        rel = _rel(path)
        if _is_allowed(rel):
            continue
        for root, lineno in _module_level_imports(path):
            if root in ISAAC_ROOTS:
                violations.append(f"{rel}:{lineno} imports {root!r} at module level")
    assert not violations, (
        "Isaac imports must be lazy (inside the function that needs them) outside "
        + ", ".join(ISAAC_ALLOWED)
        + ". Violations:\n  "
        + "\n  ".join(violations)
    )


def test_the_contract_surfaces_are_importable_without_isaac():
    """The interfaces the orchestrator codes against must import on a bare
    interpreter. This is the property that lets `pytest tests/` run with no GPU.
    """
    if any(importlib.util.find_spec(m) for m in ISAAC_ROOTS):
        pytest.skip("Isaac is importable here, so this proves nothing")

    for name in (
        "solar_twin.orchestrator.mission",
        "solar_twin.orchestrator.fake_backend",
        "solar_twin.perception.base",
        "solar_twin.perception.ground_truth",
        "solar_twin.perception.cosmos_reason",
        "solar_twin.transport.base",
        "solar_twin.control.base",
        "solar_twin.control.kinematic_math",
        "solar_twin.control.safe",
        "solar_twin.kpi.gates",
        "solar_twin.kpi.variance",
        "solar_twin.schema.pv_module",
        "solar_twin.run",
    ):
        importlib.import_module(name)


def test_pv_module_keeps_its_pxr_import_inside_the_functions():
    """Guards the pattern the rest of the codebase copies: the panel contract's
    pure half must stay above its USD adapter, not behind it.
    """
    path = PKG / "schema" / "pv_module.py"
    roots = {root for root, _ in _module_level_imports(path)}
    assert not (roots & ISAAC_ROOTS)
    # ...and it really does touch pxr somewhere, or this test is vacuous.
    assert "pxr" in path.read_text(encoding="utf-8")
