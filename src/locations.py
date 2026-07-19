"""Location registry for the PV-BESS study (Spec 04).

Binds each UK study location to its three location-specific inputs — a PVGIS
PV-generation file, an Octopus Agile price CSV, and a region-specific
``TariffRates`` instance (Spec 03) — so that ``run_stage2`` can be pointed at a
location by name and produce a like-for-like techno-economic result.

The MILP, the rolling-horizon runner, the tariff layer, the fade loop, and the
NPV model are all unchanged: this module only supplies the per-location data
bindings and a thin resolution layer that selects them.

Rates and Agile data are real **2025** values for each location's DNO region
(VAT-inclusive); the PVGIS PV profile is **2023** (aligned positionally); the
CREST demand profile is **shared across all locations** (a stated limitation)
and is therefore *not* part of a ``LocationSpec`` — the runner supplies it.

Standing charges are carried on ``TariffRates`` as **metadata only** — they are
identical with and without the PV-BESS on a given tariff, so they cancel in the
saving and never enter the NPV.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.tariffs import TariffRates


@dataclass(frozen=True)
class LocationSpec:
    """One UK study location: identity, coordinates, data files, and tariff rates.

    All rate values live on ``rates`` (a Spec 03 ``TariffRates`` instance). The
    two file fields are **basenames** resolved against a data directory by
    :func:`resolve_paths`. The demand profile is shared across locations and is
    not stored here.
    """

    name: str            # lowercase registry key, e.g. "glasgow"
    label: str           # human label, e.g. "Glasgow"
    region: str          # DNO region key; matches ``rates.region``
    latitude: float
    longitude: float
    pv_file: str         # PVGIS Timeseries basename in the data directory
    agile_file: str      # Octopus Agile CSV basename in the data directory
    rates: TariffRates   # region-specific 2025 rates (Spec 03 type, unchanged)


# Real 2025 rates (VAT-inclusive) transcribed from data/location_data_summary.md.
# SEG export is the 13.0 p/kWh national-average headline for all four locations;
# the E7 off-peak window is the standard 00:00-07:00 everywhere. Standing charges
# are metadata only (excluded from the NPV).
LOCATIONS: dict[str, LocationSpec] = {
    "inverness": LocationSpec(
        name="inverness",
        label="Inverness",
        region="north_scotland",
        latitude=57.479,
        longitude=-4.222,
        pv_file="Timeseries_57.479_-4.222_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv",
        agile_file="agile-half-hour-actual-rates-01-01-2025_31-12-2025_NScot.csv",
        rates=TariffRates(
            region="north_scotland",
            year=2025,
            flat_rate=0.2642,
            e7_day=0.3260,
            e7_night=0.1375,
            flat_standing_charge_per_day=0.5755,
            e7_standing_charge_per_day=0.5820,
        ),
    ),
    "glasgow": LocationSpec(
        name="glasgow",
        label="Glasgow",
        region="southern_scotland",
        latitude=55.829,
        longitude=-4.276,
        pv_file="Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv",
        agile_file="agile-half-hour-actual-rates-01-01-2025_31-12-2025_SScot.csv",
        # Glasgow's rates equal the TariffRates() default, so selecting Glasgow
        # (or the no-flag default) reproduces the current baseline bit-for-bit.
        rates=TariffRates(),
    ),
    "manchester": LocationSpec(
        name="manchester",
        label="Manchester",
        region="nw_england",
        latitude=53.483,
        longitude=-2.242,
        pv_file="Timeseries_53.483_-2.242_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv",
        agile_file="agile-half-hour-actual-rates-01-01-2025_31-12-2025_NWEng.csv",
        rates=TariffRates(
            region="nw_england",
            year=2025,
            flat_rate=0.2613,
            e7_day=0.3155,
            e7_night=0.1330,
            flat_standing_charge_per_day=0.4761,
            e7_standing_charge_per_day=0.4690,
        ),
    ),
    "plymouth": LocationSpec(
        name="plymouth",
        label="Plymouth",
        region="sw_england",
        latitude=50.373,
        longitude=-4.143,
        pv_file="Timeseries_50.373_-4.143_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv",
        agile_file="agile-half-hour-actual-rates-01-01-2025_31-12-2025_SWEng.csv",
        rates=TariffRates(
            region="sw_england",
            year=2025,
            flat_rate=0.2639,
            e7_day=0.3190,
            e7_night=0.1340,
            flat_standing_charge_per_day=0.5789,
            e7_standing_charge_per_day=0.5830,
        ),
    ),
}


def get_location(name: str) -> LocationSpec:
    """Return the :class:`LocationSpec` for *name* (case-insensitive).

    Raises
    ------
    KeyError
        If *name* is not a known location, with the valid keys in the message.
    """
    key = name.strip().lower()
    try:
        return LOCATIONS[key]
    except KeyError:
        valid = ", ".join(LOCATIONS)
        raise KeyError(f"Unknown location {name!r}. Valid locations: {valid}.")


def resolve_paths(spec: LocationSpec, data_dir: str | Path) -> tuple[Path, Path]:
    """Return the ``(pv_path, agile_path)`` for *spec* under *data_dir*."""
    data_dir = Path(data_dir)
    return data_dir / spec.pv_file, data_dir / spec.agile_file
