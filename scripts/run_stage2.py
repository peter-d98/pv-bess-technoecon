"""Stage 2 runner: annual rolling-horizon PV-BESS dispatch on real data.

Solves the dispatch MILP day-by-day across a full year at half-hourly
resolution, carrying the battery state of charge forward from one day to the
next, then reports an annual techno-economic viability assessment against a
no-battery counterfactual.

Run from the repository root:

    python scripts/run_stage2.py
    python scripts/run_stage2.py --deg-cost 0.05 --battery-cap 10 --battery-capex 4000
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Allow running as a plain script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.battery import BatteryParams
from src.data_loader import HALFHOURS_PER_DAY, load_all
from src.degradation import (
    DegradationParams,
    derive_throughput_penalty,
    simulate_capacity_fade,
    soc_exposure,
)
from src.economics import EconomicParams, compute_npv
from src.model import solve_dispatch
from src.tariffs import TariffRates, build_tariff

DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"

DEFAULT_PV = DATA_DIR / "Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv"
DEFAULT_DEMAND = DATA_DIR / "demand_halfhourly_2025.csv"
DEFAULT_AGILE = DATA_DIR / "agile-half-hour-actual-rates-01-01-2025_31-12-2025_SScot.csv"

DT_HOURS = 0.5


def solve_year(
    data: pd.DataFrame, battery: BatteryParams, terminal_soc_daily: bool
) -> pd.DataFrame:
    """Solve the dispatch problem day-by-day across the whole year.

    The state of charge at the end of each day is carried forward as the
    initial SOC of the next day (continuity), so the battery is not artificially
    reset. Within each day the terminal-SOC constraint is disabled by default
    (continuity); set *terminal_soc_daily* to make each day self-contained.

    Returns
    -------
    pd.DataFrame
        The concatenated half-hourly dispatch schedule for the whole year,
        indexed by the canonical datetime index.
    """
    n = len(data)
    n_days = n // HALFHOURS_PER_DAY

    pv = data["pv_kw"].to_numpy()
    demand = data["demand_kw"].to_numpy()
    imp = data["import_price"].to_numpy()
    exp = data["export_price"].to_numpy()

    carried_soc = battery.soc_init
    daily_schedules: list[pd.DataFrame] = []

    for d in range(n_days):
        sl = slice(d * HALFHOURS_PER_DAY, (d + 1) * HALFHOURS_PER_DAY)
        day_battery = replace(battery, soc_init=carried_soc)

        result = solve_dispatch(
            pv_kw=pv[sl],
            demand_kw=demand[sl],
            import_price=imp[sl],
            export_price=exp[sl],
            battery=day_battery,
            dt_hours=DT_HOURS,
            terminal_soc_equals_initial=terminal_soc_daily,
        )

        if result.status not in ("optimal", "optimal_inaccurate"):
            raise RuntimeError(
                f"Day {d} ({data.index[sl][0].date()}) failed to solve: "
                f"status={result.status}"
            )

        schedule = result.schedule.copy()
        schedule.index = data.index[sl]
        daily_schedules.append(schedule)

        # Carry the end-of-day SOC forward to the next day.
        carried_soc = float(schedule["soc_end"].iloc[-1])

    return pd.concat(daily_schedules)


def counterfactual_cost(data: pd.DataFrame) -> dict[str, float]:
    """Annual energy cost for the same household with no battery.

    Surplus PV is exported and any shortfall is imported, both at the prevailing
    half-hourly prices. There is no battery, so no degradation cost.
    """
    net_load = data["demand_kw"] - data["pv_kw"]  # +ve => import, -ve => export
    grid_import = net_load.clip(lower=0.0)
    grid_export = (-net_load).clip(lower=0.0)

    import_cost = float((grid_import * data["import_price"]).sum() * DT_HOURS)
    export_revenue = float((grid_export * data["export_price"]).sum() * DT_HOURS)

    return {
        "import_cost": import_cost,
        "export_revenue": export_revenue,
        "net_cost": import_cost - export_revenue,
    }


def grid_only_cost(data: pd.DataFrame) -> dict[str, float]:
    """Annual energy cost for a household with no PV and no battery.

    Every unit of demand is imported at the prevailing half-hourly price; there
    is no PV to self-consume or export. This is the counterfactual for the
    whole-system framing (is installing PV + battery worthwhile at all?).
    """
    import_cost = float((data["demand_kw"] * data["import_price"]).sum() * DT_HOURS)
    return {
        "import_cost": import_cost,
        "export_revenue": 0.0,
        "net_cost": import_cost,
    }


def grid_only_flat_cost(data: pd.DataFrame, flat_rate: float) -> dict[str, float]:
    """Annual energy cost for a do-nothing household on the standard FLAT tariff.

    No PV, no battery: every unit of demand is imported at a single flat rate
    (independent of the system's chosen tariff). This is the headline
    whole-system counterfactual — the honest "what if the household does
    nothing" alternative against which installing PV+BESS (and possibly
    switching tariff) is judged.
    """
    import_cost = float(data["demand_kw"].sum() * DT_HOURS * flat_rate)
    return {
        "import_cost": import_cost,
        "export_revenue": 0.0,
        "net_cost": import_cost,
    }


def battery_annual_costs(schedule: pd.DataFrame, battery: BatteryParams) -> dict[str, float]:
    """Aggregate the solved annual schedule into a cost breakdown."""
    import_cost = float((schedule["p_import_kw"] * schedule["import_price"]).sum() * DT_HOURS)
    export_revenue = float((schedule["p_export_kw"] * schedule["export_price"]).sum() * DT_HOURS)
    throughput_kwh = float((schedule["p_charge_kw"] + schedule["p_discharge_kw"]).sum() * DT_HOURS)
    degradation_cost = battery.degradation_cost_per_kwh * throughput_kwh

    return {
        "import_cost": import_cost,
        "export_revenue": export_revenue,
        "degradation_cost": degradation_cost,
        "net_cost": import_cost - export_revenue,
        "throughput_kwh": throughput_kwh,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage 2 annual PV-BESS dispatch optimisation on real data."
    )
    parser.add_argument("--deg-cost", type=float, default=None, metavar="GBP_PER_KWH",
                        help="Throughput degradation cost (GBP/kWh cycled). Default: derived "
                             "from capex / (cycle_life_efc * 2 * capacity) ≈ 0.05 (5 p/kWh at "
                             "baseline). Pass a value to override.")
    parser.add_argument("--battery-cap", type=float, default=10.0, metavar="KWH",
                        help="Battery nominal capacity (kWh). Default: 10.0")
    parser.add_argument("--max-power", type=float, default=3.0, metavar="KW",
                        help="Max charge/discharge power (kW). Default: 3.0")
    parser.add_argument("--pv-kwp", type=float, default=4.0, metavar="KWP",
                        help="PV array peak power (kWp). The hybrid inverter is sized to match "
                             "this. Default: 4.0")
    parser.add_argument("--pv-cost-per-kwp", type=float, default=1940.0, metavar="GBP_PER_KWP",
                        help="Installed PV cost per kWp. Default: 1940")
    parser.add_argument("--battery-cost-per-kwh", type=float, default=600.0,
                        metavar="GBP_PER_KWH",
                        help="Installed battery cost per kWh (also the replacement cost and the "
                             "basis for the derived throughput penalty). Default: 600")
    parser.add_argument("--inverter-cost-per-kw", type=float, default=400.0,
                        metavar="GBP_PER_KW",
                        help="Installed hybrid-inverter cost per kW (sized to the PV array). "
                             "Default: 400")
    parser.add_argument("--pv-om-frac", type=float, default=0.01, metavar="FRAC",
                        help="Annual PV O&M as a fraction of PV capital cost (whole-system "
                             "framing only). Default: 0.01")
    parser.add_argument("--framing", choices=("whole-system", "battery-marginal"),
                        default="whole-system",
                        help="'whole-system': counterfactual is grid-only (no PV, no battery), "
                             "initial capex is the whole system. 'battery-marginal': "
                             "counterfactual is PV-only, initial capex is the battery. "
                             "Default: whole-system")
    parser.add_argument("--tariff", choices=("flat", "e7", "agile"), default="agile",
                        help="Import tariff (Spec 03). 'flat' single-rate, 'e7' Economy 7, "
                             "'agile' dynamic (from the CSV). Default: agile")
    parser.add_argument("--export", choices=("matched", "seg", "agile", "none"),
                        default="matched",
                        help="Export tariff. 'matched': flat/e7 -> flat SEG, agile -> Agile "
                             "export. 'seg' flat SEG, 'agile' dynamic export, 'none' 0p. "
                             "Default: matched")
    parser.add_argument("--seg-rate", type=float, default=None, metavar="GBP_PER_KWH",
                        help="Override the flat SEG export rate (GBP/kWh). Default: the "
                             "national-average SEG in TariffRates (0.13).")
    parser.add_argument("--discount-rate", type=float, default=0.05, metavar="RATE",
                        help="Real discount rate for NPV. Default: 0.05")
    parser.add_argument("--horizon-years", type=int, default=20, metavar="YEARS",
                        help="NPV analysis horizon (years). Default: 20")
    parser.add_argument("--price-escalation", type=float, default=0.02, metavar="RATE",
                        help="Real electricity price escalation per year. Default: 0.02")
    parser.add_argument("--battery-life-years", type=float, default=10.0, metavar="YEARS",
                        help="Battery life for the flat-NPV replacement timing. Default: 10")
    parser.add_argument("--fade-npv", action="store_true",
                        help="Run the exogenous capacity-fade simulation and a fade-adjusted "
                             "NPV (re-dispatches the full year per horizon year; slow).")
    parser.add_argument("--calendar-life-years", type=float, default=10.0, metavar="YEARS",
                        help="Calendar years to SOH_eol (calendar ageing alone). "
                             "Default: 10 (battery warranty)")
    parser.add_argument("--cycle-life-efc", type=float, default=6000.0, metavar="EFC",
                        help="Equivalent full cycles to SOH_eol (cycle ageing alone). "
                             "Default: 6000 (LFP literature value, defined to 80% SOH)")
    parser.add_argument("--soh-eol", type=float, default=0.80, metavar="FRAC",
                        help="State of health at end-of-life / replacement. Default: 0.80 "
                             "(paired with the 6000-EFC literature cycle-life endpoint)")
    parser.add_argument("--calendar-form", type=str, default="linear",
                        choices=("linear", "sqrt"),
                        help="Calendar fade shape: 'linear' (baseline) or 'sqrt'. Default: linear")
    parser.add_argument("--replace-at-eol", action="store_true",
                        help="Fade-NPV replacement policy: replace at SOH_eol (the forced-"
                             "replacement sensitivity). Default (unset): run-to-fade base case "
                             "— keep operating the ageing battery, replacing only if SOH falls "
                             "to --soh-floor.")
    parser.add_argument("--soh-floor", type=float, default=0.60, metavar="FRAC",
                        help="Run-to-fade hard SOH floor: force a replacement if the battery "
                             "fades to this SOH within the horizon. Default: 0.60 "
                             "(literature-defensible end-of-life threshold)")
    parser.add_argument("--year", type=int, default=2025,
                        help="Reference calendar year for the canonical index. Default: 2025 "
                             "(matches the 2025 Agile + demand; PV is 2023, aligned "
                             "positionally).")
    parser.add_argument("--terminal-soc-daily", action="store_true",
                        help="Force each day to end at its initial SOC (self-contained days).")
    parser.add_argument("--plot-date", type=str, default=None, metavar="YYYY-MM-DD",
                        help="Date of the single-day dispatch plot (default: first July day).")
    parser.add_argument("--pv-file", type=Path, default=DEFAULT_PV)
    parser.add_argument("--demand-file", type=Path, default=DEFAULT_DEMAND)
    parser.add_argument("--agile-file", type=Path, default=DEFAULT_AGILE)
    args = parser.parse_args()

    # Component capex from per-unit costs. The hybrid inverter is sized to match
    # the PV array. The battery cost is both the replacement cost and the basis
    # for the derived throughput penalty; the whole-system outlay adds PV and
    # inverter on top.
    pv_capex = args.pv_kwp * args.pv_cost_per_kwp
    inverter_capex = args.pv_kwp * args.inverter_cost_per_kw
    args.battery_capex = args.battery_cap * args.battery_cost_per_kwh
    args.system_capex = pv_capex + inverter_capex + args.battery_capex
    # PV O&M is an incremental cost only in the whole-system framing; a
    # battery-marginal household already owns and maintains the PV, so the O&M
    # cancels against the counterfactual.
    om_cost_per_year = (
        args.pv_om_frac * pv_capex if args.framing == "whole-system" else 0.0
    )

    # Default degradation cost is the physically-grounded derived penalty
    # (capex spread over lifetime throughput), not an assumed round number.
    deg_params = DegradationParams(
        soh_eol=args.soh_eol,
        cycle_life_efc=args.cycle_life_efc,
        calendar_life_years=args.calendar_life_years,
        calendar_form=args.calendar_form,
    )
    deg_cost_is_derived = args.deg_cost is None
    if deg_cost_is_derived:
        args.deg_cost = derive_throughput_penalty(
            args.battery_capex, args.battery_cap, deg_params
        )

    print("Loading and aligning data...")
    data = load_all(args.pv_file, args.demand_file, args.agile_file, year=args.year)

    # Spec 03: replace the default (Agile) price columns with the selected tariff.
    # For --tariff agile --export matched this reproduces the Agile CSV prices
    # exactly, so the default behaviour is unchanged.
    rates = TariffRates()
    imp, exp = build_tariff(
        args.tariff, data.index, rates, agile_path=args.agile_file,
        export=args.export, seg_rate=args.seg_rate,
    )
    data["import_price"] = imp.to_numpy()
    data["export_price"] = exp.to_numpy()

    print(f"  {len(data):,} half-hourly periods ({len(data) // HALFHOURS_PER_DAY} days)")
    print(f"  PV total:     {data['pv_kw'].sum() * DT_HOURS:8.0f} kWh/yr")
    print(f"  Demand total: {data['demand_kw'].sum() * DT_HOURS:8.0f} kWh/yr")
    print(f"  Tariff:       {args.tariff} import / {args.export} export "
          f"(mean import {data['import_price'].mean() * 100:.2f} p/kWh)")

    battery = BatteryParams(
        capacity_kwh=args.battery_cap,
        max_charge_kw=args.max_power,
        max_discharge_kw=args.max_power,
        degradation_cost_per_kwh=args.deg_cost,
    )

    print("\nSolving annual rolling-horizon dispatch (day-by-day)...")
    schedule = solve_year(data, battery, terminal_soc_daily=args.terminal_soc_daily)

    bat = battery_annual_costs(schedule, battery)

    # Framing selects the counterfactual and the initial capital outlay.
    #   whole-system: vs a household with no PV and no battery (all demand
    #                 imported); initial outlay is the whole PV+inverter+battery.
    #   battery-marginal: vs a household that already owns PV but no battery;
    #                 initial outlay is the battery only.
    # Framing selects the counterfactual and the initial capital outlay.
    #   whole-system: the HEADLINE counterfactual is a do-nothing household — no
    #                 PV, no battery, all demand imported on the standard FLAT
    #                 tariff (not the system's tariff). Initial outlay = whole
    #                 system. A same-tariff grid-only cost is also reported as a
    #                 decomposition (isolating PV-BESS value from tariff switch).
    #   battery-marginal: vs a household that already owns PV (same tariff);
    #                 initial outlay is the battery only.
    if args.framing == "whole-system":
        cf = grid_only_flat_cost(data, rates.flat_rate)
        cf_same = grid_only_cost(data)
        initial_capex = args.system_capex
        # The hybrid inverter is assumed to share the battery's 10-yr life, so
        # the two are replaced together; PV persists for the horizon.
        replacement_capex = args.battery_capex + inverter_capex
    else:
        cf = counterfactual_cost(data)
        cf_same = None
        initial_capex = args.battery_capex
        replacement_capex = args.battery_capex

    # Q9: the degradation cost is NOT subtracted from the annual saving. Wear is
    # priced once, via the explicitly-timed battery replacement in the NPV.
    annual_savings = cf["net_cost"] - bat["net_cost"]
    payback_years = initial_capex / annual_savings if annual_savings > 0 else float("inf")

    print("\n" + "=" * 60)
    print("ANNUAL TECHNO-ECONOMIC ASSESSMENT")
    print("=" * 60)
    print(f"Tariff:  {args.tariff} import / {args.export} export "
          f"(mean import {data['import_price'].mean() * 100:.2f} p/kWh)")
    print(f"Framing: {args.framing}; deg cost {args.deg_cost * 100:.2f} p/kWh "
          f"({'derived' if deg_cost_is_derived else 'override'})")
    print(f"Capex:   PV {pv_capex:,.0f} + inverter {inverter_capex:,.0f} + battery "
          f"{args.battery_capex:,.0f} = GBP {initial_capex:,.0f}")
    print("-" * 60)
    print(f"  Operating cost, PV+battery (GBP/yr):  {bat['net_cost']:10.2f}")
    print(f"    (import {bat['import_cost']:.2f} - export {bat['export_revenue']:.2f})")
    if args.framing == "whole-system":
        print(f"  Counterfactual: grid-only @ flat {rates.flat_rate * 100:.2f}p: "
              f"{cf['net_cost']:10.2f}")
        print(f"  Annual saving (headline):             {annual_savings:10.2f}")
        saving_same = cf_same["net_cost"] - bat["net_cost"]
        tariff_switch = cf["net_cost"] - cf_same["net_cost"]
        print("  -- decomposition (sensitivity) --")
        print(f"     grid-only @ this tariff:           {cf_same['net_cost']:10.2f}")
        print(f"     PV-BESS saving (same tariff):      {saving_same:10.2f}")
        print(f"     tariff-switch effect:              {tariff_switch:10.2f}")
    else:
        print(f"  Counterfactual: PV-only (same tariff): {cf['net_cost']:10.2f}")
        print(f"  Annual saving (headline):             {annual_savings:10.2f}")
    print("-" * 60)
    print(f"  Battery throughput: {bat['throughput_kwh']:,.0f} kWh/yr "
          f"({bat['throughput_kwh'] / (2 * args.battery_cap):.0f} EFC); deg "
          f"GBP {bat['degradation_cost']:,.0f}/yr (diagnostic)")
    if np.isfinite(payback_years):
        print(f"  Simple payback:     {payback_years:.1f} years")
    else:
        print("  Simple payback:     never")
    print("=" * 60)

    # Lifetime NPV assessment (the headline viability metric). The initial outlay
    # is the whole system (or battery, per framing); each replacement is the
    # battery + inverter (both 10-yr life; PV persists), applied via
    # replacement_cost_factor so economics.py is unchanged.
    econ = EconomicParams(
        battery_capex=initial_capex,
        discount_rate=args.discount_rate,
        horizon_years=args.horizon_years,
        price_escalation=args.price_escalation,
        battery_life_years=args.battery_life_years,
        replacement_cost_factor=replacement_capex / initial_capex,
        om_cost_per_year=om_cost_per_year,
    )
    npv_result = compute_npv(annual_savings, econ)
    print("LIFETIME NPV — forced 10-yr replacement (conservative sensitivity)")
    print("-" * 56)
    print(f"  Discount rate (real): {args.discount_rate * 100:.1f}%")
    print(f"  Horizon:              {args.horizon_years} years")
    print(f"  Price escalation:     {args.price_escalation * 100:.1f}%/yr (real)")
    print(f"  Battery life:         {args.battery_life_years:.1f} years")
    print(f"  PV O&M (per year):    GBP {om_cost_per_year:,.2f}")
    print(f"  PV of benefits (GBP)  {npv_result.pv_benefits:11.2f}")
    print(f"  PV of costs (GBP)     {npv_result.pv_costs:11.2f}")
    print(f"  Net present value     GBP {npv_result.npv:,.2f}")
    print(f"  Benefit-cost ratio    {npv_result.bcr:.3f}")
    if np.isfinite(npv_result.discounted_payback_years):
        print(f"  Discounted payback:   {npv_result.discounted_payback_years:.0f} years")
    else:
        print(f"  Discounted payback:   never (within {args.horizon_years} yr horizon)")
    verdict = "VIABLE" if npv_result.npv > 0 else "NOT VIABLE"
    print(f"  Verdict (NPV > 0):    {verdict}")
    print("=" * 56)

    _report_degradation(data, battery, cf, schedule, econ, args)

    _save_outputs(schedule, data, args)


def _report_degradation(
    data: pd.DataFrame,
    battery: BatteryParams,
    cf: dict[str, float],
    schedule: pd.DataFrame,
    econ: EconomicParams,
    args,
) -> None:
    """Report the derived throughput penalty, SOC exposure, and (optionally) a
    fade-adjusted NPV using the exogenous capacity-fade model (Spec 02)."""
    deg = DegradationParams(
        soh_eol=args.soh_eol,
        cycle_life_efc=args.cycle_life_efc,
        calendar_life_years=args.calendar_life_years,
        calendar_form=args.calendar_form,
        replace_at_eol=args.replace_at_eol,
        soh_floor=args.soh_floor,
    )

    # Cheap, always-on diagnostics from the base-year schedule.
    c_thr = derive_throughput_penalty(args.battery_capex, args.battery_cap, deg)
    exposure = soc_exposure(schedule["soc"].to_numpy(), DT_HOURS, thresholds=(0.8,))

    print("DEGRADATION & SOC EXPOSURE (Spec 02)")
    print("-" * 56)
    print(f"  Derived throughput penalty: {c_thr * 100:.2f} p/kWh "
          f"(capex / {args.cycle_life_efc:.0f} EFC over {args.battery_cap:.1f} kWh)")
    print(f"    (dispatch used {args.deg_cost * 100:.2f} p/kWh)")
    print(f"  Mean SOC:                   {exposure['mean_soc']:.3f}")
    print(f"  Time-weighted mean SOC:     {exposure['time_weighted_mean_soc']:.3f}")
    print(f"  Fraction of time SOC>0.80:  {exposure['frac_time_above'][0.8]:.3f}")
    print("=" * 56)

    if not args.fade_npv:
        print("  (Run with --fade-npv for the fade-adjusted NPV; it re-dispatches")
        print("   the full year for each horizon year and is slow.)")
        print("=" * 56)
        return

    print("Running exogenous capacity-fade simulation "
          f"({args.horizon_years} annual re-dispatches; this is slow)...")

    def dispatch_year(capacity_kwh: float, soc_max: float):
        aged = replace(battery, capacity_kwh=capacity_kwh, soc_max=soc_max)
        sched = solve_year(data, aged, terminal_soc_daily=args.terminal_soc_daily)
        costs = battery_annual_costs(sched, aged)
        # Q9: degradation not subtracted; wear is priced via replacement capex.
        saving = cf["net_cost"] - costs["net_cost"]
        return saving, costs["throughput_kwh"], sched["soc"].to_numpy()

    fade = simulate_capacity_fade(
        dispatch_year=dispatch_year,
        capacity_kwh_nominal=args.battery_cap,
        soc_max=battery.soc_max,
        horizon_years=args.horizon_years,
        params=deg,
    )

    # Q10 fix: value the NPV replacement on the life the simulation ACTUALLY
    # realised (first simulated replacement), not the year-1-EFC extrapolation in
    # fade.effective_life_years — otherwise the capex is booked ~2 years before
    # the trajectory being valued actually replaces the battery.
    if fade.replacement_years:
        realised_life = fade.replacement_years[0] - 1
    else:
        realised_life = args.horizon_years + 1  # survives the horizon; no replacement

    # Credit the unconsumed *warranty* value of the battery+inverter in service
    # at the horizon end (straight-line over the warranty life, discounted). The
    # NPV books each replacement at t = k·ceil(realised_life); the unit running
    # in the final year was installed at the latest such t (or t = 0 if never
    # replaced). Depreciating over the warranty life (not the longer realised
    # life) is the more conservative, more defensible book-value convention.
    horizon = args.horizon_years
    warranty = args.battery_life_years
    replacement_capex = econ.battery_capex * econ.replacement_cost_factor
    if fade.replacement_years and realised_life < horizon:
        k_last = (horizon - 1) // realised_life          # largest k with k·life < horizon
        install_t = k_last * realised_life
    else:
        install_t = 0
    age_at_horizon = horizon - install_t
    remaining_frac = max(0.0, (warranty - age_at_horizon) / warranty)
    terminal_residual = replacement_capex * remaining_frac

    econ_fade = replace(econ, battery_life_years=realised_life)
    npv_fade = compute_npv(
        fade.saving_stream, econ_fade, terminal_residual_value=terminal_residual
    )

    # Persist the full fade trajectory for inspection / the dissertation. The
    # SOH column shows resets (back to 1.0) in any replacement year.
    replaced = set(fade.replacement_years)
    fade_df = pd.DataFrame(
        {
            "year": range(1, args.horizon_years + 1),
            "soh_start_of_year": fade.soh_trajectory,
            "annual_saving_gbp": fade.saving_stream,
            "efc": fade.efc_per_year,
            "replaced": [y in replaced for y in range(1, args.horizon_years + 1)],
        }
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    fade_csv = RESULTS_DIR / f"stage2_fade_trajectory_{args.year}.csv"
    fade_df.to_csv(fade_csv, index=False)

    # Report the deepest fade actually reached, not the horizon-end value: if a
    # replacement lands on the final year the SOH resets to 1.0 and would
    # otherwise hide the fade entirely.
    min_soh = min(fade.soh_trajectory)
    min_saving = min(fade.saving_stream)

    policy = (
        "forced replacement at SOH_eol"
        if args.replace_at_eol
        else f"run-to-fade (replace only below SOH {args.soh_floor:.2f})"
    )
    print("\n" + "=" * 56)
    print("LIFETIME NPV — run-to-fade (BASELINE)")
    print("=" * 56)
    print(f"  Policy:                  {policy}")
    print(f"  Replacement year(s):     "
          f"{fade.replacement_years if fade.replacement_years else 'none in horizon'}")
    print(f"  Realised life (NPV):     {realised_life} years "
          f"(first replacement at year "
          f"{fade.replacement_years[0] if fade.replacement_years else '—'})")
    print(f"  Yr-1 EFC extrapolation:  {fade.effective_life_years:.2f} years "
          f"(diagnostic; from year-1 EFC {fade.efc_per_year[0]:.0f})")
    print(f"  Min SOH reached:         {min_soh:.3f} "
          f"(deepest fade before any replacement)")
    print(f"  Year-1 saving (GBP):     {fade.saving_stream[0]:,.2f}")
    print(f"  Min annual saving (GBP): {min_saving:,.2f}  (at deepest fade)")
    print(f"  Terminal residual (GBP): {terminal_residual:,.2f}  (unused warranty "
          f"value at yr {horizon}, age {age_at_horizon} of {warranty:.0f})")
    print(f"  Fade trajectory saved:   {fade_csv}")
    print("-" * 56)
    print(f"  PV of benefits (GBP)     {npv_fade.pv_benefits:11.2f}")
    print(f"  PV of costs (GBP)        {npv_fade.pv_costs:11.2f}")
    print(f"  Net present value        GBP {npv_fade.npv:,.2f}")
    print(f"  Benefit-cost ratio       {npv_fade.bcr:.3f}")
    verdict = "VIABLE" if npv_fade.npv > 0 else "NOT VIABLE"
    print(f"  Verdict (NPV > 0):       {verdict}")
    print("=" * 56)


def _save_outputs(schedule: pd.DataFrame, data: pd.DataFrame, args) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)

    csv_path = RESULTS_DIR / f"stage2_schedule_{args.year}.csv"
    schedule.to_csv(csv_path)
    print(f"\nSaved annual schedule to {csv_path}")

    # Select a single day for the dispatch plot.
    if args.plot_date:
        plot_day = pd.Timestamp(args.plot_date)
    else:
        # Default: first day in July (representative summer day with PV output).
        plot_day = pd.Timestamp(f"{args.year}-07-01")

    day_data = schedule.loc[plot_day.strftime("%Y-%m-%d")]
    if day_data.empty:
        day_data = schedule.iloc[:HALFHOURS_PER_DAY]
        plot_day = day_data.index[0].normalize()

    fig, (ax1, ax3) = plt.subplots(
        2, 1, figsize=(11, 8),
        gridspec_kw={"height_ratios": [3, 1.5]},
        sharex=True,
    )

    # ── Top panel: power flows ────────────────────────────────────────────────
    ax1.plot(day_data.index, day_data["demand_kw"],
             label="Demand", color="black", lw=1.5)
    ax1.plot(day_data.index, day_data["pv_kw"],
             label="PV", color="orange", lw=1.5)
    ax1.plot(day_data.index, day_data["p_import_kw"],
             label="Grid import", color="red", ls="--")
    ax1.plot(day_data.index, day_data["p_export_kw"],
             label="Grid export", color="green", ls="--")
    ax1.plot(day_data.index, day_data["p_charge_kw"],
             label="Charge", color="blue")
    ax1.plot(day_data.index, day_data["p_discharge_kw"],
             label="Discharge", color="purple")
    ax1.set_ylabel("Power (kW)")
    ax1.grid(True, alpha=0.3)
    ax1.set_title(
        f"Stage 2 dispatch — {plot_day.strftime('%A %-d %B %Y')} "
        f"(deg cost {args.deg_cost * 100:.0f}p/kWh)"
    )

    # SOC on right axis of top panel
    ax2 = ax1.twinx()
    soc_index = list(day_data.index) + [day_data.index[-1] + pd.Timedelta(minutes=30)]
    soc_values = list(day_data["soc"]) + [float(day_data["soc_end"].iloc[-1])]
    ax2.plot(soc_index, soc_values, label="SOC", color="grey", lw=2, alpha=0.6)
    ax2.set_ylabel("State of charge (fraction)")
    ax2.set_ylim(0, 1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2,
               loc="upper left", ncol=3, fontsize=9)

    # ── Bottom panel: Agile prices ────────────────────────────────────────────
    import_p = day_data["import_price"] * 100   # £/kWh → p/kWh
    export_p = day_data["export_price"] * 100
    deg_p = args.deg_cost * 100                  # degradation cost in p/kWh

    # Shade any negative-price (export-to-grid) periods
    ax3.fill_between(
        day_data.index, import_p, 0,
        where=(import_p < 0).values,
        color="red", alpha=0.15, label="_nolegend_",
    )
    ax3.axhline(0, color="black", lw=0.7)

    ax3.step(day_data.index, import_p, where="post",
             label="Import price", color="red", lw=1.5)
    ax3.step(day_data.index, export_p, where="post",
             label="Export price", color="green", lw=1.5)

    # Degradation cost reference line.
    # A full charge→discharge cycle costs 2×deg_cost p/kWh of throughput.
    # The import price must exceed (export price + 2×deg_cost) for grid
    # charge→export arbitrage to be worthwhile, or import must fall below
    # (avoided peak price − 2×deg_cost) for load-shift cycling to be worthwhile.
    ax3.axhline(
        deg_p,
        color="blue", lw=1.0, ls=":",
        label=f"Degradation cost ({deg_p:.0f}p/kWh per pass)",
    )

    ax3.set_ylabel("Price (p/kWh)")
    ax3.set_xlabel("Time")
    ax3.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%H:%M"))
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc="upper right", fontsize=8, ncol=3)

    fig.tight_layout()

    png_path = RESULTS_DIR / f"stage2_dispatch_day_{plot_day.strftime('%Y%m%d')}.png"
    fig.savefig(png_path, dpi=150)
    print(f"Saved figure to {png_path}")


if __name__ == "__main__":
    main()
