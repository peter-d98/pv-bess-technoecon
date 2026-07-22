"""Compare dispatch strategies to quantify the daily-MILP myopia effect.

Three strategies are compared for a configurable cell (default: Glasgow / Agile
/ 10 kWh, 7.42 p/kWh penalty):

  1. daily_milp     — production method: one MILP solve per day with SOC
                      continuity across midnight; the solver never sees beyond
                      the current day's boundary.
  2. fullyear_milp  — the entire year in a single MILP solve; the optimiser
                      has perfect foresight across every midnight boundary.
                      Runtime scales with horizon size (see --horizon-days).
  3. self_consumption — greedy PV self-consumption heuristic; implicitly
                      carries PV surplus into the next morning.

Reported metrics (vs a PV-only, no-battery counterfactual on the same tariff):
  * annual import cost (£)
  * annual export revenue (£)
  * battery throughput (kWh)
  * annual saving vs PV-only counterfactual (£)

Usage
-----
    python scripts/test_myopia.py                         # default cell
    python scripts/test_myopia.py --tariff flat           # where myopia bites hardest
    python scripts/test_myopia.py --horizon-days 7        # 7-day rolling MILP (faster)
    python scripts/test_myopia.py --skip-fullyear         # skip the slow full-year solve
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from src.battery import BatteryParams
from src.controllers import self_consumption as sc_controller
from src.data_loader import HALFHOURS_PER_DAY, load_all
from src.degradation import DegradationParams, derive_throughput_penalty
from src.locations import get_location, resolve_paths
from src.model import solve_dispatch
from src.tariffs import build_tariff

# Re-use the Stage 2 accounting helpers without modification.
from run_stage2 import DT_HOURS, battery_annual_costs, counterfactual_cost, solve_year  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
DEFAULT_DEMAND = DATA_DIR / "demand_halfhourly_2025.csv"


# ---------------------------------------------------------------------------
# Full-year (or N-day-chunk) MILP
# ---------------------------------------------------------------------------

def solve_year_chunked(
    data: pd.DataFrame,
    battery: BatteryParams,
    horizon_days: int,
) -> pd.DataFrame:
    """Solve the dispatch with an N-day rolling MILP horizon.

    ``horizon_days=365`` (or more than the data's days) collapses to a single
    full-year solve.  ``horizon_days=1`` reproduces the daily method.  Any
    intermediate value gives a rolling multi-day window with SOC continuity.

    For the full-year solve the terminal SOC is constrained equal to the
    initial SOC (year-level accounting fairness); for shorter chunks it is
    left free (continuity only) to mirror the production method.
    """
    n = len(data)
    n_days = n // HALFHOURS_PER_DAY
    chunk = min(horizon_days, n_days)
    full_year = chunk >= n_days

    pv = data["pv_kw"].to_numpy()
    demand = data["demand_kw"].to_numpy()
    imp = data["import_price"].to_numpy()
    exp_p = data["export_price"].to_numpy()

    carried_soc = battery.soc_init
    schedules: list[pd.DataFrame] = []

    d = 0
    while d < n_days:
        end_day = min(d + chunk, n_days)
        sl = slice(d * HALFHOURS_PER_DAY, end_day * HALFHOURS_PER_DAY)
        step_bat = replace(battery, soc_init=carried_soc)

        # For the full-year solve enforce terminal == initial (year accounting).
        terminal = full_year

        result = solve_dispatch(
            pv_kw=pv[sl],
            demand_kw=demand[sl],
            import_price=imp[sl],
            export_price=exp_p[sl],
            battery=step_bat,
            dt_hours=DT_HOURS,
            terminal_soc_equals_initial=terminal,
        )
        if result.status not in ("optimal", "optimal_inaccurate"):
            raise RuntimeError(
                f"Days {d}–{end_day - 1} failed to solve: {result.status}"
            )

        schedule = result.schedule.copy()
        schedule.index = data.index[sl]
        schedules.append(schedule)

        carried_soc = float(schedule["soc_end"].iloc[-1])
        carried_soc = min(max(carried_soc, battery.soc_min), battery.soc_max)

        d = end_day  # non-overlapping chunks

    return pd.concat(schedules)


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def annual_saving(battery_costs: dict, cf_costs: dict) -> float:
    """Saving vs PV-only counterfactual: net_cost_cf - net_cost_battery."""
    return cf_costs["net_cost"] - battery_costs["net_cost"]


def print_comparison(results: dict[str, dict]) -> None:
    """Print a formatted comparison table."""
    metrics = [
        ("import_cost",      "Import cost (£/yr)",          "{:.2f}"),
        ("export_revenue",   "Export revenue (£/yr)",        "{:.2f}"),
        ("throughput_kwh",   "Battery throughput (kWh/yr)",  "{:.1f}"),
        ("annual_saving",    "Annual saving vs PV-only (£)", "{:.2f}"),
    ]
    labels = list(results.keys())
    col_w = max(len(k) for k in labels) + 2

    # Header
    header = f"{'Metric':<38}" + "".join(f"{k:>{col_w}}" for k in labels)
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))

    for key, label, fmt in metrics:
        row = f"{label:<38}"
        for k in labels:
            val = results[k].get(key, float("nan"))
            row += f"{fmt.format(val):>{col_w}}"
        print(row)

    print("=" * len(header))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quantify the daily-MILP myopia effect by comparing dispatch strategies."
    )
    parser.add_argument("--location", default="glasgow",
                        help="Study location key (default: glasgow)")
    parser.add_argument("--tariff", default="agile",
                        choices=["flat", "e7", "agile"],
                        help="Tariff name (default: agile)")
    parser.add_argument("--battery-cap", type=float, default=10.0, metavar="KWH",
                        help="Battery nominal capacity kWh (default: 10.0)")
    parser.add_argument("--deg-cost", type=float, default=None, metavar="GBP_PER_KWH",
                        help="Throughput degradation cost £/kWh cycled. "
                             "Default: derived from capex / (6000 EFC × 2 × capacity).")
    parser.add_argument("--horizon-days", type=int, default=365, metavar="N",
                        help="Days per MILP solve for the multi-day strategy "
                             "(default: 365 = full-year single solve). "
                             "Use a smaller value (e.g. 7) for a faster test.")
    parser.add_argument("--skip-fullyear", action="store_true",
                        help="Skip the full-year/multi-day MILP (can be slow).")
    args = parser.parse_args()

    # -- data ----------------------------------------------------------------
    loc = get_location(args.location)
    pv_path, agile_path = resolve_paths(loc, DATA_DIR)
    data = load_all(pv_path, DEFAULT_DEMAND, agile_path, year=2025)

    imp, exp = build_tariff(
        args.tariff,
        data.index,
        loc.rates,
        agile_path=agile_path,
    )
    data = data.assign(import_price=imp, export_price=exp)

    # -- battery -------------------------------------------------------------
    battery_cost_per_kwh = 890.0
    deg_cost = args.deg_cost
    if deg_cost is None:
        deg_cost = derive_throughput_penalty(
            battery_capex=battery_cost_per_kwh * args.battery_cap,
            capacity_kwh=args.battery_cap,
            params=DegradationParams(cycle_life_efc=6000.0),
        )

    battery = BatteryParams(
        capacity_kwh=args.battery_cap,
        max_charge_kw=3.0,
        max_discharge_kw=3.0,
        degradation_cost_per_kwh=deg_cost,
    )

    print(f"\nCell: {loc.label} / {args.tariff.upper()} / {args.battery_cap} kWh")
    print(f"  Degradation penalty: {deg_cost:.4f} £/kWh  ({deg_cost * 100:.2f} p/kWh)")

    cf = counterfactual_cost(data)
    print(f"  PV-only counterfactual net cost: £{cf['net_cost']:.2f}/yr")

    results: dict[str, dict] = {}

    # -- strategy 1: daily MILP (production method) --------------------------
    print("\n[1/3] Daily MILP (1-day rolling horizon)...", flush=True)
    t0 = time.perf_counter()
    sched_daily = solve_year(data, battery, terminal_soc_daily=False)
    elapsed = time.perf_counter() - t0
    costs_daily = battery_annual_costs(sched_daily, battery)
    costs_daily["annual_saving"] = annual_saving(costs_daily, cf)
    results["daily_milp"] = costs_daily
    print(f"  Done in {elapsed:.1f} s")

    # -- strategy 2: full-year (or N-day) MILP --------------------------------
    horizon = args.horizon_days
    strategy_label = (
        "fullyear_milp" if horizon >= 365 else f"milp_{horizon}d_horizon"
    )
    if args.skip_fullyear:
        print(f"\n[2/3] {strategy_label} — skipped (--skip-fullyear)")
    else:
        n_days = len(data) // HALFHOURS_PER_DAY
        n_solves = max(1, n_days // min(horizon, n_days))
        horizon_label = (
            "full-year single solve" if horizon >= n_days
            else f"{horizon}-day rolling ({n_solves} solves)"
        )
        print(f"\n[2/3] {strategy_label} ({horizon_label})...", flush=True)
        if horizon >= n_days:
            print("  Note: solving all 17,520 half-hours at once. "
                  "This may take several minutes.")
        t0 = time.perf_counter()
        sched_fy = solve_year_chunked(data, battery, horizon)
        elapsed = time.perf_counter() - t0
        costs_fy = battery_annual_costs(sched_fy, battery)
        costs_fy["annual_saving"] = annual_saving(costs_fy, cf)
        results[strategy_label] = costs_fy
        print(f"  Done in {elapsed:.1f} s")

    # -- strategy 3: self-consumption heuristic --------------------------------
    print("\n[3/3] Self-consumption heuristic...", flush=True)
    t0 = time.perf_counter()
    sched_sc = sc_controller(data, battery)
    elapsed = time.perf_counter() - t0
    costs_sc = battery_annual_costs(sched_sc, battery)
    costs_sc["annual_saving"] = annual_saving(costs_sc, cf)
    results["self_consumption"] = costs_sc
    print(f"  Done in {elapsed:.1f} s")

    # -- comparison table ----------------------------------------------------
    print_comparison(results)

    # -- myopia delta ---------------------------------------------------------
    if strategy_label in results and "daily_milp" in results:
        delta = results[strategy_label]["annual_saving"] - results["daily_milp"]["annual_saving"]
        print(
            f"\nMyopia loss (daily vs {strategy_label}): "
            f"£{delta:+.2f}/yr "
            f"({'full-year beats daily' if delta > 0 else 'daily matches or beats multi-day'})"
        )
    if "self_consumption" in results and "daily_milp" in results:
        delta_sc = (
            results["self_consumption"]["annual_saving"]
            - results["daily_milp"]["annual_saving"]
        )
        print(
            f"Self-consumption vs daily MILP saving delta: £{delta_sc:+.2f}/yr"
        )


if __name__ == "__main__":
    main()
