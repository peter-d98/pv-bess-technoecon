"""Battery energy storage system (BESS) parameters for the stage 1 model.

The battery is represented by a generic state-of-charge (SOC) model. All
quantities are defined in physical units so that the synthetic-data results
remain easy to verify by hand.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatteryParams:
    """Parameters describing a domestic battery energy storage system.

    Attributes
    ----------
    capacity_kwh:
        Nominal usable energy capacity of the battery (kWh).
    max_charge_kw:
        Maximum charging power (kW).
    max_discharge_kw:
        Maximum discharging power (kW).
    eta_charge:
        Charging efficiency (0-1).
    eta_discharge:
        Discharging efficiency (0-1).
    soc_min:
        Minimum allowed state of charge as a fraction of capacity (0-1).
    soc_max:
        Maximum allowed state of charge as a fraction of capacity (0-1).
    soc_init:
        Initial state of charge as a fraction of capacity (0-1).
    degradation_cost_per_kwh:
        Throughput degradation cost applied to energy charged and discharged
        (GBP per kWh). Set to 0 to disable the degradation penalty.
    """

    capacity_kwh: float = 10.0
    max_charge_kw: float = 3.0
    max_discharge_kw: float = 3.0
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    soc_min: float = 0.10
    soc_max: float = 0.90
    soc_init: float = 0.50
    degradation_cost_per_kwh: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 < self.eta_charge <= 1.0:
            raise ValueError("eta_charge must be in (0, 1].")
        if not 0.0 < self.eta_discharge <= 1.0:
            raise ValueError("eta_discharge must be in (0, 1].")
        if not 0.0 <= self.soc_min < self.soc_max <= 1.0:
            raise ValueError("Require 0 <= soc_min < soc_max <= 1.")
        if not self.soc_min <= self.soc_init <= self.soc_max:
            raise ValueError("soc_init must lie within [soc_min, soc_max].")
        if self.capacity_kwh <= 0:
            raise ValueError("capacity_kwh must be positive.")
        if self.max_charge_kw <= 0 or self.max_discharge_kw <= 0:
            raise ValueError("Charge/discharge power limits must be positive.")
        if self.degradation_cost_per_kwh < 0:
            raise ValueError("degradation_cost_per_kwh must be non-negative.")
