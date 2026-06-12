"""Data loaders for the v2 (real-data) PV-BESS dispatch model.

Three real-world data sources are combined into a single half-hourly
(30-minute) profile DataFrame for the annual rolling-horizon optimisation:

1. PV generation  -- PVGIS hourly "PV system output" CSV (SARAH-3, kW after
   conversion), upsampled to 30-minute resolution.
2. Household demand -- CREST-generated half-hourly demand profile (kW).
3. Agile tariff prices -- Octopus Agile import and export prices (p/kWh,
   converted to GBP/kWh).

Because the three sources originate from different calendar years (PV and
prices from 2023, CREST demand synthetically generated for 2025) and use
different time conventions, they are aligned *positionally* onto a single
canonical half-hourly index for a chosen reference year. This is an
acknowledged limitation: the model studies typical co-incident conditions
rather than a single physically simultaneous historical year.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

HALFHOURS_PER_DAY = 48
_PVGIS_TIME_RE = re.compile(r"^\d{8}:\d{4}")


def build_canonical_index(year: int = 2023) -> pd.DatetimeIndex:
    """Return a naive half-hourly DatetimeIndex spanning a full calendar year.

    Parameters
    ----------
    year:
        Reference calendar year. A non-leap year yields 17 520 periods; a leap
        year yields 17 568.
    """
    start = pd.Timestamp(f"{year}-01-01 00:00")
    end = pd.Timestamp(f"{year + 1}-01-01 00:00")
    return pd.date_range(start=start, end=end, freq="30min", inclusive="left")


def load_pv(path: str | Path, target_index: pd.DatetimeIndex) -> pd.Series:
    """Load a PVGIS hourly PV-output CSV and upsample to a 30-minute series.

    The PVGIS "hourly" CSV has a metadata header block, a data section whose
    timestamps are stamped at 11 minutes past each hour (e.g. ``20230101:0011``),
    and a trailing metadata footer. The ``P`` column is PV system power in W.

    The hourly values are floored to the top of the hour, converted to kW, and
    forward-filled across both half-hours of each hour to match *target_index*.

    Parameters
    ----------
    path:
        Path to the PVGIS Timeseries CSV.
    target_index:
        Canonical half-hourly index the result is aligned to (positionally by
        time-of-year).

    Returns
    -------
    pd.Series
        PV generation (kW) indexed by *target_index*, name ``pv_kw``.
    """
    path = Path(path)
    lines = path.read_text().splitlines()

    header_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("time,")), None
    )
    if header_idx is None:
        raise ValueError(f"Could not find a 'time,' header line in {path}")

    data_lines = [
        line for line in lines[header_idx + 1 :] if _PVGIS_TIME_RE.match(line)
    ]
    if not data_lines:
        raise ValueError(f"No PVGIS data rows found in {path}")

    columns = lines[header_idx].split(",")
    raw = pd.DataFrame(
        [line.split(",") for line in data_lines], columns=columns
    )

    timestamps = pd.to_datetime(raw["time"], format="%Y%m%d:%H%M").dt.floor("h")
    pv_kw_hourly = pd.Series(
        raw["P"].astype(float).to_numpy() / 1000.0,  # W -> kW
        index=timestamps,
        name="pv_kw",
    )

    if len(pv_kw_hourly) != len(target_index) // 2:
        raise ValueError(
            f"Expected {len(target_index) // 2} hourly PV rows, "
            f"got {len(pv_kw_hourly)}. Check the PVGIS file year."
        )

    # Upsample positionally: each hourly value fills its two half-hours.
    pv_halfhourly = np.repeat(pv_kw_hourly.to_numpy(), 2)
    return pd.Series(pv_halfhourly, index=target_index, name="pv_kw")


def load_demand(path: str | Path, target_index: pd.DatetimeIndex) -> pd.Series:
    """Load a CREST half-hourly demand CSV and align to *target_index*.

    The CREST profile is aligned positionally (by half-hour-of-year), because
    it was generated for a different calendar year than the PV and price data.

    Returns
    -------
    pd.Series
        Household demand (kW) indexed by *target_index*, name ``demand_kw``.
    """
    path = Path(path)
    demand = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]

    if len(demand) != len(target_index):
        raise ValueError(
            f"Demand profile has {len(demand)} rows but the canonical index "
            f"has {len(target_index)}. Both must cover a year of the same "
            "leap-status (e.g. both non-leap)."
        )

    return pd.Series(demand.to_numpy(dtype=float), index=target_index, name="demand_kw")


def load_agile_prices(
    path: str | Path, target_index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Load an Octopus Agile half-hourly rates CSV (import + export).

    The CSV has columns ``Period from``, ``Period to``, ``Agile Import price
    (p/kWh)`` and ``Agile Export price (p/kWh)``. Prices are converted from
    p/kWh to GBP/kWh. Values are reindexed onto *target_index* by timestamp;
    any periods missing in the source (e.g. the spring daylight-saving
    transition) are forward-filled.

    Returns
    -------
    pd.DataFrame
        Columns ``import_price`` and ``export_price`` in GBP/kWh, indexed by
        *target_index*.
    """
    path = Path(path)
    raw = pd.read_csv(path)

    period_from = pd.to_datetime(raw["Period from"], format="%d/%m/%Y %H:%M")
    prices = pd.DataFrame(
        {
            "import_price": raw["Agile Import price (p/kWh)"].to_numpy(dtype=float) / 100.0,
            "export_price": raw["Agile Export price (p/kWh)"].to_numpy(dtype=float) / 100.0,
        },
        index=pd.DatetimeIndex(period_from),
    ).sort_index()

    # Drop duplicate timestamps (autumn DST repeated hour) keeping the first.
    prices = prices[~prices.index.duplicated(keep="first")]

    # Reindex onto the canonical index; fill DST gaps by carrying forward.
    aligned = prices.reindex(target_index).ffill().bfill()
    aligned.index.name = None
    return aligned


def load_all(
    pv_path: str | Path,
    demand_path: str | Path,
    agile_path: str | Path,
    year: int = 2023,
) -> pd.DataFrame:
    """Load and align all three sources into one half-hourly DataFrame.

    Returns
    -------
    pd.DataFrame
        Columns ``pv_kw``, ``demand_kw``, ``import_price``, ``export_price``
        on a single canonical half-hourly index for *year*.
    """
    index = build_canonical_index(year)
    pv = load_pv(pv_path, index)
    demand = load_demand(demand_path, index)
    prices = load_agile_prices(agile_path, index)

    return pd.concat([pv, demand, prices], axis=1)
