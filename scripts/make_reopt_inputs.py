"""Hourly (8760) input files for the REopt external-validation cross-check.

REopt runs at hourly resolution; the model runs at half-hourly. This aggregates
the three inputs of one sweep cell onto an 8760 index in the format REopt's
upload templates expect (``Hour,<quantity>`` with Hour 1..8760).

Aggregation is positional — half-hours 2h and 2h+1 form hour h+1 — matching the
positional alignment `load_all` already applies across the 2023 PV / 2025 demand
and price years. Powers and prices are averaged, so annual energy is preserved
exactly; averaging the price pair is only equivalent to the half-hourly cost
when consumption is uniform within the hour, which is the approximation the
hourly comparison rests on.

Defaults are the myopia cell of docs/results_summary.md §7: Inverness, 5 kWp,
Agile (North Scotland), 2025 prices and demand, 2023 PV.

    python scripts/make_reopt_inputs.py
    python scripts/make_reopt_inputs.py --pv-kwp 4 --location glasgow
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_loader import (
    build_canonical_index,
    load_agile_prices,
    load_demand,
    load_pv,
)
from src.locations import get_location, resolve_paths

DATA_DIR = REPO_ROOT / "data"
OUT_DIR = REPO_ROOT / "results" / "reopt"
DEMAND_FILE = DATA_DIR / "demand_halfhourly_2025.csv"


def to_hourly(values: np.ndarray) -> np.ndarray:
    """Average consecutive half-hour pairs into hourly means."""
    if len(values) % 2:
        raise ValueError(f"Expected an even number of half-hours, got {len(values)}")
    return values.reshape(-1, 2).mean(axis=1)


def write_template(path: Path, column: str, values: np.ndarray) -> None:
    """Write an 8760-row REopt upload file: Hour,<column>."""
    frame = pd.DataFrame(
        {"Hour": np.arange(1, len(values) + 1), column: np.round(values, 6)}
    )
    frame.to_csv(path, index=False)


def write_bare_column(path: Path, values: np.ndarray) -> None:
    """Write a headerless single-column file, the PV production-factor format.

    REopt rejects blank or non-numeric entries, so a header row would fail;
    the custom-PV upload wants only the numbers. Fixed-point formatting is
    forced because the default float repr emits scientific notation ("2e-05")
    for the near-zero dawn/dusk values, which the parser may reject.
    """
    pd.DataFrame(values).to_csv(
        path, index=False, header=False, float_format="%.6f"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", default="inverness")
    parser.add_argument("--pv-kwp", type=float, default=5.0)
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    loc = get_location(args.location)
    _, agile_path = resolve_paths(loc, DATA_DIR)
    pv_path = DATA_DIR / f"{loc.label}_{int(args.pv_kwp)}kWp_2023.csv"

    index = build_canonical_index(args.year)
    pv_hh = load_pv(pv_path, index).to_numpy()
    demand_hh = load_demand(DEMAND_FILE, index).to_numpy()
    prices_hh = load_agile_prices(agile_path, index)

    pv = to_hourly(pv_hh)
    demand = to_hourly(demand_hh)
    imp = to_hourly(prices_hh["import_price"].to_numpy())
    exp = to_hourly(prices_hh["export_price"].to_numpy())

    # The half-hourly PV series is each hourly PVGIS value repeated, so the
    # pairwise mean must reproduce the native hourly value exactly.
    assert np.allclose(pv, pv_hh[::2], atol=0), "PV upsampling is not a clean repeat"

    stem = f"{loc.label.lower()}_{int(args.pv_kwp)}kwp_{args.year}"
    prod_factor = pv / args.pv_kwp
    outputs = {
        OUT_DIR / f"load_hourly_{stem}.csv": ("Load (kW)", demand),
        OUT_DIR / f"pv_kw_hourly_{stem}.csv": ("PV (kW)", pv),
        OUT_DIR / f"agile_import_hourly_{stem}.csv": ("Import price (GBP/kWh)", imp),
        OUT_DIR / f"agile_export_hourly_{stem}.csv": ("Export price (GBP/kWh)", exp),
    }
    for path, (column, values) in outputs.items():
        write_template(path, column, values)

    # REopt's custom-PV upload takes a bare column of kW-AC per kW-DC nameplate.
    pf_path = OUT_DIR / f"pv_prodfactor_hourly_{stem}_reopt.csv"
    write_bare_column(pf_path, prod_factor)
    outputs[pf_path] = ("Production factor (kW-AC/kW-DC)", prod_factor)

    check = pd.read_csv(pf_path, header=None)
    print("  REopt custom-PV file checks")
    print(f"    rows                {len(check)}  {'OK' if len(check) in (8760, 17520, 35040) else 'FAIL'}")
    print(f"    columns             {check.shape[1]}  {'OK' if check.shape[1] == 1 else 'FAIL'}")
    print(f"    all numeric         {'OK' if check[0].dtype.kind == 'f' else 'FAIL'}")
    print(f"    no blanks/NaN       {'OK' if int(check[0].isna().sum()) == 0 else 'FAIL'}")
    print(f"    range [{check[0].min():.4f}, {check[0].max():.4f}] kW-AC/kW-DC  "
          f"{'OK' if check[0].min() >= 0 and check[0].max() <= 1.2 else 'FAIL'}")
    print(f"    annual {check[0].sum():.2f} kWh/kW-DC; x {args.pv_kwp:g} kWp = "
          f"{check[0].sum() * args.pv_kwp:.2f} kWh  "
          f"{'OK' if abs(check[0].sum() * args.pv_kwp - pv.sum()) < 0.05 else 'FAIL'}\n")

    print(f"Cell: {loc.label} / {args.pv_kwp:g} kWp / Agile / {args.year}")
    print(f"  PV file:     {pv_path.name}")
    print(f"  Demand file: {DEMAND_FILE.name}")
    print(f"  Agile file:  {agile_path.name}\n")

    print(f"  {'quantity':<22} {'half-hourly':>14} {'hourly':>14}  match")
    checks = [
        ("demand (kWh/yr)", demand_hh.sum() * 0.5, demand.sum()),
        ("PV (kWh/yr)", pv_hh.sum() * 0.5, pv.sum()),
        ("mean import (p/kWh)", prices_hh["import_price"].mean() * 100, imp.mean() * 100),
        ("mean export (p/kWh)", prices_hh["export_price"].mean() * 100, exp.mean() * 100),
    ]
    for name, half, hour in checks:
        print(f"  {name:<22} {half:>14.4f} {hour:>14.4f}  {'OK' if abs(half - hour) < 1e-6 else 'DIFFERS'}")

    print(f"\n  peak demand   {demand_hh.max():.3f} kW half-hourly -> {demand.max():.3f} kW hourly")
    print(f"  peak PV       {pv_hh.max():.3f} kW half-hourly -> {pv.max():.3f} kW hourly")
    print(f"  import price  [{imp.min() * 100:.2f}, {imp.max() * 100:.2f}] p/kWh hourly "
          f"(half-hourly [{prices_hh['import_price'].min() * 100:.2f}, "
          f"{prices_hh['import_price'].max() * 100:.2f}])")
    print(f"\n  {len(demand)} rows written to {OUT_DIR.relative_to(REPO_ROOT)}/")
    for path in outputs:
        print(f"    {path.name}")


if __name__ == "__main__":
    main()
