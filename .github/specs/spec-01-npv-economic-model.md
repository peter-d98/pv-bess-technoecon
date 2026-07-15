# Spec 01 — NPV Economic Model

**Status:** Drafted · **Owner:** —  · **Depends on:** none (Spec 2 will refine
the degradation/fade inputs) · **Blocks:** Specs 2, 5, 6

## 1. Purpose

Replace simple payback as the headline viability metric with a **lifetime
net-present-value (NPV)** assessment. The model converts the output of the
existing annual rolling-horizon dispatch (an annual cost breakdown) into a
discounted multi-year cash-flow and reports NPV alongside supporting metrics.

This is the metric the dissertation actually defends, so it is built first —
before multi-location data and the parameter sweep.

## 2. Scope

### In scope
- A pure-Python valuation function that takes annual energy economics +
  economic/battery-life assumptions and returns lifetime NPV and supporting
  metrics.
- Battery replacement within the horizon (one replacement if battery life < horizon).
- Real-terms cash flows with electricity price escalation and discounting.
- Sensitivity-ready: all assumptions are explicit parameters.
- Unit tests covering hand-checkable cases.

### Out of scope (handled elsewhere)
- Capacity fade / cycle-life modelling → **Spec 2** (this spec consumes a
  `battery_life_years` and `degradation_cost_per_kwh` as inputs; Spec 2 will
  derive them physically and feed year-on-year capacity fade).
- Tariff construction → **Spec 3**.
- Running across locations/sizes → **Spec 5**.
- Plotting the NPV frontier → **Spec 6**.

### Stated limitations
- **Single replaceable component.** `compute_npv` models one component life
  with a single replacement schedule and residual value. The hybrid inverter is
  assumed to share the battery's life (10 years) and is replaced together with
  it, folded into the replacement cost via `replacement_cost_factor`. Its longer
  ~15-year warranty is therefore not modelled separately, so this is a mildly
  conservative treatment (the inverter is "replaced" ~5 years early). PV panels
  are assumed to last the full horizon (20-year warranty) and PV output
  degradation is not modelled here.

## 3. Inputs

The valuation consumes the **annual** result already produced by
`scripts/run_stage2.py` (`battery_annual_costs` + `counterfactual_cost`), plus
economic assumptions.

```python
@dataclass(frozen=True)
class EconomicParams:
    battery_capex: float              # GBP, installed cost of the battery
    discount_rate: float = 0.05       # real, base case
    horizon_years: int = 20           # analysis horizon (PV lifetime)
    price_escalation: float = 0.02    # real electricity price growth /yr
    battery_life_years: float = 12.0  # provided by Spec 2 (calendar/cycle limited)
    replacement_cost_factor: float = 1.0   # replacement capex as fraction of capex
    om_cost_per_year: float = 0.0     # optional fixed O&M (GBP/yr)
    include_residual_value: bool = True    # credit unconsumed battery life at horizon
```

Annual operating economics (from a solved year):
- `cf_net_cost` — counterfactual (no-battery) annual net energy cost (GBP).
- `battery_net_cost` — with-battery annual net energy cost (GBP).
- `degradation_cost` — annual throughput degradation cost (GBP).

**Saving may be a scalar OR a per-year stream.** `compute_npv` accepts
`annual_saving` as either a `float` (constant) or a sequence of length
`horizon_years` (one value per year). The stream form is the coupling point with
**Spec 2**: capacity fade reduces the achievable saving over time, so a flat
20-year saving would contradict the degradation thesis. Spec 1 implements the
stream interface now; Spec 2 supplies the fade-adjusted values later. (Second-AI
review, blocker 3.)

All monetary values are **real** (today's GBP); the discount rate is therefore
also real. This is stated explicitly because mixing real cash flows with a
nominal discount rate is a common and material error.

## 4. Method

### 4.1 Annual benefit (year 1, real terms)

```
annual_saving = cf_net_cost - (battery_net_cost + degradation_cost) - om_cost_per_year
```

This matches the **established project decision** that the annual saving must
include the degradation cost on the battery side (see project context). The
degradation cost represents real wear (earlier replacement), so it is deducted
from the battery's benefit, not treated as a mere optimiser penalty.

### 4.2 Cash-flow series

For year `t = 1 … horizon_years`:

```
benefit_t  = annual_saving * (1 + price_escalation) ** (t - 1)
capex_t    = battery_capex                 if t == 1
           + battery_capex * replacement_cost_factor  if t == ceil(battery_life_years)+1 and within horizon
           else 0
cashflow_t = benefit_t - capex_t           # year-1 capex handled below
```

Treatment of the initial capex:
- The **initial** battery capex is incurred at `t = 0` (undiscounted), i.e. it
  is `-battery_capex` at the start.
- **Replacements (multiple, not just one):** a replacement capex of
  `battery_capex * replacement_cost_factor` is incurred at every
  `t_r = k * ceil(battery_life_years)` for `k = 1, 2, …` while `t_r < horizon_years`.
  Each is discounted to present value. A single hard-coded replacement is wrong
  for short battery lives in the sweep. (Second-AI review, blocker 1.)

**Residual value (end-of-horizon):** the battery in service at the horizon end
has unconsumed life `remaining = (t_install + ceil(life)) - horizon_years`. A
straight-line residual credit `battery_capex * replacement_cost_factor *
(remaining / ceil(life))` is added as a positive cash flow at `t = horizon_years`,
discounted. Controlled by `include_residual_value` (see open decision §9).
(Second-AI review, blocker 2.)

### 4.3 Discounting

```
NPV = -battery_capex
      + Σ_{t=1..horizon} (benefit_t - replacement_capex_t) / (1 + discount_rate) ** t
```

### 4.4 Supporting metrics (reported alongside NPV)
- **Discounted payback period** — first year cumulative discounted cash flow ≥ 0
  (or `inf`). Reported for context, *not* as the headline.
- **LCOS-style metric (optional, stretch):** lifetime discounted cost of stored
  energy — defer unless the discussion needs it.
- **Benefit–cost ratio** = PV(benefits) / PV(costs).

### 4.5 Residual value / end-of-horizon
- Default: straight-line residual over `battery_life_years` for the battery still
  in service at the horizon end (`include_residual_value`, on by default),
  assuming a uniform `k·ceil(life)` replacement schedule.
- **Override (`terminal_residual_value`):** the caller may pass an explicit
  residual (real GBP, undiscounted, credited in the final year), which takes
  precedence over the default. The fade model (Spec 02) uses this to credit the
  unconsumed **warranty** value of whatever battery+inverter is in service at the
  horizon end — straight-line over the warranty life against the fade-derived
  (non-uniform) replacement schedule, which the default uniform-schedule formula
  cannot express. Depreciating over the warranty (not the longer realised life)
  is the more conservative, more defensible book-value convention.

## 5. Interface

New module `src/economics.py`:

```python
@dataclass(frozen=True)
class NPVResult:
    npv: float
    bcr: float                       # benefit-cost ratio
    discounted_payback_years: float  # inf if never
    pv_benefits: float
    pv_costs: float
    cashflows: pd.DataFrame          # year, benefit, capex, discounted

def compute_npv(
    annual_saving: float | Sequence[float],
    econ: EconomicParams,
    terminal_residual_value: float | None = None,
) -> NPVResult: ...
```

Keep `compute_npv` agnostic to *how* `annual_saving` was produced so it is
trivially reusable by the Spec 5 sweep. A thin helper may assemble
`annual_saving` from the `run_stage2` cost dicts.

## 6. Integration

- `scripts/run_stage2.py`: add NPV reporting **alongside** the existing payback
  block (do not remove payback — it is still a useful sanity check and is
  referenced in `docs/v2_controller_comparison_report.md`). Add CLI args:
  `--discount-rate`, `--horizon-years`, `--price-escalation`, `--battery-life-years`.
- Do **not** change the dispatch model (`src/model.py`) or `BatteryParams`.

## 7. Tests (`tests/test_economics.py`)

1. **Zero discount, no escalation, no replacement** → NPV equals
   `annual_saving * horizon - battery_capex` (exact, hand-checkable).
2. **Positive discount** → NPV strictly less than the undiscounted case.
3. **Replacement within horizon** (`battery_life_years < horizon`) → exactly one
   discounted replacement capex appears in `cashflows`; NPV reduced accordingly.
4. **No replacement when `battery_life_years >= horizon`.**
5. **Price escalation** increases later-year benefits geometrically.
6. **Negative `annual_saving`** → NPV < `-battery_capex`; `discounted_payback = inf`.
7. **BCR consistency**: `npv ≈ pv_benefits - pv_costs`; `bcr = pv_benefits / pv_costs`.

## 8. Acceptance criteria

- `compute_npv` implemented in `src/economics.py` with the interface above.
- All seven unit tests pass; existing 12 tests still pass.
- `run_stage2.py` prints NPV, BCR, and discounted payback for the baseline
  Glasgow run, with the new CLI args functioning.
- Baseline Glasgow NPV reported and recorded (expected negative under base
  assumptions — confirm and document).

## 9. Open decisions (resolve during implementation)

- **Nominal vs usable capacity for EFC:** existing code defines an equivalent
  full cycle as `throughput / (2 × capacity_kwh)`, treating `capacity_kwh` as the
  cycle reference even though the SOC window is 10–90%. Spec 2 must fix one
  convention and apply it consistently to fade and the derived penalty. Flag, do
  not silently change. **(Still open — belongs to Spec 2.)**
- **Residual value:** RESOLVED — default ON (`include_residual_value=True`),
  confirmed by user.
- **Replacement timing rounding:** RESOLVED — `ceil(battery_life_years)`,
  confirmed by user. Multiple replacements at every `k·ceil(life) < horizon`.
