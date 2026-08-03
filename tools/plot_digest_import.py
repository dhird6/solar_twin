#!/usr/bin/env python3
"""Emit a `turbines:` block from a GatiShakti plot digest — real, sited WTGs.

**Pure-python, no Isaac.** Our `turbines:` lists have always been `INFERRED`: the
vendor DC drawing carries hardware only, so we placed them ourselves. The
GatiShakti viewer's per-plot digests give **surveyed 5.2 MW WTG centroids in
EPSG:32642** — the same CRS as our site file — so the positions can be real.

    PYTHONPATH=src python3 tools/plot_digest_import.py \
        <path>/Plot-S05b.json --anchor-from configs/layouts/khavda_a10b_block02.yaml \
        --radius 3000 --out configs/turbines_khavda_digest.yaml

⚠ Provenance is SECOND-HAND: vendor DWG -> *their* `build_gis_digest.py` (which we
do not hold) -> JSON -> us. Every emitted entry says so, and geometry (hub height,
blade length, rpm) is still INFERRED from the rating because the digest does not
carry it. Real position, assumed size — do not collapse the two.

⚠ `--radius` is not cosmetic. Measured against BLOCK-02: plot S05b has 7 of 8
turbines within 3 km (nearest 546 m), while plot A5's 14 are all >= 7.5 km away.
Importing those would put dead geometry on the stage and phantom keep-out volumes
in the planner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solar_twin.world.plot_digest import load  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("digest", help="a Plot-*.json from the GatiShakti viewer")
    ap.add_argument("--anchor-from", help="our site YAML, for its `origin` anchor")
    ap.add_argument("--anchor", nargs=2, type=float, metavar=("EASTING", "NORTHING"))
    ap.add_argument("--extent", nargs=4, type=float,
                    metavar=("MIN_X", "MAX_X", "MIN_Y", "MAX_Y"),
                    help="site footprint in stage metres; distances are measured to "
                         "this rather than the anchor (our block is 320x647 m, so the "
                         "difference is most of a kilometre at the far corner)")
    ap.add_argument("--radius", type=float, default=3000.0)
    ap.add_argument("--out", help="write YAML here (default: stdout)")
    args = ap.parse_args(argv)

    import yaml

    if args.anchor_from:
        site = yaml.safe_load(open(args.anchor_from))
        origin = site.get("origin") or {}
        anchor = (float(origin["easting"]), float(origin["northing"]))
    elif args.anchor:
        anchor = (args.anchor[0], args.anchor[1])
    else:
        ap.error("give --anchor-from or --anchor")

    d = load(args.digest)
    extent = tuple(args.extent) if args.extent else None
    cfg = d.turbines_config(*anchor, radius_m=args.radius, site_extent=extent)

    print(f"digest {d.plot_id}: {d.counts()}", file=sys.stderr)
    print(f"anchor E{anchor[0]:.3f} N{anchor[1]:.3f} | radius {args.radius:.0f} m "
          f"-> {len(cfg)} of {len(d.turbines)} turbines imported", file=sys.stderr)
    for e in cfg:
        print(f"   {e['distance_to_site_m']:8.1f} m  pos {e['pos']}  "
              f"{e['rating_mw']} MW", file=sys.stderr)

    doc = {
        "_provenance": {
            "source": f"GatiShakti plot digest {d.plot_id} ({Path(args.digest).name})",
            "chain": "vendor DWG -> their build_gis_digest.py (not held) -> JSON -> here",
            "positions": "REAL (surveyed, EPSG:32642), second-hand",
            "geometry": "INFERRED from the MW rating — the digest carries no hub "
                        "height or rotor diameter",
            "radius_m": args.radius,
            "anchor": {"easting": anchor[0], "northing": anchor[1]},
        },
        "turbines": cfg,
    }
    text = yaml.safe_dump(doc, sort_keys=False)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
