"""Plot four PV timeseries on a single figure.

Reads the four `Timeseries_..._2023_2023.csv` files in `../` and plots the
monthly PV yield as grouped bars in kWh/kWp by location.
"""
from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _configure_style() -> None:
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = [
        "Computer Modern Serif",
        "Computer Modern Roman",
        "CMU Serif",
        "DejaVu Serif",
    ]


def _find_timeseries_files(data_dir: Path) -> list[Path]:
    return sorted(data_dir.glob("Timeseries_*_2023_2023.csv"))


def _load_p_series(csv_path: Path) -> pd.Series:
    # Find header line (starts with 'time,') because PVGIS files contain
    # a short metadata block at the top.
    with csv_path.open("r", encoding="utf8") as fh:
        for i, line in enumerate(fh):
            if line.startswith("time,"):
                header_row = i
                break
        else:
            raise ValueError(f"No header found in {csv_path}")

    df = pd.read_csv(csv_path, skiprows=header_row)
    # Parse timestamps like 20230101:0011 -> %Y%m%d:%H%M. Some files
    # intermittently repeat a short header row; coerce parsing and drop
    # any non-datetime rows.
    df["time_parsed"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M", errors="coerce")
    df = df.dropna(subset=["time_parsed"])  # drop explanatory rows
    df = df.set_index("time_parsed")
    # Column 'P' contains instantaneous power; convert to kW for plotting.
    series = pd.to_numeric(df.get("P"), errors="coerce") / 1000.0
    series.name = csv_path.stem
    return series


def _monthly_yield_kwh_per_kwp(power_kw: pd.Series, nominal_kwp: float = 4.0) -> pd.Series:
    # PVGIS P is hourly power in W; after conversion to kW, summing hourly
    # samples gives monthly energy in kWh. Normalize by installed kWp.
    monthly_kwh = power_kw.resample("ME").sum(min_count=1)
    monthly_kwh_per_kwp = monthly_kwh / nominal_kwp
    monthly_kwh_per_kwp.index = monthly_kwh_per_kwp.index.month
    return monthly_kwh_per_kwp


def make_plot(out_path: Path) -> None:
    data_dir = out_path.parent
    files = _find_timeseries_files(data_dir.parent)
    if not files:
        raise FileNotFoundError("No Timeseries_*_2023_2023.csv files found in data/")

    _configure_style()

    # Friendly location names (approx) mapped from latitude in filename.
    lat_to_name = {
        "50.373": "Plymouth",
        "53.483": "Manchester",
        "55.829": "Glasgow",
        "57.479": "Inverness",
    }

    fig, ax = plt.subplots(figsize=(14, 6))

    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    x = np.arange(12)
    n_locs = len(files)
    width = 0.8 / n_locs

    colors = ["#5D8DE5", "#FF9045", "#48A23C", "#E75959"]
    for idx, path in enumerate(files):
        s = _load_p_series(path)
        monthly = _monthly_yield_kwh_per_kwp(s).reindex(range(1, 13), fill_value=np.nan)

        # Derive a short label from the filename (latitude -> name if known).
        key = path.name.split("_")[1]
        label = lat_to_name.get(key, path.stem)
        offset = (idx - (n_locs - 1) / 2) * width
        ax.bar(x + offset, monthly.values, width=width, label=label, color=colors[idx % len(colors)])

    ax.set_xlabel("Month", fontsize=14)
    ax.set_ylabel("PV yield (kWh/kWp)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(month_labels, fontsize=12)
    ax.tick_params(axis="y", labelsize=12)
    ax.legend(title="Location", fontsize=12, title_fontsize=13)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def main() -> None:
    out = Path(__file__).resolve().parent / "pv_timeseries_2023.png"
    make_plot(out)
    print(f"Saved plot to {out}")


if __name__ == "__main__":
    main()
