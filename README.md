# PV–BESS Techno-Economic Analysis

MSc dissertation project investigating the techno-economic viability of domestic PV–battery energy storage systems (BESS) through a systematic parameter study across UK locations, system sizes, and tariff structures.

## Research Question

Under what conditions is domestic PV–BESS economically viable in the UK when battery degradation costs are explicitly accounted for in the dispatch optimisation? How do location, system sizing, and tariff structure interact to determine the economics?

## Deliverable and Contribution

The primary deliverable is the **MSc dissertation**. The graded contribution is
the **systematic comparative study** — how domestic PV–BESS economics vary across
UK location, battery size, tariff structure, and battery-degradation assumption —
rather than the dispatch method itself (degradation-aware scheduling is already
established in the literature).

## Approach

- Deterministic MILP scheduling optimisation minimising net energy cost
  (import − export) plus a throughput-based battery degradation penalty
- **Lifetime NPV as the primary viability metric** (simple payback is retained
  only as a secondary sanity check)
- **Two-timescale degradation model:** a linear throughput penalty *inside* the
  dispatch MILP (its £/kWh value *derived* from capex over lifetime throughput),
  with additive linear cycle + calendar capacity fade applied *exogenously* across
  the rolling horizon and lifetime. Capacity fade is not embedded in the
  optimisation itself (non-convex, and it couples the horizon). DoD/rainflow is not
  modelled — LFP fade is throughput-driven and largely DoD-independent (Wang et al.
  2011); the LFP-relevant lever, **SOC exposure**, is analysed post-hoc
- Real Agile half-hourly import/export prices, PVGIS-derived PV generation, and a
  CREST synthetic GB household demand profile
- Systematic parameter study across UK locations, battery capacities, tariff
  types (flat / Economy 7 / Agile), export (SEG) variants, and degradation-cost
  assumptions

## Economic Assumptions (NPV)

All values are defaults requiring literature citation; each doubles as a
sensitivity axis.

| Assumption | Base | Sensitivity |
|---|---|---|
| Discount rate (real) | 5% | 3.5% (HMT Green Book), 7% |
| Analysis horizon | 20 yr (PV life) | — |
| Battery replacement | at each `k × life` within the horizon | — |
| Electricity price escalation (real) | 2%/yr | 0%, 4% |
| Battery life | LFP, ~12 yr / 6,000 EFC to 80% capacity | — |
| End-of-life / residual | replace at 80% capacity; straight-line residual credited at horizon | — |

## Development Method

Work is planned and delivered with a lightweight **spec-driven agile loop**:
interview to pin the real goal, break work into small compartmentalised specs
with precise success criteria, verify each with an independent review gate, and
loop until done. The spec backlog and per-spec acceptance criteria live in
[.github/specs/](.github/specs/).

## Repository Structure

```
data/           # Raw and processed input data (demand, PV, prices)
docs/           # Literature, notes, dissertation drafts
results/        # Optimisation outputs and figures (gitignored)
scripts/        # One-off data fetching and processing scripts
src/            # Core model code (optimisation, battery, economics)
tests/          # Unit tests
.github/specs/  # Spec backlog and per-spec acceptance criteria
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

## Development Status

- **v2 complete** — annual rolling-horizon MILP on real Glasgow 2023 data; input
  validation and controller-comparison reports in `docs/`.
- **NPV economic model complete** (`src/economics.py`) — lifetime NPV, benefit–cost
  ratio, and discounted payback. Baseline Glasgow (10 kWh, Agile, 5p/kWh, £4,000
  capex) gives NPV ≈ **−£3,585** — not viable under base assumptions.
- **In progress (v3)** — degradation/capacity-fade model, tariff layer,
  multi-location ingestion, and the parameter-sweep harness. See
  [.github/specs/](.github/specs/).
