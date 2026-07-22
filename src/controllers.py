"""Rules-based battery controllers for the PV-BESS sweep (Spec 05).

Two heuristic controllers, both half-hourly simulations with SOC continuity
across the year (rolling horizon), respecting power limits, round-trip
efficiency, and the SOC window. No optimisation is performed — these are the
real-world strategies the MILP is benchmarked against.

``self_consumption``
    Charge from PV surplus, discharge to meet demand deficit, never grid-charge
    (upper/lower net-demand thresholds both 0 kW). Maximises PV self-consumption.

``self_consumption_tou``
    ``self_consumption`` plus a seasonal off-peak grid-charging rule: during the
    tariff's low-price window the battery is grid-charged toward a season-
    dependent target SOC (summer 0 %, spring 23 %, autumn 45 %, winter 70 % of
    usable capacity), compensating for lower winter PV. On a flat tariff there
    is no low-price window, so the ToU rule is inert and the controller reduces
    to ``self_consumption``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.battery import BatteryParams
from src.tariffs import TariffRates

DT_HOURS = 0.5

# Meteorological season -> target fraction of *usable* capacity for the ToU
# overnight grid-charge. Summer needs no top-up (ample PV); winter needs most.
_SEASON_FRACTION = {"winter": 0.70, "spring": 0.23, "summer": 0.00, "autumn": 0.45}

# Number of cheapest half-hours per day treated as the Agile "off-peak" window
# (7 hours, mirroring the Economy 7 night window length).
_AGILE_OFFPEAK_SLOTS = 14


def _season_of(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def _schedule_frame(data: pd.DataFrame, arrays: dict[str, np.ndarray]) -> pd.DataFrame:
    """Assemble a schedule DataFrame matching the MILP schedule columns."""
    return pd.DataFrame(
        {
            "pv_kw": data["pv_kw"].to_numpy(),
            "demand_kw": data["demand_kw"].to_numpy(),
            "import_price": data["import_price"].to_numpy(),
            "export_price": data["export_price"].to_numpy(),
            "p_charge_kw": arrays["p_charge"],
            "p_discharge_kw": arrays["p_discharge"],
            "p_import_kw": arrays["p_import"],
            "p_export_kw": arrays["p_export"],
            "soc": arrays["soc"],
            "soc_end": arrays["soc_end"],
        },
        index=data.index,
    )


def self_consumption(data: pd.DataFrame, battery: BatteryParams) -> pd.DataFrame:
    """Simulate the PV self-consumption controller (no grid charging).

    At each half-hour: PV surplus charges the battery (export the remainder);
    demand deficit discharges the battery (import the remainder). The battery is
    never charged from the grid.
    """
    pv = data["pv_kw"].to_numpy(dtype=float)
    dem = data["demand_kw"].to_numpy(dtype=float)
    n = len(pv)

    cap = battery.capacity_kwh
    eta_c = battery.eta_charge
    eta_d = battery.eta_discharge

    p_charge = np.zeros(n)
    p_discharge = np.zeros(n)
    p_import = np.zeros(n)
    p_export = np.zeros(n)
    soc_start = np.zeros(n)
    soc_endarr = np.zeros(n)

    soc = battery.soc_init
    for i in range(n):
        soc_start[i] = soc
        p_c_avail = min(
            battery.max_charge_kw,
            max(0.0, (battery.soc_max - soc) * cap / (eta_c * DT_HOURS)),
        )
        p_d_avail = min(
            battery.max_discharge_kw,
            max(0.0, (soc - battery.soc_min) * cap * eta_d / DT_HOURS),
        )
        net_load = dem[i] - pv[i]
        p_c = p_d = p_imp = p_exp = 0.0
        if net_load <= 0.0:
            surplus = -net_load
            p_c = min(p_c_avail, surplus)
            p_exp = surplus - p_c
        else:
            p_d = min(p_d_avail, net_load)
            p_imp = net_load - p_d

        soc = float(
            np.clip(
                soc + (eta_c * p_c - p_d / eta_d) * DT_HOURS / cap,
                battery.soc_min,
                battery.soc_max,
            )
        )
        p_charge[i] = p_c
        p_discharge[i] = p_d
        p_import[i] = p_imp
        p_export[i] = p_exp
        soc_endarr[i] = soc

    return _schedule_frame(
        data,
        {
            "p_charge": p_charge,
            "p_discharge": p_discharge,
            "p_import": p_import,
            "p_export": p_export,
            "soc": soc_start,
            "soc_end": soc_endarr,
        },
    )


def _low_price_mask(
    data: pd.DataFrame, tariff_name: str, rates: TariffRates
) -> np.ndarray:
    """Boolean mask of the tariff's low-price (grid-charge-eligible) half-hours.

    E7 -> the night window ``[offpeak_start, offpeak_end)``; Agile -> the
    cheapest ``_AGILE_OFFPEAK_SLOTS`` half-hours of each day; flat -> nowhere.
    """
    index = data.index
    n = len(index)
    if tariff_name == "flat":
        return np.zeros(n, dtype=bool)

    if tariff_name == "e7":
        tod = index.hour + index.minute / 60.0
        start, end = rates.e7_offpeak_start_h, rates.e7_offpeak_end_h
        if start <= end:
            return np.asarray((tod >= start) & (tod < end))
        return np.asarray((tod >= start) | (tod < end))

    if tariff_name == "agile":
        # The cheapest N half-hours within each calendar day.
        prices = data["import_price"].to_numpy(dtype=float)
        mask = np.zeros(n, dtype=bool)
        day = index.normalize()
        for _, idx in pd.Series(np.arange(n), index=day).groupby(level=0):
            positions = idx.to_numpy()
            if positions.size <= _AGILE_OFFPEAK_SLOTS:
                mask[positions] = True
            else:
                cheapest = positions[
                    np.argsort(prices[positions], kind="stable")[:_AGILE_OFFPEAK_SLOTS]
                ]
                mask[cheapest] = True
        return mask

    raise ValueError(f"Unknown tariff_name {tariff_name!r}.")


def self_consumption_tou(
    data: pd.DataFrame,
    battery: BatteryParams,
    tariff_name: str,
    rates: TariffRates,
) -> pd.DataFrame:
    """Simulate self-consumption plus seasonal off-peak grid-charging.

    During the tariff's low-price window (see :func:`_low_price_mask`) the
    battery is grid-charged toward a season-dependent target SOC; outside the
    window it behaves exactly like :func:`self_consumption`. On a flat tariff the
    low-price window is empty, so this reduces to :func:`self_consumption`.
    """
    pv = data["pv_kw"].to_numpy(dtype=float)
    dem = data["demand_kw"].to_numpy(dtype=float)
    months = data.index.month.to_numpy()
    low_mask = _low_price_mask(data, tariff_name, rates)
    n = len(pv)

    cap = battery.capacity_kwh
    eta_c = battery.eta_charge
    eta_d = battery.eta_discharge
    window = battery.soc_max - battery.soc_min

    p_charge = np.zeros(n)
    p_discharge = np.zeros(n)
    p_import = np.zeros(n)
    p_export = np.zeros(n)
    soc_start = np.zeros(n)
    soc_endarr = np.zeros(n)

    soc = battery.soc_init
    for i in range(n):
        soc_start[i] = soc
        p_c_avail = min(
            battery.max_charge_kw,
            max(0.0, (battery.soc_max - soc) * cap / (eta_c * DT_HOURS)),
        )
        p_d_avail = min(
            battery.max_discharge_kw,
            max(0.0, (soc - battery.soc_min) * cap * eta_d / DT_HOURS),
        )
        net_load = dem[i] - pv[i]
        target_soc = battery.soc_min + _SEASON_FRACTION[_season_of(months[i])] * window
        p_c = p_d = p_imp = p_exp = 0.0

        if low_mask[i] and soc < target_soc:
            # Off-peak grid-charge toward the seasonal target (cap the charge so
            # SOC does not overshoot the target this step). PV offsets the grid
            # draw; the grid supplies the demand deficit plus the charging.
            charge_to_target = max(0.0, (target_soc - soc) * cap / (eta_c * DT_HOURS))
            p_c = min(p_c_avail, charge_to_target)
            net_grid = net_load + p_c
            if net_grid >= 0.0:
                p_imp = net_grid
            else:
                p_exp = -net_grid
        elif net_load <= 0.0:
            surplus = -net_load
            p_c = min(p_c_avail, surplus)
            p_exp = surplus - p_c
        else:
            p_d = min(p_d_avail, net_load)
            p_imp = net_load - p_d

        soc = float(
            np.clip(
                soc + (eta_c * p_c - p_d / eta_d) * DT_HOURS / cap,
                battery.soc_min,
                battery.soc_max,
            )
        )
        p_charge[i] = p_c
        p_discharge[i] = p_d
        p_import[i] = p_imp
        p_export[i] = p_exp
        soc_endarr[i] = soc

    return _schedule_frame(
        data,
        {
            "p_charge": p_charge,
            "p_discharge": p_discharge,
            "p_import": p_import,
            "p_export": p_export,
            "soc": soc_start,
            "soc_end": soc_endarr,
        },
    )
