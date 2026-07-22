"""Replicate yearly 3D intraday-price surfaces from Agile import/export data.

This script reads the 2025 South Scotland Agile CSV and generates a surface
plot with:
  - X axis: hour of day
  - Y axis: day of year
    - Z axis: price (p/kWh)

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
    csv_path = here.parent / "agile-half-hour-actual-rates-01-01-2025_31-12-2025_SScot.csv"
    out_path = here / "agile_import_surface_2025_sscot.png"
    return csv_path, out_path


def _configure_style() -> None:
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = [
        "Computer Modern Serif",
        "Computer Modern Roman",
        "CMU Serif",
        "DejaVu Serif",
    ]


def _load_prices_df(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    # Parse day-first timestamps from the Octopus CSV format.
    df["Period from"] = pd.to_datetime(df["Period from"], dayfirst=True)
    df["Agile Import price (p/kWh)"] = pd.to_numeric(
        df["Agile Import price (p/kWh)"], errors="coerce"
    )
    df["Agile Export price (p/kWh)"] = pd.to_numeric(
        df["Agile Export price (p/kWh)"], errors="coerce"
    )
    df = df.dropna(
        subset=[
            "Period from",
            "Agile Import price (p/kWh)",
            "Agile Export price (p/kWh)",
        ]
    ).copy()

    df["day_of_year"] = df["Period from"].dt.dayofyear
    # Keep native half-hour granularity on the x-axis (0.0, 0.5, ..., 23.5).
    df["hour_of_day"] = (
        df["Period from"].dt.hour + (df["Period from"].dt.minute / 60.0)
    )

    # Avoid averaging repeated local timestamps (e.g., DST fallback);
    # keep the first observed value rather than aggregating.
    df = (
        df.sort_values("Period from")
        .drop_duplicates(subset=["day_of_year", "hour_of_day"], keep="first")
    )


    # Avoid averaging repeated local timestamps (e.g., DST fallback);
    # keep the first observed value rather than aggregating.
    return (
        df.sort_values("Period from")
        .drop_duplicates(subset=["day_of_year", "hour_of_day"], keep="first")
    )


def _build_surface(
    df: pd.DataFrame,
    value_col: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    pivot = (
        df.pivot_table(
            index="day_of_year",
            columns="hour_of_day",
            values=value_col,
            aggfunc="first",
        )
        .sort_index(axis=0)
        .sort_index(axis=1)
    )

    hour_grid = pivot.columns.to_numpy(dtype=float)
    day_grid = pivot.index.to_numpy(dtype=float)
    x, y = np.meshgrid(hour_grid, day_grid)
    z = pivot.to_numpy(dtype=float)

    avg_price = float(df[value_col].mean())
    return x, y, z, avg_price


def make_plot(csv_path: Path, out_path: Path) -> None:
    _configure_style()
    df = _load_prices_df(csv_path)
    x_i, y_i, z_i, avg_import = _build_surface(df, "Agile Import price (p/kWh)")
    x_e, y_e, z_e, avg_export = _build_surface(df, "Agile Export price (p/kWh)")

    # Use combined 2nd–98th percentile scaling across import+export to
    # reduce extreme outliers driving the palette while keeping dynamic
    # contrast in the bulk of the distribution.
    combined = np.concatenate([np.ravel(z_i), np.ravel(z_e)])
    # Keep full numeric z-limits so extreme spikes render to true height,
    # but use percentile-based colormap scaling to avoid low-end compression
    # while increasing contrast near the high end. Here we use 2nd–99.5th
    # percentiles for the colour mapping.
    data_min = float(np.nanmin(combined))
    data_max = float(np.nanmax(combined))
    p2 = float(np.nanpercentile(combined, 2))
    # Mixed scaling: vmin = 2nd percentile, vmax = actual data max.
    norm_shared = PowerNorm(gamma=0.6, vmin=float(p2), vmax=data_max)

    fig = plt.figure(figsize=(17, 7.4))
    ax_import = fig.add_subplot(121, projection="3d")
    ax_export = fig.add_subplot(122, projection="3d")

    # Apply the shared percentile-based normalization to both panels so
    # colours are directly comparable while ignoring a few extreme spikes.
    ax_import.plot_surface(
        x_i,
        y_i,
        z_i,
        cmap="viridis",
        norm=norm_shared,
        linewidth=0,
        antialiased=False,
        rcount=z_i.shape[0],
        ccount=z_i.shape[1],
        shade=False,
    )
    ax_export.plot_surface(
        x_e,
        y_e,
        z_e,
        cmap="viridis",
        norm=norm_shared,
        linewidth=0,
        antialiased=False,
        rcount=z_e.shape[0],
        ccount=z_e.shape[1],
        shade=False,
    )

    for ax, zlabel in (
        (ax_import, "Agile import price in p / kWh"),
        (ax_export, "Agile export price in p / kWh"),
    ):
        ax.set_xlabel("Hour of the day", labelpad=10, fontsize=14)
        ax.set_ylabel("Day of the year", labelpad=12, fontsize=14)
        ax.set_zlabel(zlabel, labelpad=10, rotation=90, fontsize=14)
        ax.zaxis.set_rotate_label(False)

        ax.set_xlim(0, 24)
        ax.set_xticks([0, 6, 12, 18, 24])
        # Ensure z-axis numeric limits reflect the true data extrema so tall
        # spikes (e.g. 99.99 p/kWh) show at full height rather than appearing
        # visually capped.
        ax.set_zlim(data_min, 105)
        ax.view_init(elev=10, azim=-140)
        ax.tick_params(axis="x", labelsize=12)
        ax.tick_params(axis="y", labelsize=12)
        ax.tick_params(axis="z", labelsize=12)

    ax_import.set_title(
        f"Import average for 2025: {avg_import:.2f} p / kWh",
        y=1.02,
        fontsize=16,
    )
    ax_export.set_title(
        f"Export average for 2025: {avg_export:.2f} p / kWh",
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
        description="Create a 3D surface plot of 2025 SScot Agile import prices."
    )
    parser.add_argument("--csv", type=Path, default=default_csv)
    parser.add_argument("--out", type=Path, default=default_out)
    args = parser.parse_args()

    make_plot(csv_path=args.csv, out_path=args.out)
    print(f"Saved plot to {args.out}")


if __name__ == "__main__":
    main()