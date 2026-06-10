#!/usr/bin/env python3
"""
scripts/generate_demand_profile.py

Drive the CREST Demand Model v2.3.3 Excel workbook via xlwings to simulate
every calendar day in a target year and save the results as a half-hourly
demand profile CSV.

PLATFORM REQUIREMENT
--------------------
xlwings requires Microsoft Excel.  This script must be run on Windows or
macOS — it will NOT work on Linux.  Once the CSV has been generated, copy it
back into the repo's data/ directory for use by the Python modelling pipeline.

Usage
-----
    python scripts/generate_demand_profile.py [--year YEAR] [--output OUTPUT]
                                               [--visible]

Arguments
---------
    --year      Calendar year to simulate (default: 2023)
    --output    Output CSV path (default: data/demand_halfhourly_<year>.csv)
    --visible   Show the Excel window while the simulation runs (slower, but
                useful for debugging)

Output
------
    A CSV file with a DatetimeIndex at 30-minute frequency and a single column
    'demand_kW' containing the average demand power (kW) in each half-hour
    period.  This format matches the Agile tariff data and is ready to feed
    directly into the v2 MILP scheduling model.

    Annual total is printed on completion as a sanity check: a typical UK
    household with ~2–3 occupants should produce 3,000–5,000 kWh/yr.
"""

import argparse
import datetime
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import xlwings as xw
except ImportError:
    sys.exit(
        "xlwings is not installed.  Run:  pip install xlwings\n"
        "Note: xlwings also requires Microsoft Excel to be installed."
    )

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
XLSM_DEFAULT = REPO_ROOT / "data" / "CREST_Demand_Model_v2.3.3.xlsm"

# ---------------------------------------------------------------------------
# CREST workbook layout
# ---------------------------------------------------------------------------
INPUT_SHEET = "Main Sheet"
CELL_DAY            = "F6"   # Day of month (integer 1–31)
CELL_MONTH          = "H6"   # Month of year (integer 1–12)
CELL_DAYTYPE        = "F7"   # 'wd' (weekday Mon–Fri) or 'we' (weekend Sat–Sun)
CELL_OWNERSHIP_YEAR = "K10"  # Appliance ownership year (2006–2031)

OUTPUT_SHEET = "Results - aggregated"
OUTPUT_RANGE = "E5:E1444"   # 1 440 rows at 1-minute resolution

# The Sub assigned to the "Run simulation" button.
# If xlwings raises a macro-not-found error, check whether this Sub lives in
# a standard module or in the "Main Sheet" code module, and update accordingly.
MACRO_NAME = "RunSimulationButton_Click"

MINUTES_PER_DAY       = 1440
HALFHOURS_PER_DAY     = 48
MINUTES_PER_HALFHOUR  = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def day_type(date: datetime.date) -> str:
    """Return 'wd' for Mon–Fri, 'we' for Sat–Sun."""
    return "we" if date.weekday() >= 5 else "wd"


def run_one_day(
    ws_input: "xw.Sheet",
    ws_output: "xw.Sheet",
    macro: "xw.macro",
    date: datetime.date,
) -> np.ndarray:
    """
    Set CREST inputs for *date*, trigger the simulation macro, read the
    1-minute output, and return a 48-element array of half-hourly average
    demand (kW).
    """
    ws_input.range(CELL_DAY).value    = date.day
    ws_input.range(CELL_MONTH).value  = date.month
    ws_input.range(CELL_DAYTYPE).value = day_type(date)

    macro()  # equivalent to clicking "Run simulation"

    raw = np.array(
        ws_output.range(OUTPUT_RANGE).value, dtype=float
    ).flatten()

    if len(raw) != MINUTES_PER_DAY:
        raise ValueError(
            f"{date}: expected {MINUTES_PER_DAY} output rows, got {len(raw)}.  "
            "Check that 'Include high-resolution dynamic output?' (row 14) is "
            "ticked in the workbook."
        )

    # Average each block of 30 consecutive 1-min values → mean kW per half-hour
    return raw.reshape(HALFHOURS_PER_DAY, MINUTES_PER_HALFHOUR).mean(axis=1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an annual half-hourly demand profile via CREST v2.3.3"
    )
    parser.add_argument(
        "--year", type=int, default=2023,
        help="Calendar year to simulate (default: 2023)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output CSV path (default: data/demand_halfhourly_<year>.csv)",
    )
    parser.add_argument(
        "--xlsm", type=Path, default=XLSM_DEFAULT,
        help="Path to the CREST .xlsm workbook (default: data/CREST_Demand_Model_v2.3.3.xlsm)",
    )
    parser.add_argument(
        "--visible", action="store_true",
        help="Show the Excel window during simulation (useful for debugging)",
    )
    args = parser.parse_args()

    xlsm_path: Path = args.xlsm
    if not xlsm_path.exists():
        sys.exit(f"CREST workbook not found at: {xlsm_path}")

    output_path: Path = (
        args.output
        if args.output is not None
        else REPO_ROOT / "data" / f"demand_halfhourly_{args.year}.csv"
    )

    # Build ordered list of every calendar day in the target year
    start = datetime.date(args.year, 1, 1)
    end   = datetime.date(args.year, 12, 31)
    dates = [
        start + datetime.timedelta(days=i)
        for i in range((end - start).days + 1)
    ]
    n_days = len(dates)

    print(f"CREST annual demand profile generator")
    print(f"  Year:      {args.year}  ({n_days} days)")
    print(f"  Workbook:  {xlsm_path}")
    print(f"  Output:    {output_path}")
    print(f"  Visible:   {args.visible}")
    print()

    app = xw.App(visible=args.visible, add_book=False)
    app.display_alerts = False

    try:
        wb        = app.books.open(str(xlsm_path))
        ws_input  = wb.sheets[INPUT_SHEET]
        ws_output = wb.sheets[OUTPUT_SHEET]

        # Set appliance ownership year once — affects demand profile shape
        ws_input.range(CELL_OWNERSHIP_YEAR).value = args.year

        # wb.macro() works for Subs in standard modules.  If the Sub is in the
        # "Main Sheet" code module (ActiveX button), use instead:
        #   macro = lambda: app.api.Run(f"'{xlsm_path.name}'!{MACRO_NAME}")
        macro = wb.macro(MACRO_NAME)

        all_days: list[np.ndarray] = []
        t0 = time.time()

        for i, date in enumerate(dates):
            hh = run_one_day(ws_input, ws_output, macro, date)
            all_days.append(hh)

            if (i + 1) % 10 == 0 or (i + 1) == n_days:
                elapsed   = time.time() - t0
                rate      = (i + 1) / elapsed
                remaining = (n_days - i - 1) / rate if rate > 0 else 0.0
                print(
                    f"  {i + 1:3d}/{n_days}  |  "
                    f"{elapsed:5.0f}s elapsed  |  "
                    f"~{remaining:4.0f}s remaining"
                )

        wb.close()  # xlwings won't save unless wb.save() is called explicitly

    finally:
        app.quit()

    # ------------------------------------------------------------------
    # Assemble into a pandas Series with a half-hourly DatetimeIndex
    # ------------------------------------------------------------------
    index = pd.date_range(
        start=f"{args.year}-01-01 00:00",
        periods=n_days * HALFHOURS_PER_DAY,
        freq="30min",
    )
    demand = pd.Series(
        np.concatenate(all_days),
        index=index,
        name="demand_kW",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    demand.to_csv(output_path, header=True)

    annual_kwh = demand.sum() * 0.5  # kW × 0.5 h = kWh per period
    print(f"\nSaved {len(demand):,} half-hourly values to {output_path}")
    print(f"Annual total consumption: {annual_kwh:,.0f} kWh")
    print("(Typical UK household with ~2–3 occupants: 3,000–5,000 kWh/yr)")


if __name__ == "__main__":
    main()
