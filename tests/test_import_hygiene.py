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


def _module_level_bound_names(path: Path) -> dict[str, int]:
    """The NAMES a module's top-level imports bind, not the roots they come from.

    `import solar_twin.world.textures as tex` binds `tex`; `from pathlib import
    Path` binds `Path`. Those names are what a function body can shadow.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name.split(".")[0]
                bound[name] = node.lineno
    return bound


def _assigned_names(fn: ast.AST):
    """Every name a function body BINDS, with the line that binds it.

    Skips anything the function declares `global`/`nonlocal`: rebinding a
    module-level name you have explicitly claimed is a deliberate act, not a
    shadow. Nested functions are walked too — they shadow just as effectively.
    """
    declared: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id not in declared:
                yield node.id, node.lineno


def test_no_function_shadows_a_module_level_import_alias():
    """A function that rebinds an imported name silently breaks every LATER use
    of that name in the same scope.

    Measured, not hypothetical: `farm_builder.build()` assigned the generated sky
    texture's path to `tex`, which was the module alias for `world.textures`
    imported at the top of the file. The sky lines worked; 27 lines further down
    `tex.write_all(...)` raised `AttributeError: 'str' object has no attribute
    'write_all'` and the whole farm build died. Nothing caught it, because the
    crash needs Isaac to reach -- so it shipped, was committed, and surfaced on
    the first real render.

    This is the cheap Isaac-free guard for that: it is a pure AST property of the
    source, so it holds for the Isaac-bound half exactly as well as the pure half.
    """
    violations: list[str] = []
    for path in _modules():
        source = path.read_text(encoding="utf-8")
        imported = _module_level_bound_names(path)
        if not imported:
            continue
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for name, lineno in _assigned_names(node):
                if name in imported:
                    violations.append(
                        f"{_rel(path)}:{lineno} `{name}` shadows the module-level "
                        f"import bound at line {imported[name]} "
                        f"(in `{node.name}`)"
                    )
    assert not violations, (
        "A local assignment must not reuse an imported name -- every later use of "
        "that name in the same scope gets the local value instead of the module. "
        "Rename the local, or declare `global` if the rebinding is deliberate. "
        "Violations:\n  " + "\n  ".join(violations)
    )


def test_pv_module_keeps_its_pxr_import_inside_the_functions():
    """Guards the pattern the rest of the codebase copies: the panel contract's
    pure half must stay above its USD adapter, not behind it.
    """
    path = PKG / "schema" / "pv_module.py"
    roots = {root for root, _ in _module_level_imports(path)}
    assert not (roots & ISAAC_ROOTS)
    # ...and it really does touch pxr somewhere, or this test is vacuous.
    assert "pxr" in path.read_text(encoding="utf-8")
