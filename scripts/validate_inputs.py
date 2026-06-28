"""Input validation for the PV-BESS techno-economic model.

Phase A1 — PV generation validation
    Compares PVGIS SARAH-3 monthly specific yield against Sheffield Solar
    PV_Live half-hourly outturn estimates for PES region 18 (SPEN SP
    Distribution: south Scotland including Glasgow). Also cross-checks the
    annual specific yield against the DESNZ Regional Renewable Statistics
    figure for Glasgow City (2023).

Phase A2 — Household demand analysis
    Computes annual total, monthly totals, and seasonal mean daily load
    shapes from the CREST synthetic demand profile. Reports comparison
    against Ofgem Typical Domestic Consumption Values (TDCVs).

Outputs saved to results/:
    pvlive_cache_2023_pes18.csv              -- PV_Live API fetch (cached)
    validation_pv_monthly_comparison.png     -- Monthly specific yield bar chart
    validation_demand_seasonal_profiles.png  -- Seasonal mean daily demand

Usage
-----
    python scripts/validate_inputs.py                   # run both sections
    python scripts/validate_inputs.py --pv-only         # PV section only
    python scripts/validate_inputs.py --demand-only     # demand section only
    python scripts/validate_inputs.py --refresh-pvlive  # force API re-fetch
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import pytz

# ── Repository root on sys.path so src/ imports work ──────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_loader import build_canonical_index, load_demand, load_pv

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

PV_FILE = (
    DATA_DIR
    / "Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv"
)
DEMAND_FILE = DATA_DIR / "demand_halfhourly_2023.csv"
PVLIVE_CACHE = RESULTS_DIR / "pvlive_cache_2023_pes18.csv"

# ── Constants ──────────────────────────────────────────────────────────────────
SYSTEM_KWP = 4.0    # PVGIS simulated system size (kWp)
PES_ID = 18         # SPEN SP Distribution — south Scotland including Glasgow
YEAR = 2023

# DESNZ Regional Renewable Statistics 2023: Glasgow City local authority
# Installed capacity 21.4 MWp, generation 17,604 MWh → 17604/21.4 = 822.6 kWh/kWp
DESNZ_ANNUAL_YIELD = 17_604 / 21.4  # kWh/kWp

# Ofgem Typical Domestic Consumption Values (current at time of writing)
OFGEM_TDCV = {"Low": 1_800, "Medium": 2_900, "High": 4_300}

MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

SEASON_MONTHS = {
    "Winter (Dec–Feb)": [12, 1, 2],
    "Spring (Mar–May)": [3, 4, 5],
    "Summer (Jun–Aug)": [6, 7, 8],
    "Autumn (Sep–Nov)": [9, 10, 11],
}

SEASON_COLORS = {
    "Winter (Dec–Feb)": "#4a7fb5",
    "Spring (Mar–May)": "#6ab187",
    "Summer (Jun–Aug)": "#e07b39",
    "Autumn (Sep–Nov)": "#9b6bb5",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase A1 — PV Generation Validation
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_pvlive_2023(force_refresh: bool = False) -> pd.DataFrame:
    """Fetch (or load cached) PV_Live half-hourly data for PES 18, full year 2023.

    The result is sorted chronologically and cached to ``results/`` so that
    subsequent runs do not need an internet connection.

    Returns
    -------
    pd.DataFrame
        Columns: pes_id, datetime_gmt (UTC-aware), generation_mw,
        installedcapacity_mwp. Sorted ascending by datetime_gmt.
    """
    if PVLIVE_CACHE.exists() and not force_refresh:
        print(f"  Loading cached PV_Live data from {PVLIVE_CACHE.name}")
        df = pd.read_csv(PVLIVE_CACHE, parse_dates=["datetime_gmt"])
        df["datetime_gmt"] = pd.to_datetime(df["datetime_gmt"], utc=True)
        return df

    print("  Fetching PV_Live data from API (this may take a moment)...")
    from pvlive_api import PVLive  # noqa: PLC0415

    utc = pytz.utc
    pvl = PVLive()
    df = pvl.between(
        start=datetime(YEAR, 1, 1, 0, 30, tzinfo=utc),
        end=datetime(YEAR + 1, 1, 1, 0, 0, tzinfo=utc),
        entity_type="pes",
        entity_id=PES_ID,
        extra_fields="installedcapacity_mwp",
        dataframe=True,
    )
    df = df.sort_values("datetime_gmt").reset_index(drop=True)
    # Keep only rows whose timestamp falls within 2023 UTC
    df = df[df["datetime_gmt"].dt.year == YEAR].copy()
    df.to_csv(PVLIVE_CACHE, index=False)
    print(f"  Saved {len(df)} rows to {PVLIVE_CACHE.name}")
    return df


def _pvgis_monthly_yield() -> pd.Series:
    """Return PVGIS monthly specific yield (kWh/kWp) from the 2023 SARAH-3 CSV."""
    idx = build_canonical_index(YEAR)
    pv_kw = load_pv(PV_FILE, idx)  # half-hourly kW for a SYSTEM_KWP system
    # Specific yield per half-hour: (kW / kWp) × 0.5 h = kWh/kWp
    specific_hh = pv_kw / SYSTEM_KWP * 0.5
    specific_hh.index = idx
    return (
        specific_hh.groupby(specific_hh.index.month)
        .sum()
        .rename("pvgis_kwh_per_kwp")
    )


def _pvlive_monthly_yield(df: pd.DataFrame) -> pd.Series:
    """Compute monthly specific yield (kWh/kWp) from a PV_Live DataFrame.

    Specific yield per half-hour = (generation_mw / installedcapacity_mwp) × 0.5 h
    Units: MW / MWp × h = MWh/MWp = kWh/kWp.
    """
    work = df.copy()
    work["specific_kwh_per_kwp"] = (
        work["generation_mw"] / work["installedcapacity_mwp"] * 0.5
    )
    work["month"] = work["datetime_gmt"].dt.month
    return (
        work.groupby("month")["specific_kwh_per_kwp"]
        .sum()
        .rename("pvlive_kwh_per_kwp")
    )


def validate_pv(force_refresh: bool = False) -> None:
    """Run PV validation and produce the monthly comparison chart."""
    print("\n" + "=" * 60)
    print("PHASE A1 — PV Generation Validation")
    print("=" * 60)

    print("\nLoading PVGIS SARAH-3 data...")
    pvgis_monthly = _pvgis_monthly_yield()

    print("Loading PV_Live outturn data (PES 18, south Scotland)...")
    pvlive_raw = fetch_pvlive_2023(force_refresh=force_refresh)
    pvlive_monthly = _pvlive_monthly_yield(pvlive_raw)

    pvgis_vals = pvgis_monthly.reindex(range(1, 13)).to_numpy()
    pvlive_vals = pvlive_monthly.reindex(range(1, 13)).to_numpy()

    annual_pvgis = float(pvgis_vals.sum())
    annual_pvlive = float(pvlive_vals.sum())

    # ── Printed comparison table ───────────────────────────────────────────────
    print(f"\nAnnual specific yield summary")
    print(f"  {'Source':<45}  {'kWh/kWp':>7}")
    print(f"  {'-'*45}  {'-'*7}")
    print(f"  {'PVGIS SARAH-3 (simulated, 4 kWp, Glasgow)':<45}  {annual_pvgis:>7.0f}")
    print(f"  {'PV_Live outturn (PES 18, SP Distribution)':<45}  {annual_pvlive:>7.0f}")
    print(
        f"  {'DESNZ Regional Stats (Glasgow City 2023)':<45}  {DESNZ_ANNUAL_YIELD:>7.0f}"
    )
    print(
        f"\n  PVGIS vs PV_Live: {annual_pvgis - annual_pvlive:+.0f} kWh/kWp "
        f"({(annual_pvgis - annual_pvlive) / annual_pvlive * 100:+.1f}%)"
    )
    print(
        f"  PVGIS vs DESNZ:   {annual_pvgis - DESNZ_ANNUAL_YIELD:+.0f} kWh/kWp "
        f"({(annual_pvgis - DESNZ_ANNUAL_YIELD) / DESNZ_ANNUAL_YIELD * 100:+.1f}%)"
    )

    print(f"\nMonthly breakdown (kWh/kWp)")
    print(f"  {'Month':>5}  {'PVGIS':>7}  {'PV_Live':>8}  {'Diff':>6}  {'Diff%':>6}")
    for i, name in enumerate(MONTH_NAMES):
        d = pvgis_vals[i] - pvlive_vals[i]
        pct = d / pvlive_vals[i] * 100 if pvlive_vals[i] > 0 else float("nan")
        print(
            f"  {name:>5}  {pvgis_vals[i]:>7.1f}  {pvlive_vals[i]:>8.1f}"
            f"  {d:>+6.1f}  {pct:>+5.1f}%"
        )

    # ── Monthly bar chart ─────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(12)
    w = 0.35

    ax.bar(
        x - w / 2, pvgis_vals, width=w,
        label=f"PVGIS SARAH-3 — simulated (annual: {annual_pvgis:.0f} kWh/kWp)",
        color="#e07b39", alpha=0.9, edgecolor="white",
    )
    ax.bar(
        x + w / 2, pvlive_vals, width=w,
        label=f"PV_Live outturn, PES 18 (annual: {annual_pvlive:.0f} kWh/kWp)",
        color="#4a7fb5", alpha=0.9, edgecolor="white",
    )

    # Annotate the DESNZ annual total as a text box (monthly breakdown unavailable)
    ax.annotate(
        f"DESNZ Glasgow City 2023: {DESNZ_ANNUAL_YIELD:.0f} kWh/kWp/yr\n"
        f"(annual total; monthly breakdown not available)",
        xy=(0.98, 0.97), xycoords="axes fraction",
        ha="right", va="top", fontsize=8,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", edgecolor="grey"),
    )

    ax.set_xticks(x)
    ax.set_xticklabels(MONTH_NAMES)
    ax.set_ylabel("Specific yield (kWh/kWp)")
    ax.set_title(
        "Monthly PV specific yield: PVGIS SARAH-3 vs PV_Live measured outturn\n"
        "Glasgow / south Scotland (SPEN SP Distribution, PES 18), 2023"
    )
    ax.legend(fontsize=9, loc="upper left")
    ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    out_path = RESULTS_DIR / "validation_pv_monthly_comparison.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"\nFigure saved: {out_path.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# Phase A2 — Household Demand Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def validate_demand() -> None:
    """Analyse the CREST demand profile and produce the seasonal shape figure."""
    print("\n" + "=" * 60)
    print("PHASE A2 — Household Demand Analysis")
    print("=" * 60)

    idx = build_canonical_index(YEAR)
    demand = load_demand(DEMAND_FILE, idx)  # half-hourly kW

    # ── Annual and monthly stats ───────────────────────────────────────────────
    annual_kwh = float(demand.sum() * 0.5)   # kWh = kW × 0.5 h per period
    monthly_kwh = demand.groupby(demand.index.month).sum() * 0.5
    peak_kw = float(demand.max())
    mean_kw = float(demand.mean())
    load_factor = mean_kw / peak_kw  # dimensionless

    print(f"\nAnnual summary")
    print(f"  Total consumption:    {annual_kwh:,.0f} kWh/yr")
    print(f"  Peak half-hour:       {peak_kw:.2f} kW")
    print(f"  Mean half-hour:       {mean_kw:.3f} kW")
    print(f"  Load factor:          {load_factor:.3f}")

    print(f"\nOfgem Typical Domestic Consumption Values (TDCVs)")
    for band, val in OFGEM_TDCV.items():
        marker = " <-- CREST" if val == max(OFGEM_TDCV.values()) else ""
        print(f"  {band + ':':<10} {val:,} kWh/yr{marker}")
    above_high = annual_kwh > OFGEM_TDCV["High"]
    print(
        f"  CREST:     {annual_kwh:,.0f} kWh/yr  "
        f"({'above high TDCV' if above_high else 'within TDCV range'})"
    )

    print(f"\nMonthly demand (kWh)")
    for i, m in enumerate(range(1, 13)):
        print(f"  {MONTH_NAMES[i]}: {monthly_kwh[m]:>6.0f} kWh")

    # ── Seasonal mean daily profiles ──────────────────────────────────────────
    n_days = len(demand) // 48
    hh_of_day = np.tile(np.arange(48), n_days)

    df = pd.DataFrame({
        "demand_kw": demand.to_numpy(),
        "month": demand.index.month,
        "hh_of_day": hh_of_day,
    })

    # Half-hour labels for x-axis: 00:00, 00:30, …, 23:30
    hh_labels = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
    tick_pos = list(range(0, 48, 4))          # every 2 hours
    tick_labels = [hh_labels[i] for i in tick_pos]

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharey=True, sharex=True)

    for ax, (season_name, months_list) in zip(axes.flat, SEASON_MONTHS.items()):
        season_df = df[df["month"].isin(months_list)]
        mean_profile = season_df.groupby("hh_of_day")["demand_kw"].mean()
        std_profile = season_df.groupby("hh_of_day")["demand_kw"].std()
        n_days_season = season_df["hh_of_day"].value_counts().max()
        x = np.arange(48)
        color = SEASON_COLORS[season_name]

        ax.plot(x, mean_profile.values, color=color, linewidth=2, label="Mean")
        ax.fill_between(
            x,
            (mean_profile - std_profile).values,
            (mean_profile + std_profile).values,
            color=color, alpha=0.2, label="±1 std dev",
        )
        ax.set_title(
            f"{season_name}  (n = {n_days_season} days)",
            fontsize=10, fontweight="bold",
        )
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=7)
        ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
        ax.grid(axis="y", alpha=0.3)
        ax.set_ylabel("Demand (kW)")
        ax.legend(fontsize=8)

    fig.suptitle(
        "CREST synthetic household demand — seasonal mean daily profiles\n"
        f"Annual total: {annual_kwh:,.0f} kWh/yr  |  Peak: {peak_kw:.2f} kW  |  "
        f"Ofgem 'high' TDCV: {OFGEM_TDCV['High']:,} kWh/yr",
        fontsize=10,
    )
    fig.tight_layout()

    out_path = RESULTS_DIR / "validation_demand_seasonal_profiles.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"\nFigure saved: {out_path.name}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate model inputs: PV generation (A1) and household demand (A2)."
        )
    )
    parser.add_argument(
        "--refresh-pvlive", action="store_true",
        help="Force re-fetch of PV_Live data even if a local cache exists.",
    )
    parser.add_argument(
        "--pv-only", action="store_true",
        help="Run only Phase A1 (PV validation).",
    )
    parser.add_argument(
        "--demand-only", action="store_true",
        help="Run only Phase A2 (demand analysis).",
    )
    args = parser.parse_args()

    run_pv = not args.demand_only
    run_demand = not args.pv_only

    if run_pv:
        validate_pv(force_refresh=args.refresh_pvlive)
    if run_demand:
        validate_demand()


if __name__ == "__main__":
    main()
