"""Stage 1 runner: solve the synthetic PV-BESS dispatch problem and plot results.

Run from the repository root:

    python scripts/run_stage1.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt

# Allow running as a plain script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.battery import BatteryParams
from src.data_gen import make_stage1_dataset
from src.model import solve_dispatch

RESULTS_DIR = REPO_ROOT / "results"


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1 PV-BESS dispatch optimisation.")
    parser.add_argument("--deg-cost", type=float, default=0.05,
                        metavar="GBP_PER_KWH",
                        help="Throughput degradation cost (GBP/kWh cycled). Default: 0.05")
    parser.add_argument("--battery-cap", type=float, default=10.0,
                        metavar="KWH",
                        help="Battery capacity (kWh). Default: 10.0")
    parser.add_argument("--max-power", type=float, default=3.0,
                        metavar="KW",
                        help="Max charge/discharge power (kW). Default: 3.0")
    parser.add_argument("--no-terminal-soc", action="store_true",
                        help="Disable the terminal SOC=initial constraint.")
    args = parser.parse_args()

    data = make_stage1_dataset()
    dt_hours = data.attrs["dt_hours"]

    battery = BatteryParams(
        capacity_kwh=args.battery_cap,
        max_charge_kw=args.max_power,
        max_discharge_kw=args.max_power,
        degradation_cost_per_kwh=args.deg_cost,
    )

    result = solve_dispatch(
        pv_kw=data["pv_kw"].to_numpy(),
        demand_kw=data["demand_kw"].to_numpy(),
        import_price=data["import_price"].to_numpy(),
        export_price=data["export_price"].to_numpy(),
        battery=battery,
        dt_hours=dt_hours,
        terminal_soc_equals_initial=not args.no_terminal_soc,
    )

    print(f"Solve status:        {result.status}")
    if result.status not in ("optimal", "optimal_inaccurate"):
        print("Problem did not solve to optimality; aborting.")
        return

    print(f"Objective (net+deg): GBP {result.objective:6.3f}")
    print(f"Import cost:         GBP {result.import_cost:6.3f}")
    print(f"Export revenue:      GBP {result.export_revenue:6.3f}")
    print(f"Degradation cost:    GBP {result.degradation_cost:6.3f}")
    print(f"Net energy cost:     GBP {result.net_cost:6.3f}")

    _plot(result, dt_hours)


def _plot(result, dt_hours: float) -> None:
    schedule = result.schedule
    hours = schedule.index

    fig, ax1 = plt.subplots(figsize=(11, 6))

    ax1.plot(hours, schedule["demand_kw"], label="Demand", color="black", lw=2)
    ax1.plot(hours, schedule["pv_kw"], label="PV", color="orange", lw=2)
    ax1.plot(hours, schedule["p_import_kw"], label="Grid import", color="red", ls="--")
    ax1.plot(hours, schedule["p_export_kw"], label="Grid export", color="green", ls="--")
    ax1.plot(hours, schedule["p_charge_kw"], label="Battery charge", color="blue")
    ax1.plot(
        hours, schedule["p_discharge_kw"], label="Battery discharge", color="purple"
    )
    ax1.set_xlabel("Hour of day")
    ax1.set_ylabel("Power (kW)")
    ax1.set_xticks(range(0, len(hours), 2))
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()
    soc_hours = list(hours) + [hours[-1] + dt_hours]
    soc_values = list(schedule["soc"]) + [schedule["soc_end"].iloc[-1]]
    ax2.plot(soc_hours, soc_values, label="SOC", color="grey", lw=2, alpha=0.6)
    ax2.set_ylabel("State of charge (fraction)")
    ax2.set_ylim(0, 1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", ncol=2)

    ax1.set_title("Stage 1 synthetic PV-BESS optimal dispatch")
    fig.tight_layout()

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / "stage1_dispatch.png"
    fig.savefig(out_path, dpi=150)
    print(f"Saved figure to {out_path}")


if __name__ == "__main__":
    main()
