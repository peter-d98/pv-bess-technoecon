"""Annual energy balance for the Chapter 1 case study.

The sweep retains economics and peak summaries but not dispatch schedules, so
the self-consumption and self-sufficiency rows of the case-study table cannot be
read off an assembly. This re-solves the four dispatches the table needs and
writes their energy balance to ``results/case_study_energy.csv``. The half-hourly
schedules are kept too, in ``results/case_study_schedules.csv``, so a dispatch
figure never has to re-solve what this script has already solved.

Case: Manchester, 4 kWp, 5 kWh at 0.5C, 2025 prices. The controller and wear
penalty are the per-cell optima already in ``sweep_scenarios_v2_band.csv``; the
Agile optimum differs between lifetime policies, so both are solved.

    python scripts/case_study_energy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from src.battery import BatteryParams
from src.controllers import self_consumption, self_consumption_tou
from run_stage2 import DT_HOURS, battery_annual_costs, solve_year
from run_sweep import SweepDispatchProvider

LOCATION = "manchester"
PV_KWP = 4.0
SIZE_KWH = 5.0
C_RATE = 0.5

# (tariff, controller, penalty GBP/kWh, which lifetime policies this optimum serves)
CASES = [
    ("flat", "self_consumption", None, "both"),
    ("e7", "milp", 0.07, "both"),
    ("agile", "milp", 0.01, "no-replacement"),
    ("agile", "milp", 0.03, "run-to-fade"),
    # The two rules-based controllers on Agile, for the controller-comparison
    # figure: same tariff, PV, battery and demand as the MILP row above, so
    # every difference between the three is attributable to the controller.
    ("agile", "self_consumption", None, "no-replacement"),
    ("agile", "self_consumption_tou", None, "no-replacement"),
]


def energy_balance(schedule: pd.DataFrame) -> dict[str, float]:
    """Annual energy totals and the two ratios the case-study table reports."""
    total = {name: float(schedule[name].sum() * DT_HOURS)
             for name in ("pv_kw", "demand_kw", "p_import_kw", "p_export_kw")}
    pv, demand = total["pv_kw"], total["demand_kw"]
    return {
        "pv_kwh": pv,
        "demand_kwh": demand,
        "import_kwh": total["p_import_kw"],
        "export_kwh": total["p_export_kw"],
        # Share of generation used on site rather than exported. With a battery
        # able to export, grid-charged energy leaving again would depress this,
        # so it is a lower bound on PV self-consumption rather than an identity.
        "self_consumption_pct": 100.0 * (pv - total["p_export_kw"]) / pv,
        # Share of demand met without importing. Unambiguous either way.
        "self_sufficiency_pct": 100.0 * (demand - total["p_import_kw"]) / demand,
    }


def main() -> None:
    provider = SweepDispatchProvider(c_rate=C_RATE)
    power = C_RATE * SIZE_KWH
    rows = []
    schedules = []

    # PV-only: no dispatch decision exists without a battery, so the flows are
    # fixed by the profiles and identical across tariffs.
    data = provider._data(LOCATION, "flat", PV_KWP)
    net = data["demand_kw"] - data["pv_kw"]
    pv_only = pd.DataFrame({
        "pv_kw": data["pv_kw"], "demand_kw": data["demand_kw"],
        "p_import_kw": net.clip(lower=0.0), "p_export_kw": (-net).clip(lower=0.0),
    }, index=data.index)
    rows.append({"tariff": "all", "controller": "none", "penalty_gbp_per_kwh": None,
                 "policy": "reference", "throughput_kwh": 0.0, "efc_yr1": 0.0,
                 **energy_balance(pv_only)})
    print(f"  PV-only        self-consumption {rows[0]['self_consumption_pct']:5.2f}%  "
          f"self-sufficiency {rows[0]['self_sufficiency_pct']:5.2f}%", flush=True)

    for tariff, controller, penalty, policy in CASES:
        print(f"  solving {tariff}/{controller}"
              f"{'' if penalty is None else f'/{penalty * 100:.0f}p'} ...", flush=True)
        data = provider._data(LOCATION, tariff, PV_KWP)
        battery = BatteryParams(
            capacity_kwh=SIZE_KWH, max_charge_kw=power, max_discharge_kw=power,
            degradation_cost_per_kwh=penalty or 0.0,
        )
        if controller == "milp":
            schedule = solve_year(data, battery, terminal_soc_daily=False, solver="SCIPY")
        elif controller == "self_consumption_tou":
            schedule = self_consumption_tou(data, battery, tariff,
                                            provider._rates(LOCATION))
        else:
            schedule = self_consumption(data, battery)

        costs = battery_annual_costs(schedule, battery)
        saving = provider.grid_only_flat_cost(LOCATION) - costs["net_cost"]
        balance = energy_balance(schedule)
        schedules.append(schedule.assign(tariff=tariff, controller=controller,
                                         policy=policy))
        rows.append({
            "tariff": tariff, "controller": controller, "penalty_gbp_per_kwh": penalty,
            "policy": policy, "throughput_kwh": costs["throughput_kwh"],
            "efc_yr1": costs["throughput_kwh"] / (2.0 * SIZE_KWH),
            "annual_saving_yr1_gbp": saving, **balance,
        })
        print(f"    self-consumption {balance['self_consumption_pct']:5.2f}%  "
              f"self-sufficiency {balance['self_sufficiency_pct']:5.2f}%  "
              f"export {balance['export_kwh']:7.1f} kWh  saving £{saving:.2f}", flush=True)

    out = REPO_ROOT / "results" / "case_study_energy.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\n  wrote {out}")

    out_schedules = REPO_ROOT / "results" / "case_study_schedules.csv"
    pd.concat(schedules).to_csv(out_schedules, index_label="timestamp")
    print(f"  wrote {out_schedules}")


if __name__ == "__main__":
    main()
