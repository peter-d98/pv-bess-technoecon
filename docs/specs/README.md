# Specs Backlog — PV–BESS Techno-Economic Analysis

Small, compartmentalised specs driving the dissertation to completion.
Deliverable: the **dissertation document** (~6-week horizon). Code earns its
place only by producing evidence, figures, or methodological rigour the
document needs.

## Dissertation spine (agreed)

> A degradation-aware dispatch-and-valuation framework for domestic UK PV–BESS,
> and what a systematic comparison reveals about the conditions for viability.

- **Contribution = the systematic UK comparative study** (battery size × tariff
  × location × degradation assumption). *Not* the dispatch method — degradation-aware
  scheduling is already published; do not claim it as methodological novelty.
- **Primary viability metric = NPV** (not simple payback).
- MILP-vs-heuristics comparison (already done, `docs/v2_controller_comparison_report.md`)
  becomes a **supporting chapter**, not the spine.

## Confirmed economic / degradation assumptions

All numeric values are **proposed defaults requiring a literature citation** in
the dissertation.

| Assumption | Base | Sensitivity |
|---|---|---|
| Discount rate (real) | 5% | 3.5% (HMT Green Book), 7% |
| Analysis horizon | 20 yr (PV life) | — |
| Battery replacement | at each `k·ceil(life) < horizon` (multiple if life short) | — |
| Electricity price escalation (real) | 2%/yr | 0%, 4% |
| Battery chemistry | LFP | — |
| Cycle life | 6,000 EFC to 80% capacity | — |
| Calendar life | 13.5 yr (12–15 range) | — |
| End-of-life / replace threshold | 80% of nominal capacity | — |
| Fade model | additive linear cycle + linear calendar | √t calendar; SOC-dependent calendar |
| In-dispatch penalty | derived `capex / (N_EoL · 2 · Q_nom)` ≈ 7.42p/kWh at £8.9k/10kWh (6000 EFC) | 0p / 5.56p (8000 EFC) axis points |
| PV capex | **£1,109/kWp, linear (no fixed term)** — DESNZ 2025/26 inflation-adjusted median, 4–10 kW domestic band | — |
| Battery capex | **Band-observed £/kWh** — the DESNZ median for the capacity band the size falls in: £1,300/kWh (1–5.99 kWh), £890/kWh (6–10.99 kWh) | linear £890/kWh (conventional); £4,584 + £373/kWh (decomposed), F ∈ [2,900, 4,900] with c ∈ [490, 310] |
| Battery replacement capex | full band price, `c_band(Q) · Q`, discounted to the replacement year | real battery price decline — proposed, not yet run |
| Battery sizes reported | **1, 2.5, 5, 10 kWh** — 0.5 kWh is dropped, it lies below the lowest published band | — |

**Capex decision (2026-07-29).** The battery is priced at the DESNZ published median
£/kWh **for its own capacity band**; PV stays linear. No parameter is fitted, nothing is
extrapolated, and the schedule is exact at every size in the grid. 0.5 kWh is excluded
because it lies outside the published bands, so the table cannot price it.

Two alternatives are retained as sensitivities rather than adopted:

- **Linear £890/kWh** (the original specification, `sweep_scenarios_v2.csv`) is the
  size-pooled median of a population dominated by 6–11 kWh systems. Applied at 1–5 kWh it
  underprices that band by 32%, which is the sole reason that specification finds a
  battery worth adding in 24/54 cells. Kept for comparability with the literature.
- **£4,584 + £373/kWh** (fixed-plus-variable, OLS on the same bands) requires an F that is
  an extrapolation to Q = 0 from bands whose lowest representative size is 3.5 kWh; the
  three defensible readings of the table move F by 65%, and F alone decides viability.

Two consequences for how results are reported:

- **Viability is reported as a break-even price in £/kWh** — the price at which the
  battery's discounted benefit exactly covers its cost — compared against the observed
  band price. That test needs no decomposition and is identical under all three
  specifications above.
- **Optimal battery size is not identified by this study.** The optimum depends on the
  *marginal* price, and the DESNZ bands constrain only the *average* price. Under a
  constant £/kWh no interior optimum exists; under the decomposed schedule it is 5–10 kWh.
  Report as a limitation, not a finding.

Full rationale, the sensitivity tables and the break-even surface are in
[`docs/results_summary.md`](../../docs/results_summary.md).

The derived in-dispatch penalty in the row above (7.42p/kWh) was calibrated on linear
£890/kWh and is **not used by the sweep** — all six penalty values on the degradation
axis are explicit, and `SweepDispatchProvider.resolve_penalty` never consults capex. It
survives only as `derive_throughput_penalty()` in `src/degradation.py` and as the basis
of the Spec 01–03 baseline records. `--deg-scenarios derived` would now behave
differently from its documentation.

## Degradation design (agreed: two-timescale)

- **Fast (intra-day MILP):** capacity held constant; linear throughput penalty =
  marginal degradation cost. (Already implemented.)
- **Slow (across rolling horizon / years):** usable capacity updated **exogenously**
  from cumulative equivalent full cycles + calendar ageing. Capacity fade is **not**
  endogenous inside the MILP (non-convex, couples the horizon, negligible per day).
- One battery-life dataset (cycle life, EoL threshold, capex) drives **both** the
  in-dispatch penalty and the NPV fade/replacement accounting.

### Degradation realism — decided

- **DoD / rainflow: excluded.** For LFP, fade is throughput-driven and largely
  DoD-independent (Wang et al. 2011), so rainflow would only re-derive that result;
  embedding it in the MILP is non-convex. Not modelled anywhere.
- **SOC exposure: included** (post-hoc, cheap, LFP-relevant). Descriptive metrics are
  baseline; SOC-dependent calendar fade is a sensitivity; an SOC-holding penalty inside
  the MILP is excluded.
- **Upper-SOC-bound tradeoff (90% vs 80%):** included as an analysis axis (life vs
  usable capacity vs NPV).
- **Temperature:** constant reference (~25°C), matching the basis of the life figures;
  optional sensitivity scales `N_EoL`/`T_cal`. No thermal model.
- **Fade-model validation** = parameter sensitivity on the fade inputs → NPV (replaces
  the earlier rainflow idea).

## Backlog

| # | Spec | Status | Depends on |
|---|------|--------|-----------|
| 1 | [NPV economic model](spec-01-npv-economic-model.md) | Implemented & accepted | — |
| 2 | [Unified degradation + capacity-fade model](spec-02-degradation-capacity-fade.md) | Implemented (core); SOC-max tradeoff deferred | 1 |
| 3 | [Tariff model layer (flat, E7, Agile, SEG export)](spec-03-tariff-model.md) | Implemented & accepted | — |
| 4 | [Multi-location data ingestion (Inverness, Glasgow, Manchester, Plymouth)](spec-04-multi-location-data-ingestion.md) | Implemented & accepted | — |
| 5 | [Parameter-sweep harness + results schema (+ controller axis)](spec-05-parameter-sweep-harness.md) | Implemented & accepted | 1–4 |
| 6 | [Sweep & economic extensions](spec-06-sweep-economic-extensions.md) | Implemented; experiment run and verified (2026-07-27), pending supervisor sign-off | 1–5 |
| 7 | Analysis figures (NPV frontier, viability heatmaps, tornado, peak loads) | TODO — **unblocked** 2026-07-29 by the capex decision above | 6 |

Specs 3 and 4 are independent of each other. Specs 1 and 2 are coupled but kept
separate so each stays small and independently testable.

### Spec 06 record

| Document | Contents |
|---|---|
| [spec-06-verification.md](spec-06-verification.md) | Oracles V1–V15, blind-review gate, acceptance record, known deviations |
| [`docs/spec06_run_manifest.md`](spec06_run_manifest.md) | Code identity, environment, 12-way partition, commands, incidents |
| [`docs/spec06_blind_review_report.md`](spec06_blind_review_report.md) | Full report from the independent second reviewer |
| [`docs/results_summary.md`](../../docs/results_summary.md) | Findings, incl. the capex re-specification and its sensitivity envelope |

Assembled tables (all gitignored, all from the same 2,160-curve cache, zero solves):

| File | Capex | Role |
|---|---|---|
| `sweep_scenarios_v2_band.csv` | band-observed £/kWh | **primary** |
| `sweep_scenarios_v2.csv` | linear £890/kWh | conventional sensitivity; **immutable** |
| `sweep_scenarios_v2_{central,lowF,highF}.csv` | £4,584+£373, £2,959+£475, £4,897+£312 | decomposed sensitivity |
| `sweep_peak_events_v2.csv` | — | serves all of them; dispatch does not depend on capex |

The primary table is built by [`scripts/assemble_band_capex.py`](../../scripts/assemble_band_capex.py)
from two constant-price assemblies (`_band_c1300.csv`, `_band_c890.csv`), since
`run_sweep.py` takes one scalar price. The script asserts that the two runs describe the
same grid, that capex has not perturbed any dispatch-derived column, that each size is
taken from exactly one source with no overlap or omission, and that every row's implied
battery capex reconciles to `band price × size`. It writes nothing if any check fails.

## Per-location input validation (Spec 4) — open

Whether to replicate the Glasgow `docs/verification_report.md` validation for
each new location (vs validate one and cite Glasgow for the rest) is **deferred**.
