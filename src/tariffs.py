"""Tariff layer for the PV-BESS dispatch model (Spec 03).

Constructs the two half-hourly price series the dispatch MILP consumes —
``import_price`` and ``export_price`` (GBP/kWh) — under a named set of real
tariffs, so that **tariff structure** is a controlled axis of the parameter
study. The MILP, runner, NPV, and degradation models are unchanged: they still
see only the two price arrays; this module only changes *how they are built*.

Rates default to real **Southern Scotland (SP Distribution), 2025,
VAT-inclusive** values (Glasgow's DNO region). ``TariffRates`` is
region-parameterised so Spec 04 can add regions without touching the
construction logic.

Standing charges are carried on ``TariffRates`` as **metadata only** — they are
identical with and without the PV-BESS on a given tariff, so they cancel in the
saving and never enter the NPV.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_loader import load_agile_prices


@dataclass(frozen=True)
class TariffRates:
    """Real tariff rates for one DNO region/year (Southern Scotland 2023 defaults).

    All prices are GBP/kWh, VAT-inclusive, unless stated. Region-parameterised:
    Spec 04 supplies a different instance per region.
    """

    region: str = "southern_scotland"
    year: int = 2025

    flat_rate: float = 0.2642
    e7_day: float = 0.3155
    e7_night: float = 0.1330
    e7_offpeak_start_h: float = 0.0     # inclusive hour-of-day
    e7_offpeak_end_h: float = 7.0       # exclusive hour-of-day

    seg_national: float = 0.13          # headline flat SEG
    seg_untied: float = 0.045           # untied-floor sensitivity

    # Metadata only — NOT used in the NPV (standing charges cancel on a fixed tariff).
    flat_standing_charge_per_day: float = 0.642
    e7_standing_charge_per_day: float = 0.647


def flat_import(index: pd.DatetimeIndex, rate: float) -> pd.Series:
    """Constant single-rate import price series (GBP/kWh)."""
    return pd.Series(np.full(len(index), float(rate)), index=index, name="import_price")


def economy7_import(
    index: pd.DatetimeIndex,
    day: float,
    night: float,
    offpeak_start_h: float,
    offpeak_end_h: float,
) -> pd.Series:
    """Two-rate Economy 7 import series keyed on local time-of-day.

    The off-peak (night) rate applies on the half-open time-of-day interval
    ``[offpeak_start_h, offpeak_end_h)``. A wrapping window (``start > end``,
    e.g. 23:30–06:30) is handled by unioning the two arcs.
    """
    tod_h = index.hour + index.minute / 60.0
    if offpeak_start_h <= offpeak_end_h:
        night_mask = (tod_h >= offpeak_start_h) & (tod_h < offpeak_end_h)
    else:  # wrapping window
        night_mask = (tod_h >= offpeak_start_h) | (tod_h < offpeak_end_h)
    prices = np.where(night_mask, float(night), float(day))
    return pd.Series(prices, index=index, name="import_price")


def flat_export(index: pd.DatetimeIndex, seg_rate: float) -> pd.Series:
    """Constant flat SEG export price series (GBP/kWh)."""
    return pd.Series(np.full(len(index), float(seg_rate)), index=index, name="export_price")


def no_export(index: pd.DatetimeIndex) -> pd.Series:
    """Zero export price series (self-consumption only)."""
    return pd.Series(np.zeros(len(index)), index=index, name="export_price")


def agile_import_export(
    agile_path: str | Path, index: pd.DatetimeIndex
) -> tuple[pd.Series, pd.Series]:
    """Agile import and export series (GBP/kWh) from the CSV, via ``load_agile_prices``."""
    prices = load_agile_prices(agile_path, index)
    return (
        prices["import_price"].rename("import_price"),
        prices["export_price"].rename("export_price"),
    )


_IMPORT_TARIFFS = ("flat", "e7", "agile")
_EXPORT_CHOICES = ("matched", "seg", "agile", "none")


def build_tariff(
    name: str,
    index: pd.DatetimeIndex,
    rates: TariffRates,
    agile_path: str | Path | None = None,
    export: str = "matched",
    seg_rate: float | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Build the ``(import_price, export_price)`` pair for a named tariff.

    Parameters
    ----------
    name:
        Import tariff, one of ``{"flat", "e7", "agile"}``.
    index:
        Canonical half-hourly index the series are aligned to.
    rates:
        Real per-region rates.
    agile_path:
        Path to the Agile CSV; required when the import or export uses Agile.
    export:
        Export choice, one of ``{"matched", "seg", "agile", "none"}``. ``"matched"``
        resolves to the realistic export for the import tariff: flat/E7 → flat SEG,
        Agile → Agile export.
    seg_rate:
        Overrides the flat SEG rate; defaults to ``rates.seg_national``.

    Returns
    -------
    (import_price, export_price):
        Two ``pd.Series`` in GBP/kWh indexed by ``index``.
    """
    if name not in _IMPORT_TARIFFS:
        raise ValueError(f"Unknown tariff '{name}'; expected one of {_IMPORT_TARIFFS}.")
    if export not in _EXPORT_CHOICES:
        raise ValueError(f"Unknown export '{export}'; expected one of {_EXPORT_CHOICES}.")

    seg = rates.seg_national if seg_rate is None else float(seg_rate)

    # Import series (and the Agile export, cached if loaded).
    agile_export_series: pd.Series | None = None
    if name == "flat":
        import_price = flat_import(index, rates.flat_rate)
    elif name == "e7":
        import_price = economy7_import(
            index, rates.e7_day, rates.e7_night,
            rates.e7_offpeak_start_h, rates.e7_offpeak_end_h,
        )
    else:  # agile
        if agile_path is None:
            raise ValueError("agile_path is required for the Agile import tariff.")
        import_price, agile_export_series = agile_import_export(agile_path, index)

    # Resolve the export choice.
    resolved = export
    if export == "matched":
        resolved = "agile" if name == "agile" else "seg"

    if resolved == "none":
        export_price = no_export(index)
    elif resolved == "seg":
        export_price = flat_export(index, seg)
    else:  # agile export
        if agile_export_series is not None:
            export_price = agile_export_series
        else:
            if agile_path is None:
                raise ValueError("agile_path is required for the Agile export.")
            _, export_price = agile_import_export(agile_path, index)

    return import_price, export_price
