"""Rules-based battery controller simulation and MILP comparison.

Simulates two heuristic controllers over the full 2023 year and compares
their annual techno-economic performance against the Stage 2 MILP optimum.

Controller A — PV self-consumption only
    Models the simplest sensible real-world setup: stores PV surplus in the
    battery and discharges to cover demand deficit. No grid charging at any
    time. No price awareness. Battery is only cycled on free solar energy.

Controller B — Fixed price threshold (Agile-aware heuristic)
    Models real domestic battery systems with Agile integration (e.g.
    GivEnergy Timed Charge, Tesla Powerwall Time-Based Control). Grid-charges
    when import_price ≤ T p/kWh; discharges to avoid demand import when
    import_price > T; self-consumes PV surplus at all times. T is swept from
    5–30 p/kWh to identify the optimal threshold.

Outputs saved to results/:
    rules_comparison_table.csv              -- Annual metrics for all controllers
    rules_threshold_sweep.png               -- Controller B saving vs threshold T
    rules_dispatch_A_{date}.png             -- Controller A dispatch (2 days)
    rules_dispatch_B_{date}.png             -- Controller B optimal dispatch
    rules_dispatch_MILP_{date}.png          -- MILP optimal dispatch (re-plotted)

Prerequisite: run_stage2.py must have been run first so that
    results/stage2_schedule_2023.csv exists.

Usage
-----
    python scripts/run_rules_controller.py
    python scripts/run_rules_controller.py --battery-capex 4000 --deg-cost 0.05
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.battery import BatteryParams
from src.data_loader import load_all

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR    = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

PV_FILE     = DATA_DIR / "Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv"
DEMAND_FILE = DATA_DIR / "demand_halfhourly_2023.csv"
AGILE_FILE  = DATA_DIR / "agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv"
MILP_CSV    = RESULTS_DIR / "stage2_schedule_2023.csv"

# ── Constants ──────────────────────────────────────────────────────────────────
DT                = 0.5                      # hours per timestep
YEAR              = 2023
THRESHOLD_SWEEP   = [5, 10, 15, 20, 25, 30]  # p/kWh values to test for Controller B
PLOT_DATES        = ["2023-01-10", "2023-07-01"]


# ═══════════════════════════════════════════════════════════════════════════════
# Cost accounting (same formulation as run_stage2.py)
# ═══════════════════════════════════════════════════════════════════════════════

def annual_battery_costs(schedule: pd.DataFrame, battery: BatteryParams) -> dict:
    """Compute annual import cost, export revenue, degradation, and net cost."""
    import_cost      = float((schedule["p_import_kw"] * schedule["import_price"] * DT).sum())
    export_revenue   = float((schedule["p_export_kw"] * schedule["export_price"] * DT).sum())
    throughput_kwh   = float(((schedule["p_charge_kw"] + schedule["p_discharge_kw"]) * DT).sum())
    degradation_cost = throughput_kwh * battery.degradation_cost_per_kwh
    net_cost         = import_cost - export_revenue
    total_cost       = net_cost + degradation_cost
    return {
        "import_cost":       import_cost,
        "export_revenue":    export_revenue,
        "degradation_cost":  degradation_cost,
        "net_cost":          net_cost,
        "total_cost":        total_cost,
        "throughput_kwh":    throughput_kwh,
    }


def counterfactual_cost(data: pd.DataFrame) -> dict:
    """Annual cost with no battery: grid import/export directly from net load."""
    net_load       = data["demand_kw"] - data["pv_kw"]
    grid_import    = net_load.clip(lower=0)
    grid_export    = (-net_load).clip(lower=0)
    import_cost    = float((grid_import * data["import_price"] * DT).sum())
    export_revenue = float((grid_export * data["export_price"] * DT).sum())
    return {
        "import_cost":    import_cost,
        "export_revenue": export_revenue,
        "net_cost":       import_cost - export_revenue,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Controller simulations
# ═══════════════════════════════════════════════════════════════════════════════

def simulate_controller_a(data: pd.DataFrame, battery: BatteryParams) -> pd.DataFrame:
    """Simulate Controller A: PV self-consumption only, no grid charging.

    At every half-hour:
      - PV surplus  (pv > demand): charge battery from surplus first, export rest.
      - Demand deficit (demand > pv): discharge battery first, import remainder.

    The battery is never charged from the grid. This is the simplest sensible
    domestic battery mode and is price-agnostic — the same logic runs at every
    hour of every day regardless of the Agile tariff.

    Energy balance satisfied per half-hour. SOC continuity maintained
    across days (rolling horizon, same as the MILP runner).
    """
    cap   = battery.capacity_kwh
    eta_c = battery.eta_charge
    eta_d = battery.eta_discharge
    records = []
    soc = battery.soc_init

    for ts, row in data.iterrows():
        pv  = row["pv_kw"]
        dem = row["demand_kw"]

        p_c_avail = min(battery.max_charge_kw,
                        max(0.0, (battery.soc_max - soc) * cap / (eta_c * DT)))
        p_d_avail = min(battery.max_discharge_kw,
                        max(0.0, (soc - battery.soc_min) * cap * eta_d / DT))

        net_load = dem - pv   # + = deficit, - = PV surplus
        p_c = p_d = p_imp = p_exp = 0.0

        if net_load <= 0:
            # PV surplus: store as much as possible in battery, export the rest.
            surplus = -net_load
            p_c   = min(p_c_avail, surplus)
            p_exp = surplus - p_c

        else:
            # Demand deficit: discharge battery first, import the remainder.
            p_d   = min(p_d_avail, net_load)
            p_imp = max(0.0, net_load - p_d)

        soc_new = soc + (eta_c * p_c - p_d / eta_d) * DT / cap
        soc_new = float(np.clip(soc_new, battery.soc_min, battery.soc_max))

        records.append({
            "pv_kw": pv, "demand_kw": dem,
            "import_price": row["import_price"], "export_price": row["export_price"],
            "p_charge_kw":    p_c,   "p_discharge_kw": p_d,
            "p_import_kw":    p_imp, "p_export_kw":    p_exp,
            "soc": soc, "soc_end": soc_new,
        })
        soc = soc_new

    return pd.DataFrame(records, index=data.index)


def simulate_controller_b(
    data: pd.DataFrame,
    battery: BatteryParams,
    t_p: float,
) -> pd.DataFrame:
    """Simulate Controller B: fixed Agile price threshold.

    A single threshold T (= t_p p/kWh) governs all battery decisions:
      - import_price ≤ T: grid-charges at full rate (price is cheap).
        Any PV present contributes; excess over demand + charge is exported.
      - import_price > T and PV surplus: stores surplus PV first, exports rest.
      - import_price > T and demand deficit: discharges to cover deficit,
        imports any remainder.

    This replicates the heuristic control logic of real Agile-aware domestic
    battery systems (GivEnergy Timed Charge, Powerwall Time-Based Control).
    Unlike the MILP, no joint optimisation across all 48 half-hours is
    performed; decisions are made greedily one period at a time.
    """
    t = t_p / 100.0   # p/kWh → £/kWh
    cap   = battery.capacity_kwh
    eta_c = battery.eta_charge
    eta_d = battery.eta_discharge
    records = []
    soc = battery.soc_init

    for ts, row in data.iterrows():
        pv        = row["pv_kw"]
        dem       = row["demand_kw"]
        imp_price = row["import_price"]

        p_c_avail = min(battery.max_charge_kw,
                        max(0.0, (battery.soc_max - soc) * cap / (eta_c * DT)))
        p_d_avail = min(battery.max_discharge_kw,
                        max(0.0, (soc - battery.soc_min) * cap * eta_d / DT))

        net_load = dem - pv
        p_c = p_d = p_imp = p_exp = 0.0

        if imp_price <= t and p_c_avail > 0:
            # Cheap: grid-charge at full rate.
            # If PV also present, it reduces import (or enables export if large).
            p_c = p_c_avail
            net_grid = net_load + p_c
            if net_grid >= 0:
                p_imp = net_grid
            else:
                p_exp = -net_grid

        elif net_load <= 0:
            # PV surplus (price above threshold or battery full): self-consume.
            surplus = -net_load
            p_c   = min(p_c_avail, surplus)
            p_exp = surplus - p_c

        elif imp_price > t and p_d_avail > 0:
            # Expensive and deficit: discharge battery to reduce peak import.
            p_d   = min(p_d_avail, net_load)
            p_imp = max(0.0, net_load - p_d)

        else:
            # Mid-price with deficit (T_low < price ≤ T_high with no headroom):
            # grid serves demand; battery stays idle.
            p_imp = net_load

        soc_new = soc + (eta_c * p_c - p_d / eta_d) * DT / cap
        soc_new = float(np.clip(soc_new, battery.soc_min, battery.soc_max))

        records.append({
            "pv_kw": pv, "demand_kw": dem,
            "import_price": imp_price, "export_price": row["export_price"],
            "p_charge_kw":    p_c,   "p_discharge_kw": p_d,
            "p_import_kw":    p_imp, "p_export_kw":    p_exp,
            "soc": soc, "soc_end": soc_new,
        })
        soc = soc_new

    return pd.DataFrame(records, index=data.index)


# ═══════════════════════════════════════════════════════════════════════════════
# Plotting
# ═══════════════════════════════════════════════════════════════════════════════

def plot_dispatch_day(
    day_data: pd.DataFrame,
    title: str,
    out_path: Path,
    deg_cost_per_kwh: float,
) -> None:
    """Two-panel dispatch plot matching the run_stage2.py format.

    Top panel: power flows (kW, left axis) + SOC (fraction, right axis).
    Bottom panel: Agile import and export prices (p/kWh) with degradation
    cost reference line.
    """
    fig, (ax1, ax3) = plt.subplots(
        2, 1, figsize=(11, 8),
        gridspec_kw={"height_ratios": [3, 1.5]},
        sharex=True,
    )

    # ── Top panel ─────────────────────────────────────────────────────────────
    ax1.plot(day_data.index, day_data["demand_kw"],
             label="Demand",      color="black",  lw=1.5)
    ax1.plot(day_data.index, day_data["pv_kw"],
             label="PV",          color="orange", lw=1.5)
    ax1.plot(day_data.index, day_data["p_import_kw"],
             label="Grid import", color="red",    ls="--")
    ax1.plot(day_data.index, day_data["p_export_kw"],
             label="Grid export", color="green",  ls="--")
    ax1.plot(day_data.index, day_data["p_charge_kw"],
             label="Charge",      color="blue")
    ax1.plot(day_data.index, day_data["p_discharge_kw"],
             label="Discharge",   color="purple")
    ax1.set_ylabel("Power (kW)")
    ax1.grid(True, alpha=0.3)
    ax1.set_title(title)

    ax2 = ax1.twinx()
    soc_idx = list(day_data.index) + [day_data.index[-1] + pd.Timedelta(minutes=30)]
    soc_val = list(day_data["soc"]) + [float(day_data["soc_end"].iloc[-1])]
    ax2.plot(soc_idx, soc_val, label="SOC", color="grey", lw=2, alpha=0.6)
    ax2.set_ylabel("State of charge (fraction)")
    ax2.set_ylim(0, 1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2,
               loc="upper left", ncol=3, fontsize=9)

    # ── Bottom panel: prices ───────────────────────────────────────────────────
    import_p = day_data["import_price"] * 100   # £/kWh → p/kWh
    export_p = day_data["export_price"] * 100
    deg_p    = deg_cost_per_kwh * 100

    ax3.fill_between(day_data.index, import_p, 0,
                     where=(import_p < 0).values,
                     color="red", alpha=0.15, label="_nolegend_")
    ax3.axhline(0, color="black", lw=0.7)
    ax3.step(day_data.index, import_p, where="post",
             label="Import price", color="red",   lw=1.5)
    ax3.step(day_data.index, export_p, where="post",
             label="Export price", color="green", lw=1.5)
    ax3.axhline(deg_p, color="blue", lw=1.0, ls=":",
                label=f"Degradation cost ({deg_p:.0f}p/kWh per pass)")
    ax3.set_ylabel("Price (p/kWh)")
    ax3.set_xlabel("Time")
    ax3.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%H:%M"))
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc="upper right", fontsize=8, ncol=3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  Saved {out_path.name}")


def plot_threshold_sweep(
    thresholds: list[int],
    savings: list[float],
    saving_a: float,
    saving_milp: float,
    best_t: int,
    out_path: Path,
) -> None:
    """Controller B annual saving vs threshold T, with A and MILP baselines."""
    fig, ax = plt.subplots(figsize=(8, 5))

    ax.plot(thresholds, savings, "o-", color="#4a7fb5", lw=2, ms=7,
            label="Controller B (threshold sweep)")
    ax.axhline(saving_a,    color="#e07b39", lw=1.5, ls="--",
               label=f"Controller A (self-consume): £{saving_a:.0f}/yr")
    ax.axhline(saving_milp, color="#2a9d5c", lw=1.5, ls="--",
               label=f"MILP optimal: £{saving_milp:.0f}/yr")

    best_saving = savings[thresholds.index(best_t)]
    ax.scatter([best_t], [best_saving], color="red", zorder=5, s=80,
               label=f"Best threshold T = {best_t}p/kWh (£{best_saving:.0f}/yr)")

    ax.set_xlabel("Threshold T (p/kWh)")
    ax.set_ylabel("Annual saving incl. degradation (£/yr)")
    ax.set_title(
        "Controller B: annual saving vs price threshold\n"
        "vs Controller A (timer) and MILP optimal"
    )
    ax.set_xticks(thresholds)
    ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  Saved {out_path.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate rules-based controllers and compare to MILP."
    )
    parser.add_argument("--deg-cost",       type=float, default=0.05,
                        help="Throughput degradation cost (£/kWh). Default: 0.05")
    parser.add_argument("--battery-cap",    type=float, default=10.0,
                        help="Battery capacity (kWh). Default: 10.0")
    parser.add_argument("--max-power",      type=float, default=3.0,
                        help="Max charge/discharge power (kW). Default: 3.0")
    parser.add_argument("--battery-capex",  type=float, default=4000.0,
                        help="Battery capital cost (£) for payback. Default: 4000")
    args = parser.parse_args()

    if not MILP_CSV.exists():
        raise FileNotFoundError(
            f"MILP schedule not found at {MILP_CSV}. "
            "Run scripts/run_stage2.py first."
        )

    # ── Load data ──────────────────────────────────────────────────────────────
    print("Loading input data...")
    data = load_all(PV_FILE, DEMAND_FILE, AGILE_FILE, year=YEAR)

    battery = BatteryParams(
        capacity_kwh=args.battery_cap,
        max_charge_kw=args.max_power,
        max_discharge_kw=args.max_power,
        degradation_cost_per_kwh=args.deg_cost,
    )

    # ── Counterfactual (no battery) ────────────────────────────────────────────
    cf = counterfactual_cost(data)

    # ── Controller A ───────────────────────────────────────────────────────────
    print("Simulating Controller A (PV self-consumption only)...")
    sched_a  = simulate_controller_a(data, battery)
    costs_a  = annual_battery_costs(sched_a, battery)
    saving_a = cf["net_cost"] - costs_a["total_cost"]
    payback_a = args.battery_capex / saving_a if saving_a > 0 else float("inf")

    # ── Controller B threshold sweep ───────────────────────────────────────────
    print(f"Sweeping Controller B thresholds: {THRESHOLD_SWEEP} p/kWh...")
    b_results: dict[int, dict] = {}
    for t_p in THRESHOLD_SWEEP:
        sched = simulate_controller_b(data, battery, t_p)
        costs = annual_battery_costs(sched, battery)
        saving = cf["net_cost"] - costs["total_cost"]
        b_results[t_p] = {"schedule": sched, "costs": costs, "saving": saving}

    best_t = max(b_results, key=lambda t: b_results[t]["saving"])
    best_b = b_results[best_t]
    saving_b  = best_b["saving"]
    payback_b = args.battery_capex / saving_b if saving_b > 0 else float("inf")

    # ── Load MILP results ──────────────────────────────────────────────────────
    print("Loading MILP schedule...")
    milp_sched  = pd.read_csv(MILP_CSV, index_col=0, parse_dates=True)
    costs_milp  = annual_battery_costs(milp_sched, battery)
    saving_milp = cf["net_cost"] - costs_milp["total_cost"]
    payback_milp = args.battery_capex / saving_milp if saving_milp > 0 else float("inf")

    # ── Print comparison table ─────────────────────────────────────────────────
    deg_p = args.deg_cost * 100
    print(f"\n{'=' * 72}")
    print(f"RULES-BASED vs MILP COMPARISON")
    print(f"Battery {args.battery_cap:.0f} kWh / {args.max_power:.0f} kW  |  "
          f"deg cost {deg_p:.0f}p/kWh  |  capex £{args.battery_capex:,.0f}")
    print(f"{'=' * 72}")

    header = f"{'Controller':<28} {'Import':>7} {'Export':>7} {'Degrad':>7} {'Net':>7} {'Saving':>7} {'Payback':>8}"
    print(f"\n{header}")
    print(f"  {'(no battery)':26} {cf['import_cost']:>7.0f} {cf['export_revenue']:>7.0f}"
          f"       —  {cf['net_cost']:>7.0f}       —         —")
    print("-" * 72)

    rows = [
        ("Controller A (self-consume)",        costs_a,          saving_a,    payback_a),
        (f"Controller B (T={best_t}p — best)", best_b["costs"], saving_b, payback_b),
        ("MILP optimal",                       costs_milp,        saving_milp, payback_milp),
    ]
    for label, costs, saving, payback in rows:
        pb_str = f"{payback:.1f} yr" if payback < 1000 else ">999 yr"
        print(
            f"  {label:<26}  "
            f"{costs['import_cost']:>7.0f}  "
            f"{costs['export_revenue']:>7.0f}  "
            f"{costs['degradation_cost']:>7.0f}  "
            f"{costs['net_cost']:>7.0f}  "
            f"{saving:>7.0f}  "
            f"{pb_str:>8}"
        )

    print(f"\n  All values in £/yr.  Columns: Import cost | Export rev | Degrad cost | Net energy cost | Annual saving | Payback")

    print(f"\nController B threshold sweep (annual saving £/yr):")
    print(f"  {'T (p/kWh)':<12}", end="")
    for t_p in THRESHOLD_SWEEP:
        print(f"  {t_p:>4}p", end="")
    print()
    print(f"  {'Saving (£)':<12}", end="")
    for t_p in THRESHOLD_SWEEP:
        print(f"  {b_results[t_p]['saving']:>5.0f}", end="")
    print()
    print(f"\n  Best threshold: T = {best_t}p/kWh  →  saving £{saving_b:.0f}/yr, payback {payback_b:.1f} yr")
    print(f"\nMILP advantage over best B:   £{saving_milp - saving_b:.0f}/yr"
          f"  ({(saving_milp - saving_b) / saving_b * 100:.1f}% uplift)")
    print(f"MILP advantage over A:         £{saving_milp - saving_a:.0f}/yr"
          f"  ({(saving_milp - saving_a) / max(saving_a, 1) * 100:.1f}% uplift)")

    # ── Save comparison CSV ────────────────────────────────────────────────────
    records = []
    records.append({"controller": "No battery", **cf,
                    "degradation_cost": 0, "total_cost": cf["net_cost"],
                    "annual_saving": 0, "payback_yr": float("nan")})
    for label, costs, saving, payback in rows:
        records.append({"controller": label, **costs,
                        "annual_saving": saving,
                        "payback_yr": payback if payback < 1e9 else float("nan")})
    comparison_csv = RESULTS_DIR / "rules_comparison_table.csv"
    pd.DataFrame(records).to_csv(comparison_csv, index=False, float_format="%.4f")
    print(f"\nSaved {comparison_csv.name}")

    # ── Threshold sensitivity plot ─────────────────────────────────────────────
    print("\nGenerating plots...")
    thresholds = THRESHOLD_SWEEP
    savings_b  = [b_results[t]["saving"] for t in thresholds]
    sweep_path = RESULTS_DIR / "rules_threshold_sweep.png"
    plot_threshold_sweep(thresholds, savings_b, saving_a, saving_milp, best_t, sweep_path)

    # ── 6 dispatch plots (3 controllers × 2 days) ─────────────────────────────
    controllers = [
        ("A",    sched_a,            "Controller A — PV self-consumption only (no grid charging)"),
        ("B",    best_b["schedule"], f"Controller B — threshold T = {best_t}p/kWh (Agile-aware)"),
        ("MILP", milp_sched,         "MILP — perfect-foresight optimal"),
    ]

    for date_str in PLOT_DATES:
        plot_dt = pd.Timestamp(date_str)
        date_label = plot_dt.strftime("%A %-d %B %Y")
        for ctrl_id, sched, ctrl_label in controllers:
            day_data = sched.loc[date_str]
            title = (
                f"{ctrl_label}\n"
                f"{date_label}  |  deg cost {deg_p:.0f}p/kWh"
            )
            out_path = RESULTS_DIR / f"rules_dispatch_{ctrl_id}_{date_str.replace('-', '')}.png"
            plot_dispatch_day(day_data, title, out_path, args.deg_cost)

    print("\nDone.")


if __name__ == "__main__":
    main()
