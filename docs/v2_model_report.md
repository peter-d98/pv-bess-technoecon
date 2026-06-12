# Stage 2 Model Development Report

**Project:** PV–BESS Techno-Economic Analysis  
**Stage:** v2 — Real Data, Half-Hourly Resolution, Annual Rolling Horizon  
**Date:** June 2026  
**Status:** Complete — all tests passing, annual baseline run complete

---

## 1. Objectives

The goal of this stage was to replace all synthetic inputs with real-world data and extend the single-day model to a full annual rolling-horizon simulation. The core MILP formulation from v1 was retained unchanged; the changes in v2 are entirely in data sourcing, temporal resolution, and the simulation loop. Specific objectives were:

1. Refactor the time resolution from hourly to half-hourly to match the Agile tariff settlement period
2. Source and load real PV generation, household demand, and Agile tariff data
3. Implement a multi-day rolling horizon that carries battery state of charge forward across days
4. Produce an annual techno-economic viability assessment against a no-battery counterfactual

---

## 2. System Description

The baseline system specification is unchanged from v1. The modelled installation represents a typical domestic UK PV–BESS system:

| Component | Specification |
|---|---|
| PV array | 4 kWp, crystalline silicon, 35° tilt, south-facing (azimuth 0°), 14% system losses |
| Battery capacity | 10 kWh |
| Max charge/discharge power | 3 kW |
| Round-trip efficiency | 90.25% (η_c = η_d = 0.95) |
| Usable SOC window | 10%–90% (8 kWh usable) |
| Tariff | Octopus Agile (half-hourly variable, 2023) |
| Export rate | Octopus Agile Outgoing (half-hourly variable, 2023) |
| Location | Glasgow (55.83°N, 4.28°W) — first baseline location |

---

## 3. Data Sources

### 3.1 PV Generation

**Source:** PVGIS SARAH-3, via the PVGIS interactive tool (European Commission JRC)  
**File:** `Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv`  
**Format:** Hourly AC power output (W), one row per hour, year 2023  
**Annual total:** 3,387 kWh (consistent with a 4 kWp array at ~847 kWh/kWp — plausible for Glasgow's solar resource)

PVGIS SARAH-3 is the satellite-derived reference dataset for European solar resource assessment and is preferred over global reanalysis alternatives (e.g. MERRA-2 as used by Renewables Ninja) because it is more accurate for UK irradiance, where the south England / Scotland difference (~20–25% in annual irradiance) is a key axis of the parameter study.

Two file-format quirks were handled in the loader: (a) the timestamps are labelled at 11 minutes past each hour (e.g. `20230101:0011`) representing the centre of the integration interval — these are floored to the hour and then forward-filled to both half-hours; (b) the file contains a multi-line metadata header and a trailing footer block, both stripped before parsing.

### 3.2 Household Demand

**Source:** CREST Demand Model v2.3.3 (Loughborough University)  
**File:** `demand_halfhourly_2025.csv`  
**Format:** Half-hourly average demand (kW), 17,520 rows, generated for 2025  
**Annual total:** 4,920 kWh

The CREST model was driven via xlwings (Python → Excel automation) by iterating over every calendar day of 2025, setting the month, weekday/weekend flag, and appliance ownership year, triggering the simulation macro, and reading the 1-minute output from the `Results - aggregated` sheet. The 1,440 one-minute values per day were averaged into 48 half-hourly values. The dwelling was configured with fixed (non-stochastic) occupancy parameters representing a typical UK household with ~2–3 occupants; the stochastic assignment of *when* occupants are active and *when* appliances operate was retained, producing realistic day-to-day demand variation across the year. Daily totals ranged from 2.9 to 33.9 kWh (mean 13.5 kWh/day, std 5.5 kWh/day).

**Data year mismatch.** The CREST profile was generated for 2025; the PV and tariff data are from 2023. This is an acknowledged limitation: the three inputs are aligned *positionally* (by half-hour index within the year) rather than by calendar date. Because the demand profile is synthetically generated and represents a stochastic draw rather than a historical record, this misalignment does not introduce a systematic bias. It is noted as a stated limitation in the methodology.

### 3.3 Agile Tariff Prices

**Source:** Octopus Energy (downloaded as CSV)  
**File:** `agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv`  
**Format:** Half-hourly import and export prices in p/kWh, 17,518 rows (2 missing — the spring DST transition gap is forward-filled in the loader)  
**Import prices:** mean 21.9 p/kWh, range −19.0 to +68.7 p/kWh  
**Export prices:** mean 10.6 p/kWh, range 0.0 to +33.3 p/kWh

Prices were converted from p/kWh to £/kWh in the loader. The occasional negative import prices (periods where the system operator paid consumers to take electricity) are particularly important for this study: they represent arbitrage opportunities that a battery can exploit by charging at zero or negative cost, and they are a driver of dispatch behaviour that could not be captured with a stylised flat or two-tier tariff.

---

## 4. Mathematical Formulation

The MILP formulation is identical to v1 (see Stage 1 report, Section 3). The only change is the timestep length $\Delta t = 0.5$ hours (previously 1.0 hour), which propagates into the SOC dynamics equation and the objective function scaling. All constraints, variables, and the degradation penalty structure are unchanged.

The terminal SOC constraint from v1 is **disabled** in the annual rolling horizon (i.e. `terminal_soc_equals_initial=False` is passed to `solve_dispatch()`). Instead, the SOC at the end of each day is carried forward as the `soc_init` of the following day's problem. This allows the battery to enter a new day at whatever SOC the optimiser left it at, which is physically correct for a continuously operating system and avoids the artificial forced-recharge artefact visible in the v1 terminal-SOC plot.

---

## 5. Implementation

### 5.1 New Code

Three new source files were added:

```
src/
  data_loader.py       # load_pv(), load_demand(), load_agile_prices(), load_all()
scripts/
  run_stage2.py        # Annual rolling-horizon runner and viability assessment
  generate_demand_profile.py  # xlwings-based CREST automation (Windows/macOS only)
tests/
  test_data_loader.py  # 7 unit tests for the data loaders
```

The core `src/model.py`, `src/battery.py`, and `tests/test_model.py` are unchanged from v1.

### 5.2 Data Loader (`src/data_loader.py`)

The loader builds a **canonical half-hourly DatetimeIndex** for the reference year (17,520 periods for a non-leap year) and aligns all three sources to it:

- `load_pv()` — parses PVGIS CSV, strips metadata, floors timestamps, converts W → kW, forward-fills to 30-min resolution
- `load_demand()` — reads CREST CSV and re-indexes positionally onto the canonical index
- `load_agile_prices()` — parses Octopus CSV, converts p → £/kWh, drops duplicate DST timestamps (autumn), forward-fills the DST gap (spring)
- `load_all()` — calls all three and returns a single aligned DataFrame

### 5.3 Annual Runner (`scripts/run_stage2.py`)

The runner implements the rolling horizon as follows:

1. Load and align data via `load_all()`
2. Initialise `carried_soc = battery.soc_init`
3. For each of the 365 days:
   - Slice 48 half-hourly periods from each profile
   - Construct a new `BatteryParams` with `soc_init=carried_soc`
   - Call `solve_dispatch(..., dt_hours=0.5, terminal_soc_equals_initial=False)`
   - Append the solved day schedule
   - Update `carried_soc` from the last `soc_end` value
4. Concatenate all daily schedules into an annual DataFrame
5. Compute cost breakdown and counterfactual; print viability assessment; save CSV and dispatch plot

The `--plot-date` argument allows any calendar day to be selected for the dispatch visualisation without re-running the annual solve.

---

## 6. Results

### 6.1 Annual Viability Assessment (Glasgow baseline, $c_{deg}$ = 5p/kWh)

| Metric | With battery | No battery |
|---|---|---|
| Import cost (£) | 498.85 | 892.75 |
| Export revenue (£) | 117.87 | 199.45 |
| Net energy cost (£) | 380.98 | 693.30 |
| Degradation cost (£) | 157.40 | 0.00 |
| **Total cost (£)** | **538.38** | **693.30** |

| Derived metric | Value |
|---|---|
| Battery throughput | 3,148 kWh/yr (~157 equivalent full cycles) |
| Annual saving (incl. degradation) | £154.92 |
| Simple payback (at £4,000 capex) | **25.8 years** |

The annual saving of £154.92 is the honest figure: it deducts the £157.40 degradation cost from the gross energy-cost saving of £312.32. The payback of 25.8 years substantially exceeds a typical battery warranty period (~10 years), indicating that the system is **not economically viable** at Glasgow with a 4 kWp array, 10 kWh battery, Agile tariff, and a 5p/kWh degradation assumption under the baseline capex.

This result is itself meaningful for the research question: viability is marginal-to-negative for Scotland's solar resource even on a highly favourable time-of-use tariff.

**Note on the payback calculation.** An earlier version of this code computed `annual_saving = counterfactual_net_cost − battery_net_cost`, omitting degradation from the battery side. This overstated the saving as £312.32 and gave a payback of 12.8 years. The correct calculation includes degradation in the battery total cost, giving 25.8 years. The distinction is important because degradation represents a genuine capital consumption cost that must be recovered over the battery's lifetime.

### 6.2 Dispatch Plots

Two representative days are shown to illustrate the seasonal contrast in dispatch strategy.

**Summer day (1 July 2023) — PV capture dominates**

![Summer dispatch](../results/stage2_dispatch_day_20230701.png)

PV output peaks above 3 kW in the afternoon. The battery charges during the PV peak to capture generation that exceeds demand and immediate export capacity. The battery then discharges across the evening as PV falls and demand rises. Grid import is largely absent during daylight hours. There is minimal overnight grid-charge arbitrage: the summer Agile price spread is insufficient to justify cycling at 5p/kWh degradation cost.

**Winter day (10 January 2023) — Agile price arbitrage dominates**

![Winter dispatch](../results/stage2_dispatch_day_20230110.png)

PV output is negligible all day (Glasgow, January). The battery charges in two concentrated pulses overnight (00:00–04:00) exploiting low Agile import prices, reaching ~62% SOC. It then discharges slowly across the full day (~06:00–20:00) to cover demand without grid import. Grid import returns briefly in the evening when SOC is depleted. This is classical price-arbitrage behaviour: buy cheap, defer expensive imports. The strategy is viable here because the Agile overnight troughs are deep enough that the import-to-discharge price spread exceeds the 5p/kWh degradation penalty.

### 6.3 Seasonal Dispatch Regime Transition

The two dispatch plots illustrate a qualitative regime transition across the year: in summer the battery acts as a PV buffer; in winter it acts as a price-arbitrage device. This transition is a direct consequence of the relative magnitude of the PV resource vs. the Agile price spread across seasons, mediated by the degradation cost threshold. Quantifying this transition and its dependence on location and degradation cost is a key axis of the parameter study.

---

## 7. Verification

The original five unit tests from v1 continue to pass. Seven new tests were added for the data loaders:

| Test | Assertion |
|---|---|
| `test_canonical_index_length_non_leap` | 2023 index has exactly 17,520 periods at 30-min |
| `test_canonical_index_length_leap` | 2024 index has exactly 17,568 periods |
| `test_load_pv_aligned_and_in_kw` | PV profile: correct length, no nulls, non-negative, ≤ 4.5 kW, zero overnight |
| `test_load_pv_halfhours_repeat_hourly` | Each hourly PV value fills both its half-hours identically |
| `test_load_demand_aligned_and_positive` | Demand profile: correct length, no nulls, positive, on canonical 2023 index |
| `test_load_agile_prices_converted_and_complete` | Prices in £/kWh, no nulls, import can be negative, export non-negative |
| `test_load_all_combines_columns` | Combined DataFrame: correct columns, correct length, no nulls |

All 12 tests pass (5 original + 7 new).

---

## 8. Key Modelling Decisions and Rationale

**SOC continuity across days (rolling horizon).** The v1 terminal SOC constraint forced the battery to end each day where it started. For the annual rolling horizon this is replaced by direct continuity: the optimiser is free to end any day at any SOC within bounds, which is then passed as the initial SOC for the following day. This is more physically realistic and avoids a systematic distortion where the optimiser spends energy at the end of each day to restore SOC even when that is not economically optimal.

**Positional alignment of mismatched years.** The three data sources span different calendar years (PV/prices: 2023, demand: 2025). They are aligned by position within the year (i.e. half-hour 0 of the year maps to the same row regardless of calendar year) rather than by calendar date. This is appropriate because the CREST demand profile is a synthetic stochastic realisation rather than a historical record; it cannot and should not be expected to represent the same weather and behavioural conditions as the 2023 price and PV data.

**PVGIS hourly → half-hourly upsampling.** PVGIS outputs one value per hour representing the mean power over that hour. Each hourly value is repeated for both half-hours (forward-fill equivalent). This is appropriate because PV output changes smoothly within an hour and the underlying PVGIS data is itself a one-hour integral, not an instantaneous reading; linear interpolation would imply within-hour dynamics that are not present in the source data.

**Degradation cost in annual saving calculation.** The annual saving is computed as:

$$\text{saving} = \text{net cost}_{\text{no battery}} - \left( \text{net energy cost}_{\text{battery}} + \text{degradation cost} \right)$$

This is the correct formulation because the degradation penalty in the objective function is a real economic cost (battery wear → earlier replacement) that must be recovered from the saving for the investment to be viable. Omitting it produces a saving figure that cannot be used to compute a valid payback period.

---

## 9. Limitations of v2

- **Data year mismatch.** Demand (2025), PV and prices (2023) are from different years. Aligned positionally; no systematic bias is expected, but the three datasets are not physically simultaneous.
- **Single location, single tariff.** Glasgow on Agile. The parameter study extending to south England, Midlands, flat-rate, and ToU tariffs is deferred to v3.
- **Perfect foresight.** The optimiser has full knowledge of the day's prices, PV, and demand at the start of each day. Real dispatch would use forecasts; this is a stated limitation representing an upper bound on achievable performance.
- **Single household CREST draw.** One stochastic realisation of a fixed household archetype. Running multiple CREST draws and averaging would reduce sensitivity to individual high-demand days (e.g. the 33.9 kWh maximum day observed).
- **Static degradation cost.** Constant at 5p/kWh regardless of depth of discharge, C-rate, or temperature. Sensitivity analysis across degradation cost assumptions is the primary axis for exploring this uncertainty.
- **No capital cost of PV.** Only the battery capex is used in the payback calculation. A whole-system payback would require PV capex, which is beyond the scope of this stage.

---

## 10. Next Steps — Version 3

1. **Parameter study across locations.** Re-run the annual model for south England (~51.5°N) and Midlands (~52.5°N) using PVGIS data for those coordinates. Compare annual saving and payback across the three locations.

2. **Parameter study across battery sizes.** Repeat for 5 kWh, 10 kWh (baseline), and 15 kWh batteries with proportionally scaled power limits.

3. **Flat-rate and ToU tariff scenarios.** Implement stylised flat (28p/kWh import, 15p/kWh export) and ToU (Economy 7-style) tariff profiles as alternatives to Agile, to isolate the contribution of tariff structure to viability.

4. **Degradation cost sensitivity sweep.** Repeat the annual run for $c_{deg} \in \{0, 2, 5, 8, 10\}$ p/kWh to map the breakeven degradation cost at each location and battery size.

5. **API-based data retrieval.** Automate PV data retrieval via the PVGIS REST API and Agile price retrieval via the Octopus Energy API, replacing manual CSV downloads and enabling reproducible re-runs for any location and year.
