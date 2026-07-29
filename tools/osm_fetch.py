#!/usr/bin/env python3
"""Fetch real OpenStreetMap features for a site and bake them into the site's CRS.

    python3 tools/osm_fetch.py configs/layouts/khavda_s05b_digest.yaml \
        --out configs/layouts/khavda_s05b_osm.yaml

Source: the **official OpenStreetMap API 0.6 `/map` call**, which returns every
node and way in a bounding box. Deliberately NOT Overpass: Overpass is a
third-party query service and its main instance answered every request during
this ingest with `504 ... dispatcher timeout, the server is probably too busy`,
while two community mirrors were unreachable. The `/map` endpoint is first-party
and served immediately. Its cost is a hard bbox limit of **0.25 square degrees**,
which is ~50 x 28 km here — comfortably more than a plot needs, and the tool
fails loud rather than silently truncating if a site ever exceeds it.

**Why a two-stage pipeline** (this tool, then `world/osm_features.py`): exactly
the split `tools/dem_fetch.py` uses. Network access and `pyproj` live here, at
ingest time; the build step under `./python.sh` reads a plain YAML of
stage-local metres with no network and no geo dependency. So the twin builds
offline and reproducibly, and the projection is done once instead of per build.

Provenance
----------
Everything written here is tagged `mapped` — real third-party survey-grade-ish
data, neither read from the vendor CAD (`derived`) nor invented by us
(`inferred`). That distinction is the point: see `world/site.py`.

⚠ **What OSM does NOT have, measured over the real plot** (lat 24.07-24.13, lon
69.39-69.50): **no internal plant roads at all**. The whole footprint returned 5
ways — two unpaved `highway=track`, two `power=plant` polygons and one 765 kV
`power=line`. Internal access roads between tracker rows are private and unmapped,
so they MUST stay `inferred`/`derived` from `world/site.py`. This tool adds the
real regional context around the plant; it does not replace the plant's own
furniture, and anyone reading a render must not be told otherwise (`NFR-07`).
"""

from __future__ import annotations

import argparse
import math
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

#: First-party OSM API. `/map` returns all data in a bbox as OSM XML.
OSM_MAP_URL = "https://api.openstreetmap.org/api/0.6/map"

#: The API's documented bbox area limit, in square degrees.
OSM_MAX_BBOX_SQ_DEG = 0.25

#: Tag keys that make a way worth keeping, mapped to the layer we file it under.
#: Everything else in the bbox is dropped — a bbox this size also returns scrub,
#: salt pans and administrative boundaries that carry no geometry we render.
_LAYERS: dict[str, str] = {
    "highway": "roads",
    "power": "power",
    "waterway": "water",
}

#: Nominal rendered width per road class, metres. OSM rarely tags `width` out
#: here, so these are typical Indian rural values — INFERRED WIDTH on a MAPPED
#: centreline, which is why the sidecar records the two separately.
ROAD_WIDTH_M: dict[str, float] = {
    "motorway": 22.0,
    "trunk": 14.0,
    "primary": 10.0,
    "secondary": 8.0,
    "tertiary": 6.5,
    "unclassified": 5.0,
    "residential": 5.0,
    "service": 4.0,
    "track": 3.5,
    "path": 1.5,
    "footway": 1.5,
}
DEFAULT_ROAD_WIDTH_M = 4.0


def site_bbox_wgs84(site_yaml: str, pad_m: float = 2000.0):
    """(west, south, east, north) in degrees covering the site plus a margin.

    A generous default pad: unlike the DEM — which only has to underlie the
    ground mesh — roads are the thing that tells a viewer this plant sits in a
    real landscape, so the useful ones run well past the fence.
    """
    import yaml
    from pyproj import Transformer

    cfg = yaml.safe_load(Path(site_yaml).read_text())
    crs = cfg["crs"]
    tables = cfg["tables"]
    module_len = float(cfg["module"]["length_m"])

    es = [float(t["e"]) for t in tables]
    ns = [float(t["n"]) for t in tables]
    # Modules run `length_m` NORTH of a table's insert point and the chord
    # overhangs the tube either side — same correction as world/site.py.
    n_max = max(float(t["n"]) + float(t["length_m"]) for t in tables)
    e0, e1 = min(es) - module_len / 2 - pad_m, max(es) + module_len / 2 + pad_m
    n0, n1 = min(ns) - pad_m, n_max + pad_m

    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lons, lats = [], []
    for e, n in ((e0, n0), (e1, n0), (e0, n1), (e1, n1)):
        lon, lat = to_wgs.transform(e, n)
        lons.append(lon)
        lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


def fetch_osm_xml(bbox, timeout: float = 240.0) -> bytes:
    """Raw OSM XML for a (west, south, east, north) degree bbox."""
    west, south, east, north = bbox
    area = (east - west) * (north - south)
    if area > OSM_MAX_BBOX_SQ_DEG:
        raise SystemExit(
            f"bbox is {area:.3f} sq deg, over the OSM /map limit of "
            f"{OSM_MAX_BBOX_SQ_DEG}. Reduce --pad, or split the fetch — do NOT "
            "let it truncate silently."
        )
    url = f"{OSM_MAP_URL}?" + urllib.parse.urlencode(
        {"bbox": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"}
    )
    print(f"  GET {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "solar-twin/osm_fetch"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_ways(xml_bytes: bytes, crs: str, origin_e: float, origin_n: float) -> list[dict]:
    """OSM XML -> ways in **stage-local metres**, ready for `world/osm_features.py`.

    Registration is the same rigid translation `layout_import` uses for the CAD:
    project WGS84 -> the site's own projected CRS, then subtract the site anchor.
    So an OSM road and a CAD tracker table land in one frame because both are in
    real survey metres, not because anything was scaled or fitted to match.
    """
    from pyproj import Transformer

    root = ET.fromstring(xml_bytes)
    to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    nodes = {
        n.get("id"): (float(n.get("lon")), float(n.get("lat")))
        for n in root.findall("node")
    }

    out: list[dict] = []
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
        layer = next((lay for key, lay in _LAYERS.items() if key in tags), None)
        if layer is None:
            continue
        pts: list[tuple[float, float]] = []
        for nd in way.findall("nd"):
            ll = nodes.get(nd.get("ref"))
            if ll is None:
                continue  # a way clipped by the bbox loses nodes; keep what we have
            e, n = to_crs.transform(*ll)
            pts.append((round(e - origin_e, 3), round(n - origin_n, 3)))
        if len(pts) < 2:
            continue
        kind = tags.get("highway") or tags.get("power") or tags.get("waterway") or "?"
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        rec = {
            "osm_id": int(way.get("id")),
            "layer": layer,
            "kind": kind,
            "points": [list(p) for p in pts],
            "length_m": round(length, 1),
            "closed": math.dist(pts[0], pts[-1]) < 1.0,
        }
        if tags.get("name"):
            rec["name"] = tags["name"]
        if layer == "roads":
            # A real `width` tag wins over the class default, and says so.
            if tags.get("width"):
                try:
                    rec["width_m"] = float(str(tags["width"]).split()[0])
                    rec["width_source"] = "osm_tag"
                except ValueError:
                    pass
            if "width_m" not in rec:
                rec["width_m"] = ROAD_WIDTH_M.get(kind, DEFAULT_ROAD_WIDTH_M)
                rec["width_source"] = "class_default"
            rec["surface"] = tags.get("surface", "unknown")
        if layer == "power":
            for k in ("voltage", "operator", "plant:output:electricity", "plant:source"):
                if tags.get(k):
                    rec[k.replace(":", "_")] = tags[k]
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("site", help="canonical site YAML (carries crs + origin)")
    ap.add_argument("--out", required=True, help="output YAML path")
    ap.add_argument(
        "--pad",
        type=float,
        default=2000.0,
        help="metres of context beyond the hardware footprint (default 2000)",
    )
    args = ap.parse_args(argv)

    import yaml

    cfg = yaml.safe_load(Path(args.site).read_text())
    crs = cfg["crs"]
    origin_e = float(cfg["origin"]["easting"])
    origin_n = float(cfg["origin"]["northing"])

    bbox = site_bbox_wgs84(args.site, pad_m=args.pad)
    print(
        f"site {args.site}\n  crs {crs} origin E{origin_e:.1f} N{origin_n:.1f}\n"
        f"  bbox W{bbox[0]:.4f} S{bbox[1]:.4f} E{bbox[2]:.4f} N{bbox[3]:.4f} "
        f"({(bbox[2] - bbox[0]) * (bbox[3] - bbox[1]):.4f} sq deg)",
        flush=True,
    )
    xml_bytes = fetch_osm_xml(bbox)
    print(f"  {len(xml_bytes):,} bytes of OSM XML", flush=True)

    ways = parse_ways(xml_bytes, crs, origin_e, origin_n)
    by_layer: dict[str, int] = {}
    for w in ways:
        by_layer[w["layer"]] = by_layer.get(w["layer"], 0) + 1
    roads = [w for w in ways if w["layer"] == "roads"]

    doc = {
        "source": "OpenStreetMap via the official API 0.6 /map call",
        "source_url": OSM_MAP_URL,
        "license": "ODbL 1.0 (c) OpenStreetMap contributors",
        "note": (
            "Centrelines are MAPPED (real OSM geometry, projected into the site "
            "CRS and anchored to the site origin). Rendered road WIDTHS are "
            "class defaults unless width_source is osm_tag. OSM carries NO "
            "internal plant roads here - those stay derived/inferred from "
            "world/site.py."
        ),
        "crs": crs,
        "origin": {"easting": origin_e, "northing": origin_n},
        "bbox_wgs84": [round(v, 6) for v in bbox],
        "counts": by_layer,
        "ways": ways,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(doc, sort_keys=False, width=100))

    print(f"\n  kept {len(ways)} ways: {by_layer}", flush=True)
    for w in sorted(ways, key=lambda w: -w["length_m"])[:10]:
        nm = f" '{w['name']}'" if w.get("name") else ""
        print(f"    {w['layer']:6s} {w['kind']:12s}{nm:32s} {w['length_m']:9,.0f} m")
    if not roads:
        print(
            "\n  ⚠ NO highway ways in this bbox — every road on the stage will be "
            "derived/inferred. Say so rather than implying mapped roads.",
            flush=True,
        )
    print(f"\nwrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
