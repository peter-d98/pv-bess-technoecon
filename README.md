# PV–BESS Techno-Economic Analysis

MSc dissertation project investigating the techno-economic viability of domestic PV–battery energy storage systems (BESS) through a systematic parameter study across UK locations, system sizes, and tariff structures.

## Research Question

Under what conditions is domestic PV–BESS economically viable in the UK when battery degradation costs are explicitly accounted for in the dispatch optimisation? How do location, system sizing, and tariff structure interact to determine the economics?

## Approach

- Deterministic MILP scheduling optimisation minimising net energy cost (import − export) plus a throughput-based battery degradation penalty
- Real Agile half-hourly import and export price data via the Octopus Energy API
- Realistic GB household demand profiles and PVGIS-derived PV generation data
- Systematic parameter study across UK locations, battery capacities, and tariff types
- Sensitivity analysis across degradation cost assumptions

## Repository Structure

```
data/           # Raw and processed input data (demand, PV, prices)
docs/           # Literature, notes, dissertation drafts
results/        # Optimisation outputs and figures (gitignored)
scripts/        # One-off data fetching and processing scripts
src/            # Core model code (optimisation, battery model, etc.)
tests/          # Unit tests
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Dependencies

See `requirements.txt`. Key packages:

- `cvxpy` — optimisation modelling
- `pandas` — time-series data handling
- `requests` — tariff data fetching (Octopus Agile API)
- `matplotlib` / `seaborn` — visualisation
