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

## Headline Results

Reported as **whole-system NPV** — PV plus battery against a do-nothing household on a
standard flat tariff — not as the battery's marginal return against a PV-only system.

- **The battery does not pay for itself anywhere in the study.** Across all 3,456
  battery configurations — 3 locations × 3 tariffs × 6 PV sizes × 4 battery sizes ×
  2 lifetime policies, under 3 controllers (MILP at 6 wear penalties, plus two
  rules-based benchmarks) — the break-even battery price never reaches the observed
  DESNZ band price. In the single most favourable cell — Inverness, Agile, 5 kWp PV,
  1 kWh battery, no-replacement — it breaks even at **81%** of installed cost.
- **Replacement policy is a first-order assumption.** Run-to-fade is the worse policy in
  1,496 of 1,728 paired cells (87%) and caps the best cell at **66%** of installed cost
  against no-replacement's 81% — a larger swing than the whole location axis produces.
- **Tariff structure is a stronger lever than location.** Best-cell break-even spans
  0.34 (flat) to 0.81 (Agile) across tariffs, but only 0.72 to 0.81 across locations.
  Agile's price spread is what makes arbitrage worth anything at all.
- **Optimal battery size is not identified** by this study. Break-even depends on the
  *marginal* battery price and the DESNZ bands constrain only the *average*.

The evidence base is the sweep assembly in `results/`, regenerable from cache — see
[Running the Model](#running-the-model). Assumptions behind every number are in
[`docs/specs/README.md`](docs/specs/README.md).

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
| Battery capex | DESNZ published median for the size's own capacity band — £1,300/kWh (1–5.99 kWh), £890/kWh (6–10.99 kWh) | linear £890/kWh (conventional); £4,584 + £373/kWh (decomposed) |
| Battery sizes reported | 1, 2.5, 5, 10 kWh — 0.5 kWh lies below the lowest published band and is dropped | — |

Viability is reported as a **break-even battery price in £/kWh** against the observed band
price, a test that needs no capex decomposition and gives the same answer under all three
specifications. Optimal battery *size* is not identified by this study — it depends on the
marginal price, and the bands constrain only the average. The canonical assumption table
and the capex decision it rests on are in [`docs/specs/README.md`](docs/specs/README.md);
the sweep-side specification and its acceptance record are in
[`docs/specs/spec-06-sweep-economic-extensions.md`](docs/specs/spec-06-sweep-economic-extensions.md).

## Development Method

Work is planned and delivered with a lightweight **spec-driven agile loop**:
interview to pin the real goal, break work into small compartmentalised specs
with precise success criteria, verify each with an independent review gate, and
loop until done. The spec backlog and per-spec acceptance criteria live in
[docs/specs/](docs/specs/).

## Repository Structure

```
data/               # Input data: PVGIS PV timeseries, CREST demand, Agile prices
  graphs/           # Input-data figures and the scripts that draw them
docs/
  specs/            # Spec backlog, per-spec acceptance criteria, Spec 06 run record
  figures/          # Generated input-validation figures (gitignored)
  *_report.md       # Model, controller-comparison and verification reports
results/            # All model outputs: assemblies, tables, figures, cache (gitignored)
scripts/            # Entry points — runners, the sweep harness, tables and figures
src/                # Model code (dispatch MILP, battery, degradation, tariffs, economics)
tests/              # Unit tests (124, pytest)
launch.sh           # One machine's partition of the distributed sweep (launch.ps1 = Windows)
```

### Core modules

| Module | Role |
|---|---|
| [`src/model.py`](src/model.py) | The dispatch MILP itself — one horizon, CVXPY |
| [`src/battery.py`](src/battery.py) | Battery parameters and SOC dynamics |
| [`src/controllers.py`](src/controllers.py) | Rules-based benchmarks (self-consumption, ToU) |
| [`src/degradation.py`](src/degradation.py) | Throughput penalty, cycle + calendar fade, SOC exposure |
| [`src/economics.py`](src/economics.py) | Lifetime NPV, BCR, discounted payback, residual value |
| [`src/tariffs.py`](src/tariffs.py) | Flat / Economy 7 / Agile import, SEG and Agile export |
| [`src/locations.py`](src/locations.py) | The four sites and their data bindings |
| [`src/sweep.py`](src/sweep.py) | Surrogate-based parameter sweep and results schema |
| [`src/peaks.py`](src/peaks.py) | Peak-event extraction for the network-impact analysis |
| [`src/results_data.py`](src/results_data.py) | Shared selections behind every table and figure |

## Setup

Developed on Python 3.10.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # or requirements-lock.txt to pin exact versions
python -m pytest -q                  # 124 tests, ~30 s
```

Every script is run from the repository root and resolves `src/` itself, so no
`PYTHONPATH` or editable install is needed.

## Running the Model

All commands assume the venv is active and the working directory is the repo root.
Most runners take `--help`; the flags below are the ones that matter. The two
`case_study_*.py` scripts take no arguments — they rebuild one fixed case.

**1. Check the inputs** — PV yield against PVLive, demand against Elexon profiles,
price coverage and alignment:

```bash
python scripts/validate_inputs.py
```

**2. Single dispatch run.** Stage 1 solves one synthetic horizon (fast, good for
seeing the MILP work); Stage 2 is the real thing — a year of half-hourly dispatch,
solved day-by-day with SOC carried across days, valued over the lifetime against a
grid-only counterfactual:

```bash
python scripts/run_stage1.py
python scripts/run_stage2.py --location glasgow --tariff agile --battery-cap 10 --pv-kwp 4
```

**3. Rules-based benchmark** — the two heuristic controllers over the same year,
compared against the Stage 2 MILP optimum:

```bash
python scripts/run_rules_controller.py
```

**4. The parameter sweep.** The harness caches a *saving curve* per dispatch cell,
so economics can be re-assembled at new prices, discount rates or ageing assumptions
without re-solving. One cell first, to confirm the setup works:

```bash
python scripts/run_sweep.py --locations manchester --pv-sizes 4 --tariffs agile \
    --controllers milp --deg-scenarios 0.03:6000 --solver SCIPY --quick
```

The full study is 324 MILP jobs plus 54 rules jobs. It was run as 12 disjoint
partitions across 12 machines; `launch.sh -m <0-11>` runs one partition and writes
per-job parts and logs under `results/`. With every curve in `results/cache/sweep_v2/`,
a final full `run_sweep.py` assembles the table from cache with zero solves.

> **Memory:** the 17,520-step full-year solves must run **sequentially**. Launching
> them concurrently gets processes killed silently by the OOM reaper — this is what
> `launch.sh`'s `-p` concurrency cap is for.

**5. Assemble and report.** The adopted assembly prices each battery at its own DESNZ
band, which takes one constant-price run per band; the sensitivity axes are re-reads
of the same cache (~30 s each, no solves):

```bash
python scripts/assemble_band_capex.py           # -> results/sweep_scenarios_v2_band.csv
python scripts/make_sensitivity_assemblies.py   # tornado axes
python scripts/make_capex_basis.py              # benefit / fixed / variable decomposition
python scripts/case_study_energy.py             # re-solves the 4 case-study dispatches
python scripts/case_study_fade.py               # SOH trajectories (from cache)
```

Then build the document artefacts — tables as LaTeX fragments, figures as PDFs.
Both read the same selections from `src/results_data.py`, so a figure cannot drift
from the table beside it, and each `.tex` carries a provenance header naming its
source file and sha256:

```bash
python scripts/make_tables.py --list        # or --all / --chapter 4 / --table t1.2
python scripts/make_figures.py --all        # -> results/figures/
```

## Dependencies

See `requirements.txt` (`requirements-lock.txt` pins exact versions). Key packages:

- `cvxpy` — MILP modelling layer for the dispatch problem
- `scipy` — the MILP solver actually used for the sweep (`--solver SCIPY`, HiGHS backend)
- `pandas` / `numpy` — half-hourly time-series handling
- `requests` — tariff data fetching (Octopus Agile API)
- `matplotlib` / `seaborn` — visualisation
- `pytest` — tests

## Development Status

- **v2 complete** — annual rolling-horizon MILP on real Glasgow 2023 data; input
  validation and controller-comparison reports in `docs/`.
- **NPV economic model complete** (`src/economics.py`) — lifetime NPV, benefit–cost
  ratio, and discounted payback. Whole-system baseline (Glasgow / Southern Scotland,
  4 kWp PV + 10 kWh battery incl. hybrid inverter, £13,336 capex, 2023 data, derived
  7.42p/kWh) vs a grid-only-flat counterfactual, run-to-fade: baseline Agile NPV
  **−£2,186.93** (annual saving £871.48/yr). *That baseline is a Spec 01 record priced at
  the superseded linear £890/kWh with a capex-derived penalty; the reportable tariff-axis
  results are the sweep's.*
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
- **Capital cost re-specified (2026-07-29)** — the battery is now priced at the DESNZ
  published median for its own capacity band rather than a single pooled £/kWh; PV stays
  linear because its fitted fixed term is indistinguishable from zero. Headline finding:
  the battery is not worth adding in any of the 54 cells under either lifetime policy, and
  in the single most favourable cell studied it breaks even only at 81% of its observed
  installed cost.
- **Results generation complete** — `scripts/make_tables.py` and `scripts/make_figures.py`
  build the dissertation's LaTeX tables and figures chapter by chapter from the shared
  selections in `src/results_data.py`, with a provenance header and a `MANIFEST.tsv` on
  every run so any number in the document traces back to the assembly it came from.
- **Project complete (August 2026).** The repository is archived in its submitted state.
  Per-spec acceptance criteria and the Spec 06 verification record are in
  [docs/specs/](docs/specs/).
