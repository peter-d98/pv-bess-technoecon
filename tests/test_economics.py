"""Unit tests for the lifetime NPV economic model (src/economics.py).

The numbered oracle cases (O1-O9) correspond to the hand-derived expected values
in docs/specs/spec-01-verification.md, independently reproduced by a second
reviewer.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.economics import EconomicParams, annual_saving_from_costs, compute_npv


def test_o1_zero_discount_no_escalation_no_replacement():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=20.0,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(2000.0, abs=0.01)


def test_o2_positive_discount():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.05,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=20.0,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(-261.34, abs=0.05)
    assert result.pv_benefits == pytest.approx(3738.66, abs=0.05)


def test_o3_single_replacement_within_horizon():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=10.0,
        replacement_cost_factor=1.0,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(-2000.0, abs=0.01)
    # Exactly one non-zero replacement, at year 10.
    repl = result.cashflows[result.cashflows["year"] >= 1]
    nonzero = repl[repl["capex"] != 0.0]
    assert len(nonzero) == 1
    assert int(nonzero["year"].iloc[0]) == 10


def test_o4_no_replacement_when_life_equals_horizon():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=20.0,
        replacement_cost_factor=1.0,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(2000.0, abs=0.01)
    repl = result.cashflows[result.cashflows["year"] >= 1]
    assert (repl["capex"] == 0.0).all()


def test_o5_price_escalation():
    econ = EconomicParams(
        battery_capex=0.0,
        discount_rate=0.0,
        horizon_years=3,
        price_escalation=0.02,
        battery_life_years=3.0,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(918.12, abs=0.01)


def test_o6_negative_saving():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.05,
        horizon_years=20,
        price_escalation=0.0,
    )
    result = compute_npv(-100.0, econ)
    assert result.npv < -4000.0
    assert math.isinf(result.discounted_payback_years)


def test_o7_npv_identity():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.05,
        horizon_years=20,
        price_escalation=0.02,
        battery_life_years=12.0,
    )
    result = compute_npv(250.0, econ)
    assert abs(result.npv - (result.pv_benefits - result.pv_costs)) < 1e-6
    assert result.bcr == pytest.approx(result.pv_benefits / result.pv_costs)


def test_o8_multiple_replacements_residual_off():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=6.0,
        replacement_cost_factor=1.0,
        include_residual_value=False,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(2000.0, abs=0.01)
    # Three replacements at years 6, 12, 18.
    repl = result.cashflows[result.cashflows["capex"] != 0.0]
    repl = repl[repl["year"] >= 1]
    assert sorted(int(y) for y in repl["year"]) == [6, 12, 18]


def test_o8r_multiple_replacements_residual_on():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=6.0,
        replacement_cost_factor=1.0,
        include_residual_value=True,
    )
    result = compute_npv(300.0, econ)
    assert result.npv == pytest.approx(2666.67, abs=0.01)


def test_o9_residual_value_only():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=30.0,
        include_residual_value=True,
    )
    result = compute_npv(0.0, econ)
    assert result.npv == pytest.approx(-666.67, abs=0.01)


def test_terminal_residual_negative_rejected():
    econ = EconomicParams(battery_capex=1000.0, horizon_years=20)
    with pytest.raises(ValueError):
        compute_npv(0.0, econ, terminal_residual_value=-1.0)


def test_spec06_e1_full_replacement_with_life_based_residual():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=12.0,
    )
    result = compute_npv(0.0, econ)

    assert result.cashflows.loc[result.cashflows["year"] == 12, "capex"].item() == 1000.0
    assert result.cashflows.loc[result.cashflows["year"] == 20, "benefit"].item() == pytest.approx(1000.0 * 4.0 / 12.0)
    assert result.pv_costs == pytest.approx(2000.0)
    assert result.npv == pytest.approx(-1000.0 - 1000.0 + 1000.0 * 4.0 / 12.0)


def test_spec06_e2_full_replacements_with_final_residual():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=6.0,
    )
    result = compute_npv(0.0, econ)

    capex_by_year = result.cashflows.set_index("year")["capex"]
    assert capex_by_year[6] == pytest.approx(1000.0)
    assert capex_by_year[12] == pytest.approx(1000.0)
    assert capex_by_year[18] == pytest.approx(1000.0)
    assert result.cashflows.loc[result.cashflows["year"] == 20, "benefit"].item() == pytest.approx(1000.0 * 4.0 / 6.0)
    assert result.pv_costs == pytest.approx(4000.0)
    assert result.npv == pytest.approx(-4000.0 + 1000.0 * 4.0 / 6.0)


def test_spec06_e3_discounts_full_replacement_and_terminal_residual():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.05,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=12.0,
    )
    result = compute_npv(0.0, econ)

    expected_pv_costs = 1000.0 + 1000.0 / (1.05 ** 12)
    expected_pv_residual = (1000.0 * 4.0 / 12.0) / (1.05 ** 20)
    assert result.pv_costs == pytest.approx(expected_pv_costs)
    assert result.npv == pytest.approx(-expected_pv_costs + expected_pv_residual)


def test_spec06_realised_15_year_life_credits_ten_unused_years():
    econ = EconomicParams(
        battery_capex=1000.0,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=15.0,
    )
    result = compute_npv(0.0, econ)

    assert result.cashflows.loc[result.cashflows["year"] == 15, "capex"].item() == 1000.0
    assert result.cashflows.loc[result.cashflows["year"] == 20, "benefit"].item() == pytest.approx(1000.0 * 10.0 / 15.0)


def test_cashflows_sum_to_npv():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.05,
        horizon_years=20,
        price_escalation=0.02,
        battery_life_years=12.0,
    )
    result = compute_npv(200.0, econ)
    assert result.cashflows["discounted"].sum() == pytest.approx(result.npv, abs=1e-6)


def test_monotonic_in_discount_rate():
    base = dict(
        battery_capex=4000.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=20.0,
    )
    low = compute_npv(300.0, EconomicParams(discount_rate=0.03, **base)).npv
    high = compute_npv(300.0, EconomicParams(discount_rate=0.07, **base)).npv
    assert low > high


def test_saving_stream_matches_constant():
    econ = EconomicParams(
        battery_capex=4000.0,
        discount_rate=0.05,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=20.0,
    )
    scalar = compute_npv(300.0, econ).npv
    stream = compute_npv([300.0] * 20, econ).npv
    assert scalar == pytest.approx(stream, abs=1e-9)


def test_saving_stream_wrong_length_raises():
    econ = EconomicParams(battery_capex=4000.0, horizon_years=20)
    with pytest.raises(ValueError):
        compute_npv([300.0] * 19, econ)


def test_annual_saving_from_costs():
    saving = annual_saving_from_costs(
        cf_net_cost=693.0, battery_net_cost=381.0, degradation_cost=157.0
    )
    assert saving == pytest.approx(693.0 - 538.0)
