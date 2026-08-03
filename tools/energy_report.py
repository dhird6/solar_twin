#!/usr/bin/env python3
"""What the plant would produce today, and what its faults are costing.

The CLI over `solar_twin.energy` — the pillar that turns "R12-C047 is soiled" into
a number an operator can act on. Isaac-free and GPU-free: it reads the same farm
config the stage was built from, so the geometry, tracker limit and site anchor are
the ones actually rendered.

    PYTHONPATH=src python3 tools/energy_report.py configs/farm_khavda_block02.yaml
    PYTHONPATH=src python3 tools/energy_report.py configs/farm_khavda_block02.yaml \
        --date 2026-06-21 --air-temp 42 --tariff 2.50 --currency INR
    PYTHONPATH=src python3 tools/energy_report.py configs/farm_khavda_block02.yaml \
        --json runs/energy.json

⚠⚠ Every number is MODELLED clear-sky output with no weather, no dust and no SCADA
to check it against. Read `src/solar_twin/energy/model.py`'s header before quoting
anything from here — in particular, quote the healthy-vs-faulted RATIO rather than
the absolute kWh, which is an optimistic ceiling.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from solar_twin.energy.model import (  # noqa: E402
    UNVALIDATED_CAVEAT,
    energy_kwh,
    fault_cost,
    instant_power,
    money,
    plant_from_layout_cfg,
)
from solar_twin.schema.pv_module import PanelState  # noqa: E402
from solar_twin.world.solar import solar_position  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("farm", help="a farm config, e.g. configs/farm_khavda_block02.yaml")
    ap.add_argument("--date", default="2026-06-21", help="UTC date to model (YYYY-MM-DD)")
    ap.add_argument("--step-minutes", type=int, default=15)
    ap.add_argument("--air-temp", type=float, default=38.0, help="ambient degC")
    ap.add_argument("--wind", type=float, default=1.0, help="wind speed m/s")
    ap.add_argument(
        "--tariff", type=float, default=0.0,
        help="value per kWh. No default on purpose — omit it and no money is reported, "
        "because a hard-coded tariff is how a number acquires a currency it was never "
        "quoted in.",
    )
    ap.add_argument("--currency", default="", help="label only, e.g. INR")
    ap.add_argument("--json", dest="json_out", default="", help="also write JSON here")
    args = ap.parse_args(argv)

    import yaml

    cfg = yaml.safe_load(Path(args.farm).read_text())
    from solar_twin.world.layout import FarmLayout

    layout = FarmLayout(cfg)
    plant = plant_from_layout_cfg(cfg, layout)
    faults = layout.seeded_faults()

    day = dt.datetime.fromisoformat(args.date).replace(tzinfo=dt.timezone.utc)
    step = dt.timedelta(minutes=args.step_minutes)
    steps = int(24 * 60 / args.step_minutes)

    healthy_pts, faulted_pts, rows = [], [], []
    for i in range(steps):
        when = day + step * i
        elev, azim = solar_position(plant.latitude, plant.longitude, when)
        kw = dict(
            sun_elevation_deg=elev, sun_azimuth_deg=azim,
            air_temp_c=args.air_temp, wind_speed_ms=args.wind,
        )
        h = instant_power(plant, when, **kw)
        healthy_pts.append(h)
        loss = fault_cost(plant, when, states=faults, **kw)
        faulted_pts.append(loss)
        rows.append((when, elev, h, loss))

    sec = args.step_minutes * 60
    kwh_healthy = energy_kwh(healthy_pts, sec)
    kwh_faulted = sum(l.faulted_ac_w for l in faulted_pts) * sec / 3_600_000.0
    kwh_lost = kwh_healthy - kwh_faulted
    n_faulted = sum(1 for s in faults.values() if s is not PanelState.HEALTHY)

    print(f"\n  {Path(args.farm).name} — {args.date} (UTC), clear sky, {args.air_temp:.0f}degC air")
    print(f"  {plant.n_modules:,} modules · {plant.dc_nameplate_w()/1e6:.2f} MWdc "
          f"· {plant.ac_nameplate_w()/1e6:.2f} MWac · lat {plant.latitude:.4f}")
    if plant.row_pitch_m > 0:
        print(f"  self-shading modelled at {plant.row_pitch_m:.2f} m row pitch")
    else:
        print("  ⚠ no `energy.row_pitch_m` in the config — self-shading NOT modelled")

    print(f"\n  {'UTC':>6} {'elev':>6} {'POA':>6} {'Tcell':>6} {'shade':>6} {'AC MW':>8}")
    for when, elev, h, _ in rows:
        if elev <= 0 or when.minute != 0 or when.hour % 2:
            continue
        print(f"  {when.strftime('%H:%M'):>6} {elev:6.1f} {h.poa_global:6.0f} "
              f"{h.cell_temp_c:6.1f} {h.shaded_fraction:6.2f} {h.ac_power_w/1e6:8.3f}")

    pct = (100.0 * kwh_lost / kwh_healthy) if kwh_healthy else 0.0
    print(f"\n  healthy      {kwh_healthy/1000:9.2f} MWh")
    print(f"  with faults  {kwh_faulted/1000:9.2f} MWh   ({n_faulted:,} faulted modules)")
    print(f"  lost         {kwh_lost/1000:9.2f} MWh   ({pct:.2f}%)")
    print(f"  specific yield {kwh_healthy/(plant.dc_nameplate_w()/1000):.2f} kWh/kWp/day")
    if args.tariff:
        cur = f" {args.currency}" if args.currency else ""
        print(f"  value lost  {money(kwh_lost, args.tariff):9,.0f}{cur}/day "
              f"at {args.tariff:g}{cur}/kWh")

    by_state: dict[str, int] = {}
    for s in faults.values():
        by_state[s.value] = by_state.get(s.value, 0) + 1
    if by_state:
        print(f"  faults: {by_state}")

    print(f"\n  ⚠ {UNVALIDATED_CAVEAT}\n")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "farm": args.farm, "date": args.date,
            "n_modules": plant.n_modules,
            "dc_nameplate_w": plant.dc_nameplate_w(),
            "kwh_healthy": kwh_healthy, "kwh_faulted": kwh_faulted,
            "kwh_lost": kwh_lost, "lost_pct": pct,
            "n_faulted_modules": n_faulted, "faults_by_state": by_state,
            "tariff_per_kwh": args.tariff or None,
            "currency": args.currency or None,
            "value_lost": money(kwh_lost, args.tariff) if args.tariff else None,
            "caveat": UNVALIDATED_CAVEAT,
        }, indent=2))
        print(f"  wrote {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
