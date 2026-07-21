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
from dataclasses import dataclass, field, replace
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


def panel_id(row: int, col: int) -> str:
    """Stable panel ID, e.g. ``R12-C047`` (row 2-wide, col 3-wide)."""
    return f"R{row:02d}-C{col:03d}"


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
):
    """Define a panel Xform prim and stamp the initial ``pv:`` attributes."""
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
    return prim


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
    )


def write_state(prim, state: PanelState, note: str, timestamp: str) -> None:
    """Write a new state to the prim and append one line to the log (§6.1)."""
    from pxr import Vt  # noqa: PLC0415 — lazy Isaac import

    prim.GetAttribute(ATTR_STATE).Set(state.value)
    prim.GetAttribute(ATTR_LAST_INSPECTED).Set(timestamp)
    log = list(prim.GetAttribute(ATTR_INSPECTION_LOG).Get() or [])
    log.append(f"{timestamp} {state.value}: {note}".rstrip())
    prim.GetAttribute(ATTR_INSPECTION_LOG).Set(Vt.StringArray(log))
