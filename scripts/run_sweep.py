"""Spec 05 runner: the parameter-sweep harness.

Wires the real MILP dispatch (Stage 2 ``solve_year``) and the rules controllers
into the surrogate-based sweep (:mod:`src.sweep`) and writes the tidy results
table to ``results/sweep_scenarios.csv``.

Parallelism model: the per-cell saving curves are cached to ``results/cache/``
keyed by (location, tariff, controller, penalty, power). Because each cell writes
a distinct cache file, the sweep can be parallelised simply by launching several
processes over disjoint subsets (e.g. ``--locations glasgow`` in one,
``--locations plymouth`` in another); a final full run then reads every curve
from cache and assembles the table with zero solves.

Examples
--------
    python scripts/run_sweep.py --quick               # 1-cell smoke grid
    python scripts/run_sweep.py --validate            # surrogate vs full-solve
    python scripts/run_sweep.py                        # full default sweep
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from src.battery import BatteryParams
from src.controllers import self_consumption, self_consumption_tou
from src.data_loader import HALFHOURS_PER_DAY, load_all
from src.degradation import DegradationParams, derive_throughput_penalty, simulate_capacity_fade
from src.economics import EconomicParams
from src.locations import get_location, resolve_paths
from src.sweep import SweepGrid, build_saving_curve, fade_npv, knot_grid, run_sweep
from src.tariffs import build_tariff

# Reuse the Stage 2 annual solve and cost accounting unchanged.
from run_stage2 import (  # noqa: E402
    DT_HOURS,
    battery_annual_costs,
    counterfactual_cost,
    grid_only_flat_cost,
    solve_year,
)

DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_DEMAND = DATA_DIR / "demand_halfhourly_2025.csv"


class SweepDispatchProvider:
    """Real dispatch/data provider: MILP + rules controllers on real data.

    Loads and caches each (location, tariff) data frame once, then serves
    per-capacity dispatch closures for the sweep. Keeps CVXPY out of
    ``src.sweep`` (dependency injection).
    """

    def __init__(
        self,
        *,
        year: int = 2025,
        power_kw: float = 3.0,
        export: str = "matched",
        seg_rate: float | None = None,
        pv_kwp: float = 4.0,
        pv_cost_per_kwp: float = 1109.0,
        battery_cost_per_kwh: float = 890.0,
        deg_base: DegradationParams | None = None,
    ) -> None:
        self.year = year
        self.power_kw = power_kw
        self.export = export
        self.seg_rate = seg_rate
        self.pv_kwp = pv_kwp
        self.pv_capex = pv_kwp * pv_cost_per_kwp
        self.battery_cost_per_kwh = battery_cost_per_kwh
        self.deg_base = deg_base or DegradationParams()
        self.soc_min = BatteryParams().soc_min
        self.soc_max = BatteryParams().soc_max
        self._data_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._flat_cache: dict[str, float] = {}

    # -- data --------------------------------------------------------------
    def _data(self, location: str, tariff: str) -> pd.DataFrame:
        key = (location, tariff)
        if key in self._data_cache:
            return self._data_cache[key]
        loc = get_location(location)
        pv_path, agile_path = resolve_paths(loc, DATA_DIR)
        data = load_all(pv_path, DEFAULT_DEMAND, agile_path, year=self.year)
        imp, exp = build_tariff(
            tariff, data.index, loc.rates, agile_path=agile_path,
            export=self.export, seg_rate=self.seg_rate,
        )
        data["import_price"] = imp.to_numpy()
        data["export_price"] = exp.to_numpy()
        self._data_cache[key] = data
        return data

    def _rates(self, location: str):
        return get_location(location).rates

    # -- SweepProvider interface -------------------------------------------
    def resolve_penalty(self, penalty: float | None, cycle_life_efc: float) -> float:
        if penalty is not None:
            return float(penalty)
        # Derived penalty is capacity-independent; evaluate at a nominal 10 kWh,
        # grounded in *this* scenario's end-of-life EFC.
        deg = replace(self.deg_base, cycle_life_efc=cycle_life_efc)
        return derive_throughput_penalty(10.0 * self.battery_cost_per_kwh, 10.0, deg)

    def grid_only_flat_cost(self, location: str) -> float:
        if location not in self._flat_cache:
            # Any tariff's data frame carries the same demand; use flat.
            data = self._data(location, "flat")
            flat_rate = self._rates(location).flat_rate
            self._flat_cache[location] = grid_only_flat_cost(data, flat_rate)["net_cost"]
        return self._flat_cache[location]

    def pv_only_saving(self, location: str, tariff: str) -> float:
        data = self._data(location, tariff)
        return self.grid_only_flat_cost(location) - counterfactual_cost(data)["net_cost"]

    def capex(self, size_kwh: float) -> float:
        # Battery cost already includes the hybrid inverter.
        return self.pv_capex + size_kwh * self.battery_cost_per_kwh

    def replacement_capex(self, size_kwh: float) -> float:
        return size_kwh * self.battery_cost_per_kwh

    def dispatch_year(self, location, tariff, penalty, controller):
        data = self._data(location, tariff)
        grid_flat = self.grid_only_flat_cost(location)
        power = self.power_kw
        rates = self._rates(location)

        if controller == "milp":
            def milp_year(capacity_kwh, soc_max):
                bat = BatteryParams(
                    capacity_kwh=capacity_kwh, max_charge_kw=power,
                    max_discharge_kw=power, soc_max=soc_max,
                    degradation_cost_per_kwh=penalty,
                )
                sched = solve_year(data, bat, terminal_soc_daily=False)
                costs = battery_annual_costs(sched, bat)
                saving = grid_flat - costs["net_cost"]
                return saving, costs["throughput_kwh"], sched["soc"].to_numpy()
            return milp_year

        def rules_year(capacity_kwh, soc_max):
            bat = BatteryParams(
                capacity_kwh=capacity_kwh, max_charge_kw=power,
                max_discharge_kw=power, soc_max=soc_max,
            )
            if controller == "self_consumption":
                sched = self_consumption(data, bat)
            elif controller == "self_consumption_tou":
                sched = self_consumption_tou(data, bat, tariff, rates)
            else:
                raise ValueError(f"Unknown controller {controller!r}.")
            costs = battery_annual_costs(sched, bat)
            saving = grid_flat - costs["net_cost"]
            return saving, costs["throughput_kwh"], sched["soc"].to_numpy()
        return rules_year


def _econ_base(args) -> EconomicParams:
    om = args.pv_om_frac * (args.pv_kwp * args.pv_cost_per_kwp)
    return EconomicParams(
        battery_capex=1.0,  # overridden per row by run_sweep
        discount_rate=args.discount_rate,
        horizon_years=args.horizon_years,
        price_escalation=args.price_escalation,
        battery_life_years=args.battery_life_years,
        om_cost_per_year=om,
    )


def _deg_base(args) -> DegradationParams:
    return DegradationParams(
        soh_eol=args.soh_eol,
        cycle_life_efc=args.cycle_life_efc,
        calendar_life_years=args.calendar_life_years,
        calendar_form=args.calendar_form,
        replace_at_eol=False,
        soh_floor=args.soh_floor,
    )


def validate_surrogate(provider, grid, econ_base, deg_base, cell) -> None:
    """Compare surrogate run-to-fade NPV against the full 20-solve fade for a cell."""
    location, tariff, size, penalty = cell
    penalty_value = provider.resolve_penalty(penalty, deg_base.cycle_life_efc)
    print(f"\nSurrogate validation — {location}/{tariff}/{size} kWh/"
          f"penalty={penalty_value * 100:.2f}p, EFC {deg_base.cycle_life_efc:.0f} (MILP)")
    soc_window = provider.soc_max - provider.soc_min
    e_max = max(grid.sizes_kwh) * soc_window

    dyear = provider.dispatch_year(location, tariff, penalty_value, "milp")

    init_capex = provider.capex(size)
    repl_capex = provider.replacement_capex(size)
    econ = replace(econ_base, battery_capex=init_capex,
                   replacement_cost_factor=repl_capex / init_capex)

    # Full: re-dispatch every horizon year at the real solver.
    print("  Full fade (real re-dispatch each year)...", flush=True)
    fade_full = simulate_capacity_fade(
        dispatch_year=dyear, capacity_kwh_nominal=size, soc_max=provider.soc_max,
        horizon_years=econ.horizon_years, params=deg_base,
    )
    npv_full, life_full, _ = fade_npv(fade_full, econ, econ_base.battery_life_years, repl_capex)

    for n_knots in (grid.n_knots, grid.n_knots + 4):
        knots = knot_grid(e_max, n_knots)
        curve = build_saving_curve(dyear, knots, soc_window, provider.soc_max)

        def curve_dispatch(capacity_kwh, soc_max, _c=curve):
            e = capacity_kwh * (soc_max - provider.soc_min)
            return _c.saving_at(e), _c.throughput_at(e), ()

        fade_surr = simulate_capacity_fade(
            dispatch_year=curve_dispatch, capacity_kwh_nominal=size,
            soc_max=provider.soc_max, horizon_years=econ.horizon_years, params=deg_base,
        )
        npv_surr, life_surr, _ = fade_npv(
            fade_surr, econ, econ_base.battery_life_years, repl_capex
        )
        gap = npv_surr.npv - npv_full.npv
        print(f"  {n_knots:2d} knots: surrogate NPV £{npv_surr.npv:>10.2f} "
              f"(life {life_surr}) | full £{npv_full.npv:>10.2f} (life {life_full}) "
              f"| gap £{gap:+.2f} ({gap / abs(npv_full.npv) * 100:+.2f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Spec 05 parameter-sweep harness.")
    parser.add_argument("--locations", nargs="+", default=list(SweepGrid().locations))
    parser.add_argument("--tariffs", nargs="+", default=list(SweepGrid().tariffs))
    parser.add_argument("--sizes", nargs="+", type=float, default=list(SweepGrid().sizes_kwh))
    parser.add_argument("--deg-scenarios", nargs="+", default=None, metavar="PENALTY:EFC",
                        help="Degradation scenarios as 'penalty:EFC' tokens; penalty is "
                             "GBP/kWh or 'derived' (from that EFC and the battery cost). "
                             "Default: 0:6000 derived:6000 derived:8000")
    parser.add_argument("--controllers", nargs="+", default=list(SweepGrid().controllers))
    parser.add_argument("--n-knots", type=int, default=SweepGrid().n_knots)
    parser.add_argument("--power", type=float, default=3.0)
    parser.add_argument("--export", default="matched", choices=("matched", "seg", "agile", "none"))
    parser.add_argument("--seg-rate", type=float, default=None)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--pv-kwp", type=float, default=4.0)
    parser.add_argument("--pv-cost-per-kwp", type=float, default=1109.0)
    parser.add_argument("--battery-cost-per-kwh", type=float, default=890.0)
    parser.add_argument("--pv-om-frac", type=float, default=0.01)
    parser.add_argument("--discount-rate", type=float, default=0.05)
    parser.add_argument("--horizon-years", type=int, default=20)
    parser.add_argument("--price-escalation", type=float, default=0.02)
    parser.add_argument("--battery-life-years", type=float, default=10.0)
    parser.add_argument("--calendar-life-years", type=float, default=10.0)
    parser.add_argument("--cycle-life-efc", type=float, default=6000.0)
    parser.add_argument("--soh-eol", type=float, default=0.80)
    parser.add_argument("--calendar-form", default="linear", choices=("linear", "sqrt"))
    parser.add_argument("--soh-floor", type=float, default=0.60)
    parser.add_argument("--cache-dir", type=Path, default=RESULTS_DIR / "cache")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "sweep_scenarios.csv")
    parser.add_argument("--quick", action="store_true",
                        help="1-location, 1-tariff, {0,10} kWh, MILP-only smoke grid.")
    parser.add_argument("--validate", action="store_true",
                        help="Compare surrogate vs full-solve run-to-fade NPV and exit.")
    args = parser.parse_args()

    def _parse_scenario(tok: str) -> tuple[float | None, float]:
        pstr, estr = tok.split(":")
        penalty = None if pstr.strip().lower() == "derived" else float(pstr)
        return (penalty, float(estr))

    if args.deg_scenarios is None:
        scenarios: tuple = SweepGrid().deg_scenarios
    else:
        scenarios = tuple(_parse_scenario(t) for t in args.deg_scenarios)

    if args.quick:
        args.locations = ["glasgow"]
        args.tariffs = ["agile"]
        args.sizes = [0.0, 10.0]
        args.controllers = ["milp"]
        scenarios = ((None, args.cycle_life_efc),)

    grid = SweepGrid(
        locations=tuple(args.locations),
        tariffs=tuple(args.tariffs),
        sizes_kwh=tuple(args.sizes),
        deg_scenarios=scenarios,
        controllers=tuple(args.controllers),
        power_kw=args.power,
        export=args.export,
        n_knots=args.n_knots,
    )
    provider = SweepDispatchProvider(
        year=args.year, power_kw=args.power, export=args.export, seg_rate=args.seg_rate,
        pv_kwp=args.pv_kwp, pv_cost_per_kwp=args.pv_cost_per_kwp,
        battery_cost_per_kwh=args.battery_cost_per_kwh, deg_base=_deg_base(args),
    )
    econ_base = _econ_base(args)
    deg_base = _deg_base(args)

    if args.validate:
        validate_surrogate(
            provider, grid, econ_base, deg_base,
            cell=(args.locations[0], args.tariffs[0],
                  max(s for s in args.sizes if s > 0), None),
        )
        return

    print(f"Running sweep: {len(grid.locations)} loc x {len(grid.tariffs)} tariff x "
          f"{len(grid.sizes_kwh)} size x {len(grid.deg_scenarios)} deg-scenario x "
          f"{len(grid.controllers)} controller...")
    df = run_sweep(grid, provider, econ_base, deg_base, cache_dir=args.cache_dir)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df)} scenario rows -> {args.out}")


if __name__ == "__main__":
    main()
