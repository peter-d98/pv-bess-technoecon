"""Unit tests for the location registry (src.locations, Spec 04).

Hand-checkable oracles (L1-L12) for the four study locations: registry
membership, per-location tariff rates, standing-charge metadata, file-path
resolution and on-disk existence, case-insensitive lookup, unknown-key failure,
and the Glasgow == ``TariffRates()`` baseline-preservation guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.locations import LOCATIONS, get_location, resolve_paths
from src.tariffs import TariffRates

DATA_DIR = REPO_ROOT / "data"


def test_l1_registry_membership():
    """L1: exactly the four confirmed locations (no Midlands)."""
    assert set(LOCATIONS) == {"inverness", "glasgow", "manchester", "plymouth"}


def test_l2_glasgow_rates():
    """L2: Glasgow (Southern Scotland) rates."""
    r = get_location("glasgow").rates
    assert r.flat_rate == 0.2642
    assert r.e7_day == 0.3155
    assert r.e7_night == 0.1330
    assert r.region == "southern_scotland"


def test_l3_inverness_rates():
    """L3: Inverness (North Scotland) rates."""
    r = get_location("inverness").rates
    assert r.flat_rate == 0.2642
    assert r.e7_day == 0.3260
    assert r.e7_night == 0.1375


def test_l4_manchester_rates():
    """L4: Manchester (NW England) rates."""
    r = get_location("manchester").rates
    assert r.flat_rate == 0.2613
    assert r.e7_day == 0.3155
    assert r.e7_night == 0.1330


def test_l5_plymouth_rates():
    """L5: Plymouth (SW England) rates."""
    r = get_location("plymouth").rates
    assert r.flat_rate == 0.2639
    assert r.e7_day == 0.3190
    assert r.e7_night == 0.1340


def test_l6_shared_conventions():
    """L6: every location shares year, SEG, and E7 window conventions."""
    for spec in LOCATIONS.values():
        assert spec.rates.year == 2025
        assert spec.rates.seg_national == 0.13
        assert spec.rates.e7_offpeak_start_h == 0.0
        assert spec.rates.e7_offpeak_end_h == 7.0


def test_l7_standing_charge_metadata():
    """L7: standing-charge metadata (Glasgow / Manchester), GBP/day."""
    g = get_location("glasgow").rates
    assert g.flat_standing_charge_per_day == 0.642
    assert g.e7_standing_charge_per_day == 0.647
    m = get_location("manchester").rates
    assert m.flat_standing_charge_per_day == 0.4761
    assert m.e7_standing_charge_per_day == 0.4690


def test_l8_manchester_paths():
    """L8: Manchester resolves the on-disk PV file and NWEng Agile CSV."""
    pv_path, agile_path = resolve_paths(get_location("manchester"), DATA_DIR)
    assert pv_path.name == (
        "Timeseries_53.483_-2.242_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv"
    )
    assert agile_path.name == (
        "agile-half-hour-actual-rates-01-01-2025_31-12-2025_NWEng.csv"
    )
    assert pv_path.exists()
    assert agile_path.exists()


def test_l9_all_files_exist():
    """L9: all four locations' PV and Agile files exist under data/."""
    for spec in LOCATIONS.values():
        pv_path, agile_path = resolve_paths(spec, DATA_DIR)
        assert pv_path.exists(), f"missing PV file for {spec.name}: {pv_path}"
        assert agile_path.exists(), f"missing Agile file for {spec.name}: {agile_path}"


def test_l10_case_insensitive_lookup():
    """L10: lookup is case-insensitive."""
    assert get_location("GLASGOW") is LOCATIONS["glasgow"]
    assert get_location("  Glasgow  ") is LOCATIONS["glasgow"]


def test_l11_unknown_location_raises():
    """L11: an unknown location raises with the valid keys named."""
    with pytest.raises(KeyError) as exc:
        get_location("cardiff")
    msg = str(exc.value)
    assert "glasgow" in msg and "plymouth" in msg


def test_l12_glasgow_equals_default_rates():
    """L12: Glasgow's rates equal the TariffRates() default (baseline guard)."""
    assert get_location("glasgow").rates == TariffRates()
