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
| Battery capex | **£4,584 fixed + £373/kWh** — DESNZ 2024/25 + 2025/26 capacity bands, sample-mean size × mean £/kWh | F ∈ [2,900, 4,900] paired with c ∈ [490, 310] |
| Battery replacement capex | `0.3 · F + c · Q` — a swap re-uses cabling, protection and mounting | frac ∈ [0, 1] via `--replacement-fixed-frac` |

**Capex decision (2026-07-29).** Battery capex is fixed-plus-variable; PV is linear.
The asymmetry is empirical, not a convenience: fitting the same form to the DESNZ PV
table gives a fixed term of £98 (and negative in 2023/24), i.e. indistinguishable from
zero, whereas the battery bands price at £1,300 → £890 → £630/kWh and imply a large
one. Rationale, the fitted alternatives, and the direct benefit-cost test that does not
depend on the decomposition are in [`docs/results_summary.md`](../../docs/results_summary.md) §1.

Two consequences worth noting. The derived in-dispatch penalty above was calibrated on
linear £890/kWh; under fixed-plus-variable capex the marginal wear cost falls to
~4.3p/kWh at 10 kWh and becomes **capacity-dependent**, which the "capacity-independent"
comment in `SweepDispatchProvider.resolve_penalty` no longer describes. Nothing in the
run is affected — all six penalty values on the sweep axis are explicit, and the 0–9p
grid still brackets the derived value — but `--deg-scenarios derived` would now behave
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
| [`docs/spec06_run_manifest.md`](../../docs/spec06_run_manifest.md) | Code identity, environment, 12-way partition, commands, incidents |
| [`docs/spec06_blind_review_report.md`](../../docs/spec06_blind_review_report.md) | Full report from the independent second reviewer |
| [`docs/results_summary.md`](../../docs/results_summary.md) | Findings, incl. the capex re-specification and its sensitivity envelope |

Assembled tables (all gitignored, all from the same 2,160-curve cache, zero solves):
`sweep_scenarios_v2.csv` is the original linear-capex run and is **immutable**;
`sweep_scenarios_v2_{central,lowF,highF}.csv` are the adopted capex specification and
its two envelope corners. `sweep_peak_events_v2.csv` serves all four — dispatch does
not depend on capex.

## Per-location input validation (Spec 4) — open

Whether to replicate the Glasgow `docs/verification_report.md` validation for
each new location (vs validate one and cite Glasgow for the rest) is **deferred**.
