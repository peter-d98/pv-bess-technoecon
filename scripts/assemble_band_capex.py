"""Assemble the band-observed battery capex table from two constant-price runs.

DESNZ publishes domestic battery installed cost as a median GBP/kWh per capacity
band, not as a single figure:

    1.00 -  5.99 kWh   GBP 1300 /kWh
    6.00 - 10.99 kWh   GBP  890 /kWh
   11.00 - 29.99 kWh   GBP  630 /kWh

`run_sweep.py` takes one scalar `--battery-cost-per-kwh`, so the band schedule is
produced by running it once per band and selecting each battery size from the run
that prices its own band. Sizes outside the published bands (0.5 kWh) are dropped:
the DESNZ table cannot price them.

Usage (after the two assemblies, which are ~25 s each and solve nothing):

    python scripts/run_sweep.py --pv-cost-per-kwp 1109 --pv-capex-fixed 0 \
        --battery-cost-per-kwh 1300 --battery-capex-fixed 0 \
        --out results/_band_c1300.csv --peak-out results/_band_c1300_peaks.csv --overwrite
    python scripts/run_sweep.py --pv-cost-per-kwp 1109 --pv-capex-fixed 0 \
        --battery-cost-per-kwh 890 --battery-capex-fixed 0 \
        --out results/_band_c890.csv --peak-out results/_band_c890_peaks.csv --overwrite
    python scripts/assemble_band_capex.py

Every check below is an assertion: the script writes nothing unless all pass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# DESNZ capacity bands: (lower kWh inclusive, upper kWh inclusive, median GBP/kWh).
BANDS = ((1.0, 5.99, 1300.0), (6.0, 10.99, 890.0), (11.0, 29.99, 630.0))

# Columns identifying a scenario row; everything else is a computed output.
KEY = [
    "location", "tariff", "export", "pv_kwp", "battery_kwh", "power_kw",
    "deg_penalty_gbp_per_kwh", "cycle_life_efc", "controller",
    "discount_rate", "price_escalation", "policy",
]

# Outputs that depend on capex. Everything else is dispatch-derived and must be
# identical across the two runs.
CAPEX_DEPENDENT = ["npv_gbp", "bcr", "capex_gbp", "min_saving_gbp"]


def band_price(size_kwh: float) -> float | None:
    """Median GBP/kWh for `size_kwh`, or None if it falls outside every band."""
    for low, high, price in BANDS:
        if low <= size_kwh <= high:
            return price
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src-dir", type=Path, default=Path("results"))
    parser.add_argument("--out", type=Path,
                        default=Path("results/sweep_scenarios_v2_band.csv"))
    args = parser.parse_args()

    sources = {price: pd.read_csv(args.src_dir / f"_band_c{price:.0f}.csv")
               for _, _, price in BANDS
               if (args.src_dir / f"_band_c{price:.0f}.csv").exists()}
    if not sources:
        raise SystemExit("no _band_c*.csv inputs found; run the two assemblies first")

    frames = list(sources.values())
    prices = list(sources)
    ref = frames[0]

    # --- 1. the runs describe the same grid -------------------------------
    for price, frame in sources.items():
        assert list(frame.columns) == list(ref.columns), f"column mismatch at c={price}"
        assert len(frame) == len(ref), f"row-count mismatch at c={price}"
        assert frame[KEY].equals(ref[KEY]), f"key mismatch at c={price}"
    assert not ref.duplicated(KEY).any(), "duplicate keys within a source run"

    # --- 2. capex does not touch dispatch ---------------------------------
    shared = [c for c in ref.columns if c not in CAPEX_DEPENDENT]
    for price, frame in sources.items():
        num = frame[shared].select_dtypes("number").columns
        delta = np.nanmax(np.abs(frame[num].to_numpy() - ref[num].to_numpy()))
        assert delta == 0.0, f"c={price} differs from c={prices[0]} in {list(num)} by {delta}"

    # --- 3. assign each battery size to exactly one source ----------------
    sizes = sorted(ref.battery_kwh.unique())
    assignment: dict[float, float] = {}
    dropped: list[float] = []
    for size in sizes:
        # A zero-battery reference row pays no battery capex, so any source
        # gives the same answer; take the first for determinism.
        price = prices[0] if size == 0.0 else band_price(size)
        if price is None:
            dropped.append(size)
        else:
            assert price in sources, f"size {size} needs c={price}, which was not run"
            assignment[size] = price

    parts = [sources[price][sources[price].battery_kwh == size]
             for size, price in assignment.items()]
    combined = pd.concat(parts, ignore_index=True).sort_values(KEY).reset_index(drop=True)

    # --- 4. no overlap, no omission ---------------------------------------
    expected = ref[ref.battery_kwh.isin(assignment)].sort_values(KEY).reset_index(drop=True)
    assert len(combined) == len(expected), \
        f"row count {len(combined)} != expected {len(expected)}"
    assert not combined.duplicated(KEY).any(), "a key appears twice (sources overlap)"
    assert combined[KEY].equals(expected[KEY]), "key set differs from expected (omission)"
    assert set(sizes) - set(assignment) == set(dropped)

    # --- 5. every row carries its own band's price ------------------------
    for size, price in assignment.items():
        rows = combined[combined.battery_kwh == size]
        src = sources[price][sources[price].battery_kwh == size]
        merged = rows.merge(src, on=KEY, suffixes=("", "_src"))
        assert len(merged) == len(rows), f"size {size} did not round-trip to its source"
        assert (merged.capex_gbp == merged.capex_gbp_src).all(), \
            f"size {size} carries capex from the wrong run"
        assert (merged.npv_gbp == merged.npv_gbp_src).all(), \
            f"size {size} carries NPV from the wrong run"

    # --- 6. capex reconciles against the published schedule ---------------
    # `capex_gbp` is first cost only (PV + battery), independent of policy, so
    # the zero-battery row of the same PV size isolates the battery component.
    zero = combined[combined.battery_kwh == 0].groupby("pv_kwp").capex_gbp
    assert (zero.nunique() == 1).all(), "PV-only capex is not unique per PV size"
    pv_capex = zero.first()

    battery = combined[combined.battery_kwh > 0]
    implied = battery.capex_gbp.to_numpy() - battery.pv_kwp.map(pv_capex).to_numpy()
    expected_capex = battery.battery_kwh.to_numpy() * \
        np.array([band_price(q) for q in battery.battery_kwh])
    residual = float(np.nanmax(np.abs(implied - expected_capex)))
    assert residual < 1e-6, f"capex does not reconcile to band price x size (max {residual})"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.out, index=False)

    print(f"sources: {', '.join(f'c={p:.0f} ({len(f)} rows)' for p, f in sources.items())}")
    for size, price in sorted(assignment.items()):
        n = int((combined.battery_kwh == size).sum())
        label = "reference (no battery capex)" if size == 0 else f"c={price:.0f}/kWh"
        print(f"  {size:>5} kWh <- {label:<28} {n} rows")
    for size in dropped:
        print(f"  {size:>5} kWh -- dropped, outside the published bands")
    print(f"all checks passed; wrote {len(combined)} rows -> {args.out}")


if __name__ == "__main__":
    main()
