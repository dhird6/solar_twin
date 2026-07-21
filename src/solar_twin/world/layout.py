"""Procedural farm layout — pure geometry + seeded fault injection.

Shared logic: `run.py` uses it now (against the fake backend) and
`farm_builder.py` will use the SAME layout to author the USD stage on the Spark,
so the panel grid and the seeded fault picks are identical in sim and in tests.
No Isaac import here — this is pure math and lives in `world/` only because it
is farm-shaped; importing it never drags in pxr.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from solar_twin.control.base import Waypoint
from solar_twin.orchestrator.mission import InspectionTarget
from solar_twin.schema.pv_module import (
    GeoAnchor,
    PanelRecord,
    PanelState,
    coerce_state,
    local_to_geo,
    panel_id,
)


@dataclass(frozen=True)
class PanelSite:
    panel_id: str
    row: int
    col: int
    position: tuple[float, float, float]  # stage-local meters (Z-up)
    geo_position: tuple[float, float, float]  # (lat, lon, elev)


class FarmLayout:
    """Panel grid + georef derived from a parsed ``farm.yaml`` dict."""

    def __init__(self, farm_cfg: dict):
        self.cfg = farm_cfg
        grid = farm_cfg["grid"]
        self.rows = int(grid["rows"])
        self.cols = int(grid["cols"])
        self.row_pitch = float(grid["row_pitch"])
        self.col_pitch = float(grid["col_pitch"])
        self.origin = tuple(float(v) for v in grid.get("origin", [0.0, 0.0, 0.0]))
        geo = farm_cfg["georef"]
        self.anchor = GeoAnchor(
            lat0=float(geo["lat0"]),
            lon0=float(geo["lon0"]),
            elev0=float(geo.get("elev0", 0.0)),
            heading_deg=float(geo.get("heading_deg", 0.0)),
        )
        self.sites = self._build_sites()

    def _build_sites(self) -> list[PanelSite]:
        ox, oy, oz = self.origin
        sites: list[PanelSite] = []
        for row in range(self.rows):
            for col in range(self.cols):
                x = ox + col * self.col_pitch
                y = oy + row * self.row_pitch
                z = oz
                sites.append(
                    PanelSite(
                        panel_id=panel_id(row, col),
                        row=row,
                        col=col,
                        position=(x, y, z),
                        geo_position=local_to_geo(x, y, z, self.anchor),
                    )
                )
        return sites

    @property
    def n_panels(self) -> int:
        return len(self.sites)

    def seeded_faults(self) -> dict[str, PanelState]:
        """Seeded pick of which panels are faulted and with what state.

        Deterministic in (seed, rate, states, grid) so a run replays exactly.
        """
        faults_cfg = self.cfg.get("faults", {})
        rate = float(faults_cfg.get("rate", 0.0))
        states = [coerce_state(s) for s in faults_cfg.get("states", [])]
        if rate <= 0.0 or not states:
            return {}
        seed = int(self.cfg.get("seed", 0))
        rng = random.Random(seed)
        n_fault = round(rate * self.n_panels)
        chosen = rng.sample(self.sites, k=min(n_fault, self.n_panels))
        return {site.panel_id: rng.choice(states) for site in chosen}

    def panel_records(self) -> list[PanelRecord]:
        """Panels as records with seeded faults applied (for the fake backend)."""
        faults = self.seeded_faults()
        return [
            PanelRecord(
                panel_id=s.panel_id,
                grid_index=(s.row, s.col),
                state=faults.get(s.panel_id, PanelState.HEALTHY),
                geo_position=s.geo_position,
            )
            for s in self.sites
        ]

    def inspection_targets(self, mission_cfg: dict) -> list[InspectionTarget]:
        """Waypoints per panel derived from layout + mission kinematics."""
        kin = mission_cfg.get("kinematics", {})
        screen_z = float(kin.get("screen_standoff", 3.0))
        confirm_z = float(kin.get("confirm_standoff", 1.0))
        approach_offset = self.row_pitch / 2.0
        targets: list[InspectionTarget] = []
        for s in self.sites:
            x, y, z = s.position
            targets.append(
                InspectionTarget(
                    panel_id=s.panel_id,
                    approach=Waypoint(x, y - approach_offset, z),
                    screen=Waypoint(x, y, z + screen_z),
                    confirm=Waypoint(x, y, z + confirm_z),
                )
            )
        return targets
