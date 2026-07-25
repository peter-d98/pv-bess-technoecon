"""Lifetime net-present-value (NPV) valuation for the PV-BESS system.

Converts the annual operating economics produced by the dispatch model (an
annual saving versus the no-battery counterfactual) into a discounted
multi-year cash flow, and reports NPV alongside supporting metrics.

All cash flows are expressed in **real** terms (today's GBP); the discount rate
and the electricity price escalation are therefore also **real**. Mixing a
nominal discount rate with real cash flows is a common and material error, so
the convention is stated explicitly here and enforced by keeping a single
consistent frame throughout.

Conventions (see ``.github/specs/spec-01-verification.md``):

* The initial battery capex is incurred at ``t = 0`` (undiscounted).
* The annual benefit accrues at the **end** of each year ``t = 1 .. N`` and is
  discounted by ``(1 + r) ** -t``.
* The base saving in year ``t`` is escalated by ``(1 + esc) ** (t - 1)`` so that
  year 1 equals the supplied base saving.
* Battery replacements (possibly several) are outflows at every
    ``t_r = k * ceil(life) < N`` for ``k = 1, 2, ...``, each discounted to present
    value. Escalation does not apply to capex.
* If enabled, a straight-line residual value credits the unconsumed life of the
    battery still in service at the horizon, as an inflow at ``t = N``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EconomicParams:
    """Economic assumptions for the lifetime NPV valuation.

    All rates are real. Monetary values are in GBP.
    """

    battery_capex: float
    discount_rate: float = 0.05
    horizon_years: int = 20
    price_escalation: float = 0.02
    battery_life_years: float = 12.0
    replacement_cost_factor: float = 1.0
    om_cost_per_year: float = 0.0
    include_residual_value: bool = True

    def __post_init__(self) -> None:
        if self.horizon_years < 1:
            raise ValueError("horizon_years must be a positive integer.")
        if self.discount_rate <= -1.0:
            raise ValueError("discount_rate must be greater than -1.")
        if self.price_escalation <= -1.0:
            raise ValueError("price_escalation must be greater than -1.")
        if self.battery_life_years <= 0:
            raise ValueError("battery_life_years must be positive.")
        if self.battery_capex < 0:
            raise ValueError("battery_capex must be non-negative.")
        if self.replacement_cost_factor < 0:
            raise ValueError("replacement_cost_factor must be non-negative.")


@dataclass
class NPVResult:
    """Result of a lifetime NPV valuation."""

    npv: float
    bcr: float
    discounted_payback_years: float
    pv_benefits: float
    pv_costs: float
    cashflows: pd.DataFrame


def compute_npv(
    annual_saving: float | Sequence[float],
    econ: EconomicParams,
    terminal_residual_value: float | None = None,
) -> NPVResult:
    """Compute the lifetime NPV of the battery investment.

    Parameters
    ----------
    annual_saving:
        The real annual operating saving versus the no-battery counterfactual,
        *including* the degradation cost on the battery side. Either a single
        float (held constant across the horizon) or a sequence of one value per
        horizon year (used by the capacity-fade model to let the saving decline
        as the battery ages). Price escalation is applied on top in both cases.
    econ:
        Economic assumptions.
    terminal_residual_value:
        Optional explicit residual value (real GBP, undiscounted) credited as an
        inflow in the final horizon year. When supplied it overrides the default
        straight-line residual calculated from ``battery_life_years``. Must be
        non-negative.
    Returns
    -------
    NPVResult
        NPV, benefit-cost ratio, discounted payback, the present value of
        benefits and of costs, and the full annual cash-flow table.
    """
    if terminal_residual_value is not None and terminal_residual_value < 0:
        raise ValueError("terminal_residual_value must be non-negative.")
    n = econ.horizon_years
    r = econ.discount_rate
    esc = econ.price_escalation

    # Base saving per year (before escalation), length n.
    if np.isscalar(annual_saving):
        base_saving = np.full(n, float(annual_saving))
    else:
        base_saving = np.asarray(annual_saving, dtype=float)
        if len(base_saving) != n:
            raise ValueError(
                f"annual_saving sequence length ({len(base_saving)}) must equal "
                f"horizon_years ({n})."
            )

    years = np.arange(1, n + 1)
    discount_factors = (1.0 + r) ** (-years)

    # Operating benefit each year: escalated saving net of fixed O&M.
    escalation = (1.0 + esc) ** (years - 1)
    operating_benefit = base_saving * escalation - econ.om_cost_per_year

    # Replacement outflows at every k * ceil(life) strictly inside the horizon.
    life = math.ceil(econ.battery_life_years)
    replacement_cost = econ.battery_capex * econ.replacement_cost_factor
    replacement_outflow = np.zeros(n)
    if replacement_cost > 0:
        k = 1
        while k * life < n:
            replacement_outflow[k * life - 1] = replacement_cost
            k += 1

    residual_inflow = np.zeros(n)
    if terminal_residual_value is not None:
        residual_inflow[n - 1] = float(terminal_residual_value)
    elif econ.include_residual_value and replacement_cost >= 0:
        m = math.ceil(n / life) - 1
        t_install = m * life
        remaining = (t_install + life) - n
        if remaining > 0:
            residual_inflow[n - 1] = replacement_cost * remaining / life

    benefit = operating_benefit + residual_inflow
    capex_flow = replacement_outflow
    net_undiscounted = benefit - capex_flow
    discounted_annual = net_undiscounted * discount_factors

    # Assemble cash-flow table including the t = 0 initial outlay.
    cashflows = pd.DataFrame(
        {
            "year": np.arange(0, n + 1),
            "benefit": np.concatenate([[0.0], benefit]),
            "capex": np.concatenate([[econ.battery_capex], capex_flow]),
            "discounted": np.concatenate([[-econ.battery_capex], discounted_annual]),
        }
    )

    npv = float(cashflows["discounted"].sum())

    pv_benefits = float((benefit * discount_factors).sum())
    pv_costs = float(
        econ.battery_capex + (capex_flow * discount_factors).sum()
    )
    bcr = pv_benefits / pv_costs if pv_costs != 0 else float("inf")

    # Discounted payback: first year (t >= 1) where cumulative discounted CF >= 0.
    cumulative = cashflows["discounted"].cumsum().to_numpy()
    discounted_payback = float("inf")
    for i in range(1, n + 1):
        if cumulative[i] >= 0:
            discounted_payback = float(cashflows["year"].iloc[i])
            break

    return NPVResult(
        npv=npv,
        bcr=bcr,
        discounted_payback_years=discounted_payback,
        pv_benefits=pv_benefits,
        pv_costs=pv_costs,
        cashflows=cashflows,
    )


def annual_saving_from_costs(
    cf_net_cost: float,
    battery_net_cost: float,
    degradation_cost: float,
) -> float:
    """Year-1 real annual saving versus the no-battery counterfactual.

    The degradation cost is deducted from the battery side because it is a real
    cost to the owner (wear -> earlier replacement), not merely an optimiser
    penalty. Fixed O&M, if any, is applied later inside :func:`compute_npv`.
    """
    return cf_net_cost - (battery_net_cost + degradation_cost)
