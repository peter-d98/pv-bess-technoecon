"""Synthetic input data for the stage 1 PV-BESS optimisation model.

This module produces simple, analytically predictable 24-hour hourly profiles
for PV generation, household demand, and electricity tariffs. The shapes are
deliberately stylised so the optimiser's decisions can be checked by hand
before real data is introduced in later stages.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_pv_profile(
    n_hours: int = 24,
    peak_kw: float = 2.0,
    sunrise: int = 6,
    sunset: int = 18,
) -> np.ndarray:
    """Return a half-sinusoid PV generation profile (kW).

    Generation is zero outside daylight hours and follows a half sine wave
    peaking at solar noon (midpoint between sunrise and sunset).

    Parameters
    ----------
    n_hours:
        Number of hourly timesteps.
    peak_kw:
        Peak PV output (kW) at solar noon.
    sunrise, sunset:
        Hours (0-23) bounding the daylight window.
    """
    hours = np.arange(n_hours)
    pv = np.zeros(n_hours)
    daylight = (hours >= sunrise) & (hours < sunset)
    # Map the daylight window onto [0, pi] so the sine peaks at the midpoint.
    phase = (hours[daylight] - sunrise) / (sunset - sunrise) * np.pi
    pv[daylight] = peak_kw * np.sin(phase)
    return pv


def make_demand_profile(
    n_hours: int = 24,
    baseline_kw: float = 0.5,
    morning_peak_kw: float = 1.5,
    evening_peak_kw: float = 1.5,
    morning_hours: tuple[int, int] = (7, 9),
    evening_hours: tuple[int, int] = (17, 21),
) -> np.ndarray:
    """Return a stylised household demand profile (kW).

    A flat baseline load with two raised periods representing the morning and
    evening demand peaks.
    """
    demand = np.full(n_hours, baseline_kw, dtype=float)
    hours = np.arange(n_hours)
    morning = (hours >= morning_hours[0]) & (hours < morning_hours[1])
    evening = (hours >= evening_hours[0]) & (hours < evening_hours[1])
    demand[morning] = morning_peak_kw
    demand[evening] = evening_peak_kw
    return demand


def make_price_profile(
    n_hours: int = 24,
    cheap_price: float = 0.07,
    peak_price: float = 0.28,
    cheap_start: int = 23,
    cheap_end: int = 7,
) -> np.ndarray:
    """Return a two-tier import tariff profile (GBP/kWh).

    A cheap overnight rate applies during the off-peak window (wrapping past
    midnight) and an expensive rate applies the rest of the day.
    """
    hours = np.arange(n_hours)
    if cheap_start > cheap_end:
        # Off-peak window wraps around midnight.
        cheap = (hours >= cheap_start) | (hours < cheap_end)
    else:
        cheap = (hours >= cheap_start) & (hours < cheap_end)
    price = np.where(cheap, cheap_price, peak_price)
    return price.astype(float)


def make_export_profile(
    n_hours: int = 24,
    export_price: float = 0.15,
) -> np.ndarray:
    """Return a flat Smart Export Guarantee (SEG) export price profile (GBP/kWh)."""
    return np.full(n_hours, export_price, dtype=float)


def make_stage1_dataset(n_hours: int = 24, dt_hours: float = 1.0) -> pd.DataFrame:
    """Assemble the full synthetic dataset as a tidy DataFrame.

    Columns: ``hour``, ``pv_kw``, ``demand_kw``, ``import_price``,
    ``export_price``. The index is the hour of day.
    """
    df = pd.DataFrame(
        {
            "hour": np.arange(n_hours),
            "pv_kw": make_pv_profile(n_hours),
            "demand_kw": make_demand_profile(n_hours),
            "import_price": make_price_profile(n_hours),
            "export_price": make_export_profile(n_hours),
        }
    )
    df.attrs["dt_hours"] = dt_hours
    return df
