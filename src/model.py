"""Deterministic LP scheduling model for a domestic PV-BESS system.

The model minimises total operating cost over a fixed horizon: grid import
cost minus export revenue plus a throughput-based battery degradation penalty,
subject to an energy balance and a generic SOC battery model.
"""

from __future__ import annotations

from dataclasses import dataclass

import cvxpy as cp
import numpy as np
import pandas as pd

from .battery import BatteryParams


@dataclass
class DispatchResult:
    """Container for the solved dispatch schedule and cost breakdown."""

    status: str
    objective: float
    schedule: pd.DataFrame
    import_cost: float
    export_revenue: float
    degradation_cost: float
    net_cost: float


def solve_dispatch(
    pv_kw: np.ndarray,
    demand_kw: np.ndarray,
    import_price: np.ndarray,
    export_price: np.ndarray,
    battery: BatteryParams,
    dt_hours: float = 1.0,
    max_grid_kw: float = 20.0,
    terminal_soc_equals_initial: bool = True,
    solver: str | None = None,
) -> DispatchResult:
    """Solve the single-horizon dispatch problem.

    Grid import and export are mutually exclusive within a timestep (a single
    meter cannot import and export simultaneously). Because there is no loss
    term between import and export, this exclusivity must be enforced with a
    binary variable, making the problem a small MILP. Without it the model is
    unbounded whenever an export price exceeds an import price (the optimiser
    would otherwise pass unlimited power from grid to grid for profit).

    Parameters
    ----------
    pv_kw, demand_kw:
        PV generation and household demand per timestep (kW).
    import_price, export_price:
        Grid import and export prices per timestep (GBP/kWh).
    battery:
        Battery parameters.
    dt_hours:
        Timestep length in hours.
    max_grid_kw:
        Grid connection power limit (kW), used as the big-M bound for the
        import/export mutual-exclusivity constraint.
    terminal_soc_equals_initial:
        If True, the battery must end the horizon at its initial SOC. This
        makes a single-day analysis self-contained, preventing the optimiser
        from simply draining the battery's pre-existing stored energy to
        displace imports (which would unfairly flatter the day's savings).
    solver:
        Optional CVXPY solver name. Defaults to CVXPY's choice.

    Returns
    -------
    DispatchResult
        Solve status, objective value, the dispatch schedule, and a cost
        breakdown.
    """
    pv_kw = np.asarray(pv_kw, dtype=float)
    demand_kw = np.asarray(demand_kw, dtype=float)
    import_price = np.asarray(import_price, dtype=float)
    export_price = np.asarray(export_price, dtype=float)

    n = len(demand_kw)
    if not (len(pv_kw) == len(import_price) == len(export_price) == n):
        raise ValueError("All input profiles must have the same length.")

    # Decision variables (all in kW, except SOC which is a fraction 0-1).
    p_charge = cp.Variable(n, nonneg=True)
    p_discharge = cp.Variable(n, nonneg=True)
    p_import = cp.Variable(n, nonneg=True)
    p_export = cp.Variable(n, nonneg=True)
    soc = cp.Variable(n + 1)

    # Binary: 1 => importing allowed this step, 0 => exporting allowed.
    grid_import_on = cp.Variable(n, boolean=True)

    constraints = []

    # Initial state of charge.
    constraints.append(soc[0] == battery.soc_init)

    # Terminal state of charge: return to the starting level so a single-day
    # horizon is self-contained and accounting is fair.
    if terminal_soc_equals_initial:
        constraints.append(soc[n] == battery.soc_init)

    # SOC dynamics with charge/discharge efficiencies.
    for t in range(n):
        soc_change = (
            (battery.eta_charge * p_charge[t] - p_discharge[t] / battery.eta_discharge)
            * dt_hours
            / battery.capacity_kwh
        )
        constraints.append(soc[t + 1] == soc[t] + soc_change)

    # SOC bounds.
    constraints.append(soc >= battery.soc_min)
    constraints.append(soc <= battery.soc_max)

    # Power limits.
    constraints.append(p_charge <= battery.max_charge_kw)
    constraints.append(p_discharge <= battery.max_discharge_kw)

    # Grid import/export are mutually exclusive (single meter, no loss term
    # between them) and bounded by the grid connection limit (big-M).
    constraints.append(p_import <= max_grid_kw * grid_import_on)
    constraints.append(p_export <= max_grid_kw * (1 - grid_import_on))

    # Energy balance at every timestep:
    #   PV + import + discharge = demand + export + charge
    constraints.append(
        pv_kw + p_import + p_discharge == demand_kw + p_export + p_charge
    )

    # Objective: import cost - export revenue + degradation penalty.
    import_cost = cp.sum(cp.multiply(import_price, p_import)) * dt_hours
    export_revenue = cp.sum(cp.multiply(export_price, p_export)) * dt_hours
    degradation_cost = (
        battery.degradation_cost_per_kwh
        * cp.sum(p_charge + p_discharge)
        * dt_hours
    )
    objective = cp.Minimize(import_cost - export_revenue + degradation_cost)

    problem = cp.Problem(objective, constraints)
    problem.solve(solver=solver)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        return DispatchResult(
            status=problem.status,
            objective=float("nan"),
            schedule=pd.DataFrame(),
            import_cost=float("nan"),
            export_revenue=float("nan"),
            degradation_cost=float("nan"),
            net_cost=float("nan"),
        )

    schedule = pd.DataFrame(
        {
            "pv_kw": pv_kw,
            "demand_kw": demand_kw,
            "import_price": import_price,
            "export_price": export_price,
            "p_charge_kw": p_charge.value,
            "p_discharge_kw": p_discharge.value,
            "p_import_kw": p_import.value,
            "p_export_kw": p_export.value,
            "soc": soc.value[:-1],
            "soc_end": soc.value[1:],
        }
    )

    import_cost_val = float(import_cost.value)
    export_revenue_val = float(export_revenue.value)
    degradation_cost_val = float(degradation_cost.value)

    return DispatchResult(
        status=problem.status,
        objective=float(problem.value),
        schedule=schedule,
        import_cost=import_cost_val,
        export_revenue=export_revenue_val,
        degradation_cost=degradation_cost_val,
        net_cost=import_cost_val - export_revenue_val,
    )
