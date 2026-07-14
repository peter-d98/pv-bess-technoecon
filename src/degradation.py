"""Battery degradation and capacity-fade model for the PV-BESS system.

Implements the two-timescale degradation design (Spec 02):

* **Fast timescale (inside the MILP):** a linear throughput penalty whose value
  is *derived* from the battery capex spread over its lifetime throughput,
  rather than assumed. See :func:`derive_throughput_penalty`.
* **Slow timescale (across the rolling horizon):** capacity fade applied
  *exogenously* year to year as the additive superposition of a cycle term
  (linear in equivalent full cycles) and a calendar term (linear baseline, with
  an optional square-root sensitivity). See :func:`capacity_fade` and
  :func:`simulate_capacity_fade`. The declining usable capacity feeds a per-year
  saving stream into the NPV model (:func:`src.economics.compute_npv`).

Two conventions are fixed and shared with the existing code:

* **EFC basis = nominal capacity.** One equivalent full cycle is
  ``2 * capacity_kwh`` of summed charge+discharge throughput, matching the
  ``throughput / (2 * capacity_kwh)`` reporting used elsewhere. ``capacity_kwh``
  is the nominal nameplate; the usable energy is ``capacity_kwh * (soc_max -
  soc_min)`` via the SOC window.
* **Power rating is held constant** as the battery ages; only *energy* capacity
  fades.

All parameter defaults are proposed values requiring a literature citation in
the dissertation (see ``.github/specs/spec-02-degradation-capacity-fade.md``
§10). The descriptive SOC-exposure metrics (:func:`soc_exposure`) require no
literature coefficients; only the optional SOC-dependent calendar sensitivity
does, and it is inert (``soc_stress_beta = 0``) by default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DegradationParams:
    """Battery-ageing assumptions driving both the penalty and the fade.

    Attributes
    ----------
    soh_eol:
        State of health (fraction of nominal capacity) at which the battery is
        replaced.
    cycle_life_efc:
        Equivalent full cycles to reach ``soh_eol`` (cycle ageing alone).
    calendar_life_years:
        Calendar years to reach ``soh_eol`` at the reference temperature
        (calendar ageing alone).
    calendar_form:
        ``"linear"`` (baseline) or ``"sqrt"`` (diffusion-limited sensitivity).
    soc_dependent_calendar:
        If True, scale the calendar term by a time-weighted SOC stress
        multiplier (Level B sensitivity). Off by default.
    soc_ref:
        Reference SOC for the stress multiplier.
    soc_stress_beta:
        SOC stress coefficient. ``0`` reproduces the SOC-independent baseline
        exactly. A positive value ages the cell faster at high SOC. The value is
        a citation dependency (Naumann et al.) and defaults to 0.
    """

    soh_eol: float = 0.80
    cycle_life_efc: float = 6000.0
    calendar_life_years: float = 13.5
    calendar_form: str = "linear"
    soc_dependent_calendar: bool = False
    soc_ref: float = 0.50
    soc_stress_beta: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 < self.soh_eol < 1.0:
            raise ValueError("soh_eol must be in (0, 1).")
        if self.cycle_life_efc <= 0:
            raise ValueError("cycle_life_efc must be positive.")
        if self.calendar_life_years <= 0:
            raise ValueError("calendar_life_years must be positive.")
        if self.calendar_form not in ("linear", "sqrt"):
            raise ValueError("calendar_form must be 'linear' or 'sqrt'.")
        if not 0.0 <= self.soc_ref <= 1.0:
            raise ValueError("soc_ref must be in [0, 1].")


@dataclass
class FadeResult:
    """Output of a lifetime capacity-fade simulation."""

    saving_stream: list[float]
    soh_trajectory: list[float]
    effective_life_years: float
    replacement_years: list[int]
    efc_per_year: list[float]


def derive_throughput_penalty(
    battery_capex: float, capacity_kwh: float, params: DegradationParams
) -> float:
    """Throughput degradation penalty (GBP per kWh charged+discharged).

    Spreads the battery capex over the summed charge+discharge throughput the
    battery can deliver before end of life, on the nominal (EFC) basis so it is
    consistent with the fade accounting::

        lifetime_throughput = cycle_life_efc * 2 * capacity_kwh
        c_thr = battery_capex / lifetime_throughput
    """
    if battery_capex < 0:
        raise ValueError("battery_capex must be non-negative.")
    if capacity_kwh <= 0:
        raise ValueError("capacity_kwh must be positive.")
    lifetime_throughput_kwh = params.cycle_life_efc * 2.0 * capacity_kwh
    return battery_capex / lifetime_throughput_kwh


def equivalent_full_cycles(throughput_kwh: float, capacity_kwh: float) -> float:
    """Equivalent full cycles from summed charge+discharge throughput.

    One EFC = ``2 * capacity_kwh`` of throughput (a full charge and discharge).
    """
    if capacity_kwh <= 0:
        raise ValueError("capacity_kwh must be positive.")
    return throughput_kwh / (2.0 * capacity_kwh)


def capacity_fade(
    efc_cumulative: float,
    years_elapsed: float,
    params: DegradationParams,
    soc_stress_multiplier: float = 1.0,
) -> float:
    """State of health after cycle + calendar ageing (additive superposition).

    ``SOH = 1 - phi_cyc - phi_cal`` where ``phi_cyc`` is linear in cumulative
    EFC and ``phi_cal`` is linear (or square-root) in elapsed years, optionally
    scaled by an SOC stress multiplier. Both terms consume the same
    ``(1 - soh_eol)`` budget, so combined ageing reaches end of life sooner than
    either term alone.
    """
    loss_budget = 1.0 - params.soh_eol
    phi_cyc = loss_budget * efc_cumulative / params.cycle_life_efc

    ratio = years_elapsed / params.calendar_life_years
    if params.calendar_form == "sqrt":
        cal = math.sqrt(ratio) if ratio > 0 else 0.0
    else:
        cal = ratio
    phi_cal = loss_budget * cal * soc_stress_multiplier

    return 1.0 - phi_cyc - phi_cal


def soc_stress_multiplier(
    soc_series: Sequence[float], params: DegradationParams
) -> float:
    """Time-weighted SOC stress multiplier for the calendar term (Level B).

    Returns 1.0 when the SOC-dependent calendar sensitivity is disabled or when
    ``soc_stress_beta == 0``, so the baseline is reproduced exactly.
    """
    if not params.soc_dependent_calendar:
        return 1.0
    arr = np.asarray(soc_series, dtype=float)
    return float(np.mean(np.exp(params.soc_stress_beta * (arr - params.soc_ref))))


def time_to_eol(
    efc_per_year: float, params: DegradationParams, max_years: int = 200
) -> float:
    """Years for a fresh battery to reach ``soh_eol`` at a fixed annual EFC rate.

    Samples the (possibly non-linear) fade at integer years and linearly
    interpolates the crossing. Used to size the effective battery life fed to
    the NPV replacement schedule.
    """
    soh_prev = 1.0
    for t in range(1, max_years + 1):
        soh = capacity_fade(efc_per_year * t, float(t), params)
        if soh <= params.soh_eol:
            if soh_prev == soh:
                return float(t)
            frac = (soh_prev - params.soh_eol) / (soh_prev - soh)
            return (t - 1) + frac
        soh_prev = soh
    return float(max_years)


def simulate_capacity_fade(
    dispatch_year: Callable[[float, float], tuple[float, float, Sequence[float]]],
    capacity_kwh_nominal: float,
    soc_max: float,
    horizon_years: int,
    params: DegradationParams,
) -> FadeResult:
    """Simulate capacity fade across the horizon, re-dispatching each year.

    Parameters
    ----------
    dispatch_year:
        Callable ``(capacity_kwh, soc_max) -> (annual_saving, throughput_kwh,
        soc_series)`` supplied by the caller (the runner injects the real
        annual solve; tests inject a stub). Keeping the solver out of this
        module makes the fade logic unit-testable without CVXPY.
    capacity_kwh_nominal:
        Nameplate capacity of a fresh battery.
    soc_max:
        Upper SOC bound passed straight through to ``dispatch_year`` (used by
        the upper-SOC-bound tradeoff).
    horizon_years:
        Number of years to simulate.
    params:
        Degradation assumptions.

    Returns
    -------
    FadeResult
        The per-year saving stream, start-of-year SOH trajectory, effective
        battery life (from the first year's usage), the years in which a fresh
        battery starts operating, and the per-year EFC.
    """
    if horizon_years < 1:
        raise ValueError("horizon_years must be a positive integer.")

    efc_gen = 0.0            # EFC accumulated by the current battery generation
    years_gen = 0           # completed years of the current generation
    multipliers: list[float] = []

    saving_stream: list[float] = []
    soh_trajectory: list[float] = []
    efc_per_year: list[float] = []
    replacement_years: list[int] = []

    for t in range(1, horizon_years + 1):
        cal_mult = (
            float(np.mean(multipliers))
            if params.soc_dependent_calendar and multipliers
            else 1.0
        )
        soh = capacity_fade(efc_gen, float(years_gen), params, cal_mult)

        if soh <= params.soh_eol:
            # Replace: a fresh battery starts operating this year.
            replacement_years.append(t)
            efc_gen = 0.0
            years_gen = 0
            multipliers = []
            soh = 1.0

        soh_trajectory.append(soh)
        capacity_year = capacity_kwh_nominal * soh
        saving, throughput_kwh, soc_series = dispatch_year(capacity_year, soc_max)

        efc_year = equivalent_full_cycles(throughput_kwh, capacity_kwh_nominal)
        saving_stream.append(float(saving))
        efc_per_year.append(efc_year)

        efc_gen += efc_year
        years_gen += 1
        if params.soc_dependent_calendar:
            multipliers.append(soc_stress_multiplier(soc_series, params))

    effective_life = (
        time_to_eol(efc_per_year[0], params) if efc_per_year else float(horizon_years)
    )

    return FadeResult(
        saving_stream=saving_stream,
        soh_trajectory=soh_trajectory,
        effective_life_years=effective_life,
        replacement_years=replacement_years,
        efc_per_year=efc_per_year,
    )


def soc_exposure(
    soc_series: Sequence[float],
    dt_hours: float,
    thresholds: Sequence[float] = (0.8,),
) -> dict:
    """Descriptive SOC-exposure metrics from a solved trajectory (Level A).

    Requires no literature coefficients. Returns the mean SOC, the time-weighted
    mean SOC, the fraction of time spent above each threshold, and a dwell
    histogram (hours per 10 % SOC bin).
    """
    arr = np.asarray(soc_series, dtype=float)
    if arr.size == 0:
        raise ValueError("soc_series must be non-empty.")
    total_hours = arr.size * dt_hours

    mean_soc = float(arr.mean())
    time_weighted_mean_soc = float((arr * dt_hours).sum() / total_hours)
    frac_time_above = {
        float(thr): float(np.count_nonzero(arr > thr) / arr.size) for thr in thresholds
    }

    edges = np.linspace(0.0, 1.0, 11)
    counts, _ = np.histogram(arr, bins=edges)
    histogram = pd.DataFrame(
        {
            "soc_low": edges[:-1],
            "soc_high": edges[1:],
            "hours": counts * dt_hours,
        }
    )

    return {
        "mean_soc": mean_soc,
        "time_weighted_mean_soc": time_weighted_mean_soc,
        "frac_time_above": frac_time_above,
        "histogram": histogram,
    }
