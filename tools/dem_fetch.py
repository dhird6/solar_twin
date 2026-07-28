#!/usr/bin/env python3
"""Fetch a real DEM for a site and bake it into a compact grid the twin can read.

    /home/simulationhub/venvs/dem-ingest/bin/python tools/dem_fetch.py \
        configs/layouts/khavda_a10b_block02.yaml --out assets/dem/khavda_block02

Source: **Copernicus DEM GLO-30** on AWS Open Data — 30 m global, no credentials,
no registration (SRTM/NASADEM/AW3D30 all need an Earthdata or JAXA login, which a
reproducible pipeline should not depend on). Tiles are 1x1 degree float32 COGs.

**Why a two-stage pipeline** (this tool, then `world/dem.py`): reading a COG needs
GDAL, which is not installed in Isaac Sim's bundled Python and must not be — the
build step has to run under `./python.sh`. So GDAL lives here, at ingest time, in
its own venv, exactly as `tools/layout_from_dxf.py` keeps `ezdxf` out of the
build. This writes a plain `.npy` + a YAML sidecar that `world/dem.py` samples
with numpy alone, so terrain works under Isaac and in Isaac-free tests.

⚠ GLO-30 is a **DSM**, not a DTM: it includes surface features. On bare Kutch
scrub that is very close to ground, but it is not a graded-site survey. What a
plant is actually built on is the POST-grading surface, which only the civil
drawings have — see `world/dem.py` for how the tracker tables are fitted so this
approximation cannot silently deform the hardware (`NFR-07`).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

#: AWS Open Data bucket. Tile names are on 1-degree SW corners, zero-padded.
COP30_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)


def tile_url(lat: float, lon: float) -> str:
    """URL of the 1x1 degree tile containing (lat, lon)."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return COP30_URL.format(
        ns=ns, ew=ew, lat=int(math.floor(abs(lat))), lon=int(math.floor(abs(lon)))
    )


def site_bbox_wgs84(site_yaml: str, pad_m: float = 400.0):
    """(west, south, east, north) in degrees covering the site plus a margin.

    The pad matters: the ground mesh reaches well past the last panel, and a DEM
    cropped exactly to the hardware would leave the surrounding terrain flat —
    a plant sitting on a mesa.
    """
    import yaml
    from pyproj import Transformer

    cfg = yaml.safe_load(Path(site_yaml).read_text())
    crs = cfg["crs"]
    tables = cfg["tables"]
    module_len = float(cfg["module"]["length_m"])

    es = [float(t["e"]) for t in tables]
    ns = [float(t["n"]) for t in tables]
    # Modules run `length_m` NORTH of a table's insert point, and the chord
    # overhangs the tube either side. Same correction as world/site.py.
    n_max = max(float(t["n"]) + float(t["length_m"]) for t in tables)
    e0, e1 = min(es) - module_len / 2 - pad_m, max(es) + module_len / 2 + pad_m
    n0, n1 = min(ns) - pad_m, n_max + pad_m

    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    corners = [to_wgs.transform(e, n) for e in (e0, e1) for n in (n0, n1)]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return (min(lons), min(lats), max(lons), max(lats)), crs, (e0, n0, e1, n1)


def fetch(site_yaml: str, out_base: str, pad_m: float = 400.0) -> str:
    import numpy as np
    import rasterio
    import yaml
    from pyproj import Transformer
    from rasterio.warp import transform as warp_transform

    (w, s, e, n), crs, (e0, n0, e1, n1) = site_bbox_wgs84(site_yaml, pad_m)
    url = tile_url((s + n) / 2, (w + e) / 2)
    print(f"site bbox (deg): W{w:.5f} S{s:.5f} E{e:.5f} N{n:.5f}")
    print(f"tile: {url}")

    # Sample the DEM on a regular grid in the SITE's projected CRS, not in
    # degrees: the stage works in metres, and a lat/lon grid has non-square
    # cells that would need re-projecting at every sample during the build.
    step = 20.0  # metres — finer than GLO-30's own 30 m, so no detail is lost
    nx = int(math.ceil((e1 - e0) / step)) + 1
    ny = int(math.ceil((n1 - n0) / step)) + 1
    print(f"grid: {nx} x {ny} @ {step} m  ({(e1 - e0):.0f} x {(n1 - n0):.0f} m)")

    eastings = e0 + step * np.arange(nx)
    northings = n0 + step * np.arange(ny)
    ee, nn = np.meshgrid(eastings, northings)

    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs.transform(ee.ravel(), nn.ravel())

    # GDAL reads the COG over HTTP with range requests — only the tiles that
    # cover our sample points are transferred, not the whole 1-degree file.
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_USE_HEAD="NO"):
        with rasterio.open(f"/vsicurl/{url}") as src:
            print(f"  DEM crs={src.crs} res={src.res} dtype={src.dtypes[0]}")
            xs, ys = warp_transform("EPSG:4326", src.crs, list(lon), list(lat))
            vals = np.array(list(src.sample(zip(xs, ys))), dtype=np.float32)[:, 0]
            nodata = src.nodata

    if nodata is not None:
        vals = np.where(vals == nodata, np.nan, vals)
    if np.isnan(vals).any():
        n_bad = int(np.isnan(vals).sum())
        print(f"  [warn] {n_bad} nodata samples -> filled with the grid mean")
        vals = np.where(np.isnan(vals), np.nanmean(vals), vals)

    grid = vals.reshape(ny, nx)
    out_base_p = Path(out_base)
    out_base_p.parent.mkdir(parents=True, exist_ok=True)
    npy = out_base_p.with_suffix(".npy")
    np.save(npy, grid)

    meta = {
        "source": "Copernicus DEM GLO-30 (AWS Open Data, no auth)",
        "source_url": url,
        "note": (
            "DSM (includes surface features), 30 m native, sampled to a 20 m grid "
            "in the site CRS. NOT a graded-site survey - see world/dem.py."
        ),
        "crs": crs,
        "origin_easting": float(e0),
        "origin_northing": float(n0),
        "step_m": float(step),
        "nx": int(nx),
        "ny": int(ny),
        "grid_file": npy.name,
        "elev_min_m": float(grid.min()),
        "elev_max_m": float(grid.max()),
        "elev_mean_m": float(grid.mean()),
    }
    side = out_base_p.with_suffix(".yaml")
    side.write_text(yaml.safe_dump(meta, sort_keys=False))
    print(
        f"wrote {npy} ({grid.nbytes / 1e6:.1f} MB) + {side}\n"
        f"  elevation {grid.min():.1f} .. {grid.max():.1f} m "
        f"(mean {grid.mean():.1f}, relief {grid.max() - grid.min():.1f} m)"
    )
    return str(side)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("site_yaml", help="configs/layouts/<site>.yaml (needs crs + tables)")
    ap.add_argument("--out", required=True, help="output base path, no extension")
    ap.add_argument("--pad", type=float, default=400.0, help="metres of DEM beyond the site")
    args = ap.parse_args(argv)
    try:
        import rasterio  # noqa: F401
    except ImportError:
        print(
            "rasterio is required and is deliberately NOT in the build environment.\n"
            "Run this with the ingest venv:\n"
            "  /home/simulationhub/venvs/dem-ingest/bin/python tools/dem_fetch.py ...",
            file=sys.stderr,
        )
        return 2
    fetch(args.site_yaml, args.out, args.pad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
