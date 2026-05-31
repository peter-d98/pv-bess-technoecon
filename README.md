# PV–BESS Techno-Economic Analysis

MSc dissertation project investigating the techno-economic viability of domestic PV–battery energy storage systems (BESS), with a focus on evaluating Octopus Energy's "zero-bills" claim on a whole-life cost basis.

## Research Question

Is the Octopus Energy zero-bills claim for domestic PV–BESS viable over the system lifetime when battery degradation costs are properly accounted for? How much of the claim depends on retail tariff arbitrage alone, and what ancillary service revenue is implicitly required to close the gap?

## Approach

- Deterministic LP scheduling optimisation minimising net energy cost (import − export) plus a throughput-based battery degradation penalty
- Real Octopus Agile half-hourly price data via the Octopus Energy API
- Realistic GB household demand and PV generation profiles
- System sized to Octopus Zero Bills specifications (~4 kWp PV, ~10 kWh battery)
- Sensitivity analysis across degradation cost assumptions and dispatch strategies

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
- `requests` — Octopus API data fetching
- `matplotlib` / `seaborn` — visualisation
