"""Real-DEM terrain sampling and torque-tube fitting (pure, numpy only, no Isaac)."""

import math
from pathlib import Path

import numpy as np
import pytest

from solar_twin.world.dem import DemTerrain, fit_line
from solar_twin.world.layout import FarmLayout, terrain_height

SIDECAR = Path(__file__).resolve().parents[1] / "assets/dem/khavda_block02.yaml"
SITE = Path(__file__).resolve().parents[1] / "configs/layouts/khavda_a10b_block02.yaml"


# --- fit_line: the installer's problem ------------------------------------- #

def test_fit_line_is_exact_on_a_straight_slope():
    samples = [(s, 10.0 + 0.02 * s) for s in range(0, 130, 10)]
    a, b, resid = fit_line(samples)
    assert a == pytest.approx(10.0)
    assert b == pytest.approx(0.02)
    assert resid == pytest.approx(0.0, abs=1e-9)


def test_fit_line_residual_is_the_pile_height_variation():
    """A tube over a hump cannot follow it. The residual must report how far the
    ground departs from the fitted beam — that is a real engineering number, not
    a diagnostic."""
    samples = [(0.0, 0.0), (50.0, 1.0), (100.0, 0.0)]
    a, b, resid = fit_line(samples)
    assert b == pytest.approx(0.0)          # symmetric hump -> level tube
    assert resid == pytest.approx(2.0 / 3.0, abs=1e-6)


def test_fit_line_handles_degenerate_input():
    assert fit_line([]) == (0.0, 0.0, 0.0)
    assert fit_line([(5.0, 3.0)]) == (3.0, 0.0, 0.0)
    # All samples at one station: no slope is defined, so report the spread.
    a, b, resid = fit_line([(0.0, 1.0), (0.0, 3.0)])
    assert b == 0.0 and a == pytest.approx(2.0) and resid == pytest.approx(1.0)


# --- DemTerrain ------------------------------------------------------------ #

def _synthetic_dem(tmp_path, values, step=20.0, origin=(1000.0, 2000.0)):
    import yaml

    grid = np.array(values, dtype=np.float32)
    np.save(tmp_path / "g.npy", grid)
    (tmp_path / "g.yaml").write_text(
        yaml.safe_dump(
            {
                "grid_file": "g.npy",
                "origin_easting": origin[0],
                "origin_northing": origin[1],
                "step_m": step,
                "nx": grid.shape[1],
                "ny": grid.shape[0],
            }
        )
    )
    return str(tmp_path / "g.yaml")


def test_bilinear_interpolation_between_cells(tmp_path):
    path = _synthetic_dem(tmp_path, [[0.0, 10.0], [0.0, 10.0]])
    dem = DemTerrain.load(path, 1000.0, 2000.0, datum="absolute")
    assert dem.height(0.0, 0.0) == pytest.approx(0.0)
    assert dem.height(20.0, 0.0) == pytest.approx(10.0)
    assert dem.height(10.0, 0.0) == pytest.approx(5.0)     # halfway across a cell
    assert dem.height(5.0, 0.0) == pytest.approx(2.5)


def test_sampling_outside_the_grid_clamps_instead_of_falling_to_zero(tmp_path):
    """The ground mesh reaches kilometres past the DEM patch. Beyond it the
    terrain must continue at the edge elevation — returning 0.0 would tear a
    cliff right around the site."""
    path = _synthetic_dem(tmp_path, [[7.0, 7.0], [7.0, 7.0]])
    dem = DemTerrain.load(path, 1000.0, 2000.0, datum="absolute")
    assert dem.height(-5000.0, -5000.0) == pytest.approx(7.0)
    assert dem.height(5000.0, 5000.0) == pytest.approx(7.0)


def test_datum_puts_the_site_at_stage_zero(tmp_path):
    """A DEM gives absolute elevation; authoring a plant at its true 4 m above
    sea level would silently shift every waypoint standoff."""
    path = _synthetic_dem(tmp_path, [[100.0, 102.0], [104.0, 106.0]])
    absolute = DemTerrain.load(path, 1000.0, 2000.0, datum="absolute")
    relative = DemTerrain.load(path, 1000.0, 2000.0, datum="hardware_mean")
    assert absolute.height(0.0, 0.0) == pytest.approx(100.0)
    assert relative.datum_m == pytest.approx(103.0)         # grid mean
    assert relative.height(0.0, 0.0) == pytest.approx(-3.0)
    # Relief is preserved: only the offset moved.
    assert relative.relief_m == pytest.approx(absolute.relief_m)


def test_site_origin_shifts_the_sample_point(tmp_path):
    """Stage (0,0) maps to the SITE's survey anchor, not the DEM grid's corner —
    the grid is padded around the site."""
    path = _synthetic_dem(tmp_path, [[0.0, 10.0], [0.0, 10.0]], origin=(1000.0, 2000.0))
    # Site anchored one full cell east of the grid origin.
    dem = DemTerrain.load(path, 1020.0, 2000.0, datum="absolute")
    assert dem.height(0.0, 0.0) == pytest.approx(10.0)


# --- the real Khavda DEM --------------------------------------------------- #

@pytest.mark.skipif(not SIDECAR.exists(), reason="DEM not fetched (tools/dem_fetch.py)")
def test_real_khavda_dem_is_flat_but_not_zero():
    """Khavda is in the Rann of Kutch: genuinely flat, a few metres above sea
    level. If this ever reports tens of metres of relief, the wrong tile or the
    wrong CRS is being sampled."""
    import yaml

    meta = yaml.safe_load(SIDECAR.read_text())
    assert 0.0 < meta["elev_mean_m"] < 30.0, meta["elev_mean_m"]
    relief = meta["elev_max_m"] - meta["elev_min_m"]
    assert 0.2 < relief < 15.0, relief
    assert "GLO-30" in meta["source"]


# --- the coverage guard ----------------------------------------------------- #
#
# `_raw` CLAMPS outside the grid on purpose, so the far ground mesh does not tear
# a cliff around the site (see `test_sampling_outside_the_grid_clamps_...`). The
# cost of that choice is that a DEM patch which does NOT cover its plot fails
# SILENTLY: every table outside the grid sits on one clamped edge elevation while
# the stage still looks like real terrain. That is not hypothetical — the S05b
# plot shipped pointing at BLOCK-02's patch, ~300 m west and 4.8 km too short,
# until `637aa90` re-baked it. Nothing failed; someone had to notice.
#
# So: every config that asks for `kind: dem` must have a patch that contains its
# own layout extent. Pure YAML arithmetic — no Isaac, no numpy, no GPU.

REPO = Path(__file__).resolve().parents[1]
DEM_CONFIGS = sorted(REPO.glob("configs/farm*.yaml"))


def _dem_configs():
    import yaml

    for cfg_path in DEM_CONFIGS:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        terrain = cfg.get("terrain") or {}
        layout = cfg.get("layout") or {}
        if terrain.get("kind") != "dem" or layout.get("kind") != "file":
            continue
        yield cfg_path.name, terrain.get("path"), layout.get("path")


@pytest.mark.parametrize(
    "cfg_name,dem_rel,site_rel", list(_dem_configs()), ids=lambda v: str(v)[:40]
)
def test_dem_patch_covers_the_layout_it_is_paired_with(cfg_name, dem_rel, site_rel):
    """A `kind: dem` config whose patch misses its plot is worse than `kind: flat`:
    flat is honest, a clamped edge is flat while LOOKING surveyed."""
    import yaml

    dem_path, site_path = REPO / dem_rel, REPO / site_rel
    if not dem_path.exists():
        pytest.skip(f"{dem_rel} not fetched (tools/dem_fetch.py); assets are gitignored")
    assert site_path.exists(), f"{cfg_name} names a layout that does not exist: {site_rel}"

    dem = yaml.safe_load(dem_path.read_text())
    site = yaml.safe_load(site_path.read_text())
    extent = site.get("extent")
    assert extent, f"{site_rel} carries no `extent` — cannot check DEM coverage"

    step = float(dem["step_m"])
    dem_e0, dem_n0 = float(dem["origin_easting"]), float(dem["origin_northing"])
    dem_e1, dem_n1 = dem_e0 + int(dem["nx"]) * step, dem_n0 + int(dem["ny"]) * step
    (site_e0, site_e1), (site_n0, site_n1) = extent["easting"], extent["northing"]

    # Same CRS, or the comparison is meaningless before it is wrong.
    assert dem["crs"] == site["crs"], (
        f"{cfg_name}: DEM is {dem['crs']}, layout is {site['crs']}"
    )

    shortfall = {
        "west": dem_e0 - site_e0,
        "east": site_e1 - dem_e1,
        "south": dem_n0 - site_n0,
        "north": site_n1 - dem_n1,
    }
    missed = {k: round(v, 1) for k, v in shortfall.items() if v > 0.0}
    assert not missed, (
        f"{cfg_name}: `{dem_rel}` does not cover `{site_rel}` — short by {missed} metres. "
        f"Sampling there CLAMPS to the patch edge, so those tables would stand on flat "
        f"ground while the stage looks surveyed. Re-bake with tools/dem_fetch.py for this "
        f"layout's extent, or set terrain.kind: flat so the approximation is explicit "
        f"(NFR-07)."
    )


@pytest.mark.skipif(
    not (SIDECAR.exists() and SITE.exists()), reason="DEM or site file missing"
)
def test_layout_on_the_real_dem_keeps_tubes_straight():
    """The whole point of the fit: modules along one table must be COLLINEAR in
    z even though the ground under them is not."""
    layout = FarmLayout(
        {
            "layout": {"kind": "file", "path": str(SITE), "max_tables": 3},
            "terrain": {"kind": "dem", "path": str(SIDECAR), "datum": "hardware_mean"},
            "georef": {"lat0": 24.088, "lon0": 69.418},
            "panel": {"mount_height": 1.5, "height": 0.035},
        }
    )
    # The site origin was injected from the site file, not restated in config.
    assert layout.cfg["terrain"]["site_origin_easting"] > 500000.0

    by_table: dict[int, list] = {}
    for s in layout.sites:
        by_table.setdefault(s.row, []).append(s)

    for row, sites in by_table.items():
        sites.sort(key=lambda s: s.position[1])
        ys = [s.position[1] for s in sites]
        zs = [s.position[2] for s in sites]
        # Collinear: every module's z lies on the line through the two ends.
        span = ys[-1] - ys[0]
        slope = (zs[-1] - zs[0]) / span
        for y, z in zip(ys, zs):
            assert z == pytest.approx(zs[0] + slope * (y - ys[0]), abs=1e-6), row

    # ...and the terrain is genuinely NOT flat, or this proves nothing.
    raw = [terrain_height(s.position[0], s.position[1], layout.cfg) for s in layout.sites]
    assert max(raw) - min(raw) > 0.05, "DEM sampled as flat — fit test is vacuous"
