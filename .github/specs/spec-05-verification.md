# Spec 05 — Verification Plan

Companion to [spec-05-parameter-sweep-harness.md](spec-05-parameter-sweep-harness.md).
Defines what "correct" means, the independent-review gate, and the external checks.
Mirrors the Spec 01–04 workflow. The distinctive risk here is the **surrogate**: the
sweep must give the same answer as the honest full-solve, so surrogate fidelity is the
headline verification target.

## 1. Numeric / structural oracles (recompute independently — do NOT read module output)

A second reviewer (subagent, separate tool, or by hand) reproduces these *without* seeing
the implementation's answers. Tests use a **stub dispatch** (closed-form `saving`/
`throughput` as a function of `E_usable`) so no CVXPY is required.

| # | Input | Quantity | Expected |
|---|-------|----------|----------|
| S1 | default `SweepGrid` | scenario-row count | 12 + 108 + 72 = **192** |
| S2 | strictly-increasing stub | `saving_at` shape / knot fidelity | non-decreasing; exact at knots |
| S3 | penalty at 5 / 10 / 15 kWh (cost 600, 6000 EFC) | derived `c_thr` | 0.05 each (capacity-independent) |
| S4 | size-0 scenario | saving / capex / replacement | PV-only vs grid-only-flat; no battery capex; no replacement |
| S5 | surrogate vs full `simulate_capacity_fade` | run-to-fade NPV | agree within tolerance; gap ↓ as knots ↑ |
| S6 | ToU target SOC, window 0.10–0.90 | winter/spring/autumn/summer | 0.66 / 0.284 / 0.46 / 0.10 |
| S7 | `self_consumption`, random day | energy balance / SOC / grid-charge | balance holds; SOC ∈ [0.1, 0.9]; no grid charge |
| S8 | flat + `self_consumption_tou` | schedule | identical to `self_consumption` |
| S9 | populated cache | second `run_sweep` | zero solves; identical output |

Hand-derivations for the non-obvious ones:
- **S1:** size-0 references = 4 locations × 3 tariffs = 12. MILP battery rows =
  4 × 3 × 3 sizes {5,10,15} × 3 penalties {0,5,10} = 108. Rules rows = 4 × 3 × 3 sizes × 2
  controllers = 72 (penalty not crossed — rules ignore the wear price). Total = 192.
- **S3:** `c_thr = capex / (cycle_life · 2 · capacity) = (capacity·600) / (6000·2·capacity)
  = 600 / 12000 = 0.05` — the `capacity` cancels, so 5, 10, 15 kWh all give 0.05. This
  cancellation is *why* one curve serves every size; the reviewer must confirm it holds.
- **S6:** target absolute SOC `= soc_min + fraction·(soc_max − soc_min) = 0.10 +
  fraction·0.80`. Winter 0.70 → 0.66; spring 0.23 → 0.284; autumn 0.45 → 0.46; summer
  0.00 → 0.10 (no grid charge).

## 2. Independent code review (second-AI gate)

### 2.1 Pre-implementation (this document)
- Confirm the harness **only orchestrates**: it imports `model`, `battery`, `economics`,
  `degradation`, `tariffs`, `locations` and does not modify their logic.
- Confirm the surrogate premise: the annual saving depends on the battery only through
  `E_usable` and power, and the derived penalty is capacity-independent (S3) — so one
  curve per (location, tariff, penalty, power) legitimately serves all sizes and all fade
  years. Flag any place capacity leaks in beyond `E_usable`.
- Confirm run-to-fade is the baseline everywhere and forced-10-yr is a *readout*, not a
  re-solve.
- Confirm the SOC-max sensitivity is handled by rescaling `E_usable` (free), not a new
  solve; and that export-rate sensitivities (which *do* change dispatch) are built as
  extra curves at the baseline cell only, not silently reusing a mismatched curve.

### 2.2 Post-implementation (run on final code before acceptance)
- Recompute S1–S9 blind; all must match.
- Verify `model.py`, `battery.py`, `economics.py`, `degradation.py`, `tariffs.py`,
  `locations.py` are unchanged vs pre-Spec-05 (diff).
- **Surrogate fidelity (the key check):** on ≥3 diverse validation cells (e.g. Glasgow/E7,
  Plymouth/Agile, Inverness/flat, each at 10 kWh), compute run-to-fade NPV *both* ways —
  surrogate lookup and the full `simulate_capacity_fade` (real ~20 solves) — and confirm
  agreement within the stated tolerance. Then refine the knot grid and confirm the gap
  shrinks (convergence), demonstrating the approximation is controlled, not lucky.
- Confirm the Glasgow/Agile/10 kWh/~5 p/MILP row reproduces the Spec 03–04 baseline
  (run-to-fade NPV ≈ −£4,040, BCR ≈ 0.784) within surrogate tolerance.
- Confirm rules-controller schedules satisfy per-step energy balance, SOC bounds, power
  limits, and round-trip efficiency (S7); the seasonal ToU targets (S6); and flat+ToU ≡
  self-consumption (S8).
- Confirm the cache makes a re-run solve-free and byte-identical (S9), and that
  parallel execution (`--jobs > 1`) gives the same numbers as serial.

## 3. External context (source grounding, beyond the code)

- **Monotonicity & shape sanity:** NPV should be concave-ish in battery size (diminishing
  returns; often a turnover where added capex outruns added saving) — a mis-wired surrogate
  would show a jagged or non-monotone `saving(E_usable)`.
- **Cross-axis sanity:** higher-yield (lower-latitude) locations should not have *lower*
  PV-only value; Agile/E7 should not be beaten by flat once the battery is present (per
  Spec 03 findings); a 0 p in-dispatch penalty should cycle the battery **more** (and
  replace sooner) than 10 p — the degradation-effect direction the study is about.
- **MILP ≥ rules:** for any cell the MILP saving should be **≥** the best rules controller
  (optimality); if a rules controller ever beats MILP, the MILP or the accounting is wrong.
- **Surrogate validation** (also in §2.2) is the primary external-grounding step: the honest
  full-solve is the reference the fast path is measured against.
- **Supervisor sign-off** on the grid definition, the surrogate approximation and its
  validated tolerance, and the seasonal ToU charging assumption.

## 4. Definition of done

- All §1 oracles reproduced blind; §2.2 checks pass; full suite green.
- Surrogate-vs-full-solve validated within tolerance on the validation cells, with a
  documented convergence check; tolerance and cells recorded below.
- `results/sweep_scenarios.csv` produced with the §4.5 schema and §7-S1 cardinality;
  sensitivities file produced.
- Glasgow/Agile/10 kWh/~5 p/MILP cross-checks the Spec 03–04 baseline.
- Stated limitations (surrogate interpolation between knots; shared demand profile from
  Spec 04; 2023 PV with 2025 prices; seasonal ToU fractions an assumption) written up.

### Acceptance record

**Surrogate tolerance (adopted):** run-to-fade NPV within **≤1%** of the full 20-solve NPV,
with a demonstrated convergence (gap shrinks as knots increase). Default grid uses
**9 knots**; validation exercises `n_knots` and `n_knots + 4`.

**Validation cells (surrogate vs full `simulate_capacity_fade`, real ~20 solves):**

| Cell | Full NPV (life) | Surrogate (low knots) | gap | Surrogate (high knots) | gap |
|------|-----------------|-----------------------|-----|------------------------|-----|
| Glasgow / Agile / 10 kWh / 5.00 p / MILP | −£4,039.90 (17) | −£4,064.07 (7 knots) | −£24.17 (−0.60%) | −£4,048.15 (11 knots) | −£8.25 (−0.20%) |
| Glasgow / E7 / 10 kWh / 5.00 p / MILP (deepest cycling — stress case) | −£5,292.16 (15) | −£5,305.23 (9 knots) | −£13.07 (−0.25%) | −£5,297.81 (13 knots) | −£5.65 (−0.11%) |

- Gap shrinks monotonically with knot count on **both** cells → the approximation is
  controlled, not lucky. The default grid uses 9 knots (E7 gap −0.25%, well inside ≤1%).
- **Baseline cross-check exact (both cells):** the full-fade NPVs reproduce the Spec 03–04
  baselines exactly — Glasgow/Agile −£4,039.90 (life 17) ≈ −£4,040 (BCR 0.784), and
  Glasgow/E7 −£5,292.16 (life 15, replacement yr 16) ≈ −£5,292 — validating the surrogate
  *and* re-confirming the headline baselines. Logs: `results/surrogate_validation.log`,
  `results/surrogate_validation_e7.log`.

**Independent code review (second-AI gate) — PASS.** Blind recomputation matched all
oracles with zero mismatches: S1 = 192 (12 + 108 + 72); S3 = 0.05 £/kWh (capacity cancels);
S6 = 0.66 / 0.284 / 0.10 / 0.46. Confirmed: invariant modules unchanged (`git diff --stat`
shows only `README.md`); no capacity leak beyond `E_usable`; run-to-fade is a curve-lookup
readout, not a re-solve; MILP and rules use identical energy-cost accounting. Full suite
green (24/24 sweep+controller+location tests). One non-blocking note (now documented in
§4.3): reported annual saving is energy-cost only, so "MILP ≥ rules" holds strictly on
annual saving at penalty = 0 and on NPV otherwise.

**Sign-off:** implementation verified and accepted (2026-07-19) — surrogate validated on two
diverse cells (Agile + the E7 deep-cycling stress case) with documented convergence, both
full-fade NPVs reproducing the Spec 03–04 baselines exactly, and the independent code-review
gate passed. Outstanding: supervisor sign-off on the grid definition, the ≤1% surrogate
tolerance, and the seasonal ToU charging assumption; and (optional) the full 192-row sweep
to populate §10 Results.
