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
- Check replacement handling: on reset, EFC counter and calendar clock both zero, and the
  NPV replacement year (`k·ceil(effective_life_years)`) is driven by `effective_life_years`.
  Note `replacement_years` records the year a *fresh* battery starts operating (year 13 for a
  12-year life); the NPV layer independently schedules the cost at `ceil(life)=12`. The
  1-year offset is a labelling difference, not a double count.
- Confirm fade-adjusted NPV ≤ flat-saving NPV (monotonicity: ageing cannot help).
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
- _(to be completed at acceptance)_ — reviewer, date, oracle results, residual issues.
