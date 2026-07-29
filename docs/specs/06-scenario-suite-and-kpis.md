# 06 — Scenario Suite & KPIs

This is the acceptance-testing spec: how "good enough to fly" is measured, in
numbers, from a reproducible config — never a GUI demo (`FR-17`, `NFR-02`).

## KPI definitions

| ID | Name | Formula | Gated by | Source today |
|---|---|---|---|---|
| `KPI-01` | Detection rate | fraction of panels where `detected_state == injected_state` | Every slice ≥ `SLICE-0` | **Already implemented**: `MissionResult.detection_rate` in `orchestrator/mission.py` |
| `KPI-02` | Coverage % | panels inspected / panels in scope, within the mission time budget | `SLICE-7` (fleet) | New — count from `MissionResult.panels_inspected` vs. `farm.yaml` grid size |
| `KPI-03` | False-fault rate | fraction of **healthy** panels whose `detected_state != healthy` under an adversarial (shadow/blur/dust) scenario | `SLICE-3` (the central thesis metric — see `HAZ-07`) | New — filter `MissionResult.results` where `injected_state == healthy` and `not correct`. **Baseline harness demonstrated 2026-07-24**: live Cosmos Reason over a healthy panel under sweeping turbine-blade shadows returned 0/6 false faults (moderate shadow, not worst-case) — the `SC-05` harness starting point |
| `KPI-04` | Collision-free-flight rate | fraction of scenario runs with zero collisions and zero keep-out-volume intrusions | `SLICE-2` | **Partial** — keep-out intrusions already captured in the run-record `keepout` block (`waypoints_clamped`, `min_clearance_m`) via `SafeControl` (`IF-07`); physics-contact events pending the articulation/Pegasus work |
| `KPI-05` | Station-keep error | max/mean deviation (meters) from the commanded hold pose during a screen/confirm pass under wind/wake | `SLICE-2` | **First measurement under wind, 2026-07-29** (`SC-13` `khavda_windy_hover`, PX4-governed Iris, `tools/px4_hover.py --wind-scenario`): **calm air 43 mm** altitude hold over 35 s vs **~724 mm (2.13-2.86 m) at 12 m/s with 35% gusts**, roll/pitch working +36/-36 deg against ±0.5 deg calm. It stays airborne but **never satisfies the settled criterion** (\|vz\| < 0.05), i.e. at this wind the drone holds altitude but does not station-keep — a ~17x degradation, honestly reported rather than gated away. ⚠ Compute from Isaac ground truth, never PX4's estimate (~0.23 m apart, `RISK-29`). ⚠ Wind is applied only once airborne: 15 N exceeds the frame's 14.7 N weight and tumbled a *parked* drone inverted |
| `KPI-06` | Battery/time-window adherence | fraction of missions completed without breaching the declared battery reserve floor or daylight/time window | `SLICE-7` | New — requires `IF-01` (`EnergyAware`) |
| `KPI-07` | Terrain traversal pass/fail | ground bot completes the ramp testbed at the declared max grade without loss of contact/stall | `SLICE-2`/`SLICE-6` | New — pass/fail per grade angle |
| `KPI-08` | Generated-frame validity rate | fraction of Cosmos Transfer/Predict output frames that pass the Evaluator filter | `SLICE-4` | New — from the Data Factory Blueprint's Evaluator stage (`NFR-08`) |
| `KPI-03a` | False-alarm rate | fraction of **healthy** panels given a *specific wrong diagnosis* — `detected_state` is neither `healthy` nor `unknown` | `SLICE-3`, alongside `KPI-03` | **Implemented**: `MissionResult.false_alarm_rate` |
| `KPI-03b` | Abstention rate | fraction of **all** inspected panels with `detected_state == unknown` — no usable verdict | `SLICE-3`, alongside `KPI-03` | **Implemented**: `MissionResult.abstention_rate` (+ `abstentions` count). Earned its keep twice: it separated a VLM parse bug from a real false alarm, and it distinguished "vLLM server down" (10/10 abstentions, `false_fault_rate` 1.000) from a model collapse |
| `KPI-03c` | False-alarm attributable share | fraction of a run's false alarms whose **neighbouring** panel was seeded faulty — an upper bound on how much of `KPI-03` is the panel next door | `SLICE-3`, alongside `KPI-03` | **Implemented**: `kpi/confound.py`, written to every run record's `confound` block. Measured 1.00 on `SC-01` — see the caveat below |

**Note on `KPI-01` vs `KPI-03`:** these are deliberately distinct. `KPI-01` is
overall accuracy across all injected states (including real faults); `KPI-03`
isolates the specific "swept blade shadow → false hotspot" failure mode this
project exists to prevent. A system can have decent `KPI-01` and still be
unsafe to deploy if `KPI-03` is high on adversarial scenarios — report both,
always.

**`KPI-03`'s two halves (`KPI-03a`/`KPI-03b`), decided 2026-07-29.** `unknown` is
`!= healthy`, so a panel the model *failed to answer for* scored identically in
`KPI-03` to one it wrongly called faulty — two failures needing opposite fixes
(plumbing/prompt versus model robustness) reported as one number. Measured: a VLM
reply missing its closing brace moved `KPI-03` from 0.00 to 0.053 while the model
had actually said `healthy` with confidence 1.0.

`KPI-03`'s formula is **unchanged** — it is a locked contract (`FR-03`, §6.5), and
redefining it would make every number already recorded non-comparable, including
the two verified-stimulus 0.00 points (`SC-11`, `SC-12`). The split is *reported
alongside* instead, and it is exact:

```
KPI-03  ==  KPI-03a  +  (healthy panels that abstained / healthy panels)
```

So `KPI-03` remains the conservative headline (an abstention still counts against
you — it can never flatter the system), `KPI-03a` is the number the project is
actually driving down, and `KPI-03b` answers "did the pipeline work at all?"
across every panel, not just healthy ones — losing the answer for a *faulted*
panel is equally a plumbing failure, it just surfaces as a missed detection in
`KPI-01`. Both are gateable like any other metric and both are tracked in
`variance.py`'s `DEFAULT_METRICS`, so neither can be quoted from a single run.
⚠ A vacuous `KPI-03` of 0.00 on a run with no healthy panels is exactly the case
where quoting it alone misleads — check `KPI-03b`.

### ⚠ `KPI-03`'s biggest caveat: the frame may show more than one panel

**Measured 2026-07-29 and it is not marginal.** `KPI-03` assumes the frame the model
judged shows the target panel. At the confirm standoff it does not: the module is
tilted ~46 deg to the camera and foreshortened, so the **neighbouring module is in
shot**. Across three prompt versions and nine repeats on `SC-01`:

| run | prompt | false alarms | beside a faulted panel | clean neighbourhood |
|---|---|---|---|---|
| `runs/20260729T130956` | v1 | 7 | **7** | **0** |
| `runs/20260729T133517` | v2 | 3 | **3** | **0** |
| `runs/20260729T135700` | v3 | 6 | **6** | **0** |

**All 16 false alarms sat beside a faulted panel; none of the 180
clean-neighbourhood panel-observations produced one.** So on this scenario the
false-fault rate is not a measure of the model at all — the model reports soiling
that is genuinely in the image, and ground truth scores it against the wrong panel.
Captured frames confirm it (`tools/inspect_frame.py`).

This is why prompt work could not move it: there was nothing wrong with the reading.

`kpi/confound.py` computes this and `run.py` writes it into every run record's
`confound` block, printing it beside the KPI when non-zero. **Quote `KPI-03` with its
`attributable_share`.** A share of 1.00 means the whole number may be the panel next
door. The fix is the **frame** — crop capture to the target module's own extent so
neighbours are excluded — and until that lands, `SC-11`/`SC-12` (all-healthy stages,
where no neighbour can be faulted) are the only KPI-03 points free of this confound.
That is also a reason their 0.00 stands.

#### The crop: mechanism built, geometry validated, KPI effect NOT yet measured

`perception.cosmos_reason.centre_crop` + `crop_fraction` (config: `perception_opts.
crop_fraction`, recorded in `provenance()`). **Default 1.0 = no crop**, because every
KPI on record was measured on the full frame.

The risk with any crop is that it deletes the defect instead of the confound — that
is exactly how prompt `v2`/`v3` failed. Tested on captured frames with
`tools/inspect_frame.py --crop 0.5`, which needs Isaac but **not** the VLM:

| panel | injected | confirm glass% | crop 0.5 | non-glass residue |
|---|---|---|---|---|
| `R254-C014` | healthy (false-alarmed) | 64.0 | **87.1** | warm → **cool** |
| `R258-C028` | healthy (false-alarmed) | 59.6 | **84.8** | warm → **cool** |
| `R258-C000` | healthy (control) | 57.8 | **87.0** | warm → **cool** |
| `R254-C000` | healthy (control) | 59.9 | **85.6** | warm → **cool** |
| `R243-C098` | **soiled** | 37.6 | 54.7 | warm → **warm** |
| `R253-C042` | **soiled** | 34.5 | 41.0 | warm → **warm** |

The crop is **differential**, which is what it needs to be: on healthy panels it
strips the warm/sandy content and the residue turns cool (85–87% glass), while on
genuinely soiled panels the warm content survives. Soiling is central because the
waypoint is over the target; the contaminating ground and neighbour are peripheral.
The false-alarm panels and the clean controls also converge, which is what excluding
the neighbour should look like.

⚠ **This is geometry, not a KPI.** Whether it moves `KPI-01`/`KPI-03` is unmeasured —
the vLLM server went down before a `--repeat 3` comparison could run. Do not describe
it as a fix or enable it by default until that comparison exists, and quote
`crop_fraction` with any number produced under it.

## Scenario suite

Each scenario is a `configs/scenarios/<name>.yaml` per `04-interfaces-and-data.md`
`IF-03`, composing `farm.yaml` + `mission.yaml`, seeded, with declared
`kpi_gates`. The suite starts small and grows with each slice — this is the
starting set, not the final one.

| ID | Name | Composition | Hazards exercised | Primary KPIs | Introduced |
|---|---|---|---|---|---|
| `SC-01` | `nominal_calm` | real Khavda BLOCK-02, mid-morning (trackers off their stops, rows face-on, no self-shading), no wind/turbine/birds, faults enriched to 20% so the denominator holds both classes | none | `KPI-01`, `KPI-02` | `SLICE-0` — **built 2026-07-29**, `configs/scenarios/nominal_calm.yaml` (+ `nominal_calm_vlm.yaml`, same world, `perception` flipped) |
| `SC-02` | `gust_only` | + wind force field, no turbine | `HAZ-03` | `KPI-05` | `SLICE-2` |
| `SC-03` | `turbine_static_keepout` | + one articulated (non-spinning) turbine | `HAZ-01` | `KPI-04` | `SLICE-2` |
| `SC-04` | `turbine_wake` | + spinning turbine, wake field active | `HAZ-01`, `HAZ-02` | `KPI-04`, `KPI-05` | `SLICE-2` |
| `SC-05` | `sweeping_shadow` | + low sun angle, blade shadow crosses panel row, no wind | `HAZ-07` | `KPI-03` | `SLICE-3` |
| `SC-06` | `shadow_plus_gust` | `SC-04` + `SC-05` combined (motion blur likely) | `HAZ-02`, `HAZ-03`, `HAZ-07` | `KPI-03`, `KPI-05` | `SLICE-3` |
| `SC-07` | `bird_crossing` | + scripted bird trajectories through the drone lane | `HAZ-05`, `HAZ-07` | `KPI-04`, `KPI-03` | `SLICE-5` |
| `SC-08` | `graded_terrain` | ground bot on 5/10/15/20° ramp testbed | `HAZ-04` | `KPI-07` | `SLICE-2`/`SLICE-6` |
| `SC-09` | `dust_haze_variant_pack` | off-box Transfer/Predict-generated corpus over `SC-05`/`SC-06` | `HAZ-07` | `KPI-03`, `KPI-08` | `SLICE-4` |
| `SC-10` | `full_farm_battery_window` | full farm, both robots, N panels, declared daylight/battery window | `HAZ-06` | `KPI-02`, `KPI-06` | `SLICE-7` |
| `SC-11` | `khavda_selfshade` | real Khavda BLOCK-02, HSAT trackers pinned at their 60° stop, sun 17.2° (02:00Z), every panel healthy, no turbines | `HAZ-07` | `KPI-03` | `SLICE-3` — **built**, `configs/scenarios/khavda_selfshade.yaml` |
| `SC-12` | `khavda_selfshade_lowsun` | `SC-11` one hour earlier (01:30Z, sun 10.7°): ~54% of each module shaded *and* the whole scene dimmer, so shading is confounded with underexposure | `HAZ-07` | `KPI-03` | `SLICE-3` — **built**, `configs/scenarios/khavda_selfshade_lowsun.yaml` |
| `SC-13` | `khavda_windy_hover` | real Khavda DEM + **graded civil pad** + one **articulated** 120 m turbine + **12 m/s wind with 35% gusts and a Jensen wake**, PX4-governed flight | `HAZ-01`, `HAZ-02`, `HAZ-03` | `KPI-05` | `SLICE-2` — **built + flown 2026-07-29**, `configs/scenarios/khavda_windy_hover.yaml`. The first scenario where the physics pieces run TOGETHER rather than being individually tested. ⚠ A hover, not an inspection: wind is a force and the inspection mission drives its robots kinematically, where a force does nothing (`NFR-07`) |

`SC-11`/`SC-12` supersede `SC-05`'s original stimulus rather than extending it:
the turbine-blade shadow sailed over the elevated rows onto the ground, while
tracker self-shading is a real, on-surface, many-panel shadow produced by the
plant's own hardware. Both are asserted geometrically in `tests/test_solar.py`
before any run — a KPI-03 of 0.00 means nothing if the stimulus was absent.

## Gating discipline

- Every scenario config declares its own `kpi_gates` block (see `IF-03`
  example); a slice's exit criteria (`07-roadmap-and-milestones.md`) is
  "all scenarios introduced at or before this slice meet their gates."
- **Gates are enforced, not decorative** (`FR-17`). `run.py` evaluates the
  declared block against the measured metrics via `kpi/gates.py`, writes
  `gates.json`, prints the verdict and exits non-zero on a breach. A gate naming
  a metric the run never measured **does not pass** — it reports `unmeasured`
  and fails, because a silently unevaluated bound reads as a green tick.
- **Quote a spread, not a number.** The world is seeded; the VLM is only
  reproducible when served serially (measured — see `08-platform-and-risk-register.md`
  `RISK-23`). `run.py --repeat N` re-runs one scenario N times, rewinding panel
  state between repeats, and writes `variance.json`: per-metric min/median/max
  plus every panel the repeats disagreed about, each attributed to the
  **renderer** or the **model** by comparing frame thumbnails with a measured
  tolerance — bit-exact digests cannot do this job, because the renderer is
  stochastic (`RISK-24`). Repeat sets are gated on the
  **worst** run, never the mean — a fleet flies each sortie once.
- A KPI regression on an **earlier**-slice scenario blocks merging
  **later**-slice work until fixed or explicitly waived with a tracked
  `RISK-nn` entry — fidelity deepens along a working loop, it should never
  silently break an earlier one (`01-scope-and-vision.md`, "thin
  end-to-end thread").
- `KPI-08` (generated-frame validity) is the data-integrity gate protecting
  `NFR-08`: a corpus that fails this gate must not be used to evaluate or
  fine-tune `Perception`, full stop — no averaging it away.

## What this suite deliberately does not attempt

- It does not attempt statistical bird-strike realism (`HAZ-05` residual
  risk) or true wake CFD validation (`HAZ-02`) — those require off-box
  reference data not yet available; scenarios exercise the *behavior*, not a
  certified physical envelope, per the non-goals in `01-scope-and-vision.md`.
- It is not a substitute for SIL→HIL validation before real flight
  (`FR-24`); it is the sim-side half of that gate.
