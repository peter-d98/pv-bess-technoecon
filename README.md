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
| Battery replacement | two policies reported side by side: run-to-fade-and-replace at the SOH floor, and no-replacement (one pack for the full horizon) | — |
| Electricity price escalation (real) | 2%/yr | 0%, 4% |
| Battery life | LFP, ~10 yr (warranty) / 6,000 EFC to 80% capacity | — |
| End-of-life / residual | replacements paid in full; unused life of the in-service pack credited at the horizon on its **realised** predecessor life (Spec 06 §4.1, superseding the earlier warranty-life basis). No-replacement receives zero residual | — |
| PV capex | £1,109/kWp, linear — DESNZ 2025/26 inflation-adjusted median, 4–10 kW domestic band | — |
| Battery capex | £4,584 fixed + £373/kWh — DESNZ capacity bands | F ∈ [2,900, 4,900] with c ∈ [490, 310] |

The capex specification and the evidence for it are in
[`docs/results_summary.md`](docs/results_summary.md) §1; the canonical assumption table
is [`.github/specs/README.md`](.github/specs/README.md).

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
  ratio, and discounted payback. Whole-system baseline (Glasgow / Southern Scotland,
  4 kWp PV + 10 kWh battery incl. hybrid inverter, £13,336 capex, 2023 data, derived
  7.42p/kWh) vs a grid-only-flat counterfactual, run-to-fade: baseline Agile NPV
  **−£2,186.93** (annual saving £871.48/yr); the full flat / E7 / Agile tariff-axis
  NPVs under the new capex are being regenerated by the parameter sweep.
- **Tariff layer complete** (`src/tariffs.py`, Spec 03) — flat, Economy 7, and Agile
  import tariffs with matched SEG/Agile export, real 2025 Southern Scotland rates.
- **Degradation & capacity-fade model complete** (`src/degradation.py`, Spec 02) —
  derived throughput penalty, additive cycle + calendar fade, run-to-fade
  replacement policy, and SOC-exposure metrics.
- **Multi-location ingestion and parameter-sweep harness complete** (Specs 04–05) —
  Inverness / Glasgow / Manchester / Plymouth, versioned results schema, controller axis.
- **Full parameter study complete and independently verified (Spec 06, 2026-07-27)** —
  378 distributed jobs across 12 machines produced 2,160 cached dispatch surrogates,
  4,374 scenario-policy rows and 110,700 peak events, over 3 locations × 3 tariffs ×
  6 PV sizes × 6 battery sizes × 6 wear penalties × 3 controllers × 2 lifetime policies.
  Passed a blind second-reviewer gate with zero mismatches and shown byte-reproducible
  from cache. Supervisor sign-off outstanding.
- **Capital cost re-specified (2026-07-29)** — battery capex moved from linear £890/kWh
  to £4,584 + £373/kWh fitted to DESNZ capacity bands, with a sensitivity envelope; PV
  stays linear because its fitted fixed term is indistinguishable from zero. Headline
  finding is unchanged across both specifications: in the most favourable cell studied a
  domestic battery returns 59–67% of its installed cost in discounted 20-year benefit.
- **Next: Spec 07** — analysis figures (NPV frontier, viability heatmaps, tornado, peak
  loads). See [.github/specs/](.github/specs/) and
  [`docs/results_summary.md`](docs/results_summary.md).
