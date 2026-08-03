"""The PVModule panel contract — the one object shared by all three worlds.

Design constraint (see CLAUDE.md golden rules + docs/ENVIRONMENT.md):
`usd-core` has no aarch64 wheel, so `pxr` is only available under Isaac Sim's
bundled Python on this Spark. Therefore **importing this module must never
require pxr**. The pure-python contract — the fault taxonomy, `PanelRecord`,
attribute-name constants, log/validation logic, and the georef helpers — lives
at module top with no Isaac import. The USD read/write functions import pxr
*inside the function body*, so they only load Isaac when actually called (under
`./python.sh`), and the logic above them stays fully unit-testable here.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Optional

# --------------------------------------------------------------------------- #
# Fault taxonomy (§6.5). Adding a type = one enum entry (+ a visual signature
# in the world, + later a data recipe). It must NOT change orchestration.
# --------------------------------------------------------------------------- #


class PanelState(str, Enum):
    HEALTHY = "healthy"
    SOILED = "soiled"
    HOTSPOT = "hotspot"
    CRACK = "crack"
    STRING_DROPOUT = "string_dropout"
    DIODE_FAULT = "diode_fault"
    SHADING = "shading"
    UNKNOWN = "unknown"


#: The subset Slice 0 actually exercises (§6.5).
SLICE0_STATES: frozenset[PanelState] = frozenset(
    {PanelState.HEALTHY, PanelState.HOTSPOT, PanelState.SOILED}
)

#: States that mean "something is wrong" — anything but healthy is a fault
#: for escalation purposes; unknown is treated as a fault (inspect it).
HEALTHY_STATES: frozenset[PanelState] = frozenset({PanelState.HEALTHY})


def is_valid_state(state: str) -> bool:
    """True if ``state`` is a member of the taxonomy."""
    return state in PanelState._value2member_map_


def coerce_state(state: str) -> PanelState:
    """Parse a raw string into a :class:`PanelState`, defaulting to UNKNOWN."""
    return PanelState._value2member_map_.get(state, PanelState.UNKNOWN)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# USD attribute contract (§6.1). Namespaced under ``pv:`` to avoid collisions.
# These constants are the single source of the attribute names; both the USD
# adapter below and any external reader use them.
# --------------------------------------------------------------------------- #

PREFIX = "pv"

ATTR_PANEL_ID = f"{PREFIX}:panel_id"
ATTR_GRID_INDEX = f"{PREFIX}:grid_index"
ATTR_GEO_POSITION = f"{PREFIX}:geo_position"
ATTR_STATE = f"{PREFIX}:state"
ATTR_IV_YIELD = f"{PREFIX}:iv_yield"
ATTR_RUL_DAYS = f"{PREFIX}:rul_days"
ATTR_LAST_INSPECTED = f"{PREFIX}:last_inspected"
ATTR_INSPECTION_LOG = f"{PREFIX}:inspection_log"


# --------------------------------------------------------------------------- #
# `grid:` — the dispatch-cell namespace, deliberately ABOVE `pv:` (§6.1).
#
# A panel keeps everything it already has; `grid:id` is only a JOIN KEY, so that
# coarse cell-level telemetry and fine panel-level verdicts roll up to the same
# object. It is a separate namespace, not a `pv:` attribute, because it describes
# the panel's place in an ELECTRICAL/dispatch grouping rather than its own
# physical condition — and because a future real string map should be able to
# replace it without touching the panel contract.
#
# ⚠⚠ **A CELL IS A TABLE, AND A TABLE IS NOT A STRING.** The design calls for cells
# aligned to electrical topology (a string, or strings on one combiner) because
# the coarse signal is electrical and a cell straddling two strings can never be
# scored cleanly. **We cannot satisfy that with the data we have.** The vendor DWG
# is DC *hardware geometry* only: `TableSpec` carries `table_id`, `modules`,
# `module_rows`, `layer` — and **no string map, no combiner grouping, no inverter
# assignment**. Even the 5 inverter stations are our own capacity-derived
# INFERENCE (`world/site.py`), not the drawing's. So the finest unit the real data
# actually gives is the **table** (112 modules at Khavda), while a real string is
# ~20-30 modules — i.e. one table is roughly 4-5 strings.
#
# Subdividing a table into N equal groups to *look* string-shaped was rejected:
# that invents electrical topology, which is exactly what this must not do.
# `modules_per_cell` exists so a REAL string map can refine the cell later; its
# default of 0 means "the whole table", which is the only honest grouping today.
# --------------------------------------------------------------------------- #

GRID_PREFIX = "grid"

ATTR_GRID_ID = f"{GRID_PREFIX}:id"


def grid_id(table_index: int, sub: int = 0) -> str:
    """Dispatch-cell ID for a panel, e.g. ``G-0258`` or ``G-0258-01``.

    `table_index` is the panel's table — which is `grid_index[0]`, since
    `layout_import.expand_sites` sets `(row, col) = (table index, module index)`.
    `sub` is a sub-table group and is only meaningful once a real string map
    exists; `sub=0` (the default) means the cell IS the whole table and no
    sub-group suffix is emitted.
    """
    if sub:
        return f"G-{table_index:04d}-{sub:02d}"
    return f"G-{table_index:04d}"


def cell_for_panel(
    grid_index: tuple[int, int], modules_per_cell: int = 0
) -> str:
    """The cell a panel belongs to, from its `(table, module)` index.

    `modules_per_cell=0` (default) → one cell per table, the only grouping the
    CAD supports. A positive value subdivides a table and is reserved for a real
    string map; see the namespace note above for why it is not the default.
    """
    table, module = grid_index
    if modules_per_cell and modules_per_cell > 0:
        return grid_id(table, sub=module // modules_per_cell + 1)
    return grid_id(table)


def panel_id(row: int, col: int) -> str:
    """Stable panel ID, e.g. ``R12-C047`` (row 2-wide, col 3-wide)."""
    return f"R{row:02d}-C{col:03d}"


def parse_panel_id(pid: str) -> tuple[int, int]:
    """Inverse of `panel_id`: ``"R12-C047"`` -> ``(12, 47)``.

    ⚠ Widths are MINIMUMS, not fixed: `panel_id` uses `{row:02d}`, so a plot with
    more than 99 rows produces `R272-C047` and a fixed-width slice would silently
    mis-parse it. The full Khavda plot has 6,213 tables, so that is the normal case
    rather than an edge one — hence a regex over an index.

    Raises on anything that is not a panel ID; a caller deriving neighbours from a
    malformed id would silently inspect the wrong panels.
    """
    import re  # noqa: PLC0415 — keeps module import cost where it was

    m = re.fullmatch(r"R(\d+)-C(\d+)", pid.strip())
    if not m:
        raise ValueError(f"not a panel id: {pid!r} (expected e.g. 'R12-C047')")
    return int(m.group(1)), int(m.group(2))


def panel_path(root: str, row: int, col: int) -> str:
    """USD prim path for a panel under ``root`` (e.g. ``/World/Farm``)."""
    return f"{root}/Panel_R{row:02d}_C{col:03d}"


# --------------------------------------------------------------------------- #
# Pure-python panel record — the in-memory mirror of a panel's USD state.
# The USD prim is the source of truth during sim; this record is what the
# read/write adapter maps to/from, and what tests assert against without pxr.
# --------------------------------------------------------------------------- #


@dataclass
class PanelRecord:
    panel_id: str
    grid_index: tuple[int, int]  # (row, col)
    state: PanelState = PanelState.HEALTHY
    iv_yield: float = 1.0
    rul_days: int = -1  # -1 = unknown / not yet predicted
    last_inspected: str = ""
    inspection_log: list[str] = field(default_factory=list)
    geo_position: Optional[tuple[float, float, float]] = None  # (lat, lon, elev)
    #: `grid:id` — the dispatch cell this panel rolls up to. Empty means the stage
    #: was built before the grid namespace existed, or the build disabled it; the
    #: ranker must treat "" as "not in any cell" rather than inventing one.
    cell_id: str = ""

    @property
    def is_healthy(self) -> bool:
        return self.state in HEALTHY_STATES


def append_inspection(
    record: PanelRecord,
    state: PanelState,
    note: str,
    timestamp: str,
) -> PanelRecord:
    """Return a copy of ``record`` with a new state and an appended log line.

    The log is append-only (§6.1): one ISO-stamped line per inspection. This is
    the semantics both the FSM (via the backend) and the USD adapter rely on, so
    it lives here as one pure, tested function.
    """
    line = f"{timestamp} {state.value}: {note}".rstrip()
    return replace(
        record,
        state=state,
        last_inspected=timestamp,
        inspection_log=[*record.inspection_log, line],
    )


# --------------------------------------------------------------------------- #
# FaultReport — the payload shape shared by the run record's `fault_events`
# AND the future ROS 2 `/mission/fault` topic (§6.3, docs/ROS2_CONTRACT.md).
# Defined once here so `orchestrator/mission.py`, `run.py`, and (later)
# `transport/ros2_bridge.py` never independently invent the JSON shape.
# --------------------------------------------------------------------------- #


@dataclass
class FaultReport:
    panel_id: str
    fault_type: str  # a PanelState value (§6.5)
    confidence: float
    note: str
    timestamp: str
    panel_geo_position: Optional[tuple[float, float, float]] = None  # (lat, lon, elev)

    def to_dict(self) -> dict:
        """JSON-serializable dict — the exact `/mission/fault` payload shape."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FaultReport":
        geo = data.get("panel_geo_position")
        return cls(
            panel_id=data["panel_id"],
            fault_type=data["fault_type"],
            confidence=float(data["confidence"]),
            note=data.get("note", ""),
            timestamp=data.get("timestamp", ""),
            panel_geo_position=tuple(geo) if geo is not None else None,
        )


# --------------------------------------------------------------------------- #
# Georeferencing (§6.2). One anchor: farm origin (0,0,0) maps to a known
# (lat, lon, elev) with a known compass heading. Z-up, meters. Equirectangular
# approximation — fine at farm scale (hundreds of meters), and exactly
# invertible so a fault the drone finds lines up with the panel SCADA flags.
# --------------------------------------------------------------------------- #

_M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class GeoAnchor:
    lat0: float  # degrees
    lon0: float  # degrees
    elev0: float = 0.0  # meters
    heading_deg: float = 0.0  # compass bearing of local +Y axis (0 = north, CW)


def local_to_geo(
    x: float, y: float, z: float, anchor: GeoAnchor
) -> tuple[float, float, float]:
    """Map a local metric position (Z-up, meters) to (lat, lon, elev).

    ``heading_deg`` is the compass bearing of the farm's local +Y axis. Local
    +X is 90° clockwise from +Y. We rotate (x, y) into ENU east/north, then
    convert to degrees.
    """
    h = math.radians(anchor.heading_deg)
    east = x * math.cos(h) + y * math.sin(h)
    north = -x * math.sin(h) + y * math.cos(h)
    dlat = north / _M_PER_DEG_LAT
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(anchor.lat0))
    dlon = east / m_per_deg_lon
    return (anchor.lat0 + dlat, anchor.lon0 + dlon, anchor.elev0 + z)


def geo_to_local(
    lat: float, lon: float, elev: float, anchor: GeoAnchor
) -> tuple[float, float, float]:
    """Inverse of :func:`local_to_geo` (for round-trip validation)."""
    north = (lat - anchor.lat0) * _M_PER_DEG_LAT
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(anchor.lat0))
    east = (lon - anchor.lon0) * m_per_deg_lon
    h = math.radians(anchor.heading_deg)
    # Inverse rotation of the (east, north) -> (x, y) mapping above.
    x = east * math.cos(h) - north * math.sin(h)
    y = east * math.sin(h) + north * math.cos(h)
    return (x, y, elev - anchor.elev0)


# --------------------------------------------------------------------------- #
# USD adapter — the ONLY pxr-touching code. pxr is imported inside each
# function so importing this module stays Isaac-free (golden rule). Runs only
# under Isaac Sim's Python (`./python.sh`). ⚠ verify pxr calls against the
# installed 5.1 build — do not trust these snippets from memory.
# --------------------------------------------------------------------------- #


def create_panel(
    stage,
    path: str,
    pid: str,
    row: int,
    col: int,
    geo_position: Optional[tuple[float, float, float]] = None,
    cell_id: str = "",
):
    """Define a panel Xform prim and stamp the initial ``pv:`` attributes.

    `cell_id` stamps the `grid:id` join key when given. Omitted -> the attribute is
    not authored at all, so a stage built without the grid layer is byte-identical
    to one built before it existed.
    """
    from pxr import Gf, Sdf, UsdGeom  # noqa: PLC0415 — lazy Isaac import

    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    prim.CreateAttribute(ATTR_PANEL_ID, Sdf.ValueTypeNames.String).Set(pid)
    # Use explicit Gf types: a bare tuple makes USD infer double vectors and
    # mismatch the declared Int2 (GfVec2i) / Double3 (GfVec3d) types.
    prim.CreateAttribute(ATTR_GRID_INDEX, Sdf.ValueTypeNames.Int2).Set(
        Gf.Vec2i(int(row), int(col))
    )
    prim.CreateAttribute(ATTR_STATE, Sdf.ValueTypeNames.Token).Set(
        PanelState.HEALTHY.value
    )
    prim.CreateAttribute(ATTR_IV_YIELD, Sdf.ValueTypeNames.Float).Set(1.0)
    prim.CreateAttribute(ATTR_RUL_DAYS, Sdf.ValueTypeNames.Int).Set(-1)
    prim.CreateAttribute(ATTR_LAST_INSPECTED, Sdf.ValueTypeNames.String).Set("")
    prim.CreateAttribute(ATTR_INSPECTION_LOG, Sdf.ValueTypeNames.StringArray).Set([])
    if geo_position is not None:
        prim.CreateAttribute(ATTR_GEO_POSITION, Sdf.ValueTypeNames.Double3).Set(
            Gf.Vec3d(*(float(v) for v in geo_position))
        )
    if cell_id:
        prim.CreateAttribute(ATTR_GRID_ID, Sdf.ValueTypeNames.String).Set(cell_id)
    return prim


def author_panel_spec(
    parent_spec,
    name: str,
    pid: str,
    row: int,
    col: int,
    geo_position: Optional[tuple[float, float, float]] = None,
    cell_id: str = "",
):
    """Author a panel as an **Sdf PrimSpec** and return it — the bulk-build twin of
    `create_panel`, stamping the identical ``pv:`` contract.

    Why this exists, measured rather than assumed
    ---------------------------------------------
    `create_panel` goes through `UsdStage`, and every `UsdStage::DefinePrim` fires a
    change notification that recomposes the parent's children. Authoring N panels
    one at a time under one parent is therefore **quadratic**, and it was: profiled
    on this build, plain `Xform.Define` + these attributes scaled **n^1.70** — before
    any reference or instancing — and the whole `farm_builder` measured **n^2.39**,
    which put the 679,616-panel S05b plot at ~55 hours.

    Authoring `Sdf.PrimSpec`s straight into the layer inside one `Sdf.ChangeBlock`
    defers composition to the end, so the stage composes once instead of N times.
    Measured on the same profile: **n^1.03, and 60x faster at 32k panels** (1.63 s
    against 98.28 s).

    ⚠ `UsdStage::DefinePrim` CANNOT be used inside a `ChangeBlock` — the stage never
    recomposes, so the prim is not there to return and it raises. That is why this
    is an Sdf-level function and not a flag on `create_panel`.

    ⚠ Keep this in lockstep with `create_panel`. Two authoring paths for one contract
    is a real hazard; `tests/test_schema_usd.py` asserts the two produce identical
    prims, which is the only thing making the duplication safe.
    """
    from pxr import Gf, Sdf  # noqa: PLC0415 — lazy Isaac import

    spec = Sdf.PrimSpec(parent_spec, name, Sdf.SpecifierDef, "Xform")
    # Same explicit Gf types as `create_panel`: a bare tuple makes USD infer double
    # vectors and mismatch the declared Int2 (GfVec2i) / Double3 (GfVec3d) types.
    for attr_name, type_name, value in (
        (ATTR_PANEL_ID, Sdf.ValueTypeNames.String, pid),
        (ATTR_GRID_INDEX, Sdf.ValueTypeNames.Int2, Gf.Vec2i(int(row), int(col))),
        (ATTR_STATE, Sdf.ValueTypeNames.Token, PanelState.HEALTHY.value),
        (ATTR_IV_YIELD, Sdf.ValueTypeNames.Float, 1.0),
        (ATTR_RUL_DAYS, Sdf.ValueTypeNames.Int, -1),
        (ATTR_LAST_INSPECTED, Sdf.ValueTypeNames.String, ""),
        (ATTR_INSPECTION_LOG, Sdf.ValueTypeNames.StringArray, []),
    ):
        Sdf.AttributeSpec(spec, attr_name, type_name).default = value
    if cell_id:
        Sdf.AttributeSpec(spec, ATTR_GRID_ID, Sdf.ValueTypeNames.String).default = cell_id
    if geo_position is not None:
        Sdf.AttributeSpec(spec, ATTR_GEO_POSITION, Sdf.ValueTypeNames.Double3).default = (
            Gf.Vec3d(*(float(v) for v in geo_position))
        )
    return spec


def read_panel(prim) -> PanelRecord:
    """Read a panel prim's ``pv:`` attributes into a :class:`PanelRecord`."""

    def _get(name, default=None):
        attr = prim.GetAttribute(name)
        return attr.Get() if attr and attr.IsValid() else default

    grid = _get(ATTR_GRID_INDEX, (0, 0))
    geo = _get(ATTR_GEO_POSITION)
    return PanelRecord(
        panel_id=_get(ATTR_PANEL_ID, "") or "",
        grid_index=(int(grid[0]), int(grid[1])),
        state=coerce_state(_get(ATTR_STATE, PanelState.UNKNOWN.value)),
        iv_yield=float(_get(ATTR_IV_YIELD, 1.0)),
        rul_days=int(_get(ATTR_RUL_DAYS, -1)),
        last_inspected=_get(ATTR_LAST_INSPECTED, "") or "",
        inspection_log=list(_get(ATTR_INSPECTION_LOG, []) or []),
        geo_position=tuple(geo) if geo is not None else None,
        cell_id=_get(ATTR_GRID_ID, "") or "",
    )


def restore_state(prim, record: PanelRecord) -> None:
    """Rewrite a panel's mutable fields from `record` **without** logging.

    The inverse of `write_state`, for repeat runs of one scenario (`--repeat`):
    the first mission writes its verdict onto ``pv:state``, so a second mission
    would read that verdict as ground truth and every `injected_state` in the
    record would be a lie. Restoring resets the state, the last-inspected stamp
    AND the log — the log is not bookkeeping, it feeds ``history`` in the
    perception prompt, so leaving run 1's notes in place would ask run 2 a
    different question and destroy the thing a repeat is measuring.

    Deliberately not part of the `Transport` ABC: it exists to rewind a
    measurement harness, and a real robot cannot un-inspect a panel.
    """
    from pxr import Vt  # noqa: PLC0415 — lazy Isaac import

    prim.GetAttribute(ATTR_STATE).Set(record.state.value)
    prim.GetAttribute(ATTR_LAST_INSPECTED).Set(record.last_inspected)
    prim.GetAttribute(ATTR_INSPECTION_LOG).Set(Vt.StringArray(list(record.inspection_log)))


def write_state(prim, state: PanelState, note: str, timestamp: str) -> None:
    """Write a new state to the prim and append one line to the log (§6.1)."""
    from pxr import Vt  # noqa: PLC0415 — lazy Isaac import

    prim.GetAttribute(ATTR_STATE).Set(state.value)
    prim.GetAttribute(ATTR_LAST_INSPECTED).Set(timestamp)
    log = list(prim.GetAttribute(ATTR_INSPECTION_LOG).Get() or [])
    log.append(f"{timestamp} {state.value}: {note}".rstrip())
    prim.GetAttribute(ATTR_INSPECTION_LOG).Set(Vt.StringArray(log))
