# Spec 06 — Independent Blind Reviewer Report (2026-07-27)

Verbatim final report from the blind second-reviewer gate recorded in
`docs/specs/spec-06-verification.md`. The reviewer was barred from reading
that document or any verification script.

---

I have everything. Final report.

---

# Independent Second-Reviewer Report — Spec 06

**Scope note:** I did not open `docs/specs/spec-06-verification.md` or `scripts/verify_spec06.py`. All expected values below were derived from `spec-06-sweep-economic-extensions.md` and the source code before comparison. No check is compromised.

---

## Task 1 — Scenario-row cardinality

**My derivation.** §3 axes: 3 locations × 3 tariffs × 6 PV sizes = **54 cells**. Per cell (§4.7 row definition): 1 PV-only reference row; positive battery sizes = {0.5, 1, 2.5, 5, 10} = 5; MILP rows = 5 × 6 penalties = 30; rules rows = 5 × 2 controllers = 10 (penalty-free per §4.3); every battery row carries 2 lifetime-policy readouts. Per cell = 1 + 2×(30+10) = **81**. Total = 54 × 81 = **4374**.

**Observed.** 4374 rows. Every (loc, tariff, pv) cell has exactly 81. Scenario key `(location, tariff, export, pv_kwp, battery_kwh, power_kw, penalty, controller, policy)` has **0 duplicates**. Breakdown: milp 1620+1620, self_consumption 270+270, self_consumption_tou 270+270, reference 54. MILP rows carry exactly the six `(penalty, 6000 EFC)` pairs at 540 each; all 1134 rules+reference rows have NaN penalty and rules sit at 6000 EFC. All NPVs finite.

**PASS** (also satisfies A3 and A6.)

## Task 2 — Cached saving curves

**My derivation.** §4.3: MILP curves cross the penalty axis, rules do not. §4.2/§4.4: curve identity = location × tariff × PV size × nominal battery size (own curve per size, since power is fixed at 0.5C) × controller × penalty-tag × resolved power.
- MILP: 3 × 3 × 6 × 6 × 5 = **1620**
- Rules: 3 × 3 × 6 × 5 × 2 = **540**
- Total = **2160** (17 820 MILP solves at 11 knots, matching §4.7).

I then reconstructed the expected filenames from `_curve_cache_path` in `src/sweep.py`: `{loc}_{tariff}_pv{pv:g}_bat{size:g}_{controller}_{tag}_pw{power:g}.pkl`, with MILP `tag = f"{penalty:.4f}"` ∈ {0.0000, 0.0100, 0.0300, 0.0500, 0.0700, 0.0900}, rules `tag = "rules"`, `power = 0.5 × size`.

**Observed.** 2160 `.pkl` files in a single flat directory (no leftover machine subdirectories). Set diff against my reconstructed set: **0 missing, 0 unexpected** — an exact set equality, not just a matching count.

**PASS**

## Task 3 — Retained peak-event rows

**My derivation.** Peak events are emitted per *dispatch case*, not per scenario-policy row (`_peak_event_rows` is keyed on loc/tariff/pv/battery/power/penalty/controller with no `policy`), and each case retains the top **50** half-hours (§4.6, `peaks.TOP_EVENTS`). Cases = 2160 curves + 54 PV-only reference cases (one per cell) = **2214**. Expected rows = 2214 × 50 = **110 700**. (Cross-check: the coarse run's 2400 events = (45 curves + 3 PV-only) × 50, which validates this counting rule.)

**Observed.** 110 700 rows, 2214 distinct cases, exactly 50 events each, ranks 1–50, sorted descending within every case.

**PASS**

## Task 4 — Economic oracles E1–E4

**My hand arithmetic** (replacements at `t_r = k·⌈L⌉ < N`, each paid in full; `residual = C_r · max(0, L − (N − t_install))/L` at year N):

| | Replacements | `t_install` | Residual | NPV |
|---|---|---|---|---|
| **E1** N=20, L=12, C=1000, r=0 | year 12 | 12 | 1000·4/12 = 333.3333 | −1000 − 1000 + 333.3333 = **−1666.6667** |
| **E2** N=20, L=6 | years 6, 12, 18 | 18 | 1000·4/6 = 666.6667 | −1000 − 3000 + 666.6667 = **−3333.3333** |
| **E3** N=20, L=12, r=5% | year 12 | 12 | 333.3333 @ yr 20 | −1000 − 1000/1.05¹² + 333.3333/1.05²⁰; 1.05¹²=1.795856 → 556.8374; 1.05²⁰=2.653298 → 125.6295; = **−1431.2076** |
| **E4** N=20, L=15, C=8900 | year 15 | 15 | 8900·10/15 = 5933.3333 | −8900 − 8900 + 5933.3333 = **−11866.6667** |

**Observed.** `compute_npv` with the explicit residual override returns −1666.6667, −3333.3333, −1431.2076, −11866.6667. The real Spec-06 fade caller `fade_npv` — fed replacement-year lists as the fade simulator would produce them — independently derives `realised_life = first_replacement_year − 1` (12, 6, 15), the correct `t_install`, the same residuals, and the same NPVs.

**PASS** (A1, A2.)

## Task 5 — A7, 0.5C nameplate power fixed through fade

**Derivation.** §4.4: `max_charge_kw = max_discharge_kw = 0.5·Q_nom`, fixed for that battery's life; 1.25/2.5/5 kW at 2.5/5/10 kWh; no 3 kW ceiling.

**Observed.**
- Table: `power_kw == 0.5 × battery_kwh` for all 4320 battery rows, max deviation **exactly 0.0**. Values {0.25, 0.5, 1.25, 2.5, 5.0}; PV-only rows 0.
- Code path: `SweepDispatchProvider.dispatch_year` computes `power = c_rate × nominal_size_kwh` **once**, outside the returned closure, and the closure builds `BatteryParams(capacity_kwh=capacity_kwh, max_charge_kw=power, max_discharge_kw=power, …)`. Instrumenting a full 20-year fade for a 10 kWh pack: 11 dispatch calls spanning capacities 0.8 → 10.0 kWh, all at a **single** power value of 5.0 kW. Cache filenames encode `pw{power:g}`, so no cross-power collision is possible.

**PASS**

## Task 6 — A8, no-replacement policy

**Derivation.** §4.5: no SOH/EFC/age reset, no replacement outflow, every queried capacity inside solved knots (no flat extrapolation), fail loudly on SOH ≤ 0. Mechanism should be `replace_at_eol=False, soh_floor=0.0` so the replacement threshold never binds, plus `replacement_cost_factor=0` and `include_residual_value=False` in the NPV.

**Observed.**
- All 2160 no-replacement rows: `replacement_years == "[]"` (no reset ever fired), `realised_life_years` NaN.
- `no_replacement_npv` sets `battery_life_years = horizon`, `replacement_cost_factor = 0.0`, `include_residual_value = False` → `replacement_cost = 0` means `compute_npv` writes no outflow and no residual. **Zero replacement outflow, zero residual.**
- **Minimum SOH across all 2160 trajectories = 0.26491**, strictly positive, comfortably above the **0.08** `SweepGrid.min_soh_coverage` floor. (Better than the spec's conservative 0.214 projection.) 86.8 % of no-replacement rows do fall below 0.60 SOH, as intended.
- Knot coverage: sampled pickles all have 11 knots with `e_usable[0]/e_usable[-1] = 0.0800` exactly and `e_usable[-1] = size × 0.8` exactly (fresh-battery point exact). Since `e = Q_nom·SOH·window` and `e_lo = Q_nom·window·0.08`, SOH ≥ 0.08 ⟺ query ≥ lowest knot, so the `np.clip` in `SavingCurve._at` provably never engages. `_validate_knot_coverage` enforces this per scenario and per policy.
- Run-to-fade minimum SOH is 0.60002 — the 0.60 floor never breached, as designed.

**PASS**

## Task 7 — A4, PV profile resolution, capex, and profile independence

**Mechanical checks — PASS.** `resolve_pv_size_path` builds `{Label}_{int(kwp)}kWp_2023.csv`; all 18 (3 locations × 6 sizes) files exist and are distinct; `_data()` caches on `(location, tariff, pv_kwp)`, so no 4 kWp reuse is possible. Capex is `1109·pv_kwp + 890·battery_kwh` for all 4374 rows (max deviation 0.0), giving six distinct PV capex values £1109…£6654. PV-only annual saving rises monotonically with PV size at every location (e.g. Plymouth £215.8 → £954.0), proving the generation profile actually changes.

**Empirical independence — a substantive finding, reported as requested.** The six profiles for a given location are **exact linear rescalings of one another**, not independent data:
- Least-squares scale factor of profile *k* on profile 4 is **exactly k/4** to 6 decimals for all 15 location-size pairs.
- `max |profile_k − (k/4)·profile_4|` ≤ **8.9 × 10⁻¹⁶ kW** (float round-off).
- Per-kWp annual yield is identical across sizes to 0.1 kWh (Inverness 804.1, Manchester 858.6, Plymouth 1052.6 kWh/kWp for every size).
- Shape after normalising to unit annual energy is identical to ≤ 1.6 × 10⁻¹⁹.

**Diagnosis from the code.** `scripts/download_pvgis.py` varies only `peakpower` in the PVGIS `seriescalc` call; `loss=14`, tilt, azimuth, mounting and radiation database are fixed, and `seriescalc` applies no inverter or size-dependent model. PVGIS therefore returns `P` exactly proportional to `peakpower`. Peak PV even at Plymouth 6 kWp is 5.25 kW, so there is no clipping anywhere.

**Judgement:** the implementation satisfies A4 exactly as written, and this is *not* a bug. But §4.2's justification ("Merely rescaling capex while reusing the 4 kWp generation profile is invalid") is operationally vacuous — a `pv_kwp/4 × profile_4` implementation would produce **bit-identical** dispatch. The PV-size axis is a pure linear generation scaling with no inverter clipping at any size. This should be disclosed as a limitation rather than presented as six independently sourced profiles.

## Task 8 — A9, actual grid import vs gross-deficit approximation

**Derivation.** The MILP balance (`src/model.py`) is `pv + import + discharge = demand + export + charge`, with `p_charge/p_discharge` at the meter side (efficiencies live in the SOC dynamics). So `import − (demand − pv + charge − discharge)` must equal `p_export ≥ 0`, and ≈ 0 at top-import half-hours.

**Observed over all 110 700 events.**
- Max |residual| = **6.1 × 10⁻¹¹ kW**; max = +6.1e-11, min = −3.9e-13. **Zero** rows above 1e-6, **zero** balance violations. Energy balance holds to solver precision.
- Against the forbidden approximation `max(0, demand − pv − discharge)`: **22 154 of 110 700 events (20.0 %) differ**, by up to **5.00 kW** — exactly the grid-charging term the spec says must not be dropped. The retained column is demonstrably `p_import_kw`, not the approximation.
- Timestamps present and parseable on all rows; `peak_import_kw` and `peak_import_time` in the scenario table match the rank-1 event exactly (max dev 0.0) for all 4374 rows; `peak_reduction_kw` reconciles with `ref_peak − peak_import_kw` to 2.2e-15; 578 rows carry **negative** peak reduction (min −4.71 kW), correctly unclipped.
- Hand-derived synthetic-schedule check: my by-hand max/timestamp/p95/p99/threshold counts (5/3/3/2) and top-3 events with chronological tie-break match `peak_metrics` exactly.
- Note: the pickle cache retains peak summaries for **all 11 knots** (11 × 50 events per curve), satisfying §4.6's "for each exact solved knot"; the published CSV exports the fresh-battery knot only. No data is lost.

**PASS**

## Task 9 — A10, preservation of prior artefacts

`results/` is gitignored, so I used inode timestamps (ctime cannot be moved backwards by `touch`) plus hashes.

| File | mtime | ctime | sha256 (first 12) |
|---|---|---|---|
| `sweep_scenarios.csv` | 2026-07-21 20:38:47 | **2026-07-21 20:38:47** | `44cd946cbd3b` |
| `sweep_scenarios_capexv1.csv` | 2026-07-20 14:05:22 | **2026-07-21 19:09:36** | `4587100d006a` |
| `sweep_coarse_v2.csv` | 2026-07-25 01:37:04 | **2026-07-25 01:37:04** | `1082f3861b7c` |
| `sweep_peak_events_coarse_v2.csv` | 2026-07-25 01:37:04 | **2026-07-25 01:37:04** | `537d29196 5cf` |

The full experiment ran **2026-07-26 01:30 → 2026-07-27 14:05** (first/last v2 cache write), with the v2 CSVs at 15:13. Every protected file's **ctime predates the experiment window by 1–7 days**, which is positive evidence of no write, not merely no content change. The 108 pre-existing Spec-05 cache pickles all have ctimes of 20–21 July. Spec-06 wrote to disjoint paths throughout.

- **Overwrite refusal verified live:** `run_sweep.py` with existing `--out` exits with `error: output already exists: results/sweep_scenarios_v2.csv; pass --overwrite to replace it`.
- **Bonus — reproducibility proof.** I re-assembled the entire table from cache with zero solves (27 s) into a scratch path. Both outputs are **byte-identical (sha256 equal)** to the published `sweep_scenarios_v2.csv` and `sweep_peak_events_v2.csv`, and no new cache file was written (still 2160). The published artefacts are exactly what the cache implies — no manual editing, and assembly is deterministic. All four protected files were still hash-identical after my run.
- **Gap:** the run manifest required by §5 (commit, Python/CVXPY/solver versions, partitions, commands, cache counts, completion status) **does not exist** anywhere in the repo. `results/logs` holds only per-job stdout/stderr and `results/parts` only this machine's 128 job outputs.

**PASS on A10; the §5 manifest requirement is unmet.**

## Task 10 — Test suite

`.venv/bin/python -m pytest tests/ -q` → **112 passed in 17.57 s**, 0 failures, 0 errors. (A12.)

**PASS**

---

## Task 11 — Independent judgement

### 11a. The MILP is beaten by the rule-based controllers under the flat tariff

**What theory predicts.** At penalty = 0 the MILP objective *is* net operating cost, and any rules schedule is feasible for the MILP, so MILP saving must weakly dominate both heuristics in every cell.

**What I measured** (fresh-battery annual saving, 270 cells per comparison):

| tariff | `self_consumption` − MILP(0) | `self_consumption_tou` − MILP(0) | cells where a rule wins |
|---|---|---|---|
| agile | −353.1 … −15.4 (mean −111.2) | −259.2 … −15.8 (mean −93.5) | 0 |
| e7 | −418.9 … −11.3 (mean −100.9) | −264.8 … −7.9 (mean −57.4) | 0 |
| **flat** | 0.0 … **+39.24** (mean +5.24) | 0.0 … **+39.24** (mean +5.24) | **90 / 270** |

**Diagnosis from the code, confirmed by a direct annual solve.** `solve_year` runs a **daily** rolling horizon with `terminal_soc_daily=False`: each day is solved independently, SOC carried across midnight, and end-of-day stored energy has **zero value in the objective**. Under flat + matched export the prices are constant (Plymouth: import 0.2639, SEG export 0.1300), so the myopic day-optimum dumps leftover PV to export at £0.130/kWh rather than carrying it to displace next-morning import at £0.2639/kWh. The greedy self-consumption rule never exports from the battery, so it implicitly carries surplus overnight.

I solved Plymouth / flat / 6 kWp / 10 kWh directly (72 s, no cache):
- MILP saving £1225.8827, throughput 3150 kWh — **exactly reproducing the cached curve** (also an independent spot-check of the whole artefact chain).
- Rules saving £1265.1249, throughput 3840 kWh. Gap **£39.24/yr**.
- Mean end-of-day SOC: **MILP 0.115 vs rules 0.289**; in summer (May–Sep) **0.121 vs 0.394**. Mean energy carried past midnight 1.15 kWh (MILP) vs 2.89 kWh (rules). The mechanism is exactly as diagnosed.

**Verdict: expected consequence of the model design, but an undisclosed one.** The magnitude is small (max £39.24/yr; NPV impact mean +£32.9, max **+£345.5** in favour of the rules) and it is one-directional and confined to flat. `scripts/test_myopia.py` shows the authors know about it (`--tariff flat # where myopia bites hardest`), yet Spec 06 nowhere states it, and A6/§3 present the MILP as the optimising controller. **Any claim that the MILP is an optimality benchmark, or that rules capture "x % of optimal", is invalid under the flat tariff and must be qualified.**

### 11b. Saving-curve monotonicity

**Theory.** For fixed power and a daily terminal-SOC-free horizon, a larger capacity weakly dominates: any feasible energy trajectory `e(t)` for capacity `C` maps to `e(t) + 0.5(C′−C)` for `C′ > C`, which satisfies `[0.1C′, 0.9C′]` and the same initial/terminal levels with identical charge/discharge powers. So savings must be non-decreasing in usable energy.

**Measured across all 2160 curves.**

| controller | curves | non-monotone | max drop |
|---|---|---|---|
| milp | 1620 | 85 (5.2 %) | **£0.156/yr** |
| self_consumption | 270 | 0 | £0.000 |
| self_consumption_tou | 270 | 0 | £0.000 |

All violations are E7 (56, max £0.156) and flat (29, max £0.012), concentrated at the highest penalty (0.09) where the battery is near its indifference point and the MIP is massively degenerate. Worst case is a £0.156 dip on a curve spanning £10.91–£11.32 — **0.014 % of the annual saving, and ~5 orders of magnitude below the NPV differences the study reports.**

Similarly, MILP saving should be non-increasing in the throughput penalty; 522 of 1350 adjacent pairs show a nominal *increase*, but the largest is **£0.0058/yr**. Both patterns are HiGHS branch-and-bound tolerance on degenerate optima, not a modelling error. **Not a defect; the interpolated surrogate is unaffected at any material scale.**

### 11c. The optimum is on the grid boundary again — and no battery is NPV-positive

This is the finding I think most needs the authors' attention. Comparing every battery row to its matching PV-only reference:

- **Only 289 of 4320 battery rows beat their PV-only reference at all**, and the best margin over the entire 4374-row experiment is **+£157** — all under Agile/E7 with the *no-replacement* policy. Under flat, **never** (best is −£203.6).
- The global maximum-NPV row in the whole table is the **PV-only reference** (Plymouth / flat / 6 kWp, £7856.91). The best battery row is £7651.1, i.e. **£205.8 worse**.
- Marginal NPV vs PV-only is **monotone decreasing in battery size** (mean over 108 cells, best controller/penalty each): 0.5 kWh −£98.5, 1 kWh −£250.3, 2.5 kWh −£861.1, 5 kWh −£2161.5, 10 kWh −£5446.3.
- The best battery size is **0.5 kWh in 98 of 108 cells** (1.0 kWh in the other 10) — the *smallest* positive size on the grid.

**Diagnosis.** §6 records that the grid was expanded precisely because "2.5 kWh was both the best-performing and the *smallest* size tested, the optimum was on the grid boundary." That expansion cost ~322 serial compute-hours and **reproduced the same boundary condition one grid point lower.** The cause is structural, not sampling: `SweepDispatchProvider.capex` is `pv_kwp × 1109 + size_kwh × 890` — battery cost is **purely linear in kWh with no fixed component**, despite the comment "Battery cost already includes the hybrid inverter" (a 0.5 kWh pack therefore costs £445 *including* a hybrid inverter). With a linear price and negative marginal value, `argmax NPV` is always the smallest available size; adding sub-0.5 kWh points will move the boundary again, indefinitely. The correct reading is that the marginal battery kWh is NPV-negative at £890/kWh, so the economic optimum is **zero battery**.

To make that quantitative, I computed the implied break-even battery price (the £/kWh at which the best cell's NPV delta would reach zero):

| policy | 0.5 kWh | 1 kWh | 2.5 kWh | 5 kWh | 10 kWh |
|---|---|---|---|---|---|
| no-replacement | £1108 | £1047 | £929 | £783 | £586 |
| run-to-fade | £913 | £839 | £746 | £577 | £396 |

The assumed £890/kWh sits right on this knife edge at small sizes, which is exactly why the results are boundary-dominated. **Recommendation: re-specify capex as fixed + variable (`c_fixed + c_var·kWh`), which is both more realistic and would produce a genuine interior optimum; and report the break-even price surface, which is a far more robust dissertation result than a boundary "optimum".**

### 11d. Penalty-axis partial degeneracy

Distinct fresh-battery dispatch outcomes produced by the six solved penalty points, per cell:

| tariff | mean distinct savings | min | max |
|---|---|---|---|
| agile | 6.00 | 6 | 6 |
| flat | 4.48 | 3 | 6 |
| e7 | **3.13** | **2** | 4 |

For constant-spread tariffs the response is near bang-bang: the battery cycles fully while `2 × penalty < spread` and stops when it exceeds it. Mean fresh throughput collapses from 1144.6 → 1.6 kWh/yr for flat between 0.05 and 0.07 (predicted break-even ≈ 0.1339/2 = 0.067 ✓) and from 2412.8 → 2.8 kWh/yr for E7 between 0.07 and 0.09 (predicted ≈ 0.185/2 = 0.093 ✓). This is physically correct and the collapse points confirm the penalty is being applied as specified — but roughly half the MILP compute spent on flat and E7 produced near-duplicate dispatch. Not a correctness issue; a design note for any future run (the informative penalty range for flat/E7 is 0.05–0.09, and Agile is the only tariff needing all six points).

### 11e. Smaller observations

- **Residual asymmetry (by design, disclose it).** Run-to-fade receives a realised-life terminal residual on its replacement; no-replacement receives **zero** even though it still owns a working (SOH ≈ 0.26–0.60) pack at year 20. This follows §4.5 and A2 exactly, and it *understates* no-replacement, yet no-replacement still wins 86.8 % of cases (mean NPV £1421 vs £908). The conclusion is therefore robust to the asymmetry — worth saying so explicitly rather than leaving the reader to wonder.
- **Solver pinning resolved.** §9's open item is closed: `--solver` defaults to `SCIPY` in `run_sweep.py`, `launch.sh` passes `--solver SCIPY` explicitly, and `requirements-lock.txt` exists.
- **A5 cache separation positively demonstrated** (beyond the filename set diff): PV 4 vs 5 kWp at the same battery → £976.81 vs £1078.77; battery 5 vs 2.5 kWh at the same PV → £976.81 vs £856.52 with knot ranges 4.00 vs 2.00; penalty 0 vs 0.09 → £976.81 vs £863.42. No collisions possible.

---

## Overall verdict

**The Spec 06 experiment should be ACCEPTED on technical grounds.** Every falsifiable claim I could derive independently held exactly:

| Check | Derived | Observed | Result |
|---|---|---|---|
| Scenario rows (A3) | 4374, unique | 4374, 0 dups | PASS |
| Cached curves | 2160 (1620+540), exact filename set | 2160, 0 missing / 0 unexpected | PASS |
| Peak-event rows | 110 700 (2214 × 50) | 110 700, 2214 × 50 | PASS |
| E1–E4 (A1, A2) | −1666.6667 / −3333.3333 / −1431.2076 / −11866.6667 | all four, via both `compute_npv` and `fade_npv` | PASS |
| A7 0.5C fixed | exact | max dev 0.0; 11 capacities, 1 power | PASS |
| A8 no-replacement | SOH > 0, ≥ 0.08 floor, no reset/outflow | min SOH 0.26491 | PASS |
| A4 PV resolution | exact file + matching capex | exact; capex dev 0.0 | PASS |
| A9 peak balance | residual ≡ export ≥ 0 | max 6.1e-11 kW; 20 % differ from approximation | PASS |
| A10 preservation | untouched | ctime evidence + hashes; overwrite refused | PASS |
| A12 tests | — | 112 passed | PASS |
| Byte-reproducibility | — | zero-solve reassembly sha256-identical | PASS |

The zero-solve reassembly reproducing both published artefacts byte-for-byte, plus a fresh 72-second annual MILP solve reproducing a cached curve to the last penny, together give me high confidence the artefacts are exactly what the code and cache produce.

### What the authors got wrong or should investigate

1. **(Highest priority — scientific framing.)** The optimum sits on the lower grid boundary *again* (0.5 kWh in 98/108 cells), and **no battery configuration beats PV-only by more than £157** while the best row in the whole study is the PV-only reference. The cause is a **purely linear battery capex** (£890/kWh, zero fixed cost) despite the code commenting that it includes a hybrid inverter. Expanding the grid downward cannot fix this. Re-specify capex as fixed + variable, and lead with the break-even price surface (£1108/kWh at 0.5 kWh down to £396/kWh at 10 kWh) rather than an "optimal size".
2. **(Must disclose.)** The MILP is beaten by both naive rules controllers in **90/270 flat-tariff cells**, by up to **£39.24/yr** (NPV up to **+£345.5**), because `solve_year` is a *daily* horizon with free terminal SOC that dumps stored PV at the £0.130 export rate instead of carrying it to displace £0.2639 morning import. Confirmed by direct solve (mean summer end-of-day SOC 0.121 MILP vs 0.394 rules). Known internally via `scripts/test_myopia.py` but absent from Spec 06. Remove or qualify any "MILP = optimal benchmark" framing.
3. **(Limitation to state.)** The six PV profiles per location are **exact linear rescalings** of one another (LS scale exactly k/4, residual ≤ 8.9e-16), because `download_pvgis.py` varies only `peakpower`. §4.2's insistence that rescaling is "invalid" has no numerical effect. No inverter clipping is modelled at any PV size.
4. **(Spec compliance gap.)** The §5 **run manifest is missing** — no record of commit, solver/library versions, machine partitions, or completion status exists. A10 is still evidenced by ctimes and my byte-identical reassembly, but the required audit artefact should be written retrospectively while the information is recoverable.
5. **(Efficiency note, not a defect.)** The penalty axis is partially degenerate for E7 (mean 3.1 distinct outcomes from 6 solves, min 2) and flat (4.5); only Agile uses all six. Roughly half the flat/E7 MILP compute produced near-duplicate dispatch.
6. **(Benign, quantified.)** 85/1620 MILP curves are non-monotone in usable energy, max **£0.156/yr** (0.014 % of the curve's saving), and 522/1350 penalty pairs show saving rising with penalty by at most **£0.0058/yr**. Both are HiGHS tolerance on degenerate MIPs; theory guarantees monotonicity and the surrogate is unaffected at any material scale. No action needed beyond a footnote.
7. **(Worth a sentence.)** No-replacement is credited zero terminal residual while run-to-fade is credited one — per spec, and it *penalises* no-replacement, which still wins 86.8 % of cases. Stating this makes the policy comparison visibly conservative.
