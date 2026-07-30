"""Create a two-panel CREST demand figure for the methodology section.

Panel (a): mean diurnal profile for winter (DJF) and summer (JJA), over the
           all-year interquartile range, showing that the canonical
           morning/evening double peak emerges in expectation.
Panel (b): one raw half-hourly day, showing the unsmoothed stochastic series
           that the dispatch model is actually driven by.

Output image is saved to data/graphs by default.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

WINTER_MONTHS = (12, 1, 2)
SUMMER_MONTHS = (6, 7, 8)

# Okabe-Ito blue / vermillion: distinguishable under all common CVD types.
COLOR_WINTER = "#0072B2"
COLOR_SUMMER = "#D55E00"
COLOR_DAY = "#0072B2"
INK_MUTED = "#4D4D4D"


def _default_paths() -> tuple[Path, Path]:
    here = Path(__file__).resolve().parent
    csv_path = here.parent / "demand_halfhourly_2025.csv"
    out_path = here / "crest_demand_profile_2025.png"
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

    df = df.rename(columns={ts_col: "timestamp"}).sort_values("timestamp")
    df["hour_of_day"] = df["timestamp"].dt.hour + (df["timestamp"].dt.minute / 60.0)
    df["month"] = df["timestamp"].dt.month
    return df


def _wrap_to_midnight(hours: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Repeat the 00:00 value at 24:00 so curves span the full day."""
    return np.append(hours, 24.0), np.append(values, values[0])


def _season_stats(df: pd.DataFrame, months: tuple[int, ...]) -> pd.DataFrame:
    grouped = df[df["month"].isin(months)].groupby("hour_of_day")["demand_kW"]
    return pd.DataFrame(
        {
            "mean": grouped.mean(),
            "p25": grouped.quantile(0.25),
            "p75": grouped.quantile(0.75),
        }
    ).sort_index()


def _annotate_peak(ax, hours: np.ndarray, values: np.ndarray, window: tuple[float, float],
                   label: str, y_offset: float) -> None:
    mask = (hours >= window[0]) & (hours <= window[1])
    idx = int(np.argmax(np.where(mask, values, -np.inf)))
    peak_hour, peak_value = hours[idx], values[idx]

    ax.annotate(
        f"{label}\n{int(peak_hour):02d}:{int(round((peak_hour % 1) * 60)):02d}",
        xy=(peak_hour, peak_value),
        xytext=(peak_hour, peak_value + y_offset),
        ha="center",
        va="bottom",
        fontsize=9,
        color=INK_MUTED,
        arrowprops=dict(arrowstyle="-", color=INK_MUTED, linewidth=0.8, shrinkA=0, shrinkB=2),
    )


def _style_axes(ax) -> None:
    ax.grid(True, color="0.9", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("0.6")
    ax.set_xlim(0, 24)
    ax.set_xticks(np.arange(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}:00" for h in range(0, 25, 3)])
    ax.tick_params(labelsize=10, color="0.6")


def _plot_diurnal(ax, df: pd.DataFrame) -> None:
    winter = _season_stats(df, WINTER_MONTHS)
    summer = _season_stats(df, SUMMER_MONTHS)
    hours = winter.index.to_numpy(dtype=float)

    # The IQR is drawn over all months, not per season: winter and summer
    # dispersion are near-identical (mean IQR 0.47 vs 0.42 kW, and summer is
    # the wider of the two through the evening peak), so a per-season band
    # would imply a seasonal difference in spread that the data does not show.
    allyear = _season_stats(df, tuple(range(1, 13)))

    h, p25 = _wrap_to_midnight(hours, allyear["p25"].to_numpy())
    _, p75 = _wrap_to_midnight(hours, allyear["p75"].to_numpy())
    _, winter_mean = _wrap_to_midnight(hours, winter["mean"].to_numpy())
    _, summer_mean = _wrap_to_midnight(hours, summer["mean"].to_numpy())

    ax.fill_between(h, p25, p75, color="0.55", alpha=0.20, linewidth=0,
                    label="All-year interquartile range")
    ax.plot(h, winter_mean, color=COLOR_WINTER, linewidth=2.0, label="Winter mean (DJF)")
    ax.plot(h, summer_mean, color=COLOR_SUMMER, linewidth=2.0, label="Summer mean (JJA)")

    _annotate_peak(ax, h, winter_mean, (5.0, 11.0), "Morning peak", 0.28)
    _annotate_peak(ax, h, winter_mean, (15.0, 22.0), "Evening peak", 0.28)

    _style_axes(ax)
    ax.set_ylim(0, max(p75.max(), winter_mean.max()) * 1.42)
    ax.set_ylabel("Mean demand (kW)", fontsize=11)
    ax.set_title("(a) Seasonal mean diurnal profile", fontsize=12, loc="left", pad=8)
    ax.legend(frameon=False, fontsize=9.5, loc="upper left", handlelength=1.6)


def _plot_single_day(ax, df: pd.DataFrame, day: pd.Timestamp) -> None:
    mask = df["timestamp"].dt.normalize() == day
    day_df = df.loc[mask].sort_values("timestamp")
    if day_df.empty:
        raise SystemExit(f"No demand data for {day.date()}")

    hours = day_df["hour_of_day"].to_numpy(dtype=float)
    values = day_df["demand_kW"].to_numpy(dtype=float)
    # steps-post: each half-hourly value is a piecewise-constant slot average.
    h = np.append(hours, 24.0)
    v = np.append(values, values[-1])
    ax.step(h, v, where="post", color=COLOR_DAY, linewidth=1.6)
    ax.fill_between(h, 0, v, step="post", color=COLOR_DAY, alpha=0.12, linewidth=0)

    peak_idx = int(np.argmax(values))
    daily_kwh = float(values.sum() * 0.5)
    ax.annotate(
        f"Peak {values[peak_idx]:.1f} kW",
        xy=(hours[peak_idx], values[peak_idx]),
        xytext=(hours[peak_idx] + 0.6, values[peak_idx]),
        ha="left",
        va="center",
        fontsize=9,
        color=INK_MUTED,
    )

    _style_axes(ax)
    ax.set_ylim(0, values.max() * 1.22)
    ax.set_ylabel("Demand (kW)", fontsize=11)
    ax.set_xlabel("Time of day", fontsize=11)
    ax.set_title(
        f"(b) Single day: {day.strftime('%d %B %Y')} — {daily_kwh:.1f} kWh",
        fontsize=12,
        loc="left",
        pad=8,
    )


def make_plot(csv_path: Path, out_path: Path, day: pd.Timestamp) -> None:
    _configure_style()
    df = _load_demand_df(csv_path)

    fig, axes = plt.subplots(2, 1, figsize=(7.2, 6.4), sharex=True)
    _plot_diurnal(axes[0], df)
    _plot_single_day(axes[1], df, day)

    total_kwh = float(df["demand_kW"].sum() * 0.5)
    fig.text(
        0.99,
        0.012,
        f"Annual total: {total_kwh:,.0f} kWh",
        ha="right",
        va="bottom",
        fontsize=9,
        color=INK_MUTED,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def main() -> None:
    default_csv, default_out = _default_paths()
    parser = argparse.ArgumentParser(
        description="Two-panel CREST demand profile figure for the methodology section."
    )
    parser.add_argument("--csv", type=Path, default=default_csv)
    parser.add_argument("--out", type=Path, default=default_out)
    parser.add_argument("--day", type=pd.Timestamp, default=pd.Timestamp("2025-12-12"),
                        help="Date shown in panel (b).")
    args = parser.parse_args()

    make_plot(csv_path=args.csv, out_path=args.out, day=args.day)
    print(f"Saved plot to {args.out}")


if __name__ == "__main__":
    main()
