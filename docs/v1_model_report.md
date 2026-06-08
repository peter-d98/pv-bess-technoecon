# Stage 1 Model Development Report

**Project:** PV–BESS Techno-Economic Analysis  
**Stage:** v1 — Synthetic Data, Single-Day Deterministic MILP  
**Date:** June 2026  
**Status:** Complete — all tests passing

---

## 1. Objectives

The goal of this stage was to build the minimum viable version of the scheduling optimisation model: a working, physically consistent mathematical programme whose results are simple enough to verify by hand. No real data is used at this stage. The priorities were:

1. Implement the SOC battery model as an optimisation constraint
2. Implement a degradation cost penalty in the objective function
3. Verify correctness through targeted unit tests before introducing data complexity
4. Identify modelling decisions that will carry forward into later stages

---

## 2. System Description

The modelled system represents the Octopus Energy Zero Bills domestic installation specification:

| Component | Specification |
|---|---|
| PV array | ~4 kWp (modelled as 2 kW peak synthetic output) |
| Battery capacity | 10 kWh |
| Max charge/discharge power | 3 kW |
| Round-trip efficiency | 90.25% (η_c = η_d = 0.95) |
| Usable SOC window | 10%–90% (8 kWh usable) |
| Tariff | Two-tier: 7p/kWh (23:00–07:00), 28p/kWh (07:00–23:00) |
| Export rate | 15p/kWh flat (Smart Export Guarantee) |

---

## 3. Mathematical Formulation

### 3.1 Decision Variables

The optimiser controls five sets of variables, each defined at every hourly timestep $t \in \{0, \ldots, T-1\}$:

| Variable | Units | Description |
|---|---|---|
| $P_t^{charge}$ | kW | Battery charging power |
| $P_t^{discharge}$ | kW | Battery discharging power |
| $P_t^{import}$ | kW | Power drawn from the grid |
| $P_t^{export}$ | kW | Power exported to the grid |
| $SOC_t$ | fraction | State of charge at start of timestep $t$ |
| $u_t$ | binary (0/1) | Grid import active flag |

### 3.2 Battery Model

The battery is represented by a generic SOC model. The SOC evolves according to:

$$SOC_{t+1} = SOC_t + \frac{\Delta t}{E_{BESS}} \left( \eta_c \cdot P_t^{charge} - \frac{P_t^{discharge}}{\eta_d} \right)$$

where $E_{BESS}$ is the battery capacity in kWh and $\Delta t = 1$ hour. This is a linear difference equation, directly suitable for embedding in a linear programme. It captures:

- **Charging losses**: only $\eta_c < 1$ of the input power is actually stored
- **Discharging losses**: the battery must expend $1/\eta_d > 1$ kWh of stored energy to deliver each kWh at the terminals

The combined round-trip efficiency is $\eta_c \times \eta_d = 0.9025$, meaning 10% of energy is lost per full cycle.

### 3.3 Constraints

**Boundary conditions:**

$$SOC_0 = SOC_{init} = 0.5$$

$$SOC_T = SOC_{init} \quad \text{(terminal constraint)}$$

The terminal SOC constraint forces the battery to end the day where it started, making each simulated day self-contained. Without it, the optimiser exploits the battery's initial charge as a free resource to displace grid imports, which flatters single-day savings figures in a way that is not repeatable.

**SOC and power bounds:**

$$SOC_{min} \leq SOC_t \leq SOC_{max} \quad \forall\, t$$

$$0 \leq P_t^{charge} \leq P_{max}^{charge}, \quad 0 \leq P_t^{discharge} \leq P_{max}^{discharge} \quad \forall\, t$$

**Energy balance** (conservation of energy at every timestep):

$$P_t^{PV} + P_t^{import} + P_t^{discharge} = D_t + P_t^{export} + P_t^{charge} \quad \forall\, t$$

Every kWh arriving at the household node (from PV, the grid, or the battery) must be accounted for on the right-hand side (demand served, exported, or stored). This is the central physical constraint from which all dispatch decisions flow.

**Import/export mutual exclusivity:**

$$P_t^{import} \leq M \cdot u_t, \quad P_t^{export} \leq M \cdot (1 - u_t) \quad \forall\, t$$

where $M = 20$ kW is the grid connection limit (big-M parameter). The binary variable $u_t$ ensures the model cannot simultaneously import and export — a physical requirement of a single electricity meter. This constraint is necessary because there is no efficiency loss between import and export power flows; without it the model is unbounded whenever the export price exceeds the import price (the solver would pass unlimited power through the meter for profit). This makes the problem a Mixed-Integer Linear Programme (MILP) rather than a pure LP.

> **Note:** Simultaneous charge and discharge does *not* require a binary variable. Round-trip losses mean it is always cheaper to reduce both to zero than to cycle energy needlessly, so the LP will naturally avoid it whenever prices are non-degenerate.

### 3.4 Objective Function

$$\min \sum_{t=0}^{T-1} \Delta t \left[ \lambda_t^{buy} \cdot P_t^{import} - \lambda_t^{sell} \cdot P_t^{export} + c_{deg} \cdot \left( P_t^{charge} + P_t^{discharge} \right) \right]$$

The three terms represent:

- **Import cost**: payment to the grid for each kWh consumed, at the prevailing time-of-use tariff rate
- **Export revenue** (subtracted, so it reduces cost): income from the Smart Export Guarantee for surplus energy sent to the grid
- **Degradation penalty**: a monetary cost assigned to every kWh that passes through the battery in either direction, representing amortised battery replacement cost spread across total lifetime throughput

The degradation term is the key methodological element. It forces the optimiser to ask whether the arbitrage margin from a given cycle exceeds the wear cost it incurs — which is precisely the trade-off the dissertation is investigating. A default value of $c_{deg} = 0.05$ £/kWh (5p/kWh) is used, which is consistent with a ~£2,000 battery warranted for ~40,000 kWh of lifetime throughput.

---

## 4. Synthetic Input Data

All inputs are synthetic and deliberately stylised so results can be verified by inspection.

| Profile | Shape | Values |
|---|---|---|
| PV generation | Half-sinusoid over 06:00–18:00 | 0 kW at night, 2 kW peak at noon |
| Household demand | Flat baseline + two peak blocks | 0.5 kW baseline; 1.5 kW at 07:00–09:00 and 17:00–21:00 |
| Import price | Two-tier step function | 7p/kWh from 23:00–07:00; 28p/kWh otherwise |
| Export price | Flat | 15p/kWh all hours |

Daily totals for reference: PV generation ≈ 7.6 kWh; household demand = 18.0 kWh.

---

## 5. Implementation

The model is implemented in Python using the following libraries:

- **CVXPY** — algebraic modelling of the optimisation problem; solver-agnostic
- **pandas / numpy** — data handling and array operations
- **matplotlib** — results visualisation

The codebase is organised as:

```
src/
  battery.py     # BatteryParams dataclass — system parameters and validation
  data_gen.py    # Synthetic profile generation functions
  model.py       # solve_dispatch() — the MILP formulation and solver call
scripts/
  run_stage1.py  # Runner: assemble inputs, solve, print results, save figure
tests/
  test_model.py  # Five unit tests verifying physical correctness
```

---

## 6. Results

### 6.1 Dispatch Plot

![Stage 1 dispatch](../results/stage1_dispatch.png)

### 6.2 Numerical Summary (default: $c_{deg}$ = 5p/kWh)

| Metric | Value |
|---|---|
| Solve status | Optimal |
| Import cost | £0.785 |
| Export revenue | £1.131 |
| Degradation cost | £0.839 |
| Net energy cost (import − export) | −£0.347 |
| Objective (net + degradation) | £0.493 |

### 6.3 Interpretation of the Dispatch Plot

The most notable feature of the dispatch plot is that **the battery does not cycle at all** — both the charge and discharge lines are flat at zero, and the SOC remains constant at 50% throughout the day. This is not a bug; it is the correct economic decision given a 5p/kWh degradation cost.

To understand why: the potential revenue from grid-charge-then-export arbitrage (buy at 7p, sell at 15p via SEG) must be weighed against the cost of doing so. Charging 1 kWh from the grid requires importing $1/\eta_c = 1.053$ kWh at 7p = 7.37p, and the degradation penalty applies to both the charge and discharge throughput. For a full import-charge-discharge-export cycle delivering 1 kWh to the grid:

$$\text{Revenue} = 15\text{p}, \quad \text{Import cost} = 7.37\text{p}, \quad \text{Degradation} = (1.053 + 1) \times 5\text{p} = 10.3\text{p}$$

$$\text{Net} = 15 - 7.37 - 10.3 = -2.67\text{p} \quad \text{(a loss)}$$

The optimiser correctly identifies this as unprofitable and instead exports PV directly to the grid during the day and imports from the grid to cover demand at night and at the morning and evening peaks.

### 6.4 Sensitivity to Degradation Cost

The impact of $c_{deg}$ on the result is significant and directly relevant to the research question:

| $c_{deg}$ (p/kWh) | Import cost | Export revenue | Degradation cost | Net cost |
|---|---|---|---|---|
| 0 | £1.225 | £1.961 | £0.000 | −£0.736 |
| 5 (default) | £0.785 | £1.131 | £0.839 | −£0.347 |
| 10 | £2.510 | £1.373 | £0.000 | +£1.137 |

At $c_{deg} = 0$ the battery cycles aggressively (grid-charge-then-export arbitrage dominates), generating a 73.6p daily net credit. At $c_{deg} = 10$p/kWh the battery again idles completely — the penalty exceeds the arbitrage margin — and the household pays £1.14/day net. At 5p/kWh the battery is on the boundary of viability. This sensitivity will be a central axis of analysis in later stages.

---

## 7. Verification

Five unit tests were implemented to confirm physical correctness:

| Test | Scenario | Assertion |
|---|---|---|
| `test_flat_price_no_pv_no_cycling` | Flat prices, no PV | Battery does not cycle; all demand met from import |
| `test_price_arbitrage_charges_cheap_discharges_peak` | Two-tier tariff, no PV, no export | Charging concentrated in cheap hours; discharging in peak hours |
| `test_soc_within_bounds` | Full synthetic dataset | SOC never violates [10%, 90%] at any timestep |
| `test_energy_balance_satisfied` | Full synthetic dataset | Supply equals demand at every timestep to within 0.1 W |
| `test_no_simultaneous_charge_discharge` | Full synthetic dataset | Product of charge × discharge is zero at every timestep |

All five tests pass.

---

## 8. Key Modelling Decisions and Rationale

**MILP rather than LP.** The import/export mutual-exclusivity constraint requires a binary variable, making the problem formally a MILP. For 24 hourly timesteps this adds negligible computational cost (milliseconds to solve) but is a methodological distinction worth noting. The commonly cited claim that "LP is sufficient because round-trip inefficiency prevents simultaneous charge/discharge" applies only to the battery — it does not extend to the grid-side power flows.

**Terminal SOC constraint.** Enforced by default to make each simulated day a fair, self-contained accounting unit. When extending to a multi-day rolling horizon in v2, this constraint will be relaxed in favour of continuity between days.

**Hourly resolution.** Half-hourly resolution (matching Agile tariff data) is deferred to v2. For v1, hourly resolution keeps the dataset and solver transparent.

**Throughput degradation model.** The linear throughput penalty (£/kWh cycled) is used in preference to rainflow cycle counting. It is directly embeddable in the LP objective and is a well-established approximation in the techno-economic literature. It will be retained in later stages; rainflow counting could be applied post-hoc to the solved SOC trajectory as a validation step.

---

## 9. Limitations of v1

- **Single day, no seasonality.** A single stylised summer day cannot capture the seasonal variation in PV output that dominates annual economics.
- **Synthetic profiles.** PV and demand profiles are smooth and symmetric; real profiles are noisy, asymmetric, and correlated in ways that affect optimal dispatch.
- **Flat export price.** Real Agile Outgoing rates vary half-hourly and can be negative — this is a potentially large driver of battery behaviour that is not captured.
- **No grid services revenue.** The model captures retail arbitrage only. Ancillary service revenue, which likely forms part of the Octopus zero-bills claim, is excluded by design at this stage.
- **Static degradation cost.** The throughput cost is assumed constant per kWh regardless of depth of discharge, temperature, or C-rate. These dependencies exist in real batteries and will affect lifetime estimates.

---

## 10. Next Steps — Version 2

The following changes are planned for v2, in order of priority:

1. **Half-hourly resolution.** Refactor all profiles and constraints to use $\Delta t = 0.5$ hours, matching the Agile tariff data format.

2. **Real Octopus Agile price data.** Implement a data fetching script (`scripts/fetch_agile_prices.py`) using the Octopus Energy API to pull historical half-hourly import and export prices for a full calendar year.

3. **Real PV and demand profiles.** Source realistic GB household demand profiles (e.g. from UKPN or Elexon open datasets) and PV generation data (e.g. from PVGIS or a synthetic TMY-based model calibrated to a UK location), replacing the synthetic profiles.

4. **Multi-day rolling horizon.** Extend the single-day model to simulate a full year day-by-day, carrying the terminal SOC from one day forward as the initial SOC of the next. This enables annual energy cost and degradation to be computed.

5. **Annual results and zero-bills assessment.** Compute total annual import cost, export revenue, and degradation cost; compare net cost against the counterfactual (grid-only household); quantify the shortfall from zero bills and the implied ancillary service revenue required to close it.
