"""Ingest a GatiShakti **plot digest** — real plant assets at survey coordinates.

WHAT THIS IS
------------
The `gs-vr-variant-2` viewer (Adani GatiShakti Experience) ships per-plot JSON
digests: block perimeters plus every MMS table, inverter, IDT building and **WTG**
in a plot, all as EPSG:32642 eastings/northings — **the same CRS as our own
BLOCK-02 site file**, so the two are directly comparable without reprojection.

The headline is the turbines. Our `turbines:` blocks have always been `INFERRED` —
we placed them ourselves because the vendor DC drawing carries hardware only. This
gives **22 surveyed 5.2 MW WTG positions** across two plots, which turns
`FR-13`'s wake and `HAZ-01`'s keep-out from illustrative into sited.

⚠ **PROVENANCE IS SECOND-HAND, AND THAT MATTERS.** The chain is
vendor DWG -> *their* `build_gis_digest.py` (a script we do **not** hold, in a
different workspace) -> this JSON -> us. We can verify the digest is
self-consistent and lands in the right CRS; we cannot re-derive it from the
drawing the way `tools/layout_from_dxf.py` can for BLOCK-02. So every record
carries `provenance="digest"`, never `derived`, and `NFR-07` applies: quote these
as "from the GatiShakti digest", not "from the CAD".

⚠ **DIMENSIONS ARE NOT IN THE DIGEST.** A WTG entry is a centroid plus the layer
name `"5.2MW WTG"` — no hub height, no rotor diameter. Those still have to be
inferred from the rating, and are tagged as such: real position, assumed size.

⚠ **These plots are NOT ours.** A5 sits ~10 km west and S05b begins a few hundred
metres east of BLOCK-02 (which is plot A10b). `near_turbines` exists so a caller
asks "which of these is close enough to matter?" instead of assuming all of them
do — a turbine 10 km away contributes no wake and no shadow.

Pure-python: no Isaac import.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

#: The CRS every digest is expected in. A digest in anything else is rejected
#: rather than silently mixed with our site file (`FR-27`'s fail-closed spirit).
EXPECTED_CRS_HINT = "32642"

#: Rated power -> assumed geometry, for turbines whose dimensions the digest does
#: not carry. A modern 5.2 MW onshore machine is ~140 m rotor on a ~120 m hub.
#: ⚠ INFERRED. Replace per-model from a datasheet when one arrives; the position
#: is real, this is not.
_RATING_GEOMETRY = {
    5.2: {"hub_height": 120.0, "blade_len": 70.0, "rpm": 11.5},
}
_DEFAULT_GEOMETRY = {"hub_height": 120.0, "blade_len": 70.0, "rpm": 11.5}


def parse_rating_mw(layername: str) -> float | None:
    """`"5.2MW WTG"` -> 5.2. None when the layer name carries no rating."""
    import re

    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*MW", layername, re.I)
    return float(m.group(1)) if m else None


@dataclass(frozen=True)
class Turbine:
    """A surveyed WTG position with assumed geometry."""

    easting: float
    northing: float
    rating_mw: float | None
    layername: str

    @property
    def geometry(self) -> dict:
        """Assumed hub/blade/rpm for this rating. ⚠ INFERRED, not surveyed."""
        if self.rating_mw is None:
            return dict(_DEFAULT_GEOMETRY)
        return dict(_RATING_GEOMETRY.get(round(self.rating_mw, 1), _DEFAULT_GEOMETRY))

    def stage_pos(self, origin_e: float, origin_n: float) -> tuple[float, float]:
        """Position in stage-local metres, given the site file's own anchor.

        Same convention as `layout_import`: stage (0,0) is the site origin, so
        subtracting the anchor is the whole transform — both are already
        EPSG:32642 metres, which is exactly why no reprojection is involved.
        """
        return (self.easting - origin_e, self.northing - origin_n)


@dataclass(frozen=True)
class BoxAsset:
    """An asset the digest gives as a bounding box (table, inverter, IDT)."""

    kind: str
    sw_e: float
    sw_n: float
    ne_e: float
    ne_n: float
    layername: str

    @property
    def centroid(self) -> tuple[float, float]:
        return ((self.sw_e + self.ne_e) / 2.0, (self.sw_n + self.ne_n) / 2.0)

    @property
    def size_m(self) -> tuple[float, float]:
        return (abs(self.ne_e - self.sw_e), abs(self.ne_n - self.sw_n))

    @property
    def block(self) -> str | None:
        """`"MMS Table Block-07"` -> `"Block-07"`. The digest encodes block
        membership in the layer name rather than as a field."""
        if "Block-" not in self.layername:
            return None
        return "Block-" + self.layername.split("Block-", 1)[1].strip()


@dataclass
class PlotDigest:
    plot_id: str
    plot_perimeter: list[tuple[float, float]] = field(default_factory=list)
    block_perimeters: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    turbines: list[Turbine] = field(default_factory=list)
    boxes: list[BoxAsset] = field(default_factory=list)
    source: str = ""
    #: Always "digest" — see the module docstring's provenance warning.
    provenance: str = "digest"

    # ------------------------------------------------------------------ #
    @property
    def extent(self) -> tuple[float, float, float, float]:
        """(min_e, max_e, min_n, max_n) over the plot perimeter."""
        if not self.plot_perimeter:
            return (0.0, 0.0, 0.0, 0.0)
        es = [p[0] for p in self.plot_perimeter]
        ns = [p[1] for p in self.plot_perimeter]
        return (min(es), max(es), min(ns), max(ns))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {"wtg": len(self.turbines)}
        for b in self.boxes:
            out[b.kind] = out.get(b.kind, 0) + 1
        out["blocks"] = len(self.block_perimeters)
        return out

    def near_turbines(
        self, origin_e: float, origin_n: float, radius_m: float,
        site_extent: tuple[float, float, float, float] | None = None,
    ) -> list[tuple[float, Turbine]]:
        """`(distance_m, turbine)` for turbines within `radius_m`, nearest first.

        Distance is measured to the **site footprint** when `site_extent` is
        given, otherwise to the anchor point. That distinction matters: our block
        is 320 x 647 m, so distance-to-origin can overstate the gap to a turbine
        sitting off the far corner by most of a kilometre.
        """
        out = []
        for t in self.turbines:
            x, y = t.stage_pos(origin_e, origin_n)
            if site_extent is None:
                d = math.hypot(x, y)
            else:
                min_x, max_x, min_y, max_y = site_extent
                dx = max(min_x - x, x - max_x, 0.0)
                dy = max(min_y - y, y - max_y, 0.0)
                d = math.hypot(dx, dy)
            if d <= radius_m:
                out.append((d, t))
        out.sort(key=lambda p: p[0])
        return out

    def turbines_config(
        self, origin_e: float, origin_n: float, radius_m: float = 5000.0,
        site_extent: tuple[float, float, float, float] | None = None,
    ) -> list[dict]:
        """A `turbines:` list for `farm.yaml`, in stage-local metres.

        Emits the same shape `world/siting.py` and `world/keepout.py` already
        consume, so this is a data swap and not a code change. `provenance` rides
        along on every entry so a build log can print real-vs-inferred.
        """
        out = []
        for d, t in self.near_turbines(origin_e, origin_n, radius_m, site_extent):
            x, y = t.stage_pos(origin_e, origin_n)
            entry = {"pos": [round(x, 3), round(y, 3)], **t.geometry}
            entry["provenance"] = "digest"          # position: real, second-hand
            entry["geometry_provenance"] = "INFERRED"  # hub/blade: assumed
            entry["rating_mw"] = t.rating_mw
            entry["distance_to_site_m"] = round(d, 1)
            out.append(entry)
        return out


def load(path: str, expect_crs: str | None = EXPECTED_CRS_HINT) -> PlotDigest:
    """Read a `Plot-*.json` digest.

    Fails on a digest with no plot id or no assets rather than returning an empty
    object: a silently-empty digest would show up much later as "the plant has no
    turbines", which is indistinguishable from a config mistake.
    """
    with open(path) as f:
        raw = json.load(f)

    plot_id = str(raw.get("id") or "").strip()
    if not plot_id:
        raise ValueError(f"{path}: digest has no plot `id`")

    crs = str(raw.get("crs") or "")
    if crs and expect_crs and expect_crs not in crs:
        raise ValueError(
            f"{path}: digest declares CRS {crs!r}, expected EPSG:{expect_crs}. "
            f"Reprojecting is not attempted — mixing CRSs silently is how a plant "
            f"ends up kilometres from its own terrain."
        )

    perims = raw.get("perimeters") or {}
    plot_perimeter = [(float(p[0]), float(p[1])) for p in (perims.get("plot") or [])]

    blocks_raw = perims.get("blocks") or {}
    block_perimeters: dict[str, list[tuple[float, float]]] = {}
    if isinstance(blocks_raw, dict):
        for bid, pts in blocks_raw.items():
            block_perimeters[str(bid)] = [(float(p[0]), float(p[1])) for p in pts]
    else:
        # The Plot-Ref template uses a list of {id, coordinates}; accept both so a
        # template-shaped digest does not silently read as zero blocks.
        for b in blocks_raw:
            block_perimeters[str(b.get("id"))] = [
                (float(p[0]), float(p[1])) for p in (b.get("coordinates") or [])
            ]

    turbines: list[Turbine] = []
    boxes: list[BoxAsset] = []
    for a in raw.get("assets") or []:
        kind = str(a.get("type") or "")
        layer = str((a.get("properties") or {}).get("layername") or "")
        if kind == "wtg":
            c = a.get("centroid")
            if not c:
                continue
            turbines.append(Turbine(float(c[0]), float(c[1]), parse_rating_mw(layer), layer))
            continue
        b = a.get("bounds") or {}
        sw, ne = b.get("sw"), b.get("ne")
        if not sw or not ne:
            continue
        boxes.append(BoxAsset(kind, float(sw[0]), float(sw[1]),
                              float(ne[0]), float(ne[1]), layer))

    if not turbines and not boxes:
        raise ValueError(f"{path}: digest contains no assets — refusing to load it silently")

    return PlotDigest(
        plot_id=plot_id,
        plot_perimeter=plot_perimeter,
        block_perimeters=block_perimeters,
        turbines=turbines,
        boxes=boxes,
        source=path,
    )
