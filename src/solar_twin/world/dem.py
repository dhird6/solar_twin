"""Real terrain from a baked DEM grid (numpy only — no GDAL, no Isaac).

`tools/dem_fetch.py` downloads a Copernicus GLO-30 tile and bakes the site's
patch into a `.npy` grid plus a YAML sidecar. This samples it. The split is
deliberate: GDAL cannot go into Isaac Sim's bundled Python, but the build step
runs there, so the heavy geo work happens once at ingest and the build only ever
does bilinear interpolation.

Two things here matter more than the interpolation:

**The datum.** A DEM gives absolute elevation — Khavda is 3.3-5.4 m above sea
level. Authoring panels at those z values would put the whole plant metres off
the stage origin and quietly invalidate every waypoint standoff, which are
measured from the panel. So the grid is shifted to a **datum**: the mean
elevation over the hardware footprint becomes stage z = 0, and relief is what
remains. `datum_m` keeps the offset so a real elevation can still be recovered.

**Straight tubes.** A tracker's torque tube is a rigid steel beam up to 128 m
long. Real ground undulates under it and the installer cuts each pile to suit,
so the tube is a straight line through the terrain, NOT a copy of it. Sampling
the DEM per module and mounting each one at its own height would bend a 128 m
steel beam into the shape of the desert — visibly wrong, and wrong in the
direction that flatters the twin (`NFR-07`). `fit_line` does the fit the
installer does, and the residual it returns is a real engineering quantity: how
much pile-height variation the row needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DemTerrain:
    """A baked DEM patch, sampled in stage-local metres.

    `site_origin_e/n` is the survey coordinate the stage's (0, 0) maps to — i.e.
    the site file's own anchor — so stage x/y are converted to survey metres
    before the grid is indexed.
    """

    grid: object  # numpy 2-D array [ny, nx], metres above sea level
    origin_e: float  # survey easting of grid cell (0, 0)
    origin_n: float  # survey northing of grid cell (0, 0)
    step_m: float
    site_origin_e: float
    site_origin_n: float
    datum_m: float
    source: str = ""
    note: str = ""

    @property
    def relief_m(self) -> float:
        return float(self.grid.max() - self.grid.min())

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, sidecar_path: str, site_origin_e: float, site_origin_n: float,
             datum: str = "hardware_mean", bounds=None) -> DemTerrain:
        """Load a `dem_fetch.py` sidecar + grid.

        `datum='hardware_mean'` (the default) puts the mean elevation over
        `bounds` — the panel footprint in stage metres — at stage z = 0.
        `datum='origin'` uses the elevation at the stage origin instead, and
        `datum='absolute'` keeps real sea-level elevations (for export, not for
        rendering).
        """
        import numpy as np
        import yaml

        p = Path(sidecar_path)
        meta = yaml.safe_load(p.read_text())
        grid = np.load(p.parent / meta["grid_file"])

        dem = cls(
            grid=grid,
            origin_e=float(meta["origin_easting"]),
            origin_n=float(meta["origin_northing"]),
            step_m=float(meta["step_m"]),
            site_origin_e=float(site_origin_e),
            site_origin_n=float(site_origin_n),
            datum_m=0.0,
            source=str(meta.get("source", "")),
            note=str(meta.get("note", "")),
        )
        if datum == "absolute":
            return dem
        if datum == "origin":
            dem.datum_m = dem._raw(0.0, 0.0)
            return dem
        # hardware_mean: average over the footprint the panels actually occupy,
        # so the plant straddles z=0 instead of one arbitrary corner doing so.
        if bounds is None:
            dem.datum_m = float(grid.mean())
            return dem
        min_x, min_y, max_x, max_y = bounds
        n = 12
        vals = [
            dem._raw(min_x + (max_x - min_x) * i / (n - 1), min_y + (max_y - min_y) * j / (n - 1))
            for i in range(n)
            for j in range(n)
        ]
        dem.datum_m = sum(vals) / len(vals)
        return dem

    # ------------------------------------------------------------------ #
    def _raw(self, x: float, y: float) -> float:
        """Absolute elevation (m above sea level) at stage-local (x, y).

        Bilinear, with indices CLAMPED to the grid. Clamping is the intended
        behaviour, not a guard: the ground mesh reaches kilometres past the DEM
        patch, and beyond it the terrain simply continues at the edge elevation
        rather than falling to zero and tearing a cliff around the site.
        """
        gx = (x + self.site_origin_e - self.origin_e) / self.step_m
        gy = (y + self.site_origin_n - self.origin_n) / self.step_m
        ny, nx = self.grid.shape

        i0 = int(gx) if gx >= 0 else 0
        j0 = int(gy) if gy >= 0 else 0
        i0 = min(max(i0, 0), nx - 1)
        j0 = min(max(j0, 0), ny - 1)
        i1 = min(i0 + 1, nx - 1)
        j1 = min(j0 + 1, ny - 1)
        fx = min(max(gx - i0, 0.0), 1.0)
        fy = min(max(gy - j0, 0.0), 1.0)

        g = self.grid
        top = float(g[j0, i0]) * (1 - fx) + float(g[j0, i1]) * fx
        bot = float(g[j1, i0]) * (1 - fx) + float(g[j1, i1]) * fx
        return top * (1 - fy) + bot * fy

    def height(self, x: float, y: float) -> float:
        """Elevation in STAGE metres (datum removed)."""
        return self._raw(x, y) - self.datum_m


def fit_line(samples: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares fit `z = a + b*s` over (s, z) samples along a torque tube.

    Returns `(a, b, max_abs_residual)`. The residual is the point of this
    function: it is the pile-height variation the row needs, and a real site has
    a tolerance for it. If it comes out large, the terrain and the hardware
    disagree and the answer is grading — not bending the beam.
    """
    n = len(samples)
    if n == 0:
        return (0.0, 0.0, 0.0)
    if n == 1:
        return (samples[0][1], 0.0, 0.0)
    sum_s = sum(s for s, _ in samples)
    sum_z = sum(z for _, z in samples)
    mean_s, mean_z = sum_s / n, sum_z / n
    var = sum((s - mean_s) ** 2 for s, _ in samples)
    if var <= 1e-12:
        return (mean_z, 0.0, max(abs(z - mean_z) for _, z in samples))
    cov = sum((s - mean_s) * (z - mean_z) for s, z in samples)
    b = cov / var
    a = mean_z - b * mean_s
    resid = max(abs(z - (a + b * s)) for s, z in samples)
    return (a, b, resid)
