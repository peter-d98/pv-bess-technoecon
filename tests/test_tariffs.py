"""Unit tests for the tariff layer (src.tariffs, Spec 03).

Hand-checkable numeric oracles for the flat, Economy 7, and Agile constructors
and the ``build_tariff`` factory. Rates are Southern Scotland 2023, VAT-inclusive.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data_loader import build_canonical_index, load_agile_prices
from src.tariffs import (
    TariffRates,
    agile_import_export,
    build_tariff,
    economy7_import,
    flat_export,
    flat_import,
    no_export,
)

DATA_DIR = REPO_ROOT / "data"
AGILE_FILE = DATA_DIR / "agile-half-hour-actual-rates-01-01-2023_31-12-2023.csv"

YEAR = 2023
EXPECTED_PERIODS = 365 * 48  # 17 520; 2023 is not a leap year

requires_agile = pytest.mark.skipif(
    not AGILE_FILE.exists(), reason="Agile CSV not present in data/"
)


@pytest.fixture(scope="module")
def index():
    return build_canonical_index(YEAR)


@pytest.fixture(scope="module")
def rates():
    return TariffRates()


# --- T1: flat import ---------------------------------------------------------

def test_t1_flat_import(index):
    s = flat_import(index, 0.30)
    assert len(s) == EXPECTED_PERIODS
    assert (s == 0.30).all()


# --- T2/T3/T4: Economy 7 -----------------------------------------------------

def test_t2_e7_boundary_values(index):
    s = economy7_import(index, day=0.3439, night=0.1435,
                        offpeak_start_h=0.0, offpeak_end_h=7.0)
    assert s.loc["2023-01-01 00:00"] == pytest.approx(0.1435)
    assert s.loc["2023-01-01 06:30"] == pytest.approx(0.1435)
    assert s.loc["2023-01-01 07:00"] == pytest.approx(0.3439)
    assert s.loc["2023-01-01 12:00"] == pytest.approx(0.3439)


def test_t3_e7_night_count(index):
    s = economy7_import(index, day=0.3439, night=0.1435,
                        offpeak_start_h=0.0, offpeak_end_h=7.0)
    n_night = int((s == 0.1435).sum())
    assert n_night == 14 * 365  # 5110


def test_t4_e7_daily_mean(index):
    s = economy7_import(index, day=0.3439, night=0.1435,
                        offpeak_start_h=0.0, offpeak_end_h=7.0)
    day_one = s.loc["2023-01-01"]
    assert len(day_one) == 48
    assert day_one.mean() == pytest.approx(0.285450, abs=1e-6)


def test_t5_e7_wrapping_window(index):
    # 23:30-06:30 wrapping window.
    s = economy7_import(index, day=0.3439, night=0.1435,
                        offpeak_start_h=23.5, offpeak_end_h=6.5)
    assert s.loc["2023-01-01 23:30"] == pytest.approx(0.1435)  # night
    assert s.loc["2023-01-01 06:00"] == pytest.approx(0.1435)  # night
    assert s.loc["2023-01-01 06:30"] == pytest.approx(0.3439)  # day (end exclusive)


# --- T6/T7: export constructors ----------------------------------------------

def test_t6_flat_export(index):
    assert (flat_export(index, 0.045) == 0.045).all()
    assert (flat_export(index, 0.13) == 0.13).all()


def test_t7_no_export(index):
    assert (no_export(index) == 0.0).all()


# --- T8: Agile constructor wraps the loader ----------------------------------

@requires_agile
def test_t8_agile_matches_loader(index):
    imp, exp = agile_import_export(AGILE_FILE, index)
    ref = load_agile_prices(AGILE_FILE, index)
    np.testing.assert_array_equal(imp.to_numpy(), ref["import_price"].to_numpy())
    np.testing.assert_array_equal(exp.to_numpy(), ref["export_price"].to_numpy())


# --- T9/T10/T11: build_tariff factory ----------------------------------------

def test_t9_build_flat_matched_export(index, rates):
    imp, exp = build_tariff("flat", index, rates, export="matched")
    assert (imp == 0.2642).all()
    assert (exp == 0.13).all()  # matched => national SEG


@requires_agile
def test_t10_build_agile_matched(index, rates):
    imp, exp = build_tariff("agile", index, rates, agile_path=AGILE_FILE, export="matched")
    ref = load_agile_prices(AGILE_FILE, index)
    np.testing.assert_array_equal(imp.to_numpy(), ref["import_price"].to_numpy())
    np.testing.assert_array_equal(exp.to_numpy(), ref["export_price"].to_numpy())


def test_t11_build_e7_no_export(index, rates):
    imp, exp = build_tariff("e7", index, rates, export="none")
    assert (exp == 0.0).all()
    assert set(np.unique(imp.to_numpy())) == {0.1330, 0.3155}


def test_build_tariff_rejects_unknown(index, rates):
    with pytest.raises(ValueError):
        build_tariff("go", index, rates)
    with pytest.raises(ValueError):
        build_tariff("flat", index, rates, export="wholesale")


def test_build_agile_requires_path(index, rates):
    with pytest.raises(ValueError):
        build_tariff("agile", index, rates, agile_path=None)


def test_build_agile_export_requires_path(index, rates):
    # Non-Agile import but Agile export still needs the CSV path.
    with pytest.raises(ValueError):
        build_tariff("flat", index, rates, agile_path=None, export="agile")


def test_build_seg_untied_override(index, rates):
    imp, exp = build_tariff("flat", index, rates, export="seg", seg_rate=rates.seg_untied)
    assert (exp == 0.045).all()
