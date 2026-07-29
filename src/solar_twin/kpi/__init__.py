"""KPI measurement support: gate evaluation and run-to-run variance.

Two small pure-python modules that turn run records into *judged* numbers:

- `gates.py` — evaluates a scenario's declared `kpi_gates` against measured
  metrics (`FR-17`: slices are gated on measured KPIs, not a GUI demo). Before
  this existed, `kpi_gates` was loaded, printed, and never checked.
- `variance.py` — aggregates N repeats of one scenario into a spread and
  attributes every per-panel disagreement to the renderer or the model.

Both take plain dicts (the shape `run.py` already writes to `results.json`), so
they work on live results and on any archived run directory, and neither
imports Isaac (`NFR-01`).
"""
