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


def test_soc_dynamics_quantitative():
    """SOC must increase by exactly eta_c * p_charge * dt / capacity per timestep.

    With max_grid_kw=0 the model cannot import or export, so the energy balance
    forces p_charge = PV (3 kW) and p_discharge = 0 for both timesteps. The
    resulting SOC increment is fully determined by the constraint equations and
    can be verified without running the solver independently.

    Expected increment per timestep:
        delta = eta_c * p_charge * dt / capacity
               = 0.95 * 3.0 * 0.5 / 10.0 = 0.1425
    """
    T = 2
    dt = 0.5
    eta_c = 0.95
    p_charge_forced = 3.0          # kW; all PV must be stored (no export allowed)
    delta = eta_c * p_charge_forced * dt / 10.0  # = 0.1425 per timestep

    battery = BatteryParams(
        capacity_kwh=10.0,
        eta_charge=eta_c,
        eta_discharge=0.95,
        soc_min=0.0,
        soc_max=1.0,
        soc_init=0.0,
        degradation_cost_per_kwh=0.0,
    )
    result = solve_dispatch(
        pv_kw=np.full(T, p_charge_forced),
        demand_kw=np.zeros(T),
        import_price=np.full(T, 0.10),
        export_price=np.full(T, 0.10),
        battery=battery,
        dt_hours=dt,
        max_grid_kw=0.0,               # forces p_import = p_export = 0
        terminal_soc_equals_initial=False,
    )
    assert result.status == "optimal"

    soc_end = result.schedule["soc_end"].to_numpy()
    assert abs(soc_end[0] - delta) < 1e-5, (
        f"SOC after t=0: expected {delta:.6f}, got {soc_end[0]:.6f}"
    )
    assert abs(soc_end[1] - 2 * delta) < 1e-5, (
        f"SOC after t=1: expected {2 * delta:.6f}, got {soc_end[1]:.6f}"
    )


def test_import_export_mutual_exclusivity_negative_price():
    """The binary variable must prevent simultaneous import and export.

    A negative import price (grid pays you to consume — as occurs on Agile
    during excess-wind periods) creates an incentive to import and export at the
    same time. The binary ``grid_import_on[t]`` must prevent this by forcing
    p_import[t] = 0 whenever p_export[t] > 0 and vice versa.

    This test uses four timesteps with mixed positive/negative import prices and
    asserts that for every timestep the product p_import * p_export ≈ 0.
    """
    T = 4
    battery = BatteryParams(degradation_cost_per_kwh=0.05)
    result = _solve(
        pv=   [0.5, 1.0, 2.0, 0.5],
        demand=[0.5, 0.5, 0.5, 0.5],
        import_price=[-0.10, 0.10, -0.05, 0.08],
        export_price=[ 0.05, 0.05,  0.05, 0.05],
        battery=battery,
        dt_hours=0.5,
    )
    assert result.status == "optimal"
    p_imp = result.schedule["p_import_kw"].to_numpy()
    p_exp = result.schedule["p_export_kw"].to_numpy()
    assert np.all((p_imp < TOL) | (p_exp < TOL)), (
        "Simultaneous import and export detected — binary constraint not enforced."
    )


def test_terminal_soc_equals_initial_exactly():
    """When terminal_soc_equals_initial=True the final SOC must equal soc_init.

    This is the self-contained daily accounting constraint. The optimiser is
    free to cycle the battery during the day, but the last soc_end value must
    match soc_init to within numerical tolerance.
    """
    n = 24
    data = make_stage1_dataset(n_hours=n)
    battery = BatteryParams(degradation_cost_per_kwh=0.05)
    # terminal_soc_equals_initial defaults to True inside solve_dispatch
    result = _solve(
        pv=data["pv_kw"].to_numpy(),
        demand=data["demand_kw"].to_numpy(),
        import_price=data["import_price"].to_numpy(),
        export_price=data["export_price"].to_numpy(),
        battery=battery,
    )
    assert result.status == "optimal"
    terminal_soc = result.schedule["soc_end"].iloc[-1]
    assert abs(terminal_soc - battery.soc_init) < 1e-5, (
        f"Terminal SOC {terminal_soc:.6f} does not equal soc_init {battery.soc_init:.6f}"
    )


def test_pv_exactly_meets_demand_no_flows():
    """When PV equals demand every timestep all power flows should be zero.

    With PV covering demand exactly at every half-hour, the household is
    self-sufficient and there is no reason to import, export, or cycle the
    battery. A positive degradation cost ensures the optimiser does not cycle
    gratuitously for zero net benefit.
    """
    T = 48
    dt = 0.5
    battery = BatteryParams(degradation_cost_per_kwh=0.05)
    result = _solve(
        pv=np.full(T, 0.5),
        demand=np.full(T, 0.5),
        import_price=np.full(T, 0.20),
        export_price=np.full(T, 0.15),
        battery=battery,
        dt_hours=dt,
    )
    assert result.status == "optimal"
    s = result.schedule
    total_flow = (
        s["p_charge_kw"].sum()
        + s["p_discharge_kw"].sum()
        + s["p_import_kw"].sum()
        + s["p_export_kw"].sum()
    )
    assert total_flow < TOL, (
        f"Expected zero power flows but got total_flow = {total_flow:.6f} kW"
    )


def test_optimal_cost_matches_analytical_solution():
    """The solver must find the analytically correct minimum-cost solution.

    Scenario (2 half-hours, dt = 0.5 h):
      - Period 0 (cheap): demand = 0, PV = 0, import = 0.07 £/kWh, export = 0
      - Period 1 (peak):  demand = 1 kW, PV = 0, import = 0.28 £/kWh, export = 0

    The cheapest strategy is to pre-charge in period 0 and discharge to serve
    the full peak demand in period 1 (zero peak import). The amount charged must
    account for round-trip losses:

        p_charge[0] = demand / (eta_c * eta_d) = 1 / 0.9025 ≈ 1.108 kW

    Expected import cost:
        0.07 * (1 / (eta_c * eta_d)) * 0.5 = 0.07 / (2 * 0.9025) ≈ 0.03878 £

    This tests that the objective function and constraints together steer the
    solver to the provably optimal dispatch.
    """
    eta = 0.95
    dt = 0.5
    demand_peak = 1.0             # kW in period 1
    cheap_price = 0.07            # £/kWh in period 0

    expected_import_cost = cheap_price * demand_peak * dt / (eta * eta)

    battery = BatteryParams(
        eta_charge=eta,
        eta_discharge=eta,
        soc_min=0.0,
        soc_max=1.0,
        soc_init=0.0,
        degradation_cost_per_kwh=0.0,
    )
    result = solve_dispatch(
        pv_kw=np.zeros(2),
        demand_kw=np.array([0.0, demand_peak]),
        import_price=np.array([cheap_price, 0.28]),
        export_price=np.zeros(2),
        battery=battery,
        dt_hours=dt,
        terminal_soc_equals_initial=False,
    )
    assert result.status == "optimal"

    # Peak period should be served entirely from battery, not the grid
    assert result.schedule["p_import_kw"].iloc[1] < TOL, (
        "Period 1 import should be zero — demand should come from battery"
    )
    assert abs(result.import_cost - expected_import_cost) < TOL, (
        f"import_cost {result.import_cost:.6f} != expected {expected_import_cost:.6f}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
