# Spec 02 — Unified Degradation & Capacity-Fade Model

**Status:** Implemented (core) · SOC-max tradeoff deferred (§9) · **Owner:** — · **Depends on:** Spec 01 (NPV) · **Blocks:** Specs 5, 6

## 1. Purpose

Give the lifetime NPV a physically-grounded battery-ageing basis. One battery-life
dataset (capex, cycle life, calendar life, end-of-life threshold) drives **both**:

1. the **in-dispatch throughput penalty** (£/kWh cycled) used by the MILP, derived
   rather than assumed; and
2. the **exogenous capacity fade** applied across the rolling horizon, which makes
   the achievable annual saving decline as the battery ages and triggers
   replacement — feeding a per-year saving **stream** into `compute_npv`
   (the coupling point Spec 01 already exposes).

It also adds two post-hoc analyses that are cheap because they run on the already-solved
SOC trajectory: **SOC-exposure characterisation** and an **upper-SOC-bound (90 % vs 80 %)
life-vs-usable-capacity-vs-NPV tradeoff**.

## 2. Scope

### In scope
- A pure-Python module `src/degradation.py`:
  - derive the throughput penalty from `capex / lifetime throughput`;
  - equivalent-full-cycle (EFC) counting;
  - a two-component additive capacity-fade law (cycle + calendar), linear baseline
    with a √t calendar sensitivity;
  - a lifetime simulation (dependency-injected dispatch) that returns the per-year
    saving stream, SOH trajectory, effective battery life, and replacement years;
  - SOC-exposure metrics.
- Runner orchestration in `scripts/run_stage2.py` (or a thin new runner) that wires
  the real annual dispatch into the lifetime simulation and reports fade-adjusted NPV.
- The upper-SOC-bound tradeoff as a small sweep over `soc_max ∈ {0.90, 0.80}`.
- Unit tests with hand-checkable numeric oracles.

### Out of scope (handled elsewhere or deliberately excluded)
- **Rainflow / DoD-dependent cycle ageing.** Deliberately excluded: for LFP, fade is
  throughput-driven and largely DoD-independent (Wang et al. 2011), so rainflow would
  merely re-derive that result. Embedding rainflow in the MILP (non-convex) is also out.
- **Cell-temperature modelling / thermal dynamics.** A constant reference temperature
  (~25 °C) is assumed — this is the temperature at which the cycle- and calendar-life
  figures are themselves quoted, so it is made explicit, not newly introduced. An
  optional temperature sensitivity scales `N_EoL`/`T_cal`; no thermal model is built.
- **SOC-holding penalty inside the MILP** (Level C). Excluded: scope creep, and a
  daily-cycling domestic system does not dwell at high SOC long enough for it to matter.
- **Changes to `src/model.py` or `src/battery.py`.** Fade is applied by *rebuilding*
  `BatteryParams` per year with a reduced `capacity_kwh`; the dispatch formulation is
  untouched (project invariant).
- Tariff construction → Spec 03. Multi-location → Spec 04. Sweep harness → Spec 05.
  Figures → Spec 06.

## 3. Inputs

```python
@dataclass(frozen=True)
class DegradationParams:
    # End-of-life / fade budget
    soh_eol: float = 0.80            # replace at 80% of nominal capacity
    cycle_life_efc: float = 6000.0   # equivalent full cycles to reach soh_eol (LFP)
    calendar_life_years: float = 13.5  # calendar years to reach soh_eol at reference T

    # Fade-law selection
    calendar_form: str = "linear"    # "linear" (baseline) or "sqrt" (sensitivity)

    # SOC-dependent calendar fade (Level B sensitivity; OFF by default)
    soc_dependent_calendar: bool = False
    soc_ref: float = 0.50            # reference SOC for the stress multiplier
    soc_stress_beta: float = 0.0     # 0 => SOC-independent; >0 => higher SOC ages faster
                                     # NOTE: coefficient is a citation dependency (Naumann et al.)

    # Replacement policy (see §4.5)
    replace_at_eol: bool = True      # sensitivity: replace the year SOH reaches soh_eol
    soh_floor: float = 0.0           # run-to-fade base case: replace only if SOH reaches this
```

Two conventions are fixed and shared with the existing code:

- **EFC basis = nominal capacity.** One EFC = `2 × capacity_kwh` of summed
  charge+discharge throughput, matching the existing `run_stage2` reporting line
  `throughput / (2 × capacity_kwh)`. `capacity_kwh` is the nominal nameplate; the
  usable energy is `capacity_kwh × (soc_max − soc_min)` via the SOC window.
- **Power rating is held constant** as the battery ages (only *energy* capacity
  fades). Stated as a modelling choice.

## 4. Method

### 4.1 Throughput penalty derivation

Spread the battery capex over the summed charge+discharge throughput it can deliver
before end-of-life, on the **nominal (EFC)** basis so it is consistent with the fade
accounting:

```
lifetime_throughput_kwh = cycle_life_efc * 2 * capacity_kwh_nominal
c_thr = battery_capex / lifetime_throughput_kwh          # GBP per kWh (charge+discharge)
```

**Function oracle (O1):** `battery_capex = 4000`, `cycle_life_efc = 6000`,
`capacity_kwh = 10` → `lifetime_throughput = 120,000 kWh` →
`c_thr = 0.033333 GBP/kWh ≈ 3.33 p/kWh`. This is a fixed-input **unit-test** oracle,
not the baseline.

**Baseline penalty:** with the whole-system baseline (`battery_capex = 8900`
= 10 kWh × £890/kWh incl. hybrid inverter, `cycle_life_efc = 6000`, `capacity_kwh = 10`) →
`lifetime_throughput = 120,000 kWh` → `c_thr = 0.074167 GBP/kWh ≈ 7.42 p/kWh` (the
pessimistic degradation axis point). The optimistic point uses `cycle_life_efc = 8000`
→ `c_thr = 0.055625 GBP/kWh ≈ 5.56 p/kWh`; a **zero-penalty** case (wear ignored in
dispatch) is the third axis point. The derived penalty is thus **physically grounded**
in the replacement cost rather than an assumed placeholder. (A *usable*-basis convention
— `2 × usable_kwh` in the denominator — would raise it; the nominal basis is chosen for
consistency with EFC counting.)

### 4.2 Equivalent full cycles

```
EFC(period) = throughput_kwh(period) / (2 * capacity_kwh_nominal)
```
where `throughput_kwh = Σ (p_charge + p_discharge) · Δt`. Accumulated across years.

### 4.3 Cycle fade (linear in EFC)

```
phi_cyc(EFC_cum) = (1 - soh_eol) * EFC_cum / cycle_life_efc
```
Baseline: `(1 - 0.80)/6000 = 3.333e-5` capacity loss per EFC.

### 4.4 Calendar fade

Baseline (linear) and sensitivity (√t), both reaching the full `(1 - soh_eol)` budget
at `t = calendar_life_years` when acting alone:

```
phi_cal(t) = (1 - soh_eol) * (t / calendar_life_years)             # calendar_form="linear"
phi_cal(t) = (1 - soh_eol) * sqrt(t / calendar_life_years)         # calendar_form="sqrt"
```

**SOC-dependent variant (Level B sensitivity, `soc_dependent_calendar=True`):** scale
the calendar term by a time-weighted SOC stress multiplier from that year's trajectory:

```
S̄ = mean_t exp(soc_stress_beta * (soc_t - soc_ref))       # = 1 when beta = 0
phi_cal_soc = phi_cal(t) * S̄
```
`soc_stress_beta` and the functional form are a **citation dependency** (Naumann et al.);
default `beta = 0` recovers the SOC-independent baseline exactly.

### 4.5 State of health & replacement (additive superposition)

```
SOH(t) = 1 - phi_cyc(EFC_cum) - phi_cal(t_since_install)
```

`soh_eol` is the **fade-curve anchor**: `cycle_life_efc` and `calendar_life_years`
are defined as the EFC / years at which each term alone consumes the full
`(1 - soh_eol)` budget. It is **decoupled from the replacement decision**, which is
governed by policy:

- **Run-to-fade (base case, `replace_at_eol=False`).** The battery is *not* replaced
  at `soh_eol`; it keeps operating at faded capacity, delivering a **declining**
  saving stream. A replacement is forced only if start-of-year SOH falls to the hard
  `soh_floor` (baseline **0.60**, a literature-defensible end-of-life threshold). This
  reflects realistic homeowner behaviour (a working, gently-faded battery is not
  scrapped on a fixed date) and is the only regime in which degradation-aware dispatch
  can affect replacement timing (less throughput → slower fade → later/absent floor
  crossing).
- **Forced replacement (sensitivity, `replace_at_eol=True`).** The battery is replaced
  the year SOH reaches `soh_eol` — a fixed end-of-life trigger, retained as a
  pessimistic bound.

On replacement, SOH resets to 1, the EFC counter and calendar clock reset, and a
replacement capex lands in the NPV that year. Because the budget is shared additively,
combined cycling + calendar reaches a given SOH **sooner** than either figure alone.
The battery (incl. inverter) in service at the horizon end is credited its **unconsumed
warranty value** — straight-line over the warranty life, discounted (§4.6) — rather than
assumed worthless; a unit older than its warranty contributes zero.

Usable capacity handed to the next annual dispatch:
```
capacity_kwh_year = capacity_kwh_nominal * SOH_start_of_year
```
(fed to `BatteryParams`; the 10–90 % SOC window then applies to the faded nominal).

### 4.6 Lifetime simulation & NPV coupling

`simulate_capacity_fade` takes an **injected** dispatch callable so it stays pure and
testable (the runner supplies the real `solve_year`; tests supply a stub):

```
dispatch_year(capacity_kwh, soc_max) -> (annual_saving, throughput_kwh, soc_series)
```

Per year `t = 1 … horizon`:
1. `SOH_t` from accumulated EFC + calendar time (§4.5); if `≤` the replacement
   threshold (§4.5: `soh_eol` if `replace_at_eol`, else `soh_floor`), replace first.
2. `capacity_kwh_year = capacity_kwh_nominal * SOH_t`.
3. `saving_t, throughput_t, soc_t = dispatch_year(capacity_kwh_year, soc_max)`.
4. Accumulate EFC from `throughput_t`; advance calendar clock.

Returns: `saving_stream` (length `horizon`), `soh_trajectory`, `effective_life_years`
(first year SOH crosses `soh_eol` under year-1 usage — a **diagnostic** extrapolation),
and `replacement_years`.

**Coupling to Spec 01:** the runner passes `saving_stream` to
`compute_npv(saving_stream, econ)` and sets `econ.battery_life_years` to the **realised**
replacement life from the simulation (`replacement_years[0] − 1`, or `> horizon` if the
battery is never replaced), so the NPV replacement schedule aligns with the fade
trajectory actually valued — *not* the year-1 EFC extrapolation `effective_life_years`,
which is kept only as a diagnostic. The battery (incl. inverter) running in the final year is
credited its unconsumed **warranty** value via `compute_npv`'s `terminal_residual_value`
override: `remaining_frac = max(0, (warranty − age)/warranty)` × replacement capex, where
`age = horizon − install_year` and `install_year` is the latest NPV replacement booking
(`k·ceil(realised_life)`, or 0 if never replaced). This is discounted to the horizon
end inside `compute_npv`. `compute_npv` gains only this one optional argument; its core
is otherwise unchanged (the per-year-stream interface was built for exactly this).

Price escalation stays in the NPV cash-flow layer, **not** inside the dispatch: a uniform
real escalation scales all prices equally and does not change the optimal schedule, so
each year is dispatched on the true 2023 relative prices. Reusing the single 2023 profile
across the horizon (only capacity varies year to year) is a stated **representative-year**
simplification — inter-annual weather/demand/price variability is not captured.

### 4.7 SOC-exposure metrics (Level A, baseline)

Post-hoc on a solved trajectory:
```
soc_exposure(soc_series, dt_hours, thresholds=(0.8,)) -> {
    "mean_soc", "time_weighted_mean_soc",
    "frac_time_above": {thr: fraction, ...},
    "histogram": DataFrame(soc_bin, hours),
}
```
Descriptive only; not fed back into control.

### 4.8 Upper-SOC-bound tradeoff (baseline analysis)

Run the full pipeline at `soc_max ∈ {0.90, 0.80}` and compare effective life, usable
capacity, annual saving, and NPV. Tightening the cap trades usable capacity against
calendar life; this quantifies whether the standard 90 % cap is NPV-optimal.

## 5. Interface

New module `src/degradation.py`:

```python
@dataclass(frozen=True)
class DegradationParams: ...        # §3

def derive_throughput_penalty(battery_capex: float, capacity_kwh: float,
                              params: DegradationParams) -> float: ...        # £/kWh

def equivalent_full_cycles(throughput_kwh: float, capacity_kwh: float) -> float: ...

def capacity_fade(efc_cumulative: float, years_elapsed: float,
                  params: DegradationParams,
                  soc_stress_multiplier: float = 1.0) -> float: ...           # returns SOH

def simulate_capacity_fade(dispatch_year, capacity_kwh_nominal: float, soc_max: float,
                           horizon_years: int, params: DegradationParams
                           ) -> FadeResult: ...

def soc_exposure(soc_series, dt_hours: float, thresholds=(0.8,)) -> dict: ...
```

```python
@dataclass
class FadeResult:
    saving_stream: list[float]       # length horizon_years
    soh_trajectory: list[float]      # SOH at the start of each year
    effective_life_years: float
    replacement_years: list[int]
    efc_per_year: list[float]
```

## 6. Integration

- `scripts/run_stage2.py`: after the single-capacity solve, run
  `simulate_capacity_fade` with a `dispatch_year` closure wrapping `solve_year` +
  `battery_annual_costs` + the framing counterfactual (grid-only for whole-system,
  PV-only for battery-marginal), then feed `saving_stream` and the realised replacement
  life into the existing NPV block. Report fade-adjusted NPV **alongside** (not replacing)
  the flat-saving NPV, so the effect of fade is visible.
- Default in-dispatch `degradation_cost_per_kwh` sourced from
  `derive_throughput_penalty(...)` (≈ 7.42 p/kWh at the whole-system baseline); overridable
  via `--deg-cost`.
- SOC-exposure metrics are printed. The `soc_max` (90 % vs 80 %) tradeoff is **deferred**
  (see §9) — not yet built.
- **No changes** to `src/model.py` or `src/battery.py`.

## 7. Tests (`tests/test_degradation.py`)

Hand-checkable oracles (compute independently, do not read from the module):

| # | Case | Expected |
|---|------|----------|
| O1 | `derive_throughput_penalty(4000, 10, default)` | `0.0333333 GBP/kWh` |
| O2 | `equivalent_full_cycles(120000, 10)` | `6000.0` |
| O3 | `capacity_fade(EFC=300, years=1, linear, T_cal=13.5)` | `phi_cyc=0.01`, `phi_cal=0.0148148`, `SOH=0.9751852` |
| O4 | Pure calendar (EFC=0), linear → SOH at t=13.5 | `SOH = 0.80` exactly |
| O5 | Pure cycling (years=0) at EFC=6000 | `SOH = 0.80` exactly |
| O6 | Combined 300 EFC/yr + linear calendar: annual Δfade | `0.0248148/yr` → effective life ≈ `8.06 yr` |
| O7 | √t calendar at t=1, T_cal=13.5 | `phi_cal = 0.20·√(1/13.5) = 0.0544331` |
| O8 | `soc_stress_beta=0` | SOC-dependent result equals SOC-independent (multiplier = 1) |
| O9 | `battery_capex=0` | `c_thr = 0` |
| O10 | `simulate_capacity_fade` with stub dispatch (const throughput → life 12 yr, saving 150) | saving stream constant-per-generation, `effective_life=12`, `replacement_years=[13]` (a fresh battery starts operating in year 13, after 12 years of service) |
| O11 | `soc_exposure` on constant `soc=0.5` day | `mean_soc=0.5`, `frac_time_above(0.8)=0.0` |

Plus: the full suite (51 tests) passes.

## 8. Acceptance criteria (MUST unless marked SHOULD)

- `src/degradation.py` implemented with the §5 interface.
- All O1–O11 oracles pass; the full test suite is green (51 tests: 48 pre-existing +
  3 run-to-fade policy tests).
- `run_stage2.py` reports fade-adjusted NPV alongside the flat NPV for baseline Glasgow,
  with the derived throughput penalty, and prints SOC-exposure metrics.
- Baseline NPVs recorded (whole-system Glasgow, general-cost baseline): flat-saving NPV
  ≈ −£9,587 (forced replacement at 10 yr); run-to-fade fade-adjusted NPV ≈ −£6,425
  (BCR 0.656; 0.60 SOH floor; one replacement at year 18; min SOH 0.601; year-1 → min
  saving £777.71 → £739.52; warranty-residual credit £5,320 at the horizon). **Monotonicity
  note:** run-to-fade is *higher* (less negative) than the flat NPV because it defers
  replacement and credits the residual — they use different replacement policies and are
  not directly comparable. The valid ageing-monotonicity check is that the
  **forced-replacement** fade sensitivity (`--replace-at-eol`) is ≤ the constant-saving
  NPV at the same 10-yr life.
  > **Superseded (capex overhaul, 2026-07-21):** the figures above were recorded under the
  > retired £600/kWh general-cost baseline (£15,360 whole-system capex, derived 5 p/kWh).
  > They remain the accepted Spec 02 record. Under the current baseline (£890/kWh battery
  > incl. inverter, £1,109/kWp PV → £13,336 whole-system capex, derived 7.42 p/kWh) the
  > Glasgow/Agile run-to-fade NPV is −£2,186.93 (annual saving £871.48/yr); the full
  > tariff-axis figures are regenerated by the Spec 05 sweep.
- `src/model.py` and `src/battery.py` unchanged (verified by diff).
- Independent verification per `spec-02-verification.md` completed.
- **SHOULD (deferred):** upper-SOC-bound tradeoff (90 % vs 80 %) comparison table/plot —
  not yet built; parked in §9 as it is an analysis output, not a dependency of Spec 03.

## 9. Open decisions

- **Penalty basis (nominal vs usable):** RESOLVED — nominal, for consistency with EFC
  counting. Usable basis retained only as a documented alternative (~6.25 p/kWh).
- **`calendar_life_years`:** the module default is 13.5 (midpoint of the 12–15 yr range),
  but the **baseline uses 10 yr** (matching the battery warranty), set via
  `--calendar-life-years`. Citation for the 10 yr / 6000 EFC → 80 % pairing still pending.
- **SOC stress coefficient (`soc_stress_beta`) and form:** citation dependency (Naumann
  et al.); default 0 (SOC-independent) until sourced. Level B is a sensitivity only.
- **Derived penalty (RESOLVED):** the whole-system baseline (£8900 battery incl. inverter,
  6000 EFC, 10 kWh) derives **7.42 p/kWh** (pessimistic); the optimistic axis point uses
  8000 EFC → 5.56 p/kWh, and a zero-penalty case is the third axis point. The derived
  penalty *is* the baseline (the earlier 3.3 p/kWh / 5 p/kWh figures came from the retired
  £4000 battery-marginal and £600/kWh capex assumptions).
- **End-of-horizon replacement (RESOLVED):** under run-to-fade the `soh_floor` can trigger
  a replacement late in the horizon (near PV end-of-life), so a fresh battery (incl. inverter)
  captures little service. Rather than suppress such replacements with an arbitrary
  "no-replace-in-last-K-years" rule, the unconsumed **warranty** value of the in-service
  unit is **credited back** to the NPV (straight-line over warranty life, discounted; §4.6).
  This is more defensible and needs no extra tuning parameter.
- **Upper-SOC-bound tradeoff (90 % vs 80 %):** DEFERRED — not yet built; it is an analysis
  output rather than a Spec 03 dependency, so it can be delivered later (Spec 05/06 harness
  or a dedicated analysis pass).

## 10. References (supporting the fade model)

Each modelling choice is anchored to a source. **Exact equations, coefficient values, and
DOIs must be confirmed from the source PDFs during implementation/write-up** — no
coefficient is hard-coded from memory, and the Level B SOC-stress coefficient
(`soc_stress_beta`) stays `0` until sourced.

| Modelling choice | Source | What it supports |
|---|---|---|
| Throughput-driven fade; DoD/rainflow excluded | **Wang et al. (2011)**, *J. Power Sources* — "Cycle-life model for graphite–LiFePO₄ cells" | LFP capacity fade is dominated by charge (Ah) throughput and temperature, and is largely **DoD-independent** over a wide range → justifies §4.1 penalty and §4.3 linear-in-throughput cycle fade, and the exclusion of DoD/rainflow |
| Linear cycle fade vs equivalent full cycles | **Naumann et al. (2020)**, *J. Power Sources* — "Analysis and modeling of cycle aging of a commercial LiFePO₄/graphite cell" | cyclic capacity loss modelled against full-equivalent cycles (6000 EFC to 80% SOH) / throughput → §4.3, `cycle_life_efc` |
| √t calendar fade; SOC- & temperature-dependent rate | **Naumann et al. (2018)**, *J. Energy Storage* — "Analysis and modeling of calendar aging of a commercial LiFePO₄/graphite cell" | LFP calendar loss follows a √t (SEI-growth) law with rate rising with SOC and temperature → §4.4 √t sensitivity, §4.4 Level B SOC-dependent calendar, and the constant-reference-temperature framing |
| Additive superposition of calendar + cycle stress | **Xu et al. (2018)**, *IEEE Trans. Smart Grid* — "Modeling of Li-Ion Battery Degradation for Cell Life Assessment" | semi-empirical combination of calendar and cycle contributions → §4.5 additive SOH |
| Throughput/opportunity cost of degradation | **Collath et al. (2023)**, *J. Energy Storage* | economic interpretation of degradation cost → context for §4.1 penalty derivation |
| Whole system capex data | https://www.gov.uk/government/statistics/solar-pv-cost-data (TIC Inflation adjusted median for 4-10kW PV), https://www.gov.uk/government/statistics/mcs-certified-domestic-battery-installation-statistics (battery and inverter costs) | per kwh and per kwp capex for UK battery and PV in 2026. |

Note: the **descriptive SOC-exposure metrics (§4.7, Level A)** require no literature
coefficients — they are computed directly from the solved SOC trajectory. Only the Level B
SOC-dependent calendar sensitivity needs the Naumann SOC-stress parameterisation.
