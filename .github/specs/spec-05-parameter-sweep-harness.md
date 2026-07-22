# Spec 05 — Parameter-Sweep Harness & Results Schema

**Status:** Implemented & accepted (2026-07-19) · **Owner:** — · **Depends on:** Specs 1 (NPV), 2 (degradation/fade), 3 (tariffs), 4 (locations) · **Blocks:** Spec 6 (figures)

## 1. Purpose

Produce the dissertation's central evidence: a **systematic techno-economic sweep**
over the study's controlled axes — UK location × tariff × battery size × in-dispatch
degradation penalty — plus an **operational-strategy (controller)** comparison that
quantifies the value of MILP dispatch over the simple heuristics a real household would
use. The output is a tidy, tabular results set that Spec 06 turns into the NPV frontier,
viability heatmaps, and tornado diagrams.

The MILP, tariff layer, degradation/fade model, NPV model, and location registry are all
**reused unchanged**. This spec adds an orchestration layer that (a) drives them across
the parameter grid efficiently and (b) records the results in a stable schema.

## 2. Scope

### In scope
- A pure-Python module `src/sweep.py`:
  - a `SweepGrid` config dataclass defining the axes and the fixed economic baseline;
  - a **`saving(E_usable)` surrogate** builder + evaluator (§4.1) that makes the sweep
    tractable under a run-to-fade baseline;
  - a run-to-fade evaluator that reads the surrogate instead of re-solving each year;
  - `run_sweep(...)` returning a tidy long-format `DataFrame`;
  - dependency-injected dispatch (solver kept out of the module, as in Spec 02), so the
    logic is unit-testable without CVXPY.
- A rules-controller module `src/controllers.py`:
  - `self_consumption` (charge PV surplus, discharge to demand, no grid charging);
  - `self_consumption_tou` (adds seasonal off-peak grid-charging, §4.3).
- A thin runner `scripts/run_sweep.py` with CLI subsetting (`--locations`, `--tariffs`,
  `--sizes`, `--penalties`, `--controllers`, `--n-knots`, `--quick`, `--validate`) and a
  disk curve-cache enabling **process-level** parallelism (launch disjoint subsets
  concurrently; a final full run assembles the table from cache with zero solves).
- A stable **results schema** written to `results/` (§4.5).
- Unit tests with hand-checkable oracles, plus a surrogate-vs-full-solve validation.

### Out of scope (handled elsewhere or deliberately excluded)
- **Changes to `src/model.py`, `src/battery.py`, `src/economics.py`,
  `src/degradation.py`, `src/tariffs.py`, `src/locations.py` logic** (project invariant).
  The harness imports and composes them; it does not modify them.
- **Figures / plots** → Spec 06. This spec emits *data*, not charts.
- **New tariffs, new locations, new fade laws.** The axes use only what Specs 2–4 provide.
- **Stochastic / robust optimisation, forecast error.** Deterministic sweep only.
- **The Agile-threshold sweep controller** from `run_rules_controller.py` — superseded by
  the confirmed `self_consumption_tou` controller (§4.3).

## 3. Inputs — the parameter grid

The **core cube is full-factorial** over four axes; a **controller axis** overlays it; and
a set of **free post-processing sensitivities** is layered on the baseline without extra
solves (§4.4).

| Axis | Values | Notes |
|---|---|---|
| Location | inverness, glasgow, manchester, plymouth | Spec 04 registry |
| Tariff (matched export) | flat, e7, agile | Spec 03 |
| Battery size (kWh nominal) | **0, 5, 10, 15** | 0 = PV-only reference (no battery) |
| Degradation scenario (penalty GBP/kWh @ cycle life) | **0p @ 6000 EFC, 7.42p @ 6000 EFC (derived), 5.56p @ 8000 EFC (derived)** | penalty changes MILP cycling → fresh curve; EFC sets the exogenous fade rate |
| Controller | **milp**, self_consumption, self_consumption_tou | rules are simulation-only |

Fixed baseline (held constant across the cube; varied only as free sensitivities):

| Quantity | Baseline | Free sensitivities |
|---|---|---|
| Max charge/discharge power | 3 kW | 5 kW (separate) |
| Replacement policy | **run-to-fade** (0.60 SOH floor, warranty residual) | forced-10-yr readout |
| Discount rate (real) | 5% | 3.5%, 7% |
| Price escalation (real) | 2%/yr | 0%, 4% |
| Calendar life | 10 yr | 13.5 yr |
| Fade form | linear | √t |
| SOC-max | 0.90 | 0.80 |
| Export | matched (13 p SEG / Agile) | 4.5 p untied, none |
| Horizon | 20 yr | — |

**Curve-building cost.** Only location, tariff, in-dispatch penalty, and power change the
MILP dispatch, so only they require a fresh surrogate curve. The core cube needs
`4 × 3 × 3 = 36` MILP curves (battery **size is free within a curve**; §4.1). At ~9 solves
per curve and ~60 s per solve that is ~324 solves ≈ ~1.4 h on 4–8 cores. Rules controllers
are simulation-only (penalty-independent), so their curves are effectively free.

## 4. Method

### 4.1 The `saving(E_usable)` surrogate (the tractability trick)

The annual dispatch outcome depends on the battery only through its **usable energy**
`E_usable = capacity_kwh · SOH · (soc_max − soc_min)` and its power (held at 3 kW). The
derived in-dispatch penalty `c_thr = cost_per_kWh / (cycle_life · 2)` is **independent of
nominal capacity**, so a single curve is valid across every nominal size *and* every fade
year for a given (location, tariff, penalty, power).

For each such cell:
1. Choose ~6–9 `E_usable` knots spanning the whole range the sweep will query — from just
   above 0 up to `max_size · (soc_max − soc_min)` (15 kWh → 12 kWh usable), covering both
   the size axis and the faded range of every pack.
2. Solve the annual rolling-horizon dispatch at each knot (set
   `capacity_kwh = E_usable / (soc_max − soc_min)`), recording **annual saving**,
   **throughput (kWh)**, and mean SOC.
3. Fit shape-preserving monotone interpolants `saving(E_usable)`,
   `throughput(E_usable)` (PCHIP), passing exactly through the knots.

`saving` is non-decreasing in `E_usable` (an optimiser with more usable energy does at
least as well) — a checkable invariant (§7 S2).

### 4.2 Run-to-fade evaluation via the surrogate
For a scenario (location, tariff, penalty, size, controller), the per-year saving stream
is generated exactly as `simulate_capacity_fade` (Spec 02) does — SOH from the additive
cycle+calendar law, capacity `= size · SOH`, replacement when SOH ≤ `soh_floor` — except
each year's `(saving, throughput)` come from **curve lookups**, not a fresh solve. The
stream feeds `compute_npv` (Spec 01) with the run-to-fade economics (warranty-residual
credit). Forced-10-yr NPV is a second, free readout on the same stream.

**Validation (per the confirmed decision):** for a small set of cells the surrogate-based
run-to-fade NPV is checked against the full `simulate_capacity_fade` (real 20 re-solves)
and must agree within tolerance (§7 S5); refining the knot grid must shrink the gap.

### 4.3 Rules controllers (`src/controllers.py`)
Both are half-hourly simulations with SOC continuity (rolling horizon), respecting power,
round-trip efficiency, and the SOC window — no optimisation.

**`self_consumption`** (net demand `= demand − pv`):
- net demand > 0 → discharge to meet it (within power/SOC);
- net demand < 0 (PV surplus) → charge from surplus (within power/SOC), export the rest;
- never grid-charge. (Upper/lower net-demand thresholds both = 0 kW.)

**`self_consumption_tou`** = `self_consumption` **plus** seasonal off-peak grid-charging:
- during the tariff's **low-price window**, grid-charge (cheapest-first) toward a
  **season-dependent target SOC**, then self-consume through the day;
- **season target fraction of usable capacity:** summer **0%**, spring **23%**,
  autumn **45%**, winter **70%** (meteorological seasons: spring Mar–May, summer Jun–Aug,
  autumn Sep–Nov, winter Dec–Feb);
- **target absolute SOC** `= soc_min + fraction · (soc_max − soc_min)` (e.g. winter
  `= 0.10 + 0.70 · 0.80 = 0.66`);
- **low-price window per tariff:** E7 → the night window (00:00–07:00); Agile → the
  cheapest half-hours of the day, taken cheapest-first until the target is reached;
  **flat → none (the ToU rule is inert; the controller reduces to `self_consumption`)**.

Rules-controller savings also fade with capacity, so they use the **same surrogate
machinery** (§4.1) with the dispatch provider being the simulation rather than the MILP;
this is cheap and keeps MILP and rules strictly comparable.

**Interpreting MILP vs rules.** The *reported* annual saving is energy-cost only (the
in-dispatch degradation penalty is excluded from `net_cost`, since wear enters lifetime
value via capacity fade — avoiding a double-count). The MILP minimises `energy_cost +
penalty · throughput`, so at a **non-zero** penalty it deliberately trades a little energy
saving for less throughput. Consequently a wear-blind rules controller can post a
*nominally higher annual saving* than the MILP for the same cell; the MILP still wins on
the true objective (lifetime NPV, where the deferred replacement is credited). "MILP ≥
rules" therefore holds strictly on annual saving only at **penalty = 0**, and on **NPV**
otherwise — this is the correct comparison and must be stated in the write-up.

**Stated limitation — daily-decomposition myopia (flat tariff).** `solve_year` optimises
the year one day at a time, carrying SOC forward but with **no terminal-SOC value** at the
midnight boundary (the established rolling-horizon method). The daily solve is therefore
optimal *within* each day but not *globally*: it will not hold PV surplus overnight to
offset the next morning's import, exporting it instead. Where inter-temporal **arbitrage**
exists (E7, Agile) this is immaterial and the MILP beats both heuristics comfortably. On
the **flat** tariff, however — where the only value is PV time-shifting, some of it across
midnight — the greedy `self_consumption` heuristic (which implicitly carries surplus
forward) can *exceed* the daily MILP's reported saving by a small margin (measured at
≤ ~£57/yr, < 1 % of operating cost; e.g. Glasgow/10 kWh: MILP £749 vs self-consumption
£777, even at 0 p penalty). This is an artefact of the daily decomposition, **not** an
economic result; it is documented as a limitation and does not affect the study's headline
(arbitrage-tariff) conclusions. A longer/overlapping horizon would remove it at higher
compute cost and is out of scope here.

### 4.4 Free post-processing sensitivities
Because the NPV, discount rate, escalation, fade form, calendar life, `soh_floor`, SOC-max,
and export choice either (a) act only on the already-built saving stream or (b) merely
rescale `E_usable` (SOC-max) — none requires a new solve. Each is emitted as a tornado row
against the baseline cell (Glasgow / Agile / 10 kWh / ~5 p / MILP unless noted).
> Note: export rate *does* affect dispatch; the 4.5 p / none export sensitivities are built
> as a small number of extra curves at the baseline cell only, not across the cube.

### 4.5 Results schema (tidy, long-format CSV)
`results/sweep_scenarios.csv` — one row per baseline scenario:

`location, tariff, export, battery_kwh, power_kw, deg_penalty_gbp_per_kwh, cycle_life_efc,
controller, discount_rate, price_escalation, policy, operating_cost_gbp,
grid_only_flat_cost_gbp, annual_saving_yr1_gbp, npv_gbp, bcr, realised_life_years,
replacement_years, min_soh, min_saving_gbp, efc_yr1, throughput_yr1_kwh, mean_soc,
capex_gbp`

- Rows: **size 0** → PV-only reference (`controller="none"`, `deg_penalty` + `cycle_life_efc`
  NaN), one per (location, tariff); **sizes {5,10,15}** → MILP crossed with the three
  degradation scenarios (0p @ 6000 EFC, 7.42p @ 6000 EFC, 5.56p @ 8000 EFC), and rules
  controllers {self_consumption, self_consumption_tou} with `deg_penalty` NaN (rules ignore
  the wear price) at the baseline 6000 EFC fade. Cardinality is fixed and hand-checkable
  (§7 S1).

`results/sweep_sensitivities.csv` — one row per tornado variation:
`scenario_id, sensitivity, value, npv_gbp, bcr` (baseline cell).

`results/sweep_fade_trajectories.csv` (optional) — `scenario_id, year, soh, saving_gbp,
efc, replaced` for the scenarios needing a per-year trace.

The saving curves are cached to `results/cache/` keyed by
(location, tariff, penalty, power, controller, year) so re-runs skip solving.

## 5. Interface

```python
# src/sweep.py
@dataclass(frozen=True)
class SweepGrid:
    locations: tuple[str, ...] = ("inverness", "glasgow", "manchester", "plymouth")
    tariffs: tuple[str, ...] = ("flat", "e7", "agile")
    sizes_kwh: tuple[float, ...] = (0.0, 5.0, 10.0, 15.0)
    # each entry is (penalty_spec, cycle_life_efc); None => derive penalty from that EFC
    deg_scenarios: tuple[tuple[float | None, float], ...] = (
        (0.0, 6000.0), (None, 6000.0), (None, 8000.0),
    )
    controllers: tuple[str, ...] = ("milp", "self_consumption", "self_consumption_tou")
    power_kw: float = 3.0
    export: str = "matched"
    n_knots: int = 7

@dataclass(frozen=True)
class SavingCurve:
    e_usable: np.ndarray            # knot abscissae (kWh usable)
    saving: np.ndarray              # GBP/yr at each knot
    throughput: np.ndarray          # kWh/yr at each knot
    def saving_at(self, e_usable) -> float: ...      # monotone PCHIP
    def throughput_at(self, e_usable) -> float: ...

def build_saving_curve(dispatch_year, e_usable_knots, soc_window, soc_max) -> SavingCurve: ...
def run_to_fade_stream(curve, size_kwh, soc_window, econ, deg) -> FadeResult: ...   # via simulate_capacity_fade
def fade_npv(fade, econ, warranty_years, replacement_capex) -> (NPVResult, realised_life, residual): ...
def run_sweep(grid, provider, econ_base, deg_base, *, cache_dir=None) -> pd.DataFrame: ...

# src/controllers.py
def self_consumption(data, battery) -> pd.DataFrame: ...            # half-hourly schedule
def self_consumption_tou(data, battery, tariff_name, rates) -> pd.DataFrame: ...
```

- `dispatch_year(capacity_kwh, soc_max) -> (saving, throughput_kwh, soc_series)` matches
  the Spec 02 injection signature, so MILP and rules controllers plug in identically.
- `dispatch_provider(location, tariff, penalty, controller) -> dispatch_year` supplies the
  right closure per cell (loading the location's data + tariff once, then varying capacity);
  `provider.resolve_penalty(penalty_spec, cycle_life_efc)` derives the in-dispatch penalty
  from the scenario's cycle life when `penalty_spec` is `None`.

## 6. Integration

- `scripts/run_sweep.py`: build a `SweepGrid` (optionally subset via CLI), wire the real
  MILP (`solve_dispatch` via the Stage 2 `solve_year`) and the rules controllers as a
  `SweepDispatchProvider`, run `run_sweep`, and write the §4.5 CSVs. Per-cell curves are
  cached to `results/cache/`; **parallelism is process-level** — run disjoint subsets
  (e.g. one process per location) concurrently, each writing distinct cache files, then a
  final full run reads every curve from cache. `--quick` runs a 1-location, 1-tariff smoke
  grid; `--validate` compares surrogate vs full-solve run-to-fade NPV.
- Reuses `src.data_loader.load_all`, `src.tariffs.build_tariff`, `src.locations`,
  `src.degradation`, `src.economics` unchanged.
- **No changes** to the invariant modules (§2).

## 7. Tests (`tests/test_sweep.py`, `tests/test_controllers.py`)

Hand-checkable oracles (compute independently; do not read module output). Use a **stub
dispatch** (a closed-form `saving`/`throughput` function of `E_usable`) so no CVXPY is
needed, mirroring Spec 02.

| # | Case | Expected |
|---|------|----------|
| S1 | default grid row count | 12 reference (size 0) + 108 MILP (4·3·3·3) + 72 rules (4·3·3·2) = **192** |
| S2 | `build_saving_curve` on a strictly-increasing stub | `saving_at` is non-decreasing; equals the stub exactly at every knot |
| S3 | `derive_throughput_penalty` at 5/10/15 kWh (cost 600, 6000 EFC) | all `0.05` (capacity-independent — the reuse premise) |
| S4 | size-0 scenario | saving = PV-only vs grid-only-flat; capex excludes the battery; no replacement |
| S5 | run-to-fade via surrogate vs full `simulate_capacity_fade` (stub dispatch) | NPV agree to within tolerance; refining knots shrinks the gap |
| S6 | `self_consumption_tou` winter/spring/autumn/summer target SOC (window 0.10–0.90) | 0.66 / 0.284 / 0.46 / 0.10 |
| S7 | `self_consumption` energy balance & bounds (random day) | per-step balance holds; SOC ∈ [soc_min, soc_max]; no grid charging |
| S8 | flat tariff + `self_consumption_tou` | identical schedule to `self_consumption` (ToU rule inert) |
| S9 | cache hit | a second `run_sweep` with a populated cache performs **zero** solves and returns identical numbers |

Plus: the full existing suite still passes; the invariant modules are unchanged (diff).

## 8. Acceptance criteria (MUST unless marked SHOULD)

- `src/sweep.py` and `src/controllers.py` implemented with the §5 interface; the invariant
  modules unchanged (diff).
- All S1–S9 oracles pass; full suite green.
- `run_sweep` produces `results/sweep_scenarios.csv` with the §4.5 schema and the exact
  §7-S1 cardinality for the default grid; sensitivities and (optional) fade trajectories
  written to their files.
- The surrogate run-to-fade NPV matches the full-solve `simulate_capacity_fade` within a
  stated tolerance on the validation cells (§4.2, S5); the validation set and tolerance are
  recorded in `spec-05-verification.md`.
- The Glasgow / Agile / 10 kWh / ~5 p / MILP row reproduces the Spec 03–04 baseline NPV
  (run-to-fade ≈ −£4,040, BCR ≈ 0.784) as a cross-check (SHOULD, within surrogate tol).
- Rules controllers satisfy per-step energy balance and SOC bounds (S7) and the seasonal
  ToU targets (S6); flat + ToU reduces to self-consumption (S8).
- The run is reproducible (fixed solver, cached curves; S9) and parallel-safe.
- Independent verification per `spec-05-verification.md` completed.

*(Recorded sweep results live in §10 (Results), not in the acceptance criteria.)*

## 9. Open decisions

*(All four resolved — user-confirmed 2026-07-20.)*

- **Agile low-price window sizing** (§4.3): cheapest-first until the seasonal target is
  reached — **CONFIRMED**. Alternative — a fixed 7 h cheapest-contiguous block to mirror E7 —
  is a possible sensitivity. **CONFIRM** the cheapest-first rule during write-up.
- **Season boundaries:** meteorological — **CONFIRMED**. Astronomical is an alternative;
  unlikely to matter materially.
- **Sensitivity baseline cell:** Glasgow / Agile / 10 kWh / ~5 p / MILP — **CONFIRMED** for
  the tornado (may be revisited once results are in). All tornado rows use the run-to-fade
  baseline, not forced replacement.
- **Knot count / placement** (§4.1): **finalised at 9** (denser at the low-`E_usable` end
  via `lin**1.5` spacing). Validated (§4.2) on two diverse cells: Glasgow/Agile/10 kWh
  surrogate NPV within **0.6% (7 knots) → 0.2% (11 knots)** of the full 20-solve NPV, and
  the Glasgow/E7 deep-cycling stress case within **0.25% (9 knots) → 0.11% (13 knots)** —
  both gaps shrink monotonically, so the 9-knot default sits comfortably inside the ≤1%
  tolerance. Both full-fade NPVs reproduce the Spec 03–04 baselines exactly.

## 10. Results (first full sweep, 2026-07-21)

`results/sweep_scenarios.csv` — 192 rows, 24 columns (12 PV-only refs + 108 MILP + 72
rules), whole-system framing, run-to-fade policy, £13,336 baseline capex (£1,109/kWp PV,
£890/kWh battery incl. inverter). **Baseline cross-check:** Glasgow / Agile / 10 kWh /
7.42 p (6000 EFC) / MILP → annual saving £871.47/yr, NPV **−£2,196.40** (BCR 0.871,
realised life 18 yr) — within £9 (0.4 %) of the direct run-to-fade baseline −£2,186.93,
i.e. inside the 9-knot surrogate tolerance.

**Finding 1 — PV alone is now viable everywhere; the battery is a drag on NPV.** All 12
PV-only (size-0) cells have **positive** whole-system NPV (Plymouth/flat +£6,171 down to
Inverness/E7 +£1,800). Adding storage *reduces* whole-system NPV monotonically with size
in every cell (e.g. Glasgow/Agile: PV-only +£4,196 → +5 kWh +£1,680 → +10 kWh −£2,196), so
the **incremental** battery value is negative throughout — the battery never pays for
itself, though a small pack keeps the *whole system* net-positive in the better cells. The
best nonzero size is always the smallest (5 kWh); the 10 kWh baseline is over-sized.

**Finding 2 — viability heatmap (best-size = 5 kWh, MILP @ 7.42 p).** 8 of 12
location×tariff cells stay net-positive whole-system:

| NPV (£) | flat | E7 | Agile |
|---|---|---|---|
| Plymouth | +1,727 | +2,428 | +2,681 |
| Manchester | −168 | +313 | +1,816 |
| Glasgow | −473 | +177 | +1,680 |
| Inverness | −697 | −474 | +606 |

Tariff rank **Agile > E7 > flat**; location rank **Plymouth > Manchester > Glasgow >
Inverness** (PV-yield ordered). Flat + the northern cells are the losers. Overall best
battery cell: Plymouth / Agile / 5 kWh / 5.56 p → NPV **+£3,028** (BCR 1.28).

**Finding 3 — MILP beats rules on E7/Agile; the flat-tariff daily-myopia caveat (§4.3)
bites.** At 5 kWh Glasgow, MILP@7.42p vs self-consumption: E7 +£177 vs −£757, Agile +£1,680
vs +£835 — MILP wins comfortably where arbitrage exists. On **flat**, MILP (−£473) trails
self-consumption (+£36): the daily-decomposition myopia (§4.3), a <1 % effect confined to
the arbitrage-free flat tariff.

**Finding 4 — the degradation penalty shapes cycling and replacement timing.** Glasgow /
Agile / 10 kWh MILP across the axis: 0 p → 359 EFC/yr, replace yr 14, NPV −£2,446; 7.42 p
(6000 EFC) → 103 EFC/yr, replace yr 18, NPV −£2,196; 5.56 p (8000 EFC) → 145 EFC/yr,
replace yr 18, NPV −£1,485. The wear price cuts year-1 cycling ~3.5× and defers replacement
14→18 yr; the optimistic (8000 EFC) scenario has the best NPV — a lower penalty permits more
saving while the slower fade preserves the deferred replacement.

## 11. References

Inherits the Spec 1–4 citations (NPV assumptions, LFP fade parameters, tariff rates,
location data). No new external data is introduced. Season-dependent ToU charging fractions
(0/23/45/70 %) are a modelling assumption to be justified in the dissertation (winter PV
shortfall compensation).
