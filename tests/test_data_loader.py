"""Unit tests for the v2 real-data loaders (src.data_loader).

These tests verify that the PVGIS, CREST demand, and Octopus Agile files in
data/ load and align correctly onto a single canonical half-hourly index,
with sensible units and no missing values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_loader import (
    build_canonical_index,
    load_agile_prices,
    load_all,
    load_demand,
    load_pv,
)

DATA_DIR = REPO_ROOT / "data"
PV_FILE = DATA_DIR / "Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv"
DEMAND_FILE = DATA_DIR / "demand_halfhourly_2025.csv"
AGILE_FILE = DATA_DIR / "agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv"

YEAR = 2023
EXPECTED_PERIODS = 365 * 48  # 2023 is not a leap year

requires_data = pytest.mark.skipif(
    not (PV_FILE.exists() and DEMAND_FILE.exists() and AGILE_FILE.exists()),
    reason="v2 data files not present in data/",
)


def test_canonical_index_length_non_leap():
    idx = build_canonical_index(2023)
    assert len(idx) == EXPECTED_PERIODS
    assert idx.freqstr == "30min"
    assert idx[0].year == 2023
    assert idx[-1].year == 2023


def test_canonical_index_length_leap():
    idx = build_canonical_index(2024)
    assert len(idx) == 366 * 48


@requires_data
def test_load_pv_aligned_and_in_kw():
    idx = build_canonical_index(YEAR)
    pv = load_pv(PV_FILE, idx)

    assert len(pv) == EXPECTED_PERIODS
    assert not pv.isnull().any()
    assert (pv >= 0).all()
    # A 4 kWp array should never exceed ~4 kW AC output.
    assert pv.max() <= 4.5
    # PV must be zero overnight (e.g. 02:00 on 1 Jan).
    assert pv.iloc[4] == 0.0


@requires_data
def test_load_pv_halfhours_repeat_hourly():
    """Each hourly PV value should fill both of its half-hours identically."""
    idx = build_canonical_index(YEAR)
    pv = load_pv(PV_FILE, idx).to_numpy()
    # Compare even-indexed (HH:00) with odd-indexed (HH:30) values.
    assert (pv[0::2] == pv[1::2]).all()


@requires_data
def test_load_demand_aligned_and_positive():
    idx = build_canonical_index(YEAR)
    demand = load_demand(DEMAND_FILE, idx)

    assert len(demand) == EXPECTED_PERIODS
    assert not demand.isnull().any()
    assert (demand >= 0).all()
    # Index must be the canonical (2023) index, not the source 2025 index.
    assert demand.index.equals(idx)


@requires_data
def test_load_agile_prices_converted_and_complete():
    idx = build_canonical_index(YEAR)
    prices = load_agile_prices(AGILE_FILE, idx)

    assert list(prices.columns) == ["import_price", "export_price"]
    assert len(prices) == EXPECTED_PERIODS
    assert not prices.isnull().any().any()
    # Converted to GBP/kWh: magnitudes should be well under 1 in absolute value.
    assert prices["import_price"].abs().max() < 2.0
    # Agile import prices can be negative; export prices should be non-negative.
    assert (prices["export_price"] >= 0).all()


@requires_data
def test_load_all_combines_columns():
    data = load_all(PV_FILE, DEMAND_FILE, AGILE_FILE, year=YEAR)

    assert list(data.columns) == ["pv_kw", "demand_kw", "import_price", "export_price"]
    assert len(data) == EXPECTED_PERIODS
    assert not data.isnull().any().any()
