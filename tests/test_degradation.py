"""Unit tests for the degradation and capacity-fade model (Spec 02).

Numeric oracles O1-O11 are hand-derived in
``docs/specs/spec-02-verification.md`` and reproduced here independently.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.degradation import (
    DegradationParams,
    capacity_fade,
    derive_throughput_penalty,
    equivalent_full_cycles,
    simulate_capacity_fade,
    soc_exposure,
    soc_stress_multiplier,
    time_to_eol,
)


def _default() -> DegradationParams:
    return DegradationParams()  # soh_eol=0.80, cycle_life=6000, cal=13.5, linear


# --- O1 / O9: throughput penalty derivation -----------------------------------

def test_o1_penalty_derivation():
    c_thr = derive_throughput_penalty(4000.0, 10.0, _default())
    assert c_thr == pytest.approx(4000.0 / 120000.0)  # 0.0333333 GBP/kWh


def test_o9_penalty_zero_capex():
    assert derive_throughput_penalty(0.0, 10.0, _default()) == 0.0


# --- O2: equivalent full cycles ----------------------------------------------

def test_o2_equivalent_full_cycles():
    assert equivalent_full_cycles(120000.0, 10.0) == pytest.approx(6000.0)


# --- O3: combined fade after one year ----------------------------------------

def test_o3_capacity_fade_one_year():
    soh = capacity_fade(300.0, 1.0, _default())
    # phi_cyc = 0.20*300/6000 = 0.010; phi_cal = 0.20*1/13.5 = 0.0148148
    assert soh == pytest.approx(0.9751852, abs=1e-6)


# --- O4: pure calendar reaches EoL at calendar life --------------------------

def test_o4_pure_calendar_eol():
    soh = capacity_fade(0.0, 13.5, _default())
    assert soh == pytest.approx(0.80, abs=1e-12)


# --- O5: pure cycling reaches EoL at cycle life ------------------------------

def test_o5_pure_cycling_eol():
    soh = capacity_fade(6000.0, 0.0, _default())
    assert soh == pytest.approx(0.80, abs=1e-12)


# --- O6: combined effective life --------------------------------------------

def test_o6_combined_effective_life():
    # annual fade = 0.20*(300/6000) + 0.20*(1/13.5) = 0.0248148
    # years to consume 0.20 budget = 0.20 / 0.0248148 = 8.0596
    life = time_to_eol(300.0, _default())
    assert life == pytest.approx(8.0596, abs=1e-3)


# --- O7: square-root calendar sensitivity ------------------------------------

def test_o7_sqrt_calendar():
    params = DegradationParams(calendar_form="sqrt")
    soh = capacity_fade(0.0, 1.0, params)
    phi_cal = 1.0 - soh
    # 0.20 * sqrt(1/13.5) = 0.0544331
    assert phi_cal == pytest.approx(0.20 * math.sqrt(1.0 / 13.5), abs=1e-7)
    assert phi_cal == pytest.approx(0.0544331, abs=1e-6)


# --- O8: SOC-dependent calendar with beta=0 equals baseline ------------------

def test_o8_soc_stress_beta_zero_is_unity():
    params = DegradationParams(soc_dependent_calendar=True, soc_stress_beta=0.0)
    mult = soc_stress_multiplier(np.linspace(0.1, 0.9, 48), params)
    assert mult == pytest.approx(1.0)


def test_o8_soc_dependent_beta_zero_matches_independent():
    soc = np.full(48, 0.7)

    def dispatch(cap, soc_max):
        return 100.0, 4000.0, soc

    base = simulate_capacity_fade(dispatch, 10.0, 0.9, 20, DegradationParams())
    dep = simulate_capacity_fade(
        dispatch,
        10.0,
        0.9,
        20,
        DegradationParams(soc_dependent_calendar=True, soc_stress_beta=0.0),
    )
    assert dep.saving_stream == base.saving_stream
    assert dep.soh_trajectory == pytest.approx(base.soh_trajectory)
    assert dep.replacement_years == base.replacement_years


# --- O10: lifetime simulation with a stub dispatch ---------------------------

def test_o10_simulate_stub():
    # Pure cycling: calendar life effectively infinite; throughput 10000 kWh
    # gives EFC = 10000/(2*10) = 500/yr, so SOH hits 0.80 after 12 years.
    params = DegradationParams(calendar_life_years=1e9)

    def dispatch(cap, soc_max):
        return 150.0, 10000.0, np.full(48, 0.5)

    result = simulate_capacity_fade(dispatch, 10.0, 0.9, 20, params)

    assert len(result.saving_stream) == 20
    assert all(s == 150.0 for s in result.saving_stream)
    assert result.effective_life_years == pytest.approx(12.0, abs=1e-3)
    # A fresh battery starts operating in year 13 (after 12 years of service).
    assert result.replacement_years == [13]
    assert result.efc_per_year[0] == pytest.approx(500.0)


def test_o10_soh_trajectory_declines_then_resets():
    params = DegradationParams(calendar_life_years=1e9)

    def dispatch(cap, soc_max):
        return 150.0, 10000.0, np.full(48, 0.5)

    result = simulate_capacity_fade(dispatch, 10.0, 0.9, 20, params)
    # Year 1 full health, monotonic decline to year 12, reset at year 13.
    assert result.soh_trajectory[0] == pytest.approx(1.0, abs=1e-9)
    assert result.soh_trajectory[11] == pytest.approx(0.80 + 0.20 / 12, abs=1e-3)
    assert result.soh_trajectory[12] == pytest.approx(1.0, abs=1e-9)


# --- Run-to-fade replacement policy ------------------------------------------

def _pure_cycling_dispatch(cap, soc_max):
    # EFC = 10000 / (2 * 10) = 500/yr, so additive cycle fade is 0.20 * t / 12.
    return 150.0, 10000.0, np.full(48, 0.5)


def test_run_to_fade_no_replacement_below_soh_eol():
    # Run-to-fade with a floor that never binds: the battery keeps operating
    # past soh_eol (0.80) and is never replaced within the horizon.
    params = DegradationParams(
        calendar_life_years=1e9, replace_at_eol=False, soh_floor=0.60
    )
    result = simulate_capacity_fade(_pure_cycling_dispatch, 10.0, 0.9, 20, params)

    assert result.replacement_years == []
    # SOH falls below soh_eol yet the battery is retained.
    assert result.soh_trajectory[-1] < 0.80
    assert result.soh_trajectory[-1] == pytest.approx(1 - 0.20 * 19 / 12, abs=1e-3)


def test_run_to_fade_floor_forces_replacement():
    # A higher floor (0.70) binds at year 19 (SOH reaches 0.70 there).
    params = DegradationParams(
        calendar_life_years=1e9, replace_at_eol=False, soh_floor=0.70
    )
    result = simulate_capacity_fade(_pure_cycling_dispatch, 10.0, 0.9, 20, params)

    assert result.replacement_years == [19]
    assert result.soh_trajectory[18] == pytest.approx(1.0, abs=1e-9)


def test_soh_floor_out_of_range_rejected():
    with pytest.raises(ValueError):
        DegradationParams(soh_floor=1.0)


# --- O11: SOC exposure metrics -----------------------------------------------

def test_o11_soc_exposure_constant():
    exposure = soc_exposure(np.full(48, 0.5), 0.5, thresholds=(0.8,))
    assert exposure["mean_soc"] == pytest.approx(0.5)
    assert exposure["time_weighted_mean_soc"] == pytest.approx(0.5)
    assert exposure["frac_time_above"][0.8] == pytest.approx(0.0)
    assert exposure["histogram"]["hours"].sum() == pytest.approx(48 * 0.5)


def test_soc_exposure_above_threshold():
    # Half the samples at 0.9, half at 0.3 -> 50% time above 0.8.
    soc = np.array([0.9, 0.3] * 24)
    exposure = soc_exposure(soc, 0.5, thresholds=(0.8,))
    assert exposure["frac_time_above"][0.8] == pytest.approx(0.5)


# --- Parameter validation -----------------------------------------------------

def test_invalid_calendar_form_raises():
    with pytest.raises(ValueError):
        DegradationParams(calendar_form="cubic")


def test_invalid_soh_eol_raises():
    with pytest.raises(ValueError):
        DegradationParams(soh_eol=1.5)
