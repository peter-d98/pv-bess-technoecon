# Spec 06 — Sweep & Economic Extensions

**Status:** Increments 1–7 implemented; full experiment run and independently verified
(2026-07-27), pending supervisor sign-off · **Owner:** — · **Depends on:** Specs 1–5 ·
**Blocks:** Spec 7 (figures) — **no longer blocking as of 2026-07-29**, see the capex
decision in [README.md](README.md)

Acceptance record and known deviations: [spec-06-verification.md](spec-06-verification.md).
Run provenance: [`docs/spec06_run_manifest.md`](../../docs/spec06_run_manifest.md).
Findings: [`docs/results_summary.md`](../../docs/results_summary.md).

## 1. Purpose

Extend the accepted parameter study to resolve the small-battery optimum, add PV size as
an explicit design axis, broaden the in-dispatch throughput-penalty sensitivity, revise
the treatment of replacements close to the analysis horizon, and report the grid peaks
left after dispatch.

Specs 01–05 remain the canonical record of the previously verified model. This spec
records only the changed behaviour and writes to new artefacts so both completed sweeps
remain reproducible.

## 2. Scope

### In scope
- Full replacement outflows plus a realised-life terminal residual credit (§4.1).
- Battery sizes `0, 0.5, 1, 2.5, 5, 10` kWh.
- PV sizes `1, 2, 3, 4, 5, 6` kWp, loaded from the existing location-and-size PV files.
- MILP throughput penalties `0, 1, 3, 5, 7, 9` p/kWh; every point uses 6000 EFC for
  cyclic capacity fade so the dispatch-price sensitivity is isolated.
- Nameplate charge/discharge power fixed at **0.5C** for each nominal battery size and
  held constant as its energy capacity fades (§4.4).
- A no-replacement sensitivity in which the original battery continues operating for
  the full 20-year horizon, including below 0.60 SOH (§4.5).
- Compact post-dispatch grid-peak data retained for later analysis (§4.6).
- A coarse smoke grid followed by the full factorial experiment.
- Versioned Spec-06 outputs and cache keys; no overwrite of previous sweep artefacts.

### Out of scope
- Rewriting or retrospectively changing accepted Specs 01–05.
- Changes to the MILP constraints, battery equations, tariff definitions, location set,
  controller algorithms, or capacity-fade law.
- Arbitrary interpolation between PV sizes. Only the four selected downloaded profiles
  are in scope.
- Outage simulation or unserved-energy/resilience claims. Grid import is available in
  every timestep, as in the existing model.
- Figures themselves. The versioned outputs feed a subsequent figures spec.

## 3. Confirmed inputs

| Axis | Values | Notes |
|---|---|---|
| Location | **Inverness, Manchester, Plymouth** | Glasgow omitted: prior results are similar to Manchester |
| Tariff | flat, E7, Agile | matched export, as Spec 05 |
| PV size | **1, 2, 3, 4, 5, 6 kWp** | select the matching on-disk profile |
| Battery size | **0, 0.5, 1, 2.5, 5, 10 kWh** | 0 = PV-only reference |
| MILP penalty | **0, 1, 3, 5, 7, 9 p/kWh** | all paired with 6000 EFC fade |
| Controller | MILP, self-consumption, self-consumption-ToU | rules remain penalty-free |
| Lifetime policy | run-to-fade-and-replace; no replacement | no-replacement retains the original battery for 20 years |

Battery power is `0.5 * nominal_capacity_kwh` kW across the size axis and remains at that
nameplate value as the battery fades. There is no shared inverter ceiling. All other
economic and fade assumptions retain the accepted Spec-05 defaults unless this spec
explicitly changes them.

## 4. Method

### 4.1 Full replacement outflows and realised-life residual

Let `N` be the analysis horizon, `L` the replacement battery's expected life (the
predecessor's realised life in a fade scenario), `C_r` the full replacement cost, and
`t_install` the installation time of the battery in service at the horizon. Each
replacement at `t_r = k * ceil(L) < N` is paid in full:

```
replacement_outflow(t_r) = C_r
```

At the end of year `N`, credit the unused fraction of the replacement battery's expected
life:

```
age_at_horizon = N - t_install
remaining_years = max(0, L - age_at_horizon)
terminal_residual = C_r * remaining_years / L
```

For fade-derived run-to-fade scenarios, `L` is the first battery's **realised life**
(`first replacement year - 1`), not its warranty life. This assumes the replacement has
the same expected service life as its predecessor under the scenario. If multiple
replacements occur, `t_install` is the latest replacement strictly before the horizon.
If no replacement occurs within the horizon, the original faded battery receives zero
terminal residual. The initial system capex and every replacement are full cash outflows;
the residual is a discounted benefit at year `N`.

`compute_npv` retains its generic `include_residual_value` setting and explicit
`terminal_residual_value` override. Fade callers use the override because only they know
the realised replacement schedule. The previous warranty-life residual basis is
superseded for Spec 06.

#### Hand-derived economic oracles

Unless stated otherwise, examples have zero annual saving, zero discount, replacement
factor 1, and £1,000 initial/replacement cost.

| Oracle | Horizon | Life | Full replacement outflows | Terminal residual | Expected NPV |
|---|---:|---:|---|---:|---:|
| E1 | 20 | 12 | year 12: `1000` | `1000 * 4/12 = 333.3333` | `-1666.6667` |
| E2 | 20 | 6 | years 6, 12, 18: `1000` each | `1000 * 4/6 = 666.6667` | `-3333.3333` |
| E3 | 20 | 12, 5% discount | year 12: `1000` | `1000 * 4/12` at year 20 | `-1000 - 1000/1.05^12 + (1000*4/12)/1.05^20` |
| E4 | 20 | realised 15, cost £8,900 | year 15: `8900` | `8900 * 10/15 = 5933.3333` | `-11866.6667` |

E4 is the controlling fade-policy example: replacement at year 15 means the replacement
is five years old at the year-20 horizon and has 10 of its expected 15 years unused.

### 4.2 PV-size axis

PV size changes both annual generation and initial PV capex. The provider must resolve
the exact `{location}_{pv_kwp}kWp_2023.csv` profile and compute PV capex as
`pv_kwp * pv_cost_per_kwp`. Merely rescaling capex while reusing the 4 kWp generation
profile is invalid.

PV size changes dispatch, so it must be part of both the in-memory curve key and the
disk-cache key. A cache generated for one PV size must never be returned for another.
Because power now scales with nominal battery size and does not decline with SOH,
battery size is **not** a free lookup within one shared curve. Each positive nominal size
requires its own `saving(current_capacity)` curve at its fixed nameplate power.

### 4.3 Degradation-penalty sensitivity

Each MILP penalty value changes dispatch and therefore requires its own saving curve.
All five penalty points use `cycle_life_efc=6000`; unlike the accepted Spec-05 axis, this
experiment does not couple a lower penalty to a longer cycle life. It isolates the
sensitivity of dispatch and NPV to the assumed marginal throughput price.

Rules controllers remain penalty-free and are not crossed with the penalty axis. Their
fade calculation continues to use 6000 EFC.

### 4.4 Constant-C-rate power model

For each positive nominal battery size `Q_nom`:

```
max_charge_kw = max_discharge_kw = 0.5 * Q_nom
```

This gives 1.25 kW at 2.5 kWh, 2.5 kW at 5 kWh, and 5 kW at 10 kWh. The rating is fixed
for that installed battery's life: if a 10 kWh battery fades to 6 kWh available energy,
its charge/discharge limit remains 5 kW. This represents a fixed nameplate power stage,
not an inverter whose rating shrinks with SOH. There is no 3 kW ceiling.

The accepted Spec-05 fixed-3-kW results remain the power-model sensitivity and are not
overwritten. The v2 cache identity must include nominal battery size and resolved power,
because two batteries with the same current usable energy can have different power
limits and therefore different dispatch outcomes.

### 4.5 Lifetime policies

Every battery scenario has two NPV readouts from the same dispatch surrogate:

1. **Run-to-fade-and-replace:** replace at the established 0.60 SOH floor and apply the
  full replacement outflow and realised-life terminal residual in §4.1.
2. **No replacement:** retain the original battery for all 20 years. Do not reset SOH,
  EFC, or calendar age when SOH crosses 0.60; charge no replacement outflow. Continue
  annual dispatch at the capacity produced by the existing additive fade law.

The no-replacement policy does not require a second full set of annual MILP solves if
each nominal-size curve includes the lower current-capacity range reached by year 20.
It does require additional low-SOH knots if the nine-knot replacement-policy curve does
not cover that range accurately. The implementation must determine the required range
conservatively, include the fresh-battery point exactly, and demonstrate convergence for
both policies. It must not use flat extrapolation below the lowest solved knot.

Implemented as `SweepGrid.min_soh_coverage` (0.08): every curve's lowest knot is
`0.08 * Q_nom * SOC-window` and its highest is the exact fresh-battery point. Each
policy's realised minimum SOH is checked against that floor and the scenario fails if it
falls below, so the clamped lookup can never act as flat extrapolation. The floor was
lowered from 0.15 when the expanded grid added sub-kWh packs: a 1 kWh pack at zero
penalty (Plymouth, 6 kWp, Agile) cycles at 641 EFC/yr, whose conservative 19-year
projection is `1 - 0.380 - 0.406 = 0.214` SOH.

The existing linear fade law can eventually produce zero or negative SOH because it was
originally coupled to replacement. If any v2 no-replacement trajectory reaches
`SOH <= 0` within 20 years, the scenario must fail with a clear validation error rather
than clip SOH or solve a battery with non-positive capacity. Such a result would require
a separate decision about a physical lower-bound fade model before the full experiment.
Operation below 0.60 SOH is an explicit sensitivity outside the battery's assumed useful
life and must be labelled accordingly; it is not a claim of warranty coverage.

### 4.6 Deferred grid-peak analysis and retained data

Peak analysis is deferred, but the expensive v2 dispatch runs must retain enough compact
data that selected peak questions do not require rerunning the sweep. This collection is
post-processing only: it does not alter the MILP objective, constraints, solve count, or
dispatch result.

The primary quantity is actual grid-served demand from the solved schedule:

```
grid_import_kw = schedule["p_import_kw"]
```

For each exact solved knot retain:
- maximum grid import (kW) and its first timestamp;
- 95th and 99th percentiles of grid import (kW);
- counts above **2, 3, 5, and 7 kW**; and
- the top 50 grid-import half-hours with timestamp, demand, PV, charge, and discharge.

Later analysis may report peak reduction against the matching no-battery case:
  `max(no_battery_grid_import_kw) - max(battery_grid_import_kw)`.

The matching no-battery profile must use the same location, tariff, PV size, and demand.
Negative peak reduction is valid and must not be clipped. Threshold interpretation,
histograms, time-of-day plots, lifetime interpolation, and dissertation conclusions are
deferred. The top-event records and fixed-threshold counts are compact retained evidence,
not a new sweep axis.

This metric is not `max(0, demand - pv - discharge)`: that expression ignores battery
charging and can differ from actual grid import. It is also not unserved load.

### 4.7 Experiment size, runtime, and staging

There are `3 * 3 * 6 = 54` location × tariff × PV cells. Each contains one PV-only row,
5 positive battery sizes × 6 MILP penalties, and 5 sizes × 2 rules controllers. With two
lifetime-policy readouts for every battery row, the final long table contains:

```
54 * (1 + 2 * (5*6 + 5*2)) = 4374 rows
```

PV size, penalty, and nominal battery size/power require distinct MILP curves:

```
3 locations * 3 tariffs * 6 PV sizes * 6 penalties * 5 battery sizes = 1620 curves
1620 curves * 11 knots = 17820 annual MILP solves
```

Rules controllers add `54 * 5 * 2 = 540` curves but no MILP solves. At the measured
~65 s per annual solve this is about **322 serial compute-hours**. The experiment is
staged as **324 MILP jobs** of `location × tariff × PV × penalty` (55 solves, ~1 h each)
plus 54 cheap rules jobs of `location × tariff × PV`.

324 divides exactly into **27 MILP jobs per machine across 12 machines**. Running 12
concurrent workers on a 14-core machine gives `27 * 55 * 65 s / 12 ≈ 2.2 h` ideal;
budget **3-4 hours** for imbalance, I/O, and setup. Each machine writes to its own
working copy of the cache; final assembly merges every curve into one **flat**
directory and rebuilds the table with zero solves.

> **As built (2026-07-27).** The launchers do not partition the 324 MILP jobs separately.
> They enumerate all **378** jobs (324 MILP + 54 rules, interleaved in nested
> location → PV → tariff → penalty order) and split `job_index mod 12`, giving **31–32
> jobs per machine** rather than 27 MILP + rules. The split is still disjoint and
> complete (oracle V14). Per-partition job and curve counts are in
> [`docs/spec06_run_manifest.md`](../../docs/spec06_run_manifest.md) §3.

## 5. Outputs and preservation

The following existing artefacts are immutable inputs to the audit trail and **MUST NOT
be overwritten**:
- `results/sweep_scenarios.csv`;
- `results/sweep_scenarios_capexv1.csv`; and
- existing `results/cache/` entries.

Spec 06 writes to new, explicit paths:
- `results/sweep_scenarios_v2.csv`;
- `results/sweep_peak_events_v2.csv`; and
- `results/cache/sweep_v2/`.

`sweep_scenarios_v2.csv` is the original **linear-capex** assembly and joins the
immutable list above. The capex re-specification of 2026-07-29 writes four further
assemblies from the same cache: `sweep_scenarios_v2_band.csv` (**adopted primary**,
band-observed £/kWh, built by `scripts/assemble_band_capex.py`) and
`sweep_scenarios_v2_{central,lowF,highF}.csv` (the fixed-plus-variable sensitivity).
These supersede `sweep_scenarios_v2.csv` for reporting but do not replace it — it is
retained as the conventional-literature sensitivity. All share
`sweep_peak_events_v2.csv`, since capex does not enter dispatch.

The increment-6 coarse experiment writes to separate paths so it can never be mistaken
for the full experiment or pollute its cache:
- `results/sweep_coarse_v2.csv`;
- `results/sweep_peak_events_coarse_v2.csv`; and
- `results/cache/sweep_v2_coarse/`.

Note that `results/` is listed in `.gitignore`, so none of these artefacts are version
controlled and `git status` can never evidence their preservation. The A10 non-overwrite
check must therefore be made with recorded file hashes, not with git.

Distributed workers use disjoint staging subdirectories such as
`results/cache/sweep_v2/machine_01/`. A run manifest records the commit, Python/CVXPY/
solver versions, assigned partitions, commands, cache counts, and completion status.

The runner must refuse to overwrite an existing output unless an explicit `--overwrite`
flag is supplied. Parallel workers write disjoint temporary result files; final assembly
must validate uniqueness of the scenario key before writing the combined CSV.

## 6. Incremental implementation

1. **Economic policy and tests:** implement E1–E4, retain residual plumbing with the
  realised-life basis in fade callers, and run `tests/test_economics.py` plus affected
  sweep tests.
2. **Sweep grids and power:** add the confirmed battery/penalty values and 0.5C nameplate
   power; verify cardinality, size-specific curve identity, and power held through fade.
3. **PV-size axis:** add exact profile resolution, PV-aware provider/cache keys and capex;
   verify all 12 selected location-size files exist and that yields/capex change with size.
4. **Lifetime policies:** add no-replacement trajectories, lower-capacity curve coverage,
   and positive-SOH validation; verify both policy readouts without duplicate MILP curves.
5. **Peak retention and outputs:** add pure post-processing summaries/top events and v2
   persistence; verify hand-derived schedules and non-overwrite behaviour.
6. **Coarse experiment:** Glasgow is excluded from the full grid but is retained as the
   familiar smoke cell: Agile, PV sizes 2/4/6, battery sizes 0/2.5/5/10, penalties
   0/5/7 p/kWh, MILP first, five knots for plumbing and nine for one convergence cell.
   Inspect schema, uniqueness, policy results, cache isolation, and peak retention.
7. **Full experiment:** partition the 324 MILP jobs across 12 machines (27 each), assemble
   once, and independently
   verify cardinality and sampled cells.

Each increment is independently reviewable and revertible. Implementation commits are
recommended at these boundaries, but this agent will not create commits without an
explicit request.

**Progress (2026-07-25):** increments 1–6 are implemented. The coarse Glasgow/Agile
experiment (PV 2/4/6 kWp, battery 0/2.5/5/10 kWh, penalties 0/5/7 p/kWh, all three
controllers, five knots) ran as three parallel PV-size partitions sharing one cache,
producing **93 rows** and **2400 peak events** from 45 cached curves (135 MILP solves,
~45 min wall-clock on three workers). Re-running the whole coarse grid against the merged
cache assembled the identical table in **4.2 s**, confirming zero-solve assembly. The
convergence cell (4 kWp / 10 kWh / 5p) gave a surrogate-vs-full-fade NPV gap of
**-1.16% at five knots and -0.34% at nine**, both at realised life 16.

Coarse findings: all 45 no-replacement trajectories stayed positive (min SOH 0.339) and
all fell below the 0.60 floor as intended; no-replacement out-performed run-to-fade on
average (mean NPV £1,623 vs £609); every top-NPV scenario used the 2.5 kWh pack; and
peak reduction was **negative for 10 kWh packs (down to -3.6 kW)** because 5 kW
Agile grid-charging stacks on top of household demand. Both previous sweep CSVs were
confirmed byte-identical by sha256 after the run.

**Grid expansion (2026-07-25):** because 2.5 kWh was both the best-performing and the
*smallest* size tested, the optimum was on the grid boundary. With 12 machines of 14
cores available, the axes were widened to PV `1-6` kWp, battery `0, 0.5, 1, 2.5, 5, 10`
kWh and penalties `0-9` p/kWh, at 11 knots and a 0.08 SOH floor. A worst-case cycling
probe (Plymouth / 6 kWp / Agile / zero penalty) confirmed both new extremes solve without
validation error: the 1 kWh pack reached 641 EFC/yr with no-replacement min SOH 0.324,
and a 15 kWh probe raised peak import from 5.14 to 10.30 kW. 15 kWh was subsequently
dropped to keep the battery axis at six points.

## 7. MUST acceptance criteria

- **A1:** E1–E4 match the hand-derived full replacement and residual cash-flow oracles.
- **A2:** Fade callers compute terminal residual from realised predecessor life and the
  latest replacement installation time, not warranty life; no-replacement receives zero.
- **A3:** The full grid has exactly 4374 unique scenario-policy rows under the confirmed
  axes.
- **A4:** Every PV scenario loads the matching location-and-size profile and uses matching
  PV capex; no 4 kWp profile is silently reused for another size.
- **A5:** Cache identity includes v2 namespace, location, tariff, PV size, nominal battery
  size, controller, penalty, and resolved 0.5C power. Cache-hit tests prove that different
  PV sizes and battery sizes cannot collide.
- **A6:** MILP rows contain exactly the six confirmed `(penalty, 6000 EFC)` pairs; rules
  rows remain penalty-free at 6000 EFC.
- **A7:** Power equals 0.5C for every nominal size and remains fixed as capacity fades.
- **A8:** Both lifetime policies reuse the same dispatch curves; no-replacement performs
  no SOH reset, charges no replacement outflow, covers every queried capacity with solved
  knots, and rejects any non-positive-SOH trajectory.
- **A9:** Retained peak summaries/top events match hand-derived schedules, retain timestamp
  information, and use actual `p_import_kw` rather than the gross-deficit approximation.
- **A10:** The runner does not modify either previous sweep CSV or the existing cache and
  refuses an accidental Spec-06 output overwrite without `--overwrite`.
- **A11:** A coarse end-to-end sweep completes with unique keys and finite NPV/peak outputs.
- **A12:** Existing accepted residual-policy tests and the new E1–E4 tests pass.

## 8. Independent verification gate

Before acceptance, an independent reviewer must:
- recompute E1–E4, the 4374-row cardinality, and 1620-curve count without being shown
  expected answers;
- inspect one profile per location and at least two PV sizes for correct file selection;
- prove cache separation for two PV sizes and two nominal battery sizes;
- verify 0.5C nameplate power remains fixed across at least one fade trajectory;
- inspect one no-replacement trajectory below 0.60 SOH and confirm no reset/outflow;
- recompute peak metrics from a small synthetic schedule;
- compare sampled Spec-06 scenarios against direct annual/full-fade runs; and
- confirm hashes or byte equality of the two previous sweep CSVs before and after the
  Spec-06 experiment.

## 9. Open decisions

- **Resolved (2026-07-25):** eleven knots is the final count. The coarse convergence cell
  gave a -0.34% surrogate-vs-full NPV gap at nine knots against -1.16% at five, so nine
  already met the 1% bar; eleven was adopted because the expanded grid widened the knot
  range to a 0.08 SOH floor, and the extra knots preserve interpolation accuracy over
  that wider range.
- **Partly resolved (2026-07-25):** every coarse no-replacement trajectory stayed at
  positive SOH (minimum 0.339), so the additive fade law needs no lower bound for
  Glasgow/Agile. The harness fails loudly rather than clipping, so the full experiment
  will surface any remaining location/tariff that violates this.
- **Resolved (2026-07-25):** the MILP solver was unpinned — CVXPY selected `SCIPY`
  (HiGHS) only because no SCIP or CBC was installed, so a machine with a different
  MIP-capable solver would have silently used it. Closed by adding `--solver` to both
  runners and passing `--solver SCIPY` on every one of the 378 jobs, plus
  `requirements-lock.txt` (see §10).
- **Resolved (2026-07-29):** battery capex is no longer a single pooled £/kWh. The blind
  gate raised it as open item 1. The adopted specification prices each size at the DESNZ
  median for its own capacity band; `F + c·Q` and the original linear £890/kWh are both
  retained as sensitivities. Recorded in [README.md](README.md), derived in
  [`docs/results_summary.md`](../../docs/results_summary.md). PV remains linear because
  its fitted fixed term is indistinguishable from zero. Two reporting consequences: the
  0.5 kWh size is dropped (below the lowest published band), and optimal battery size is
  reported as not identified, since the bands constrain average and not marginal price.

## 10. Decision record

- User superseded the proposed prorated-purchase convention: every replacement is paid
  in full, with unused replacement life credited at the horizon using the predecessor's
  realised life. Example: replacement at year 15, £8,900 cost, 20-year horizon gives
  `10/15 * £8,900 = £5,933.33` terminal residual (2026-07-24).
- Core power model confirmed as uncapped 0.5C nameplate power, fixed through fade;
  accepted Spec-05 fixed-3-kW results provide the sensitivity comparison (2026-07-24).
- No-replacement means one original battery operates for the entire 20-year horizon,
  including below 0.60 SOH; it does not switch to PV-only operation (2026-07-24).
- Peak analysis is deferred, but compact summaries, 2/3/5/7-kW counts, and top 50 events
  are retained during expensive v2 dispatch runs (2026-07-24).
- Grid expanded to 6 PV sizes, 6 battery sizes (0.5 kWh added, 15 kWh rejected to keep
  the axis at six points) and 6 penalties, at 11 knots and a 0.08 SOH coverage floor.
  Peak thresholds stay at 2/3/5/7 kW. Staged as 324 MILP jobs, 27 per machine across 12
  machines (2026-07-25).
- MILP solver pinned to `SCIPY` (HiGHS) via a `--solver` flag on both runners, and
  `requirements-lock.txt` added, so all 12 machines solve identically (2026-07-25).
