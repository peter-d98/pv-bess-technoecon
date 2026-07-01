# Project Context — PV–BESS Techno-Economic Analysis

## What This Project Is

MSc dissertation (summer research project) investigating the techno-economic viability of domestic PV–battery energy storage systems (BESS) in the UK. The primary output is a validated MILP scheduling optimisation model applied in a systematic parameter study across UK locations, system sizes, and tariff structures.

## Research Question

Under what conditions is domestic PV–BESS economically viable in the UK when battery degradation costs are explicitly accounted for in the dispatch optimisation? How do location, system sizing, and tariff structure interact to determine the economics?

## Working Method — Spec-Driven Agile Workflow

This project is developed with a lightweight spec-driven agile loop. Follow it for any non-trivial task:

1. **Interview before building.** Clarify the real goal before writing code. Surface assumptions and tradeoffs, present options rather than picking silently, and make the user verify key decisions explicitly.
2. **Small, compartmentalised specs.** Break work into small, independently testable specs under `.github/specs/`, tracked in `.github/specs/README.md`. Each spec states its scope, interface, and an explicit out-of-scope list.
3. **Precise success criteria.** Every spec defines measurable acceptance criteria — ideally with hand-derived numeric oracles and explicit MUST/SHOULD distinctions. "Done" must be unambiguous.
4. **Independent verification gate.** Have a second, independent reviewer (a subagent) check the work — recomputing numeric oracles *without* being shown the expected answers, and reviewing the final code against the stated conventions, not merely that tests pass.
5. **Bring in external context.** Strengthen verification with sources outside the code under test: independent recomputation in a separate tool, literature anchors for assumptions, reference-tool cross-checks, and supervisor sign-off.
6. **Loop until verified.** Transform tasks into verifiable goals and iterate until every MUST criterion passes. Record decisions and outcomes (session memory / spec files) so they persist.

Spec 01 (the NPV economic model) in `.github/specs/` is the worked reference example of this workflow.

## Core Methodology

- **Optimisation:** Deterministic MILP (not simulation, not Simulink) minimising net energy cost (grid import cost − export revenue) plus a throughput-based battery degradation penalty
- **Battery model:** Generic SOC model — linear difference equation, no electrochemical dynamics
- **Degradation model:** Throughput penalty (£/kWh cycled), linear and embeddable in the MILP objective
- **Data:** Real Agile half-hourly tariff data from a downloaded Octopus Agile CSV (live API retrieval planned); PVGIS-derived PV generation; realistic GB household demand profiles (CREST)
- **Validation & benchmarking:** PV yield cross-checked against Sheffield Solar PV_Live and DESNZ regional statistics; demand against Ofgem TDCVs; the MILP optimum benchmarked against two rules-based heuristic controllers (PV self-consumption; Agile price-threshold) as the real-world baseline
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
| Baseline degradation cost | 5p/kWh cycled (scenario-level; `BatteryParams` default is 0) |

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
  economics.py         # compute_npv() — lifetime NPV valuation
scripts/
  run_stage1.py        # v1 runner with CLI args
  run_stage2.py        # v2 annual rolling-horizon runner and viability assessment
  run_rules_controller.py     # heuristic controllers (PV self-consumption; Agile threshold) vs MILP
  validate_inputs.py          # PV vs PV_Live/DESNZ; demand vs Ofgem TDCV input validation
  generate_demand_profile.py  # xlwings CREST automation (Windows/macOS only)
tests/
  test_model.py        # 10 unit tests — all passing
  test_data_loader.py  # 7 unit tests for v2 data loaders — all passing
  test_economics.py    # 15 unit tests for the NPV model — all passing
docs/
  v1_model_report.md                   # Stage 1 development report
  v2_model_report.md                   # Stage 2 development report
  v2_controller_comparison_report.md   # MILP vs rules-based controllers
  verification_report.md               # Input validation (PV / demand) results
data/
  CREST_Demand_Model_v2.3.3.xlsm
  demand_halfhourly_2023.csv          # CREST output, half-hourly kW, year 2023 (default)
  demand_halfhourly_2025.csv          # CREST output, half-hourly kW, year 2025 (legacy)
  Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv  # PVGIS Glasgow 2023
  agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv                  # Octopus Agile 2023
results/          # Outputs, figures, cached artefacts (e.g. stage2_schedule_2023.csv,
                  #   rules_comparison_table.csv, pvlive_cache_2023_pes18.csv)
.github/specs/    # Spec backlog and per-spec acceptance criteria
```

## Development Stage

**Current stage: v2 complete; NPV economic model added.** Annual rolling-horizon model at half-hourly resolution using real data, plus a lifetime NPV valuation (`src/economics.py`). Input validation (`validate_inputs.py`) and a rules-based controller benchmark (`run_rules_controller.py`) are also complete. All 32 unit tests pass. Baseline annual run complete for Glasgow.

**What's next is tracked in the spec backlog, not as monolithic version bumps.** `.github/specs/README.md` is the authoritative list. Spec 01 (lifetime NPV model) is implemented and accepted; Spec 02 (unified degradation + capacity-fade, feeding a per-year saving stream into `compute_npv`) is next. Later specs cover the tariff layer, multi-location ingestion (south England / Midlands / Scotland, with API-based retrieval to replace manual CSV downloads), the parameter-sweep harness, and analysis figures.

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

- **Data year alignment:** All three sources are now aligned to 2023. The CREST demand profile was regenerated under the 2023 calendar (`demand_halfhourly_2023.csv`, the default) so weekday/weekend flags match the 2023 Agile price structure; the earlier `demand_halfhourly_2025.csv` is retained as legacy but is no longer used. PV and Agile prices are 2023. Sources are aligned positionally by half-hour index within the year.
- **PVGIS timestamp offset:** PVGIS hourly CSVs stamp each row at 11 minutes past the hour (e.g. `20230101:0011`). The loader floors these to the hour. Do not mistake this for missing data.
- **PVGIS upsampling:** Each hourly PV value is repeated for both its half-hours (forward-fill). This is correct — the PVGIS value is a one-hour mean, not an instantaneous reading.
- **Agile DST gap:** The spring DST transition produces 2 missing half-hours in the Agile CSV (17,518 rows instead of 17,520). The loader forward-fills these. The autumn DST duplicate is dropped.
- **CREST automation (generate_demand_profile.py) requires Windows/macOS:** xlwings requires Microsoft Excel via COM. This script cannot run on Linux. Run it from Windows PowerShell using `\\wsl$\Ubuntu-22.04\...` UNC paths to access WSL files directly.
- **Glasgow is the first test location** (55.83°N, 4.28°W). The PVGIS file in data/ is for Glasgow. South England and Midlands files will be added for v3.
- **Baseline annual result (Glasgow, Agile, 10 kWh, 5p/kWh deg cost, £4,000 capex):** Annual saving £145.92 (2023 demand); NPV ≈ −£3,585 (5% real discount, 20-year horizon, 2% real price escalation, 12-year battery life); simple payback 27.4 years — not economically viable under baseline assumptions. **NPV, not payback, is the headline viability metric.**
