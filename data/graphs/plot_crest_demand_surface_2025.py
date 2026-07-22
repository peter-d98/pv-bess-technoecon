"""Create a yearly 3D intraday-demand surface from CREST 2025 demand data.

This script reads demand_halfhourly_2025.csv and generates a single 3D surface:
  - X axis: hour of day (half-hour resolution)
  - Y axis: day of year
  - Z axis: demand (kW)

Output image is saved to data/graphs by default.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import PowerNorm


def _default_paths() -> tuple[Path, Path]:
    here = Path(__file__).resolve().parent
    csv_path = here.parent / "demand_halfhourly_2025.csv"
    out_path = here / "crest_demand_surface_2025.png"
    return csv_path, out_path


def _configure_style() -> None:
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = [
        "Computer Modern Serif",
        "Computer Modern Roman",
        "CMU Serif",
        "DejaVu Serif",
    ]


def _load_demand_df(csv_path: Path) -> pd.DataFrame:
    # The timestamp is stored in the first unnamed column.
    df = pd.read_csv(csv_path)
    ts_col = df.columns[0]

    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    df["demand_kW"] = pd.to_numeric(df["demand_kW"], errors="coerce")
    df = df.dropna(subset=[ts_col, "demand_kW"]).copy()

    df["day_of_year"] = df[ts_col].dt.dayofyear
    df["hour_of_day"] = df[ts_col].dt.hour + (df[ts_col].dt.minute / 60.0)

    # Keep first value for repeated local timestamps.
    return (
        df.sort_values(ts_col)
        .drop_duplicates(subset=["day_of_year", "hour_of_day"], keep="first")
    )


def _build_surface(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    pivot = (
        df.pivot_table(
            index="day_of_year",
            columns="hour_of_day",
            values="demand_kW",
            aggfunc="first",
        )
        .sort_index(axis=0)
        .sort_index(axis=1)
    )

    hour_grid = pivot.columns.to_numpy(dtype=float)
    day_grid = pivot.index.to_numpy(dtype=float)
    x, y = np.meshgrid(hour_grid, day_grid)
    z = pivot.to_numpy(dtype=float)
    # Half-hourly demand in kW -> energy in kWh via multiplication by 0.5 h.
    total_demand_kwh = float((df["demand_kW"] * 0.5).sum())
    return x, y, z, total_demand_kwh


def make_plot(csv_path: Path, out_path: Path) -> None:
    _configure_style()
    df = _load_demand_df(csv_path)
    x, y, z, total_demand_kwh = _build_surface(df)

    # Use low-end percentile floor plus full max to keep detail in typical
    # values while preserving full color range up to true peaks.
    p2 = float(np.nanpercentile(z, 2))
    z_max = float(np.nanmax(z))
    norm = PowerNorm(gamma=0.6, vmin=p2, vmax=z_max)

    fig = plt.figure(figsize=(9.4, 7.4))
    ax = fig.add_subplot(111, projection="3d")

    ax.plot_surface(
        x,
        y,
        z,
        cmap="viridis",
        norm=norm,
        linewidth=0,
        antialiased=False,
        rcount=z.shape[0],
        ccount=z.shape[1],
        shade=False,
    )

    ax.set_xlabel("Hour of the day", labelpad=10, fontsize=14)
    ax.set_ylabel("Day of the year", labelpad=12, fontsize=14)
    ax.set_zlabel("CREST demand in kW", labelpad=10, rotation=90, fontsize=14)
    ax.zaxis.set_rotate_label(False)

    ax.set_xlim(0, 24)
    ax.set_xticks([0, 6, 12, 18, 24])
    ax.set_zlim(float(np.nanmin(z)), z_max * 1.02)
    ax.view_init(elev=10, azim=-140)

    ax.tick_params(axis="x", labelsize=12)
    ax.tick_params(axis="y", labelsize=12)
    ax.tick_params(axis="z", labelsize=12)

    ax.set_title(
        f"Total demand for 2025: {total_demand_kwh:,.0f} kWh",
        y=1.02,
        fontsize=16,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> None:
    default_csv, default_out = _default_paths()
    parser = argparse.ArgumentParser(
        description="Create a 3D surface plot of 2025 CREST demand."
    )
    parser.add_argument("--csv", type=Path, default=default_csv)
    parser.add_argument("--out", type=Path, default=default_out)
    args = parser.parse_args()

    make_plot(csv_path=args.csv, out_path=args.out)
    print(f"Saved plot to {args.out}")


if __name__ == "__main__":
    main()
