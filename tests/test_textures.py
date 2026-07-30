"""Procedural PBR texture maps — pure, no Isaac, no GPU.

These pin the two properties that keep the PBR change from aliasing into an
existing measurement: the albedo modulation's mean is exactly 1.0 (so mean
albedo does not drift), and it is achromatic (so it cannot shift the ground's
R-B balance, which is the quantity Session 10c used to catch a sky dome lighting
the desert floor blue).
"""

from __future__ import annotations

import pytest

from solar_twin.world import textures as T


def test_every_textured_surface_is_a_balance_of_plant_surface():
    """Panel glass and frame must NOT be here — texturing them changes what the
    soiling bake blends against and re-opens the bright-frame-as-hotspot bug."""
    assert set(T.SURFACES) == {
        "ground", "road", "concrete", "equipment", "structure", "fence_frame",
    }


def test_deferred_panel_surfaces_are_never_textured():
    """The guard, as an assertion rather than a comment. `panel_frame` feeds the
    soiling bake's substrate colour; the `cell_*` looks are what it blends over."""
    assert T.DEFERRED_SURFACES.isdisjoint(T.SURFACES)
    assert "panel_frame" in T.DEFERRED_SURFACES
    # The pre-split name must not creep back in as a textured surface either.
    assert "frame" not in T.SURFACES


class TestNoise:
    def test_fbm_is_in_range_and_roughly_centred(self):
        f = T.fbm(64, 4, seed=11)
        assert 0.0 <= f.min() and f.max() <= 1.0
        assert 0.3 < f.mean() < 0.7

    def test_fbm_is_deterministic_for_a_seed(self):
        import numpy as np

        assert np.array_equal(T.fbm(32, 3, seed=5), T.fbm(32, 3, seed=5))

    def test_fbm_differs_between_seeds(self):
        import numpy as np

        assert not np.array_equal(T.fbm(32, 3, seed=5), T.fbm(32, 3, seed=6))

    def test_fbm_tiles(self):
        """A non-tiling map shows a seam on every road quad."""
        import numpy as np

        f = T.fbm(64, 4, seed=7)
        # Wrapping means opposite edges are neighbours: their difference should be
        # no larger than a typical interior step.
        interior = np.abs(np.diff(f, axis=1)).mean()
        seam = np.abs(f[:, 0] - f[:, -1]).mean()
        assert seam < 6.0 * interior

    def test_normal_map_is_unit_length_and_wraps(self):
        import numpy as np

        h = T.fbm(64, 4, seed=9)
        n = T.height_to_normal(h, 0.06)
        v = n * 2.0 - 1.0
        assert np.abs(np.linalg.norm(v, axis=-1) - 1.0).max() < 1e-9
        assert 0.0 <= n.min() and n.max() <= 1.0

    def test_flat_height_gives_a_flat_normal(self):
        import numpy as np

        n = T.height_to_normal(np.full((16, 16), 0.5), 0.1)
        assert n[..., 0] == pytest.approx(0.5)
        assert n[..., 1] == pytest.approx(0.5)
        assert n[..., 2] == pytest.approx(1.0)


class TestTextureSet:
    @pytest.mark.parametrize("name", sorted(T.SURFACES))
    def test_albedo_modulation_mean_is_one(self, name):
        """THE invariant: the map modulates, it does not re-tint. If the mean
        drifts, the ground's measured 0.30 albedo drifts with it."""
        ts = T.make_texture_set(T.SURFACES[name], size=128)
        assert ts.albedo_mean == pytest.approx(1.0, abs=0.005)

    @pytest.mark.parametrize("name", sorted(T.SURFACES))
    def test_albedo_is_achromatic(self, name):
        """A per-channel modulation would move the ground R-B balance and alias
        into the Session-10c lighting measurement."""
        import numpy as np

        a = T.make_texture_set(T.SURFACES[name], size=64).albedo
        assert np.array_equal(a[..., 0], a[..., 1])
        assert np.array_equal(a[..., 1], a[..., 2])

    @pytest.mark.parametrize("name", sorted(T.SURFACES))
    def test_shapes_and_dtypes(self, name):
        import numpy as np

        ts = T.make_texture_set(T.SURFACES[name], size=64)
        assert ts.albedo.shape == (64, 64, 3) and ts.albedo.dtype == np.uint8
        assert ts.roughness.shape == (64, 64) and ts.roughness.dtype == np.uint8
        assert ts.normal.shape == (64, 64, 3) and ts.normal.dtype == np.uint8

    @pytest.mark.parametrize("name", sorted(T.SURFACES))
    def test_roughness_stays_inside_the_declared_band(self, name):
        spec = T.SURFACES[name]
        r = T.make_texture_set(spec, size=64).roughness.astype(float) / 255.0
        assert r.min() >= max(0.0, spec.rough_mid - spec.rough_var) - 0.01
        assert r.max() <= min(1.0, spec.rough_mid + spec.rough_var) + 0.01

    def test_rebuild_is_byte_identical(self):
        """A stage must be reproducible from a script + config."""
        import numpy as np

        a = T.make_texture_set(T.SURFACES["ground"], size=64)
        b = T.make_texture_set(T.SURFACES["ground"], size=64)
        assert np.array_equal(a.albedo, b.albedo)
        assert np.array_equal(a.roughness, b.roughness)
        assert np.array_equal(a.normal, b.normal)

    def test_surfaces_do_not_share_a_pattern(self):
        import numpy as np

        g = T.make_texture_set(T.SURFACES["ground"], size=64).albedo
        r = T.make_texture_set(T.SURFACES["road"], size=64).albedo
        assert not np.array_equal(g, r)

    def test_painted_metal_is_smoother_than_gravel(self):
        """Sanity on the physical ordering, not just that numbers exist."""
        eq = T.make_texture_set(T.SURFACES["equipment"], size=64).roughness.mean()
        rd = T.make_texture_set(T.SURFACES["road"], size=64).roughness.mean()
        assert eq < rd


class TestTintedAlbedo:
    #: The real ground constant from farm_builder._LOOKS — the measured 0.30 albedo.
    GROUND = (0.30, 0.25, 0.19)

    def test_channel_means_equal_the_diffuse_constant(self):
        """Mean albedo and hue are preserved exactly, so the textured ground is
        photometrically the same surface as the flat one."""
        ts = T.make_texture_set(T.SURFACES["ground"], size=256)
        a = T.tinted_albedo(ts, self.GROUND).astype(float) / 255.0
        for i, c in enumerate(self.GROUND):
            assert a[..., i].mean() == pytest.approx(c, abs=0.002)

    def test_r_minus_b_balance_is_preserved(self):
        """Session 10c caught a blue-lit ground via R-B. The texture must not
        contribute to that signal at all."""
        ts = T.make_texture_set(T.SURFACES["ground"], size=256)
        a = T.tinted_albedo(ts, self.GROUND).astype(float)
        got = a[..., 0].mean() - a[..., 2].mean()
        want = (self.GROUND[0] - self.GROUND[2]) * 255.0
        assert got == pytest.approx(want, abs=1.0)

    def test_still_varies_spatially(self):
        """Preserving the mean must not have flattened it into the old constant."""
        ts = T.make_texture_set(T.SURFACES["ground"], size=128)
        a = T.tinted_albedo(ts, self.GROUND).astype(float)
        assert a[..., 0].std() > 2.0


def test_write_texture_set_tints_when_given_a_colour(tmp_path):
    from PIL import Image
    import numpy as np

    p = T.write_texture_set(
        str(tmp_path), T.SURFACES["ground"], size=64, rgb=(0.30, 0.25, 0.19)
    )
    arr = np.asarray(Image.open(p["albedo"])).astype(float) / 255.0
    assert arr[..., 0].mean() == pytest.approx(0.30, abs=0.01)
    assert arr[..., 2].mean() == pytest.approx(0.19, abs=0.01)


def test_write_texture_set_emits_three_maps(tmp_path):
    paths = T.write_texture_set(str(tmp_path), T.SURFACES["road"], size=32)
    assert set(paths) == {"albedo", "roughness", "normal"}
    for p in paths.values():
        assert (tmp_path / p.rsplit("/", 1)[-1]).exists()


def test_write_all_covers_every_surface(tmp_path):
    out = T.write_all(str(tmp_path), size=32)
    assert set(out) == set(T.SURFACES)


class TestFrameSplit:
    #: The one shared tuple, from farm_builder._LOOKS. The split must be a pure
    #: separation of materials, not a re-tune of either.
    ALUMINIUM = (0.62, 0.63, 0.66)

    def test_fence_frame_is_textured_and_panel_frame_is_not(self):
        assert "fence_frame" in T.SURFACES
        assert "panel_frame" not in T.SURFACES

    def test_fence_texture_preserves_the_shared_base_look(self):
        """`fence_frame` inherits the panel frame's values, so the split alone is
        visually a no-op — only the fence's new texture changes anything."""
        ts = T.make_texture_set(T.SURFACES["fence_frame"], size=256)
        a = T.tinted_albedo(ts, self.ALUMINIUM).astype(float) / 255.0
        for i, c in enumerate(self.ALUMINIUM):
            assert a[..., i].mean() == pytest.approx(c, abs=0.003)

    def test_fence_does_not_share_a_pattern_with_the_structure_look(self):
        import numpy as np

        f = T.make_texture_set(T.SURFACES["fence_frame"], size=64).albedo
        s = T.make_texture_set(T.SURFACES["structure"], size=64).albedo
        assert not np.array_equal(f, s)

    def test_galvanised_steel_is_smoother_than_concrete(self):
        f = T.make_texture_set(T.SURFACES["fence_frame"], size=64).roughness.mean()
        c = T.make_texture_set(T.SURFACES["concrete"], size=64).roughness.mean()
        assert f < c
