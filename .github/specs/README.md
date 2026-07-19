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
| In-dispatch penalty | derived `capex / (N_EoL · 2 · Q_nom)` ≈ 3.3p/kWh at £4k/10kWh | 5p/kWh placeholder |

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
| 5 | Parameter-sweep harness + results schema | TODO | 1–4 |
| 6 | Analysis figures (NPV frontier, viability heatmaps, tornado) | TODO | 5 |

Specs 3 and 4 are independent of each other. Specs 1 and 2 are coupled but kept
separate so each stays small and independently testable.

## Per-location input validation (Spec 4) — open

Whether to replicate the Glasgow `docs/verification_report.md` validation for
each new location (vs validate one and cite Glasgow for the rest) is **deferred**.
