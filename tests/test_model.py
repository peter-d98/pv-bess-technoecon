"""Unit tests for the stage 1 PV-BESS dispatch model.

These tests use simple, hand-verifiable scenarios to confirm the optimiser
behaves as expected and that physical constraints are respected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.battery import BatteryParams
from src.data_gen import make_stage1_dataset
from src.model import solve_dispatch

TOL = 1e-4


def _solve(pv, demand, import_price, export_price, battery, dt_hours=1.0):
    return solve_dispatch(
        pv_kw=np.asarray(pv, dtype=float),
        demand_kw=np.asarray(demand, dtype=float),
        import_price=np.asarray(import_price, dtype=float),
        export_price=np.asarray(export_price, dtype=float),
        battery=battery,
        dt_hours=dt_hours,
    )


def test_flat_price_no_pv_no_cycling():
    """With flat prices and no PV, the battery should not cycle."""
    n = 24
    battery = BatteryParams(degradation_cost_per_kwh=0.0)
    result = _solve(
        pv=np.zeros(n),
        demand=np.full(n, 0.5),
        import_price=np.full(n, 0.20),
        export_price=np.full(n, 0.15),
        battery=battery,
    )
    assert result.status == "optimal"
    assert np.allclose(result.schedule["p_charge_kw"], 0.0, atol=TOL)
    assert np.allclose(result.schedule["p_discharge_kw"], 0.0, atol=TOL)
    # All demand met from grid import.
    assert np.allclose(result.schedule["p_import_kw"], 0.5, atol=TOL)


def test_price_arbitrage_charges_cheap_discharges_peak():
    """Battery should charge during the cheap window and discharge at peak.

    Export price is set to zero to isolate load-shifting arbitrage (avoiding
    expensive peak imports) from grid-charge-then-export arbitrage, which would
    otherwise dominate because the flat SEG rate exceeds the cheap import rate.
    """
    n = 24
    data = make_stage1_dataset(n_hours=n)
    battery = BatteryParams(degradation_cost_per_kwh=0.0)
    result = _solve(
        pv=np.zeros(n),  # isolate arbitrage from PV
        demand=data["demand_kw"].to_numpy(),
        import_price=data["import_price"].to_numpy(),
        export_price=np.zeros(n),  # isolate load-shifting from export arbitrage
        battery=battery,
    )
    assert result.status == "optimal"

    cheap_hours = data["import_price"].to_numpy() < 0.10
    peak_hours = data["import_price"].to_numpy() > 0.20

    charge = result.schedule["p_charge_kw"].to_numpy()
    discharge = result.schedule["p_discharge_kw"].to_numpy()

    # Charging happens predominantly in cheap hours.
    assert charge[cheap_hours].sum() > charge[peak_hours].sum()
    # Discharging happens predominantly in peak hours.
    assert discharge[peak_hours].sum() > discharge[cheap_hours].sum()


def test_soc_within_bounds():
    """SOC must stay within [soc_min, soc_max] at every timestep."""
    n = 24
    data = make_stage1_dataset(n_hours=n)
    battery = BatteryParams(degradation_cost_per_kwh=0.01)
    result = _solve(
        pv=data["pv_kw"].to_numpy(),
        demand=data["demand_kw"].to_numpy(),
        import_price=data["import_price"].to_numpy(),
        export_price=data["export_price"].to_numpy(),
        battery=battery,
    )
    assert result.status == "optimal"
    soc_all = np.concatenate(
        [result.schedule["soc"].to_numpy(), result.schedule["soc_end"].to_numpy()]
    )
    assert soc_all.min() >= battery.soc_min - TOL
    assert soc_all.max() <= battery.soc_max + TOL


def test_energy_balance_satisfied():
    """Energy balance must hold exactly at every timestep."""
    n = 24
    data = make_stage1_dataset(n_hours=n)
    battery = BatteryParams(degradation_cost_per_kwh=0.05)
    result = _solve(
        pv=data["pv_kw"].to_numpy(),
        demand=data["demand_kw"].to_numpy(),
        import_price=data["import_price"].to_numpy(),
        export_price=data["export_price"].to_numpy(),
        battery=battery,
    )
    assert result.status == "optimal"
    s = result.schedule
    lhs = s["pv_kw"] + s["p_import_kw"] + s["p_discharge_kw"]
    rhs = s["demand_kw"] + s["p_export_kw"] + s["p_charge_kw"]
    assert np.allclose(lhs, rhs, atol=TOL)


def test_no_simultaneous_charge_discharge():
    """The optimiser should not charge and discharge in the same timestep."""
    n = 24
    data = make_stage1_dataset(n_hours=n)
    battery = BatteryParams(degradation_cost_per_kwh=0.05)
    result = _solve(
        pv=data["pv_kw"].to_numpy(),
        demand=data["demand_kw"].to_numpy(),
        import_price=data["import_price"].to_numpy(),
        export_price=data["export_price"].to_numpy(),
        battery=battery,
    )
    assert result.status == "optimal"
    product = (
        result.schedule["p_charge_kw"].to_numpy()
        * result.schedule["p_discharge_kw"].to_numpy()
    )
    assert np.allclose(product, 0.0, atol=TOL)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
