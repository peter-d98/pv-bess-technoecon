# Project Context — PV–BESS Techno-Economic Analysis

## What This Project Is

MSc dissertation (summer research project) investigating the techno-economic viability of domestic PV–battery energy storage systems (BESS) in the UK. The primary output is a validated MILP scheduling optimisation model applied in a systematic parameter study across UK locations, system sizes, and tariff structures.

## Research Question

Under what conditions is domestic PV–BESS economically viable in the UK when battery degradation costs are explicitly accounted for in the dispatch optimisation? How do location, system sizing, and tariff structure interact to determine the economics?

## Core Methodology

- **Optimisation:** Deterministic MILP (not simulation, not Simulink) minimising net energy cost (grid import cost − export revenue) plus a throughput-based battery degradation penalty
- **Battery model:** Generic SOC model — linear difference equation, no electrochemical dynamics
- **Degradation model:** Throughput penalty (£/kWh cycled), linear and embeddable in the MILP objective
- **Data:** Real Agile half-hourly tariff data via Octopus Energy API; PVGIS-derived PV generation; realistic GB household demand profiles
- **Parameter study axes:** UK location (south England / Midlands / Scotland), battery capacity, tariff type (flat / ToU / Agile), degradation cost assumption

## Important Modelling Decisions (already established)

- **MILP not LP:** Import/export mutual exclusivity requires a binary variable — without it the model is unbounded when export price > import price. Charge/discharge exclusivity does NOT need a binary (round-trip losses prevent it naturally).
- **Terminal SOC constraint:** Battery must end each day at its initial SOC to make single-day accounting self-contained. Relaxed to continuity constraint in multi-day rolling horizon.
- **Simultaneous charge+discharge:** No binary needed — the LP naturally avoids it when prices are non-degenerate and a degradation cost is present.
- **Grid services excluded by scope:** Ancillary service revenue (Dynamic Containment, BM participation) is excluded by design. This is a stated limitation, not an oversight.

## Baseline System Specification

| Parameter | Value |
|---|---|
| PV peak output | ~4 kWp |
| Battery capacity | 10 kWh (baseline) |
| Max charge/discharge power | 3 kW |
| Round-trip efficiency | 90.25% (η_c = η_d = 0.95) |
| Usable SOC window | 10%–90% |
| Default degradation cost | 5p/kWh cycled |

## Technology Stack

- **Language:** Python (not MATLAB)
- **Optimisation:** CVXPY (solver-agnostic algebraic modelling)
- **Data:** pandas, numpy
- **Visualisation:** matplotlib, seaborn
- **Testing:** pytest
- **Solver:** SCIP/CBC (open source, handles MILP)

## Repository Structure

```
src/
  __init__.py
  battery.py           # BatteryParams dataclass — system parameters and validation
  data_gen.py          # Synthetic profile generation (v1 only)
  data_loader.py       # load_pv(), load_demand(), load_agile_prices(), load_all()
  model.py             # solve_dispatch() — the MILP formulation
scripts/
  run_stage1.py        # v1 runner with CLI args
  run_stage2.py        # v2 annual rolling-horizon runner and viability assessment
  generate_demand_profile.py  # xlwings CREST automation (Windows/macOS only)
tests/
  test_model.py        # 5 unit tests — all passing
  test_data_loader.py  # 7 unit tests for v2 data loaders — all passing
docs/
  v1_model_report.md   # Stage 1 development report
  v2_model_report.md   # Stage 2 development report
data/
  CREST_Demand_Model_v2.3.3.xlsm
  demand_halfhourly_2025.csv          # CREST output, half-hourly kW, year 2025
  Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv  # PVGIS Glasgow 2023
  agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv                  # Octopus Agile 2023
results/          # Outputs and figures (gitignored)
```

## Development Stage

**Current stage: v2 complete.** Annual rolling-horizon model at half-hourly resolution using real data. All 12 unit tests pass. Baseline annual run complete for Glasgow.

**Next stage: v3.** Parameter study across locations (south England, Midlands, Scotland), battery sizes (5/10/15 kWh), tariff types (flat / ToU / Agile), and degradation cost assumptions. API-based data retrieval to replace manual CSV downloads.

## Established Scope Decisions

These decisions have been made deliberately and should not be revisited without good reason. If a new situation genuinely warrants reconsidering one, flag it explicitly rather than silently working around it.

- **Python not MATLAB:** MILP in Python (CVXPY) is more reproducible, better supported, and more employable than an equivalent MATLAB implementation.
- **Throughput degradation, not rainflow-in-loop:** Rainflow counting is non-convex and cannot be embedded directly in the LP. The throughput penalty is the chosen approach; rainflow can be applied post-hoc to the solved SOC trajectory as a validation step.
- **Degradation cost term must stay:** It is central to the research question. Removing it produces an incomplete and misleading result.
- **Import/export binary variable must stay:** Without it the model is unbounded when export price > import price.
- **No stochastic or robust optimisation:** Out of scope given the project timeline and background. Forecast uncertainty is handled through sensitivity analysis instead.
- **Annual saving must include degradation cost:** `saving = cf_net_cost − (battery_net_cost + degradation_cost)`. Omitting degradation from the battery side overstates the saving and produces a misleading payback figure.
- **SOC continuity in rolling horizon:** The annual runner does NOT enforce terminal_soc_equals_initial per day. The end-of-day SOC is carried forward as the next day's soc_init. Do not change this without good reason.

## Data Decisions and Known Issues

- **Data year mismatch (acknowledged limitation):** Demand profile is 2025 (CREST synthetic); PV and Agile prices are 2023. The three sources are aligned positionally by half-hour index within the year. This is acceptable because CREST demand is synthetic, not historical. Document as a stated limitation; do not attempt to "fix" by regenerating demand for 2023 unless specifically requested.
- **PVGIS timestamp offset:** PVGIS hourly CSVs stamp each row at 11 minutes past the hour (e.g. `20230101:0011`). The loader floors these to the hour. Do not mistake this for missing data.
- **PVGIS upsampling:** Each hourly PV value is repeated for both its half-hours (forward-fill). This is correct — the PVGIS value is a one-hour mean, not an instantaneous reading.
- **Agile DST gap:** The spring DST transition produces 2 missing half-hours in the Agile CSV (17,518 rows instead of 17,520). The loader forward-fills these. The autumn DST duplicate is dropped.
- **CREST automation (generate_demand_profile.py) requires Windows/macOS:** xlwings requires Microsoft Excel via COM. This script cannot run on Linux. Run it from Windows PowerShell using `\\wsl$\Ubuntu-22.04\...` UNC paths to access WSL files directly.
- **Glasgow is the first test location** (55.83°N, 4.28°W). The PVGIS file in data/ is for Glasgow. South England and Midlands files will be added for v3.
- **Baseline annual result (Glasgow, Agile, 10 kWh, 5p/kWh deg cost, £4,000 capex):** Annual saving £154.92, simple payback 25.8 years — not economically viable under baseline assumptions.
