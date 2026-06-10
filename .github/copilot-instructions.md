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
  battery.py      # BatteryParams dataclass — system parameters and validation
  data_gen.py     # Synthetic profile generation (v1 only)
  model.py        # solve_dispatch() — the MILP formulation
scripts/
  run_stage1.py   # v1 runner with CLI args (--deg-cost, --battery-cap, etc.)
tests/
  test_model.py   # 5 unit tests — all passing
docs/
  v1_model_report.md  # Stage 1 development report
data/             # Raw and processed input data (empty until v2)
results/          # Outputs and figures (gitignored)
```

## Development Stage

**Current stage: v1 complete.** Synthetic 24-hour single-day model with stylised profiles. All 5 unit tests pass. Results are hand-verifiable.

**Next stage: v2.** Half-hourly resolution, real Agile tariff data via API, real PV/demand profiles, multi-day rolling horizon, annual viability metrics.

## Established Scope Decisions

These decisions have been made deliberately and should not be revisited without good reason. If a new situation genuinely warrants reconsidering one, flag it explicitly rather than silently working around it.

- **Python not MATLAB:** MILP in Python (CVXPY) is more reproducible, better supported, and more employable than an equivalent MATLAB implementation.
- **Throughput degradation, not rainflow-in-loop:** Rainflow counting is non-convex and cannot be embedded directly in the LP. The throughput penalty is the chosen approach; rainflow can be applied post-hoc to the solved SOC trajectory as a validation step.
- **Degradation cost term must stay:** It is central to the research question. Removing it produces an incomplete and misleading result.
- **Import/export binary variable must stay:** Without it the model is unbounded when export price > import price.
- **No stochastic or robust optimisation:** Out of scope given the project timeline and background. Forecast uncertainty is handled through sensitivity analysis instead.
