# Spec 02 — Verification Plan

Companion to [spec-02-degradation-capacity-fade.md](spec-02-degradation-capacity-fade.md).
Defines what "correct" means, the independent-review gate, and the external context that
strengthens the check. Mirrors the Spec 01 workflow.

## 1. Numeric oracles (recompute independently — do NOT read module output)

A second reviewer (subagent, separate tool, or by hand) must reproduce these *without*
seeing the implementation's answers.

| # | Input | Quantity | Expected |
|---|-------|----------|----------|
| O1 | capex 4000, cap 10, N_EoL 6000 | `c_thr` | 0.0333333 £/kWh |
| O2 | throughput 120000, cap 10 | EFC | 6000.0 |
| O3 | EFC 300, t 1 yr, linear, T_cal 13.5, SOH_EoL 0.80 | SOH | 0.9751852 |
| O4 | EFC 0, linear, t 13.5 | SOH | 0.80 |
| O5 | t 0, EFC 6000 | SOH | 0.80 |
| O6 | 300 EFC/yr + linear calendar | annual Δfade; effective life | 0.0248148/yr; ≈8.06 yr |
| O7 | √t calendar, t 1, T_cal 13.5 | phi_cal | 0.0544331 |
| O8 | soc_stress_beta 0 | SOC-dep = SOC-indep | equal |
| O9 | capex 0 | `c_thr` | 0 |
| O10 | stub dispatch, life 12, saving 150, horizon 20 | replacement year; effective life | 13; 12 |
| O11 | soc ≡ 0.5 over a day | mean_soc; frac>0.8 | 0.5; 0.0 |

Hand-derivations for the non-obvious ones:
- **O3:** phi_cyc = 0.20·300/6000 = 0.010; phi_cal = 0.20·1/13.5 = 0.0148148; SOH = 1 − 0.010 − 0.0148148.
- **O6:** annual fade = 0.20·(300/6000) + 0.20·(1/13.5) = 0.010 + 0.0148148 = 0.0248148;
  years to consume 0.20 budget = 0.20 / 0.0248148 = 8.060 yr.
- **O7:** 0.20·√(1/13.5) = 0.20·0.272166 = 0.0544331.

## 2. Independent code review (second-AI gate)

### 2.1 Pre-implementation (this document)
- Confirm the fade law is **additive** and that the shared EoL budget makes combined life
  shorter than either standalone figure (O6 demonstrates this).
- Confirm the penalty basis (nominal) is consistent between `derive_throughput_penalty`
  and `equivalent_full_cycles` — a mismatch here silently mis-sizes the penalty.
- Confirm `simulate_capacity_fade` is dependency-injected (pure) and does not import the
  solver, so it is unit-testable without CVXPY.

### 2.2 Post-implementation (run on final code before acceptance)
- Recompute O1–O11 blind; all must match.
- Verify `src/model.py` and `src/battery.py` are byte-identical to pre-Spec-02 (diff).
- Check the escalation is applied **only** in the NPV layer, never inside `dispatch_year`.
- Check replacement handling: on reset, EFC counter and calendar clock both zero. The NPV
  replacement schedule is driven by the **realised** life (`replacement_years[0] − 1`, or
  `> horizon` when the battery is never replaced), not the diagnostic `effective_life_years`.
  `replacement_years` records the year a *fresh* battery starts operating; the NPV books the
  cost at `ceil(realised_life)`. Salvage is off (`include_residual_value=False`) for the fade
  valuation.
- Confirm the **forced-replacement** fade sensitivity (`--replace-at-eol`) NPV ≤ the
  constant-saving flat NPV at the same life (monotonicity: ageing cannot help *for a fixed
  replacement policy*). Do **not** expect run-to-fade ≤ flat — run-to-fade legitimately
  defers the replacement and is therefore higher.
- Confirm `soc_dependent_calendar=True, beta=0` reproduces the baseline exactly (no
  accidental behavioural change from the sensitivity switch).

## 3. External context (beyond the code under test)

- **Wang et al. (2011)** — LFP fade is throughput-driven / largely DoD-independent:
  the justification for a throughput penalty and for excluding rainflow/DoD.
- **Naumann et al. (2018 calendar; 2020 cyclic)** — LFP calendar (√t, SOC-dependent) and
  cyclic models: source for the √t sensitivity and the SOC stress coefficient.
- **Independent recomputation** of the baseline fade-adjusted NPV in a separate
  scratch script (no import of `src/degradation.py`), as was done for Spec 01.
- **Sanity anchor:** derived penalty (~3.3 p/kWh) should sit within the literature range
  of reported LFP throughput costs; flag if it does not.
- **Supervisor sign-off** on: adopting the derived penalty over the 5 p/kWh placeholder,
  and the constant-temperature / representative-year simplifications as stated limitations.

## 4. Definition of done

- All §1 oracles reproduced blind; §2.2 checks pass.
- All acceptance criteria in spec §8 met; full test suite green.
- Baseline fade-adjusted NPV, effective life, and the 90 %-vs-80 % tradeoff recorded.
- Stated limitations (representative year; constant temperature; additive budget; no
  DoD dependence) written up for the dissertation limitations section.

### Acceptance record
- **2026-07-15 — Independent AI reviewer — PASS.** All oracles O1–O9 re-derived
  blind (from the method equations, not the expected columns) and matched the
  implementation exactly: O1 3.333 p/kWh, O2 6000 EFC, O3 SOH 0.975185, O4/O5 SOH
  0.800000, O6 0.0248148/yr → 8.060 yr, O7 φ_cal 0.054433, O9 0. Baseline derived
  penalty independently confirmed 5.00 p/kWh (£6000 / 6000 EFC / 10 kWh). Terminal
  warranty-residual traced (horizon 20, warranty 10, replacement_years [18] →
  realised_life 17, install_t 17, age 3, remaining 0.7, residual £5,320) — logic
  sound, the realised_life = replacement_years[0]−1 / ceil-booking timing is
  internally consistent (no off-by-one). `compute_npv` override precedence,
  non-negativity validation, and discounted final-year crediting confirmed.
- **2026-07-21 — Capex overhaul (record note, not a re-verification).** The 5.00 p/kWh
  confirmed above reflects the then-current £600/kWh general-cost baseline and remains a
  faithful record of that recomputation. The baseline has since moved to £890/kWh (battery
  incl. inverter), which derives 7.42 p/kWh at 6000 EFC (and 5.56 p/kWh at 8000 EFC); the
  `derive_throughput_penalty` logic itself is unchanged, so the oracle re-derivations above
  still hold for their stated inputs.
  `src/model.py` and `src/battery.py` byte-identical (empty diff). 53/53 tests pass.
  Fade reset and decoupled soh_eol-vs-soh_floor threshold logic correct.
  Residual issues: none blocking. Notes: (i) add one sentence to the write-up on the
  year-17-vs-18 replacement-timing convention; (ii) `DegradationParams` defaults
  remain literature-citation dependencies.
