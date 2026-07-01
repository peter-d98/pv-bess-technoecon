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
from src.economics import EconomicParams, annual_saving_from_costs, compute_npv
from src.model import solve_dispatch

DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"

DEFAULT_PV = DATA_DIR / "Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv"
DEFAULT_DEMAND = DATA_DIR / "demand_halfhourly_2023.csv"
DEFAULT_AGILE = DATA_DIR / "agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv"

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
    parser.add_argument("--deg-cost", type=float, default=0.05, metavar="GBP_PER_KWH",
                        help="Throughput degradation cost (GBP/kWh cycled). Default: 0.05")
    parser.add_argument("--battery-cap", type=float, default=10.0, metavar="KWH",
                        help="Battery capacity (kWh). Default: 10.0")
    parser.add_argument("--max-power", type=float, default=3.0, metavar="KW",
                        help="Max charge/discharge power (kW). Default: 3.0")
    parser.add_argument("--battery-capex", type=float, default=4000.0, metavar="GBP",
                        help="Installed battery capital cost for payback (GBP). Default: 4000")
    parser.add_argument("--discount-rate", type=float, default=0.05, metavar="RATE",
                        help="Real discount rate for NPV. Default: 0.05")
    parser.add_argument("--horizon-years", type=int, default=20, metavar="YEARS",
                        help="NPV analysis horizon (years). Default: 20")
    parser.add_argument("--price-escalation", type=float, default=0.02, metavar="RATE",
                        help="Real electricity price escalation per year. Default: 0.02")
    parser.add_argument("--battery-life-years", type=float, default=12.0, metavar="YEARS",
                        help="Battery calendar/cycle life for replacement timing. Default: 12")
    parser.add_argument("--year", type=int, default=2023,
                        help="Reference calendar year for the canonical index. Default: 2023")
    parser.add_argument("--terminal-soc-daily", action="store_true",
                        help="Force each day to end at its initial SOC (self-contained days).")
    parser.add_argument("--plot-date", type=str, default=None, metavar="YYYY-MM-DD",
                        help="Date of the single-day dispatch plot (default: first July day).")
    parser.add_argument("--pv-file", type=Path, default=DEFAULT_PV)
    parser.add_argument("--demand-file", type=Path, default=DEFAULT_DEMAND)
    parser.add_argument("--agile-file", type=Path, default=DEFAULT_AGILE)
    args = parser.parse_args()

    print("Loading and aligning data...")
    data = load_all(args.pv_file, args.demand_file, args.agile_file, year=args.year)
    print(f"  {len(data):,} half-hourly periods ({len(data) // HALFHOURS_PER_DAY} days)")
    print(f"  PV total:     {data['pv_kw'].sum() * DT_HOURS:8.0f} kWh/yr")
    print(f"  Demand total: {data['demand_kw'].sum() * DT_HOURS:8.0f} kWh/yr")

    battery = BatteryParams(
        capacity_kwh=args.battery_cap,
        max_charge_kw=args.max_power,
        max_discharge_kw=args.max_power,
        degradation_cost_per_kwh=args.deg_cost,
    )

    print("\nSolving annual rolling-horizon dispatch (day-by-day)...")
    schedule = solve_year(data, battery, terminal_soc_daily=args.terminal_soc_daily)

    bat = battery_annual_costs(schedule, battery)
    cf = counterfactual_cost(data)

    # True annual saving must deduct degradation: it is a real cost to the
    # battery owner (wear → earlier replacement) not just an optimiser penalty.
    battery_total_cost = bat["net_cost"] + bat["degradation_cost"]
    annual_savings = cf["net_cost"] - battery_total_cost
    payback_years = args.battery_capex / annual_savings if annual_savings > 0 else float("inf")

    print("\n" + "=" * 56)
    print("ANNUAL TECHNO-ECONOMIC VIABILITY ASSESSMENT")
    print("=" * 56)
    print(f"Degradation cost assumption: {args.deg_cost * 100:.1f} p/kWh")
    print(f"Battery: {args.battery_cap:.0f} kWh / {args.max_power:.0f} kW, capex GBP {args.battery_capex:,.0f}")
    print("-" * 56)
    print("                        With battery   No battery")
    print(f"  Import cost (GBP)     {bat['import_cost']:11.2f}  {cf['import_cost']:11.2f}")
    print(f"  Export revenue (GBP)  {bat['export_revenue']:11.2f}  {cf['export_revenue']:11.2f}")
    print(f"  Net energy cost (GBP) {bat['net_cost']:11.2f}  {cf['net_cost']:11.2f}")
    print(f"  Degradation (GBP)     {bat['degradation_cost']:11.2f}  {0.0:11.2f}")
    print(f"  Total cost (GBP)      {battery_total_cost:11.2f}  {cf['net_cost']:11.2f}")
    print("-" * 56)
    print(f"  Battery throughput:   {bat['throughput_kwh']:,.0f} kWh/yr "
          f"({bat['throughput_kwh'] / (2 * args.battery_cap):.0f} equiv. full cycles)")
    print(f"  Annual saving:        GBP {annual_savings:,.2f}  (net cost reduction incl. degradation)")
    if np.isfinite(payback_years):
        print(f"  Simple payback:       {payback_years:.1f} years")
    else:
        print(f"  Simple payback:       never (battery not economic at this deg cost)")
    print("=" * 56)

    # Lifetime NPV assessment (the headline viability metric).
    econ = EconomicParams(
        battery_capex=args.battery_capex,
        discount_rate=args.discount_rate,
        horizon_years=args.horizon_years,
        price_escalation=args.price_escalation,
        battery_life_years=args.battery_life_years,
    )
    npv_result = compute_npv(annual_savings, econ)
    print("LIFETIME NPV ASSESSMENT")
    print("-" * 56)
    print(f"  Discount rate (real): {args.discount_rate * 100:.1f}%")
    print(f"  Horizon:              {args.horizon_years} years")
    print(f"  Price escalation:     {args.price_escalation * 100:.1f}%/yr (real)")
    print(f"  Battery life:         {args.battery_life_years:.1f} years")
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

    _save_outputs(schedule, data, args)


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
