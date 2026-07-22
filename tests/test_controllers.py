"""Unit tests for the rules controllers (src.controllers, Spec 05).

Oracles S6-S8: seasonal ToU target SOC, self-consumption energy balance / SOC
bounds / no-grid-charge, and flat-tariff ToU inertness.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.battery import BatteryParams
from src.controllers import self_consumption, self_consumption_tou
from src.tariffs import TariffRates, economy7_import

RATES = TariffRates()  # Glasgow: e7 window 00:00-07:00


def _day_frame(date: str, pv: float, demand: float) -> pd.DataFrame:
    index = pd.date_range(date, periods=48, freq="30min")
    imp = economy7_import(index, RATES.e7_day, RATES.e7_night,
                          RATES.e7_offpeak_start_h, RATES.e7_offpeak_end_h)
    return pd.DataFrame(
        {
            "pv_kw": np.full(48, pv),
            "demand_kw": np.full(48, demand),
            "import_price": imp.to_numpy(),
            "export_price": np.full(48, 0.13),
        },
        index=index,
    )


@pytest.mark.parametrize(
    "date, expected_target",
    [
        ("2025-01-15", 0.66),   # winter: 0.10 + 0.70*0.80
        ("2025-04-15", 0.284),  # spring: 0.10 + 0.23*0.80
        ("2025-10-15", 0.46),   # autumn: 0.10 + 0.45*0.80
        ("2025-07-15", 0.10),   # summer: 0.10 + 0.00*0.80 (no grid charge)
    ],
)
def test_s6_seasonal_tou_target_soc(date, expected_target):
    """S6: overnight grid-charge reaches the season-dependent target SOC."""
    data = _day_frame(date, pv=0.0, demand=0.2)
    battery = BatteryParams(capacity_kwh=10.0, soc_init=0.10)  # start at soc_min
    sched = self_consumption_tou(data, battery, "e7", RATES)
    reached = float(sched["soc_end"].max())
    assert reached == pytest.approx(expected_target, abs=0.01)


def test_s7_self_consumption_balance_bounds_no_gridcharge():
    """S7: per-step energy balance, SOC bounds, and never grid-charging."""
    rng = np.random.default_rng(0)
    index = pd.date_range("2025-06-01", periods=96, freq="30min")
    data = pd.DataFrame(
        {
            "pv_kw": rng.uniform(0, 4, 96),
            "demand_kw": rng.uniform(0, 3, 96),
            "import_price": np.full(96, 0.30),
            "export_price": np.full(96, 0.13),
        },
        index=index,
    )
    battery = BatteryParams(capacity_kwh=10.0)
    sched = self_consumption(data, battery)

    # AC-side power balance: pv + discharge + import == demand + charge + export.
    lhs = sched["pv_kw"] + sched["p_discharge_kw"] + sched["p_import_kw"]
    rhs = sched["demand_kw"] + sched["p_charge_kw"] + sched["p_export_kw"]
    assert np.allclose(lhs, rhs)

    assert sched["soc"].min() >= battery.soc_min - 1e-9
    assert sched["soc_end"].max() <= battery.soc_max + 1e-9

    # No grid charging: charging never coincides with importing.
    charging = sched["p_charge_kw"] > 1e-9
    importing = sched["p_import_kw"] > 1e-9
    assert not (charging & importing).any()


def test_s8_flat_tou_reduces_to_self_consumption():
    """S8: on a flat tariff the ToU rule is inert -> identical schedule."""
    index = pd.date_range("2025-01-10", periods=48, freq="30min")
    rng = np.random.default_rng(1)
    data = pd.DataFrame(
        {
            "pv_kw": rng.uniform(0, 3, 48),
            "demand_kw": rng.uniform(0, 2, 48),
            "import_price": np.full(48, 0.2642),
            "export_price": np.full(48, 0.13),
        },
        index=index,
    )
    battery = BatteryParams(capacity_kwh=10.0)
    a = self_consumption(data, battery)
    b = self_consumption_tou(data, battery, "flat", RATES)
    for col in ("p_charge_kw", "p_discharge_kw", "p_import_kw", "p_export_kw", "soc_end"):
        assert np.allclose(a[col], b[col]), col
