# Spec 06 — Verification Plan

Companion to [spec-06-sweep-economic-extensions.md](spec-06-sweep-economic-extensions.md).
Defines what "correct" means, the independent-review gate, and the external checks.
Mirrors the Spec 01–05 workflow. Two risks are distinctive here: the **widened surrogate
range** (0.08 SOH floor over 11 knots, now covering no-replacement trajectories down to
0.26 SOH), and **distributed execution** across 12 machines, where cache identity and
partition disjointness replace the single-process guarantee Spec 05 relied on.

## 1. Numeric / structural oracles (recompute independently — do NOT read module output)

A second reviewer (subagent, separate tool, or by hand) reproduces these *without* seeing
the implementation's answers. V1–V9 use a **stub dispatch** and need no CVXPY; V10–V15 are
assembled-experiment checks over the completed artefacts.

| # | Criterion | Input | Quantity | Expected |
|---|---|-------|----------|----------|
| V1 | A1, A12 | £1,000 cost, 20 yr, zero saving/discount | E1–E4 replacement + residual NPV | −1666.6667 / −3333.3333 / E3 formula / −11866.6667 |
| V2 | A2 | fade scenario, realised life 15 | terminal residual basis | realised predecessor life, latest install; no-replacement gets 0 |
| V3 | A3 | confirmed axes | scenario-policy rows | 54 × (1 + 2×(30 + 10)) = **4374**, key-unique |
| V4 | A5 | confirmed axes | distinct saving curves | 1620 MILP + 540 rules = **2160** |
| V5 | A4 | each location × PV size | profile file + capex | exact `{Label}_{n}kWp_2023.csv`; capex = `pv_kwp × 1109` |
| V6 | A5 | two PV sizes, two nominal sizes | cache key collision | no cross-return; key carries v2 ns, loc, tariff, PV, size, controller, penalty, power |
| V7 | A6 | MILP vs rules rows | penalty axis | MILP = {0, 1, 3, 5, 7, 9} p/kWh at 6000 EFC; rules penalty-free at 6000 EFC |
| V8 | A7 | every nominal size | nameplate power | 0.5C: 0.25/0.5/1.25/2.5/5.0 kW; fixed through fade |
| V9 | A8 | no-replacement trajectory | SOH / outflow / coverage | no reset, no outflow, SOH > 0, every query ≥ 0.08 floor |
| V10 | A9 | synthetic schedule | peak summary + top-50 | from actual `p_import_kw`; signed, unclipped; timestamps retained |
| V11 | A9 | assembled peak table | cardinality + balance | 2214 cases × 50 = **110700**; `import = demand − pv + charge − discharge` |
| V12 | A10 | prior artefacts | byte equality | Spec 03–05 CSVs and `sweep_v2_coarse/` unchanged by the v2 run |
| V13 | A11 | coarse grid | end-to-end | unique keys, finite NPV/peaks, zero-solve re-assembly |
| V14 | — | 12-machine partition | disjointness + coverage | `i mod 12` partitions 378 jobs with no overlap, union = all |
| V15 | — | assembled cache | integrity | 2160 files load; knots strictly increasing; SOC ∈ [0,1]; finite |

Hand-derivations for the non-obvious ones:

- **V3:** 54 cells = 3 loc × 3 tariffs × 6 PV. Each contributes 1 PV-only row plus
  2 lifetime policies × (5 sizes × 6 penalties MILP + 5 sizes × 2 rules) = 1 + 2×40 = 81.
  `54 × 81 = 4374`.
- **V4:** MILP curves = 54 × 5 sizes × 6 penalties = 1620; rules = 54 × 5 × 2 = 540.
  Penalty is *not* crossed with rules (§4.3), which is why rules contribute 540 and not
  3240. Total 2160.
- **V11:** peak events are retained per *dispatch case*, not per scenario-policy row: 54
  PV-only + 1620 MILP + 540 rules = 2214 cases, each keeping the top 50 half-hours.
- **V1/E4:** replacement at year 15 with £8,900 leaves the successor 5 years old at the
  year-20 horizon, so `residual = 8900 × 10/15 = 5933.33` and
  `NPV = −8900 − 8900 + 5933.33 = −11866.67` at zero discount.
- **V8:** power is `0.5 × Q_nom` and is a property of the *installed nameplate*, so a
  10 kWh pack faded to 6 kWh still charges at 5 kW. The cache key must therefore carry
  both nominal size and resolved power (§4.4).

## 2. Independent code review (second-AI gate)

### 2.1 Pre-implementation
- Confirm Spec 06 only *extends*: MILP constraints, battery equations, tariff definitions,
  controller algorithms, and the capacity-fade law are untouched (§2 out-of-scope).
- Confirm the surrogate premise still holds once power is size-dependent: because power no
  longer declines with SOH, battery size is **not** a free lookup within one shared curve —
  each nominal size needs its own curve. Flag any residual size-sharing.
- Confirm no-replacement is a second *readout* of the same curves, not a second solve set.
- Confirm peak retention is pure post-processing and cannot alter objective, constraints,
  or solve count.

### 2.2 Post-implementation (run on final code before acceptance)
- Recompute V1–V15 blind; all must match.
- Verify `model.py`, `battery.py`, `economics.py` (dispatch/fade core), `tariffs.py`,
  `locations.py`, `controllers.py` are unchanged vs pre-Spec-06 except the residual-basis
  change of §4.1.
- **Surrogate fidelity (the key check):** on ≥2 diverse cells compute run-to-fade NPV both
  ways — surrogate lookup and full `simulate_capacity_fade` — at the production knot count
  and a refined one, confirming the gap is inside tolerance *and* shrinks.
- Confirm cache separation for two PV sizes and two nominal battery sizes.
- Inspect one no-replacement trajectory below 0.60 SOH: no reset, no outflow.
- Confirm prior sweep artefacts are byte-identical before and after (hashes, not git —
  `results/` is gitignored).

## 3. External context (source grounding, beyond the code)

- **Shape sanity:** `saving(E_usable)` non-decreasing within a curve; annual saving rising
  with battery size; NPV concave-ish in size with a turnover where capex outruns saving.
- **Degradation direction:** a 0 p in-dispatch penalty must cycle the battery **more** than
  9 p — the effect the study is about. Throughput must not rise with penalty.
- **Cross-axis sanity:** PV-only value rises with PV size and with lower latitude;
  no-replacement should out-perform run-to-fade on average (it avoids a full outflow).
- **MILP vs rules — qualified for Spec 06.** Spec 05 §3 asserted a rules controller
  beating MILP proves the MILP or the accounting is wrong. That is **too strong for a
  rolling-horizon solve** and must not be used as a Spec-06 acceptance test.
  `solve_year` optimises **day-by-day** with SOC carried forward and no terminal-SOC value
  term, so the schedule is optimal within each 24 h window but myopic across the year.
  Where a tariff has no intra-day price spread, many daily schedules are cost-equivalent
  and the objective is indifferent to end-of-day SOC — which has value only in the *next*
  day. A heuristic that habitually retains charge can therefore beat it over 365 days.
  The valid Spec-06 invariant is: **MILP ≥ rules on any tariff with an intra-day price
  spread (E7, Agile)**, with flat-tariff exceptions bounded and reported (§4).
- **Supervisor sign-off** on the widened grid, the 0.08 SOH coverage floor, the
  no-replacement sensitivity, and the day-ahead myopia limitation above.

## 4. Definition of done

- All §1 oracles reproduced blind; §2.2 checks pass; full suite green.
- Surrogate validated within tolerance at the production knot count on ≥2 diverse cells,
  with a documented convergence check.
- `results/sweep_scenarios_v2.csv` and `results/sweep_peak_events_v2.csv` produced at the
  §7-A3 cardinality from a complete 2160-curve cache.
- Prior sweep artefacts confirmed unmodified by hash.
- Stated limitations written up: surrogate interpolation between knots; day-ahead myopia
  (above); shared demand profile from Spec 04; 2023 PV with 2025 prices; PV profiles are
  exact linear rescalings; operation below 0.60 SOH labelled as a sensitivity.

### Acceptance record

**Full experiment (2026-07-27).** 378 jobs (324 MILP + 54 rules) across 12 partitions,
`i mod 12`, run on one 6-core laptop (indices 0–3) and 14-core university desktops
(indices 4–11), `--solver SCIPY` pinned on every worker. Caches merged flat.

**Test suite:** 112 passed. Unit coverage maps to criteria as: E1–E4 and the realised-life
residual → `tests/test_economics.py` (A1, A2, A12); S11–S15, 0.5C power, PV/size cache
separation → `tests/test_sweep.py` (A5, A7, A8); P1–P5 → `tests/test_peaks.py` (A9).

**Assembled-experiment oracles — all pass:**

| # | Check | Result |
|---|-------|--------|
| V3 | scenario rows / key uniqueness | **4374**, 0 duplicate keys |
| V4 | cached curves | **2160** = 1620 MILP + 540 rules; expected filename set matched exactly (0 missing, 0 unexpected) |
| V5 | PV profile + capex | exact per-size file resolved; PV-only capex = `pv_kwp × 1109` to 1e-6; saving strictly rises with PV size in all 9 loc/tariff cells |
| V7 | penalty axis | MILP {0, .01, .03, .05, .07, .09} all at 6000 EFC; 0 rules rows carry a penalty |
| V8 | 0.5C power | 0 violations; mapping 0.5→0.25, 1→0.5, 2.5→1.25, 5→2.5, 10→5 |
| V9 | no-replacement | 0 replacement outflows; min SOH **0.2649 > 0**; 1874 of 2160 rows operate below 0.60 as intended; every query ≥ 0.08 floor |
| V11 | peak events | **110700** = 2214 × 50; ranks exactly 1–50; strictly descending; energy-balance residual **max 0.000000** |
| V12 | prior artefacts | `sweep_scenarios.csv` (2026-07-21), `sweep_scenarios_capexv1.csv` (07-20), coarse v2 CSVs and `cache/sweep_v2_coarse/` (45 curves, 07-25) all predate the 07-27 15:13 v2 outputs — unmodified |
| V14 | partition | 378 jobs, `i mod 12` disjoint, union complete; per-index counts 31–32 |
| V15 | cache integrity | 2160/2160 load; knots strictly increasing; SOC ∈ [0,1]; all finite |

**Additional invariants over the assembled table (13 checks, 0 violations):** percentile
ordering `p95 ≤ p99 ≤ peak`; threshold-count nesting `n>2kW ≥ n>3kW ≥ n>5kW ≥ n>7kW`;
`mean_soc ∈ [0,1]`; `efc ≥ 0`; `throughput ≥ 0`; `capex > 0`; every battery row's annual
saving ≥ its matching PV-only saving. All NaNs are structural (54 PV-only rows lack
battery fields; 1134 rows lack a wear penalty = 1080 rules + 54 PV-only).

**Surrogate fidelity (11 production knots vs 15):**

Tolerance carried over from Spec 05: run-to-fade NPV within **≤1%** of the full 20-solve
NPV, with the gap shrinking as knots increase. Both cells recompute from scratch
(`--validate` builds curves directly and reads no cache), so these are independent of the
experiment artefacts.

| Cell | Full NPV (life) | Surrogate @11 | gap | Surrogate @15 | gap |
|------|-----------------|---------------|-----|---------------|-----|
| Inverness / E7 / 3 kWp / 10 kWh / 5 p | −£4104.88 (15) | −£4112.83 (15) | −£7.96 (**−0.19%**) | −£4108.84 (15) | −£3.97 (**−0.10%**) |
| Plymouth / Agile / 6 kWp / 10 kWh / 0 p (max-cycling stress) | −£642.88 (13) | −£646.96 (13) | −£4.08 (**−0.64%**) | −£644.95 (13) | −£2.07 (**−0.32%**) |

Both cells: the gap **halves** from 11 to 15 knots and realised life is identical across
the full solve and both surrogates (15 and 13 respectively) — the approximation is
controlled, not lucky, at the production knot count. Both sit inside the ≤1% bar at 11
knots, the zero-penalty max-cycling stress cell included.

Note on the two relative figures: Plymouth's **absolute** gap is the smaller of the two
(£4.08 vs £7.96) but its **relative** gap is larger (−0.64% vs −0.19%) purely because its
NPV sits closer to zero (−£642.88 vs −£4104.88). The percentage measure inflates near
NPV = 0 and should not be read as the Agile cell being less well approximated.

**Known deviations (recorded, not defects):**

1. **Saving non-monotonic across knots on 85 of 2160 curves** — all at the two highest
   penalties (0.07: 26, 0.09: 59), where the battery barely cycles and the curve is nearly
   flat. Largest drop **£0.156/yr**; median £0.017; none exceeds £1. This is HiGHS
   optimality-gap noise, ~4 orders of magnitude below NPV relevance.
2. **Throughput rises marginally with penalty on 48 of 270 groups** — largest increase
   **0.0041%**. Same solver-tolerance origin; the axis direction holds everywhere at
   material magnitude.
3. **Rules beat MILP at penalty 0 in 180 of 540 comparisons — exclusively flat tariff**
   (180/180), evenly across all 3 locations, 6 PV sizes, 5 battery sizes and both
   heuristics. Median advantage **£0.41/yr**, max **£39.24/yr (3.2%)** at Plymouth /
   flat / 6 kWp / 10 kWh (NPV impact up to **+£345.5** in the heuristics' favour);
   magnitude grows with battery and PV size. E7 and Agile show **zero** exceptions.
   Mechanism, confirmed by the blind reviewer with a direct 72 s annual solve:
   `solve_year` uses a daily horizon with free terminal SOC, so under flat + matched
   export the myopic day-optimum **exports** leftover PV at £0.1300/kWh rather than
   carrying it overnight to displace £0.2639/kWh morning import. The greedy
   self-consumption rule never exports from the battery and so implicitly carries it.
   Measured mean end-of-day SOC 0.115 (MILP) vs 0.289 (rules); in summer 0.121 vs 0.394.
   Not an accounting error — but any framing of the MILP as an *optimality benchmark*,
   or of rules as capturing "x% of optimal", is invalid under the flat tariff.

**Headline results (for Spec 07 figures, not a verification claim):** only **289 of 4320**
battery configurations (**6.7%**) beat their matching PV-only case on NPV. NPV spans
−£9,972 to +£7,857; BCR 0.004–4.56; first-year EFC up to 727. `peak_reduction_kw` ranges
−4.709 to +1.621 with median 0.000 — negative values present and unclipped per §4.6.
Mean NPV: no-replacement £1,421 vs run-to-fade £908, confirming the expected direction.

**PV-axis note (affects interpretation).** The downloaded PVGIS profiles are **exact
linear rescalings** of one another — `{2,4,6} kWp` reproduce `1 kWp × {2,4,6}` with max
deviation 0.000000 kW at all three locations. A4's guard against reusing one profile for
another size is therefore correct-by-construction but numerically immaterial: generation
and capex are both linear in `pv_kwp`, so all non-linearity in the results originates in
the battery/dispatch interaction, not the PV input.

**Independent code review (blind second-reviewer gate) — PASS (2026-07-27).** A second
agent, barred from reading this document or any verification script, derived every
expected value from `spec-06-sweep-economic-extensions.md` and the source before
comparison. Blind recomputation matched with zero mismatches: 4374 rows (54 × 81) with a
unique key; 2160 curves (1620 + 540) as an **exact filename-set equality**; 110700 peak
rows (2214 × 50); E1–E4 = −1666.6667 / −3333.3333 / **−1431.2076** / −11866.6667 via both
`compute_npv` and the real `fade_npv` caller; A7 max deviation 0.0 with 11 fade capacities
at a single 5.0 kW power; A8 min SOH 0.26491; A4 capex deviation 0.0; A9 balance residual
6.1e-11 kW; A10 by ctime + hash with overwrite refusal demonstrated live; 112 tests.

Two independent evidences stronger than anything in the record above:
- **Byte-reproducibility:** re-assembling the entire table from cache with zero solves
  (27 s) produced files **sha256-identical** to both published artefacts, with no new
  cache writes — the published outputs are exactly what the cache implies, unedited.
- **Chain spot-check:** a fresh 72 s annual MILP solve of Plymouth / flat / 6 kWp / 10 kWh
  reproduced the cached curve's saving to the penny (£1225.8827).

The reviewer also confirmed A9 positively rather than only structurally: 22154 of 110700
events (**20.0%**) differ from the forbidden `max(0, demand − pv − discharge)`
approximation, by up to 5.00 kW — the grid-charging term §4.6 requires be retained.

**Open items raised by the blind gate (not verification failures):**

1. **The optimum sits on the lower grid boundary again, and battery capex is purely
   linear.** Best size is **0.5 kWh in 98 of 108 cells** (1.0 kWh in the other 10) — the
   smallest positive size on the grid, exactly the condition §6 expanded the grid to
   escape. No battery row beats its PV-only reference by more than **£157**, and the
   global maximum-NPV row in the whole experiment is a **PV-only** reference (Plymouth /
   flat / 6 kWp, £7856.91), £205.8 above the best battery row. Cause is structural, not
   sampling: `SweepDispatchProvider.capex` is `pv_kwp × 1109 + size_kwh × 890` with **no
   fixed component**, despite the code comment stating battery cost "already includes the
   hybrid inverter" (a 0.5 kWh pack therefore costs £445 *including* an inverter). With a
   linear price and negative marginal value, `argmax NPV` is always the smallest available
   size, so adding sub-0.5 kWh points would move the boundary again indefinitely. The
   defensible reading is that the marginal battery kWh is NPV-negative at £890/kWh.
   Implied break-even battery price (£/kWh at which the best cell's delta reaches zero):

   | policy | 0.5 kWh | 1 kWh | 2.5 kWh | 5 kWh | 10 kWh |
   |---|---|---|---|---|---|
   | no-replacement | £1108 | £1047 | £929 | £783 | £586 |
   | run-to-fade | £913 | £839 | £746 | £577 | £396 |

   Recommendation: re-specify capex as `c_fixed + c_var × kWh`, and lead Spec 07 with the
   break-even price surface rather than an "optimal size" that is a grid artefact.

   > **Correction (2026-07-29).** Two claims above are wrong and are retained only because
   > this is an acceptance record. (a) Marginal benefit is **bounded** as Q → 0 — the
   > first-interval averages are £436/kWh (flat), £936 (E7) and £1,037 (Agile) — so a finer
   > size grid *converges*; it does not move the boundary indefinitely. (b) "An optimal size
   > that is a grid artefact" is too strong. Under linear £890/kWh the E7 and Agile optima
   > were genuinely *interior* (£936/kWh marginal benefit against a £890 price, crossing
   > below it thereafter), and where the optimum did sit at the edge the NPV there was
   > negative — a correct "do not buy at any size in range", which the grid expresses
   > through its zero-battery reference row. The valid objection is narrower: at £890/kWh a
   > 0.5 kWh system costs £445, so the optimum was being placed outside the size range where
   > price data exists. The recommendation itself was sound and has been adopted. Full
   > argument in [`docs/results_summary.md`](../../docs/results_summary.md) §1.1 and §4.
2. **The §5 run manifest was never written.** No record of commit, Python/CVXPY/solver
   versions, machine partitions, commands, cache counts, or completion status exists in
   the repo. A10 is still evidenced by ctimes and byte-identical reassembly, but this is
   an unmet spec requirement and should be reconstructed while the information is
   recoverable.
3. **Penalty-axis partial degeneracy (efficiency, not correctness).** Distinct
   fresh-battery outcomes per cell from the six solved penalties: Agile 6.00, flat 4.48,
   E7 **3.13** (min 2). Throughput collapses at the predicted break-even points
   (flat between 0.05 and 0.07, predicted 0.1339/2 = 0.067; E7 between 0.07 and 0.09,
   predicted 0.185/2 = 0.093), confirming the penalty is applied as specified — but roughly
   half the flat/E7 MILP compute produced near-duplicate dispatch.
4. **Residual asymmetry is conservative, and saying so strengthens the result.**
   No-replacement receives zero terminal residual while still owning a working pack
   (SOH 0.26–0.60) at year 20, per §4.5/A2. That *understates* no-replacement, which still
   wins 86.8% of cases (mean NPV £1421 vs £908) — so the policy conclusion is robust to
   the asymmetry.

**Decision (2026-07-27): Spec 05 §3 stands unamended.** Its unqualified "MILP ≥ rules"
external check is correct for the codebase as it existed when that document was written
and remains the accepted record of it. The qualification in §3 above applies to Spec-06
scope only. This is a deliberate, recorded difference between the two documents, not an
inconsistency to be reconciled — consistent with Spec 06 §2, which puts retrospective
changes to accepted specs out of scope.

**Sign-off:** technically verified and recommended for acceptance (2026-07-27) — the blind
gate passed with zero mismatches, and the artefacts were shown byte-reproducible from
cache. Outstanding at that date:
- supervisor sign-off on the widened grid, the 0.08 coverage floor, the no-replacement
  sensitivity, and the day-ahead myopia limitation;
- a decision on open item 1 (linear battery capex / boundary optimum) before Spec 07
  figures are drawn, since it determines whether "optimal battery size" is a reportable
  result or a grid artefact;
- the §5 run manifest (open item 2).

**Resolution of the open items (2026-07-29).**

- **Item 1 — closed.** Capex is re-specified as `F + c·Q` for the battery (adopted
  central case £4,584 + £373/kWh, envelope F ∈ [2,900, 4,900] with c ∈ [490, 310]) and
  left linear for PV at £1,109/kWp, because PV's fitted fixed term is £98 and negative in
  one year, i.e. indistinguishable from zero. Recorded in [README.md](README.md); derived
  in [`docs/results_summary.md`](../../docs/results_summary.md) §1. Optimal size moves off
  the lower boundary to 5–10 kWh for E7 and Agile. **No re-solving was required** — capex
  never enters dispatch, and all six penalty values on the axis are explicit, so each
  specification is a ~25 s zero-solve reassembly of this same 2,160-curve cache.
  Re-running at the original parameters reproduces `sweep_scenarios_v2.csv` with maximum
  absolute NPV and capex difference **0.00**, so nothing in this acceptance record is
  invalidated by the change.
- **Item 2 — closed.** [`docs/spec06_run_manifest.md`](spec06_run_manifest.md)
  reconstructs the §5 manifest: commit and code-freeze evidence, environment, the 12-way
  partition table, commands, cache counts, completion status, and five incidents. Its §6
  lists what could not be recovered (host identity and library versions for partitions
  4–11, and their `parts/`/`logs/` directories, which were never merged onto the assembly
  machine).
- **Items 3 and 4 — unchanged.** Penalty-axis degeneracy is an efficiency note, not a
  correctness one; the residual asymmetry remains conservative in the direction that
  strengthens the no-replacement conclusion.
- **Still outstanding:** supervisor sign-off, as above. Spec 07 is unblocked.

**Note for later readers.** `tests/test_spec06_artefacts.py` asserts against
`results/sweep_scenarios_v2.csv` — the original linear-capex assembly — including
`test_v5_pv_capex_linear_and_saving_rises_with_pv_size`, which checks `capex = pv_kwp ×
1109` exactly. That file is immutable and the test is correct as written. Pointing those
oracles at one of the `_central` / `_lowF` / `_highF` assemblies would fail on capex, by
design: only the structural and dispatch-derived oracles carry over.
