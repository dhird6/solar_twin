"""The glass mask, and why its threshold is 2.0.

⚠⚠ This exists because a physically-based sky BROKE the old 1.15 rule and nobody
noticed until a stimulus measurement came back with a mask covering 99% of the
frame. Shadowed desert is lit only by the sky dome, so under a blue sky it goes
blue and passes a low blue-over-red test. The "% dark glass" statistic then
becomes whole-frame brightness -- the exact confound `verify_shade.py` was written
to prevent, and the third time this project has hit that same trap.

Measured on SC-11 (2026-07-31): the fake differential was +23.7 points; re-derived
under the 2.0 rule it is +12.6, against the legacy stage's +13.8.
"""

from __future__ import annotations

import numpy as np
import pytest

from solar_twin.kpi import glass


#: The PV cell's own diffuse constant (`farm_builder._LOOKS["cell_healthy"]`).
CELL = (0.02, 0.04, 0.13)
#: Dry desert earth (`_LOOKS["ground"]`).
SAND = (0.30, 0.25, 0.19)


def _px(rgb, scale=255.0):
    return np.array([[list(c * scale for c in rgb)]], dtype=np.float32)


class TestTheThresholdSeparatesGlassFromSkyLitGround:
    def test_a_pv_cell_is_glass_by_a_wide_margin(self):
        """blue/red for the cell is ~6.5, so 2.0 leaves a lot of headroom."""
        assert CELL[2] / CELL[0] == pytest.approx(6.5, abs=0.01)
        assert glass.glass_mask(_px(CELL)).all()

    def test_sunlit_sand_is_never_glass(self):
        """Warm ground: blue/red < 1, nowhere near any plausible threshold."""
        assert SAND[2] / SAND[0] < 1.0
        assert not glass.glass_mask(_px(SAND)).any()

    def test_shadowed_sand_under_a_blue_sky_defeats_the_OLD_threshold(self):
        """The regression, as an executable demonstration.

        Sand in shadow is lit only by the sky dome, so it takes the sky's colour.
        Modelled here as the sand albedo times a Preetham-ish zenith (70,105,168)
        -- the measured SC-11 zenith from the session log. That product has
        blue/red ~1.5: it passes the old 1.15 rule and fails the new 2.0 one.
        """
        sky = np.array([70.0, 105.0, 168.0]) / 255.0
        shadowed = tuple(s * k for s, k in zip(SAND, sky))
        ratio = shadowed[2] / shadowed[0]

        assert 1.15 < ratio < 2.0, f"blue/red is {ratio:.2f}"
        assert glass.glass_mask(_px(shadowed), ratio=1.15).all()  # old rule: fooled
        assert not glass.glass_mask(_px(shadowed)).any()  # current rule: not fooled

    def test_the_threshold_is_two_and_changing_it_is_a_flagged_decision(self):
        """Pinned, because every stimulus number on record depends on it and the
        last change to this constant invalidated a measurement."""
        assert glass.GLASS_BLUE_OVER_RED == 2.0


class TestDarkFractionIsScoredAgainstTheMaskItself:
    def test_dark_fraction_uses_the_masked_bright_reference(self):
        """A panel half in shadow reads ~0.5 dark REGARDLESS of exposure, because
        the reference is the panel's own P90, not the frame's."""
        bright = np.tile(np.array(CELL) * 255.0 * 4.0, (10, 20, 1)).astype(np.float32)
        dim = bright.copy()
        dim[:, :10] *= 0.2  # half the panel deep in shadow
        share, dark = glass.dark_fraction(dim)
        assert share == pytest.approx(1.0)  # all glass
        assert dark == pytest.approx(0.5, abs=0.05)

    def test_too_few_masked_pixels_returns_no_answer_rather_than_a_number(self):
        """A dark fraction off a handful of pixels is noise wearing a number."""
        frame = np.tile(np.array(SAND) * 255.0, (10, 10, 1)).astype(np.float32)
        share, dark = glass.dark_fraction(frame)
        assert share == 0.0
        assert dark is None

    def test_a_black_pixel_cannot_admit_the_whole_frame(self):
        """Red is floored at 1.0 so `blue > ratio * 0` never trivially passes --
        which matters because the broken PBR stage rendered ground at ~(1,1,1)."""
        assert not glass.glass_mask(_px((0.0, 0.0, 0.0))).any()


def test_both_tools_import_the_same_definition():
    """Structural, not a comment. `verify_shade` and `inspect_frame` each used to
    carry their own copy with a note saying they must agree."""
    from pathlib import Path

    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("verify_shade.py", "inspect_frame.py"):
        path = root / "tools" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "solar_twin.kpi.glass"
            for alias in node.names
        }
        assert "GLASS_BLUE_OVER_RED" in imported, f"{name} does not import the rule"

        # ...and does not then shadow it with a local copy, which is what both
        # files used to carry.
        assigned = {
            t.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
        }
        assert "GLASS_BLUE_OVER_RED" not in assigned, f"{name} redefines the rule"
