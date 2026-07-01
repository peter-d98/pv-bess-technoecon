# Spec 01 — Verification & Acceptance Plan

Companion to [spec-01-npv-economic-model.md](spec-01-npv-economic-model.md).
Defines **precise, measurable** criteria for "done", an independent second-AI
review gate, and the external context that strengthens verification.

A result is **accepted only if every MUST criterion below passes**. SHOULD
criteria are recorded but do not block.

---

## 0. Pre-implementation review outcome (completed)

An independent second-AI pass (protocol §2.1) **reproduced oracles O1–O7 exactly**
(annuity factor 12.46221034; O2 pv_benefits 3738.66). It raised three blockers,
now folded into [spec-01](spec-01-npv-economic-model.md):

- **B1 — multiple replacements:** replacements now fire at every
  `k·ceil(life) < horizon`, not once. (Adopted.)
- **B2 — residual value:** straight-line credit for unconsumed battery life at
  the horizon, via `include_residual_value`. **Default ON — CONFIRMED by user.**
- **B3 — declining saving:** `compute_npv` accepts a per-year saving stream, not
  only a scalar, so Spec 2's fade can reduce later-year savings. (Adopted.)

Minor notes (escalation real-vs-nominal must be documented; `ceil` rounds
replacement late; off-by-one risk between escalation exponent `t−1` and discount
exponent `t`) are captured as MUST checks in §1.5 and the §2.2 reviewer tasks.

---

## 1. Precise success criteria

### 1.1 Numerical correctness (MUST) — exact oracles

These are deterministic, hand-derived oracles. The implementation must match
each to within the stated tolerance. Conventions assumed (must be implemented
exactly as stated):

- Initial battery capex incurred at **t = 0, undiscounted**.
- Annual benefit accrues at the **end** of each year `t = 1 … N`, discounted by
  `(1 + r)^(-t)`.
- Year-1 benefit uses escalation exponent `t − 1` (so year 1 = base saving).
- Replacements (potentially **several**) are cash outflows at every
  `t_r = k · ceil(battery_life_years) < horizon_years` for `k = 1, 2, …`, each
  discounted by `(1 + r)^(-t_r)`.
- Residual value (if enabled) is a positive cash flow at `t = N` crediting the
  unconsumed life of the battery in service at the horizon, discounted by
  `(1 + r)^(-N)`.

| # | Inputs | Expected output | Tol |
|---|--------|-----------------|-----|
| O1 | S=300, r=0, esc=0, N=20, capex=4000, **life=20** | `npv = +2000.00` | ±0.01 |
| O2 | S=300, r=0.05, esc=0, N=20, capex=4000, **life=20** | `npv ≈ -261.34` (PV_benefits = 3738.66, annuity factor 12.462211) | ±0.05 |
| O3 | S=300, r=0, esc=0, N=20, capex=4000, life=10, repl_factor=1 | `npv = -2000.00` (one replacement of 4000 at t=10; residual = 0) | ±0.01 |
| O4 | Same as O3 but life=20 | `npv = +2000.00` (no replacement; residual = 0) | ±0.01 |
| O5 | S=300, r=0, esc=0.02, N=3, capex=0, life≥3 | `npv = 918.12` (300 + 306 + 312.12) | ±0.01 |
| O6 | S=-100, r=0.05, esc=0, N=20, capex=4000 | `npv < -4000`; `discounted_payback = inf` | — |
| O7 | Any valid case | `abs(npv - (pv_benefits - pv_costs)) < 1e-6`; `bcr = pv_benefits / pv_costs` | 1e-6 |
| O8 | S=300, r=0, esc=0, N=20, capex=1000, life=6, repl_factor=1, **residual OFF** | `npv = +2000.00` (3 replacements at t=6,12,18) | ±0.01 |
| O8r | As O8 but **residual ON** | `npv = +2666.67` (last unit installed t=18, 4/6 life unused → +666.67) | ±0.01 |
| O9 | S=0, r=0, esc=0, N=20, capex=1000, life=30, **residual ON** | `npv = -666.67` (residual 1000×10/30 at t=20) | ±0.01 |

> O1, O2, O4 fix `life = 20` so the residual term is exactly zero and the oracle
> is valid under either `include_residual_value` setting. O8/O8r/O9 are the
> generalized-behaviour oracles added after the second-AI review (multiple
> replacements and residual). O8r and O9 assume residual ON is confirmed (§1.7).
>
> The exact figures in O2 (annuity factor 12.462211; PV_benefits 3738.66) are the
> error-prone ones and were independently reproduced by the second-AI pass (§0).

### 1.2 Functional behaviour (MUST)
- `compute_npv` returns an `NPVResult` with all fields populated (no NaN unless
  a metric is genuinely undefined, e.g. payback = `inf`).
- `cashflows` DataFrame has one row per horizon year, columns `{year, benefit,
  capex, discounted}`, and `discounted.sum() - battery_capex == npv` (to 1e-6).
- Exactly one replacement row carries a non-zero `capex` when a replacement is
  due; zero replacement rows otherwise.
- Monotonicity: holding all else fixed, `npv` is **non-increasing** in
  `discount_rate` and **non-decreasing** in `annual_saving`, `horizon_years`
  (when saving > 0), and `price_escalation` (when saving > 0).

### 1.3 Integration (MUST)
- `scripts/run_stage2.py` prints NPV, BCR, and discounted payback **alongside**
  the existing simple-payback block (payback NOT removed).
- New CLI args `--discount-rate`, `--horizon-years`, `--price-escalation`,
  `--battery-life-years` parse and propagate; defaults match
  [README.md](README.md) (5%, 20yr, 2%, 12yr).
- Baseline Glasgow run completes and reports a baseline NPV figure, which is
  recorded in the run output and session memory.

### 1.4 Non-regression (MUST)
- All existing 12 tests still pass.
- `src/model.py` and `src/battery.py` are **unchanged** (diff is empty for both).

### 1.5 Quality gates (MUST)
- New tests in `tests/test_economics.py` cover O1–O7; `pytest` green.
- `get_errors` clean on `src/economics.py`, `tests/test_economics.py`,
  `scripts/run_stage2.py`.
- Real vs nominal stated explicitly in the module docstring (real cash flows +
  real discount rate).

### 1.6 SHOULD (non-blocking)
- Type hints on public functions; concise docstrings on `compute_npv` and
  `NPVResult` only (no over-documentation of trivial internals).
- Optional residual-value path present but defaulted **off**.

### 1.7 Resolved decisions
- **Residual value default:** `include_residual_value=True` (ON). **Confirmed by
  user** — standard appraisal practice, reviewer-recommended. O8r and O9 are
  therefore firm acceptance oracles.
- **Fractional battery life rounding:** replacement year uses `ceil(life)`
  (replace at the end of the year life runs out). **Confirmed by user.**

---

## 2. Independent second-AI review gate

A separate agent instance acts as an adversarial reviewer. It is **not** told my
expected numbers up front for the recomputation step — it derives them from the
method definitions and only then compares.

### 2.1 Pre-implementation pass (run now, on this plan)
**Inputs given to reviewer:** the method definitions in Section 1.1 (conventions
+ inputs), but NOT my expected outputs.
**Reviewer tasks:**
1. Independently compute O1–O7 from the conventions and report its own figures.
2. Flag any ambiguity, missing convention, or internal inconsistency in the spec.
3. Critique the NPV methodology against standard investment-appraisal practice
   (real/nominal consistency, capex timing, replacement treatment, residual
   value, payback definition).
**Pass condition:** reviewer's independent O1–O7 match this plan within tolerance,
OR any mismatch is traced to a genuine spec error that is then corrected here.

### 2.2 Post-implementation pass (gate on the final code)
**Inputs given to reviewer:** the final `src/economics.py`, the tests, and this
plan's Section 1.
**Reviewer tasks:**
1. Confirm the code implements the stated conventions (not just that tests pass —
   tests could encode the same error).
2. Re-derive O2 and O3 by hand and confirm the code reproduces them.
3. Check for OWASP-irrelevant but correctness-relevant issues: off-by-one in
   discount exponents, replacement double-counting, escalation applied to capex
   by mistake, integer/float division.
4. Confirm non-regression (model/battery untouched).
**Pass condition:** no MUST criterion violated; all reviewer-found defects fixed
and re-reviewed.

---

## 3. Additional context to strengthen verification

Ways to bring in external ground truth so verification does not rely on the
model checking itself:

1. **Independent recomputation in a second tool.** Reproduce O1–O7 (and the
   baseline Glasgow NPV) in a throwaway spreadsheet or a standalone numpy
   snippet that does **not** import `src/economics.py`. Agreement rules out
   shared-code bugs. (Cheapest, highest-value cross-check.)
2. **Literature anchor values.** Pull discount rate, horizon, price-escalation,
   and LFP cycle-life/fade figures from named sources (HMT Green Book; recent UK
   domestic PV-BESS NPV papers; manufacturer LFP warranty sheets). Store them in
   a `docs/economic_assumptions.md` table with citations so every default is
   defensible and the dissertation can cite directly. This is the same evidence
   the marking rubric rewards.
3. **Reference-tool sanity check.** Cross-check a single representative scenario
   against an established public calculator (e.g. an open-source PV-BESS payback/
   NPV tool, or a published worked example) and record the % agreement.
4. **Supervisor review of assumptions.** The economic assumptions (Section 1 of
   README) are judgement calls; a one-page summary for supervisor sign-off
   converts them from "assumed" to "agreed", which is defensible in the viva.
5. **Internal consistency with existing validation.** The baseline Glasgow annual
   energy economics already feed `docs/v2_controller_comparison_report.md`
   (net cost £381, saving £155). The NPV model must reproduce that **annual
   saving** as its year-1 input before discounting — a free regression anchor
   against already-validated numbers.
6. **Dimensional / units audit.** Confirm GBP vs pence and kWh vs kW·half-hour
   are handled once at the boundary (the existing code mixes £/kWh prices and
   p/kWh display); a units assertion in tests prevents silent 100× errors.

---

## 4. Definition of done (summary)

NPV model is **done** when: O1–O7 pass; integration + non-regression MUSTs pass;
the post-implementation second-AI review finds no unresolved MUST defect; the
baseline Glasgow NPV is recorded; and at least context source (1) — independent
recomputation — confirms the baseline figure.

### Acceptance record (completed)
- All 15 economics oracles/tests pass; full suite 32 passed, no regressions.
- Post-implementation second-AI review: 18 PASS, 0 unresolved (item 19 residual
  default now confirmed ON).
- Baseline Glasgow NPV recorded: **−£3,584.87**, BCR 0.424, NOT VIABLE
  (annual saving £145.92; discount 5%, horizon 20yr, escalation 2%, life 12yr).
- Independent recomputation (no module import) reproduced −£3,584.85 / BCR 0.424.
- `src/model.py` and `src/battery.py` unchanged.
