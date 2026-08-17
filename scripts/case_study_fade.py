"""SOH trajectory for the Chapter 1 case study.

The sweep retains each scenario's minimum SOH and replacement years but not the
year-by-year trajectory behind them, so the fade figure cannot be read off an
assembly. This replays the fade simulation for the dispatches the case-study
table reports and writes the trajectories to ``results/case_study_fade.csv``.

The dispatch surrogates are the sweep's own cached saving curves, so this costs
no solves; if ``results/cache/sweep_v2`` is absent, re-run run_sweep.py first.

Case: Manchester, 4 kWp, 5 kWh at 0.5C, 2025 prices — the controller and wear
penalty are the per-cell optima already in ``sweep_scenarios_v2_band.csv``.

    python scripts/case_study_fade.py
"""

from __future__ import annotations

import pickle
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.battery import BatteryParams
from src.degradation import DegradationParams, simulate_capacity_fade

LOCATION = "manchester"
PV_KWP = 4.0
SIZE_KWH = 5.0
POWER_KW = 2.5
HORIZON_YEARS = 20
CACHE_DIR = REPO_ROOT / "results" / "cache" / "sweep_v2"

# run_sweep.py defaults, which produced sweep_scenarios_v2_band.csv. Note
# `replace_at_eol=False`: replacement is triggered at the hard 60% floor, not at
# the 80% end-of-life the fade curve is anchored on.
DEG_BASE = DegradationParams(soh_eol=0.80, cycle_life_efc=6000.0,
                             calendar_life_years=10.0, soh_floor=0.60,
                             replace_at_eol=False)

# (tariff, controller, penalty GBP/kWh, policies this optimum serves)
CASES = [
    ("flat", "self_consumption", None, ("run-to-fade", "no-replacement")),
    ("e7", "milp", 0.07, ("run-to-fade", "no-replacement")),
    ("agile", "milp", 0.01, ("no-replacement",)),
    ("agile", "milp", 0.03, ("run-to-fade",)),
]


def load_curve(tariff: str, controller: str, penalty: float | None):
    tag = "rules" if controller != "milp" else f"{penalty:.4f}"
    path = CACHE_DIR / (f"{LOCATION}_{tariff}_pv{PV_KWP:g}_bat{SIZE_KWH:g}_"
                        f"{controller}_{tag}_pw{POWER_KW:g}.pkl")
    if not path.exists():
        raise SystemExit(f"missing saving curve {path}; re-run scripts/run_sweep.py")
    return pickle.loads(path.read_bytes())


def main() -> None:
    battery = BatteryParams()
    rows = []

    for tariff, controller, penalty, policies in CASES:
        curve = load_curve(tariff, controller, penalty)

        def curve_dispatch(capacity_kwh, soc_max, _curve=curve):
            e = capacity_kwh * (soc_max - battery.soc_min)
            return _curve.saving_at(e), _curve.throughput_at(e), ()

        for policy in policies:
            # no-replacement is the same simulation with the replacement trigger
            # removed, exactly as src/sweep.py does it.
            params = (DEG_BASE if policy == "run-to-fade"
                      else replace(DEG_BASE, replace_at_eol=False, soh_floor=0.0))
            fade = simulate_capacity_fade(
                dispatch_year=curve_dispatch, capacity_kwh_nominal=SIZE_KWH,
                soc_max=battery.soc_max, horizon_years=HORIZON_YEARS, params=params,
            )
            for year, soh in enumerate(fade.soh_trajectory, start=1):
                rows.append({"tariff": tariff, "controller": controller,
                             "penalty_gbp_per_kwh": penalty, "policy": policy,
                             "year": year, "soh": soh,
                             "replaced": year in fade.replacement_years})
            print(f"  {tariff:5s} {policy:15s} min SOH {min(fade.soh_trajectory):.4f}  "
                  f"replacements {fade.replacement_years}", flush=True)

    out = REPO_ROOT / "results" / "case_study_fade.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\n  wrote {out}")


if __name__ == "__main__":
    main()
