"""Keep the docs' test-count claim true, mechanically.

`docs/TASKS.md` carries its own warning that its status block "keeps recurring"
as stale — it has claimed "nothing pushed" after a push, listed the DEM as to-do
after it shipped, warned about a `build_keepouts` fix two sessions after the fix
landed, and quoted three different test counts (204, 247, 294) none of which were
current. Every one of those was caught by a human re-reading prose.

The test count is the one claim in that family a machine can check, so it is
checked here rather than trusted. This does not make the *prose* fresh — nothing
can — but it removes the number that went stale most often, and it fails on the
PR rather than in a status report weeks later.

Deliberately narrow: it asserts only that a stated "N Isaac-free tests" matches
what pytest actually collects. Claims that cannot be mechanically verified are
left to `SESSIONS.md` and review.

**Why it measures *collected* tests, and only off-Isaac.** The three `pxr`-guarded
modules call `pytest.importorskip` at module scope, so without `pxr` they collect
*zero* tests rather than collecting-then-skipping them. The collected count is
therefore a property of the interpreter: run the suite under `./python.sh`, where
`pxr` exists, and it legitimately goes up. Asserting an exact match everywhere
would fail on the Spark for being right, so this only runs where Isaac is absent
— which is precisely the interpreter the phrase "Isaac-free tests" describes.
Collection is also stabler than the pass count, which moves with whether the
optional `imageio` extra happens to be installed.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Present only under Isaac Sim's bundled Python; their presence changes what
# collects, so the claim is only meaningful when they are all absent.
ISAAC_ROOTS = ("omni", "isaacsim", "pxr", "carb")

# Files that state a test count, and are therefore load-bearing for a reader
# deciding whether the branch is healthy.
DOCS_CLAIMING_A_COUNT = ("README.md", "docs/TASKS.md")

# "298 Isaac-free tests", tolerating markdown emphasis and a line break between
# the number and the words (TASKS.md wraps mid-phrase).
_CLAIM = re.compile(r"(\d+)\s*\**\s*Isaac-free\s+tests", re.IGNORECASE)


def _collected_test_count() -> int:
    """What pytest actually collects, from a subprocess so there is no recursion.

    `--collect-only` does not execute tests, so this is a parse of the real
    collection rather than a second opinion about it.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    match = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
    if not match:
        pytest.skip(
            "could not parse a collected-test count from pytest output; "
            "this guard is advisory, not a reason to fail an unrelated change"
        )
    return int(match.group(1))


@pytest.mark.parametrize("rel", DOCS_CLAIMING_A_COUNT)
def test_stated_test_count_matches_reality(rel: str):
    if any(importlib.util.find_spec(m) for m in ISAAC_ROOTS):
        pytest.skip(
            "Isaac is importable here, so the pxr-guarded modules collect too and "
            "the count is legitimately higher than the Isaac-free claim"
        )

    path = ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} absent")

    text = " ".join(path.read_text(encoding="utf-8").split())
    claims = [int(m.group(1)) for m in _CLAIM.finditer(text)]
    if not claims:
        pytest.skip(f"{rel} states no test count — nothing to keep fresh")

    actual = _collected_test_count()
    stale = sorted({c for c in claims if c != actual})
    assert not stale, (
        f"{rel} claims {stale} Isaac-free tests; pytest collects {actual}. "
        f"Update the number in {rel} (this doc's count has gone stale three "
        f"times — that is why this test exists)."
    )
