"""Clear-sky PV production off the twin's own geometry (pure, Isaac-free).

The missing pillar. Every other pillar asks "can the fleet operate" or "do its eyes
work"; for a *solar farm* twin the question an operator actually asks is **does the
plant make power, and what is this fault costing me**. The twin had real modules,
real tracker angles, a real sun and real shading, and converted none of it into
kilowatt-hours.

**Why an external solver rather than something in Isaac.** This mirrors how NVIDIA's
own DSX (AI-factory) twin works: OpenUSD carries the geometry, an external solver
computes the physics (Cadence for thermal, ETAP for electrical), and the results are
imported back onto the stage. Omniverse aggregates and visualises; it does not solve.
pvlib is our ETAP. That is why this module is pure-python and knows nothing about USD
— `run.py` reads the stage and hands us numbers.

**The sun is OUR sun, deliberately.** pvlib ships a perfectly good solar-position
routine and this module does not use it for the panel geometry. `world/solar.py`
drives the rendered tracker angles and the stage light, so if energy used a second,
independent sun the model and the picture would silently disagree — the exact trap
`solar.py`'s own header warns about. Measured 2026-08-03 at Khavda over 02:00-12:00Z:
the two agree to **0.13 deg elevation / 0.17 deg azimuth**, inside `solar.py`'s
documented 0.1-0.5 deg claim. `tests/test_energy.py` pins that agreement, so a drift
in either implementation fails a test instead of quietly biasing kWh.

## The four assumptions that bound every number here

1. **Clear sky. No weather.** `pvlib.clearsky.ineichen` with a climatological Linke
   turbidity. There is no TMY file, no measured irradiance, no cloud, and — for a
   Kutch site — no dust storm. Real annual output at Khavda will be **below** this.
   Directionally: this is a ceiling, not a forecast.
2. **Generic module electrical parameters.** PVWatts needs nameplate DC and a
   temperature coefficient; ours are typical-crystalline-silicon defaults, NOT the
   site's datasheet. The vendor DWG is hardware *geometry* — it carries no
   electrical spec. Override `ModuleSpec` the moment a real datasheet exists.
3. **No validation.** We hold no SCADA for this plant, so nothing here has been
   checked against reality. Same standing limitation as `kpi/simulated_scada.py`,
   and the reason `UNVALIDATED_CAVEAT` travels with every result.
4. **Fault derates are literature-typical, not measured here.** `DERATE_BY_STATE`
   encodes what each fault class costs a module. Those are plausible published
   ranges for the fault *type*; nobody has measured them on Khavda hardware.

None of that makes the model useless — a physics-based expected-output curve is what
turns "panel R12-C047 is soiled" into "that is 4.1 kWh/day". It does mean the
absolute kWh is a modelled ceiling, and the trustworthy quantity is the **ratio**
(loss vs healthy) rather than the absolute.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional

from solar_twin.schema.pv_module import PanelState
from solar_twin.world.solar import tracker_rotation_deg

#: Travels with every result, for the same reason `simulated_scada.SIMULATED_CAVEAT`
#: does: a modelled number that loses its caveat gets quoted as a measurement.
UNVALIDATED_CAVEAT = (
    "MODELLED clear-sky output from the twin's own geometry — no TMY, no measured "
    "irradiance, no dust, and NO SCADA to validate against. Absolute kWh is an "
    "optimistic ceiling; quote the healthy-vs-faulted RATIO, not the magnitude."
)

#: Fractional power loss per fault state, at the module.
#:
#: ⚠ These are typical published ranges for each fault CLASS, not measurements of
#: Khavda hardware — assumption (4) in this module's header. They are deliberately
#: mid-range rather than worst-case, because a headline "this fault costs X" built
#: from worst-case inputs is the kind of flattering number this project keeps having
#: to retract.
#:
#: `SHADING` is 0.0 on purpose and is NOT a missing entry: shading loss is computed
#: geometrically from the tracker and row pitch (`self_shaded_fraction`), so also
#: derating it here would count it twice. It is the one "fault" the twin can already
#: derive from first principles.
DERATE_BY_STATE: dict[PanelState, float] = {
    PanelState.HEALTHY: 0.00,
    PanelState.SOILED: 0.15,
    PanelState.HOTSPOT: 0.20,
    PanelState.CRACK: 0.10,
    PanelState.DIODE_FAULT: 0.33,     # one bypassed sub-string of three
    PanelState.STRING_DROPOUT: 1.00,  # the module contributes nothing
    PanelState.SHADING: 0.00,         # modelled geometrically — see above
    PanelState.UNKNOWN: 0.00,         # never assume a loss we have not established
}


@dataclass(frozen=True)
class ModuleSpec:
    """Per-module electrical spec.

    Defaults are typical mono-c-Si for a ~2.3 m² module and are **assumption (2)** —
    replace them from the datasheet when one exists. `pdc0_w` is nameplate DC at STC;
    `gamma_pdc` is the power temperature coefficient in 1/degC (negative).

    ⚠ A cross-check that says the default is LOW: 30,016 modules x 550 W is 16.51 MW,
    while `docs/TASKS.md` describes BLOCK-02 as "one ~18 MWdc block". If that 18 MW is
    right the real module is nearer **600 W**, and every absolute kWh here is ~9%
    under. Left at 550 W rather than back-fitted to the docs, because reverse-engineering
    a datasheet number from a rounded capacity figure is how an assumption becomes a
    fact. Get the datasheet.
    """

    pdc0_w: float = 550.0
    gamma_pdc: float = -0.0034
    #: Fraction lost to DC wiring, mismatch, connections — the PVWatts "system
    #: losses" bucket, minus soiling (we model soiling as a fault state instead, so
    #: including it here would double-count).
    dc_loss_fraction: float = 0.06


@dataclass(frozen=True)
class PlantSpec:
    """Site + array geometry the energy model needs, all of it already in the twin."""

    latitude: float
    longitude: float
    altitude_m: float = 0.0
    n_modules: int = 1
    #: Torque-tube bearing, degrees from north. 0 = N-S axis, the Khavda case.
    axis_azimuth_deg: float = 0.0
    max_rotation_deg: float = 60.0
    #: Centre-to-centre row spacing and the module's cross-axis chord, both metres.
    #: Used for geometric self-shading; 0 disables it.
    row_pitch_m: float = 0.0
    module_width_m: float = 0.0
    module: ModuleSpec = field(default_factory=ModuleSpec)
    #: Inverter nameplate AC. 0 means "derive as 1.2 DC/AC" at construction time.
    pac0_w: float = 0.0

    def dc_nameplate_w(self) -> float:
        return self.n_modules * self.module.pdc0_w

    def ac_nameplate_w(self) -> float:
        """Inverter AC rating; defaults to a 1.2 DC:AC ratio, a common design point."""
        return self.pac0_w if self.pac0_w > 0 else self.dc_nameplate_w() / 1.2


@dataclass(frozen=True)
class PowerPoint:
    """Instantaneous plant state. Powers in watts, irradiance in W/m²."""

    when_utc: _dt.datetime
    sun_elevation_deg: float
    sun_azimuth_deg: float
    tracker_rotation_deg: float
    ghi: float
    dni: float
    dhi: float
    poa_global: float
    cell_temp_c: float
    shaded_fraction: float
    dc_power_w: float
    ac_power_w: float
    caveat: str = UNVALIDATED_CAVEAT

    @property
    def is_dark(self) -> bool:
        return self.sun_elevation_deg <= 0.0


@dataclass(frozen=True)
class ProductionLoss:
    """What a set of faulted modules costs, against the same instant run healthy."""

    healthy_ac_w: float
    faulted_ac_w: float
    lost_w: float
    lost_fraction: float
    n_faulted: int
    caveat: str = UNVALIDATED_CAVEAT


def _surface_orientation(rotation_deg: float, axis_azimuth_deg: float) -> tuple[float, float]:
    """(tilt, azimuth) of the module plane for an HSAT at `rotation_deg`.

    `tracker_rotation_deg` is positive when the panel faces the +cross-axis side —
    east, for the N-S axis at Khavda, i.e. morning. Tilt is therefore the magnitude
    and the azimuth flips 180 deg with the sign. Getting this backwards is a silent
    error that costs ~nothing at noon and everything at 07:00, which is why
    `tests/test_energy.py` checks a morning instant faces east.
    """
    tilt = abs(rotation_deg)
    side = 90.0 if rotation_deg >= 0.0 else -90.0
    return tilt, (axis_azimuth_deg + side) % 360.0


def instant_power(
    plant: PlantSpec,
    when_utc: _dt.datetime,
    *,
    sun_elevation_deg: float,
    sun_azimuth_deg: float,
    air_temp_c: float = 30.0,
    wind_speed_ms: float = 1.0,
    derate: float = 0.0,
    shaded_fraction: Optional[float] = None,
) -> PowerPoint:
    """Plant AC power at one instant, from the twin's own sun and geometry.

    `sun_elevation_deg`/`sun_azimuth_deg` are passed in rather than computed so the
    caller uses the SAME sun that drove the stage — see this module's header.
    `derate` is an extra fractional loss (0..1) applied to DC, which is how fault
    states enter. `shaded_fraction` overrides the geometric self-shading estimate.

    Returns zeros below the horizon rather than raising: night is a legitimate
    instant and the caller should be able to integrate straight across it.
    """
    rot = tracker_rotation_deg(
        sun_elevation_deg, sun_azimuth_deg, plant.axis_azimuth_deg, plant.max_rotation_deg
    )
    tilt, surf_az = _surface_orientation(rot, plant.axis_azimuth_deg)

    if sun_elevation_deg <= 0.0:
        return PowerPoint(
            when_utc=when_utc,
            sun_elevation_deg=sun_elevation_deg,
            sun_azimuth_deg=sun_azimuth_deg,
            tracker_rotation_deg=rot,
            ghi=0.0, dni=0.0, dhi=0.0, poa_global=0.0,
            cell_temp_c=air_temp_c,
            shaded_fraction=0.0,
            dc_power_w=0.0, ac_power_w=0.0,
        )

    # Imported here, not at module scope, so `import solar_twin.energy` works on a
    # bare interpreter — the same rule `perception/base.py` follows for numpy. The
    # message names the fix, because "No module named pvlib" three frames deep is
    # how a missing optional dep reads as a code bug (which is what a gitignored
    # asset did to CI earlier today).
    try:
        import pandas as pd  # noqa: PLC0415
        import pvlib  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — exercised by the skip guard
        raise ImportError(
            "the energy model needs pvlib + pandas: pip install --break-system-packages "
            f"'pvlib>=0.11' (original error: {exc})"
        ) from exc

    times = pd.DatetimeIndex([when_utc])
    loc = pvlib.location.Location(plant.latitude, plant.longitude, altitude=plant.altitude_m)
    cs = loc.get_clearsky(times, model="ineichen")
    ghi = float(cs["ghi"].iloc[0])
    dni = float(cs["dni"].iloc[0])
    dhi = float(cs["dhi"].iloc[0])

    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=surf_az,
        solar_zenith=90.0 - sun_elevation_deg,
        solar_azimuth=sun_azimuth_deg,
        dni=dni,
        ghi=ghi,
        dhi=dhi,
    )
    poa_global = float(poa["poa_global"])

    # Geometric self-shading, from the same function that quantifies the KPI-03
    # stimulus. ⚠ It assumes no backtracking, so it is a worst case (see its own
    # docstring) — and it is applied to the BEAM component only, because a shaded
    # module still sees diffuse sky.
    if shaded_fraction is None:
        if plant.row_pitch_m > 0.0 and plant.module_width_m > 0.0:
            from solar_twin.world.solar import self_shaded_fraction  # noqa: PLC0415

            shaded_fraction = self_shaded_fraction(
                plant.row_pitch_m,
                plant.module_width_m,
                sun_elevation_deg,
                sun_azimuth_deg,
                plant.axis_azimuth_deg,
                plant.max_rotation_deg,
            )
        else:
            shaded_fraction = 0.0
    poa_direct = float(poa["poa_direct"])
    poa_effective = max(poa_global - poa_direct * shaded_fraction, 0.0)

    temp_params = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][
        "open_rack_glass_glass"
    ]
    cell_temp = float(
        pvlib.temperature.sapm_cell(poa_effective, air_temp_c, wind_speed_ms, **temp_params)
    )

    # `effective_irradiance` since pvlib 0.13; `g_poa_effective` is deprecated and
    # warns once per call, which on a day sweep is one warning per timestep.
    dc = float(
        pvlib.pvsystem.pvwatts_dc(
            effective_irradiance=poa_effective,
            temp_cell=cell_temp,
            pdc0=plant.dc_nameplate_w(),
            gamma_pdc=plant.module.gamma_pdc,
        )
    )
    dc *= (1.0 - plant.module.dc_loss_fraction) * (1.0 - max(0.0, min(1.0, derate)))
    ac = float(pvlib.inverter.pvwatts(pdc=dc, pdc0=plant.ac_nameplate_w()))
    ac = max(ac, 0.0)  # pvwatts returns negative (tare) draw below its turn-on point

    return PowerPoint(
        when_utc=when_utc,
        sun_elevation_deg=sun_elevation_deg,
        sun_azimuth_deg=sun_azimuth_deg,
        tracker_rotation_deg=rot,
        ghi=ghi, dni=dni, dhi=dhi,
        poa_global=poa_global,
        cell_temp_c=cell_temp,
        shaded_fraction=float(shaded_fraction),
        dc_power_w=dc,
        ac_power_w=ac,
    )


def fault_cost(
    plant: PlantSpec,
    when_utc: _dt.datetime,
    *,
    sun_elevation_deg: float,
    sun_azimuth_deg: float,
    states: dict[str, PanelState],
    air_temp_c: float = 30.0,
    wind_speed_ms: float = 1.0,
) -> ProductionLoss:
    """What the faults in `states` cost at this instant, against the same plant healthy.

    `states` maps panel_id -> state, i.e. exactly what a mission's verdicts produce.
    Modules not named are assumed healthy.

    The derate is applied as an **array-weighted average**, not per-string: we have
    the module positions but no string map (the vendor DWG is hardware geometry and
    carries no electrical topology). That is the honest simplification — a real
    string-level model would let one dropped module drag its whole series string,
    which this UNDER-states. Named here rather than buried, because it is the first
    thing a plant engineer will ask.
    """
    n_faulted = sum(1 for s in states.values() if s is not PanelState.HEALTHY)
    total_derate = sum(DERATE_BY_STATE.get(s, 0.0) for s in states.values())
    mean_derate = total_derate / plant.n_modules if plant.n_modules > 0 else 0.0

    kw = dict(
        sun_elevation_deg=sun_elevation_deg,
        sun_azimuth_deg=sun_azimuth_deg,
        air_temp_c=air_temp_c,
        wind_speed_ms=wind_speed_ms,
    )
    healthy = instant_power(plant, when_utc, derate=0.0, **kw)
    faulted = instant_power(plant, when_utc, derate=mean_derate, **kw)
    lost = healthy.ac_power_w - faulted.ac_power_w
    return ProductionLoss(
        healthy_ac_w=healthy.ac_power_w,
        faulted_ac_w=faulted.ac_power_w,
        lost_w=lost,
        lost_fraction=(lost / healthy.ac_power_w) if healthy.ac_power_w > 0 else 0.0,
        n_faulted=n_faulted,
    )


def plant_from_layout_cfg(cfg: dict, layout) -> PlantSpec:  # noqa: ANN001 — duck-typed
    """Build a `PlantSpec` from a farm config + a built `FarmLayout`.

    Reads the anchor, tracker limit and module geometry the stage was actually built
    with, so the energy model cannot drift from the rendered plant. `layout` is
    duck-typed (`.anchor`, `.sites`) to keep this module Isaac-free and testable
    against a stub.
    """
    anchor = layout.anchor
    sun_cfg = cfg.get("sun", {}) or {}
    panel_cfg = cfg.get("panel", {}) or {}
    energy_cfg = cfg.get("energy", {}) or {}
    mod_cfg = energy_cfg.get("module", {}) or {}

    module = ModuleSpec(
        pdc0_w=float(mod_cfg.get("pdc0_w", ModuleSpec.pdc0_w)),
        gamma_pdc=float(mod_cfg.get("gamma_pdc", ModuleSpec.gamma_pdc)),
        dc_loss_fraction=float(
            mod_cfg.get("dc_loss_fraction", ModuleSpec.dc_loss_fraction)
        ),
    )
    return PlantSpec(
        latitude=float(anchor.lat0),
        longitude=float(anchor.lon0),
        altitude_m=float(getattr(anchor, "elev0", 0.0) or 0.0),
        n_modules=len(layout.sites),
        axis_azimuth_deg=float(energy_cfg.get("axis_azimuth_deg", 0.0)),
        max_rotation_deg=float(sun_cfg.get("tracker_max_rotation_deg", 60.0)),
        row_pitch_m=float(energy_cfg.get("row_pitch_m", 0.0)),
        module_width_m=float(
            energy_cfg.get("module_width_m", panel_cfg.get("width", 0.0)) or 0.0
        ),
        module=module,
        pac0_w=float(energy_cfg.get("pac0_w", 0.0)),
    )


def energy_kwh(points: list[PowerPoint], step_seconds: float) -> float:
    """Integrate a series of instants into kWh (rectangular, one step per point)."""
    if step_seconds <= 0:
        raise ValueError("step_seconds must be positive")
    return sum(p.ac_power_w for p in points) * step_seconds / 3_600_000.0


def money(kwh: float, tariff_per_kwh: float) -> float:
    """Value of `kwh` at `tariff_per_kwh`. Currency-agnostic: it is whatever you pass.

    Deliberately not defaulted — a hard-coded tariff is how a number acquires a
    currency it was never quoted in.
    """
    if not math.isfinite(kwh) or not math.isfinite(tariff_per_kwh):
        raise ValueError("kwh and tariff must be finite")
    return kwh * tariff_per_kwh
