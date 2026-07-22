"""Unit tests for the sweep harness (src.sweep, Spec 05).

Oracles S1-S5, S9 use a closed-form STUB dispatch (no CVXPY): the annual saving
is a concave, strictly-increasing function of usable energy, so the surrogate is
exercised without a solver. S3 confirms the capacity-independence premise.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.degradation import DegradationParams, derive_throughput_penalty, simulate_capacity_fade
from src.economics import EconomicParams
from src.sweep import (
    SweepGrid,
    build_saving_curve,
    fade_npv,
    knot_grid,
    run_sweep,
)

SOC_MIN, SOC_MAX = 0.10, 0.90
SOC_WINDOW = SOC_MAX - SOC_MIN


class StubProvider:
    """Closed-form dispatch: saving = 100*sqrt(E_usable), throughput = 200*E_usable."""

    def __init__(self):
        self.soc_min = SOC_MIN
        self.soc_max = SOC_MAX
        self.solves = 0

    def resolve_penalty(self, penalty, cycle_life_efc):
        if penalty is not None:
            return float(penalty)
        return 890.0 / (cycle_life_efc * 2.0)   # derived, capacity-independent

    def dispatch_year(self, location, tariff, penalty, controller):
        def dyear(capacity_kwh, soc_max):
            self.solves += 1
            e = capacity_kwh * (soc_max - self.soc_min)
            return 100.0 * np.sqrt(e), 200.0 * e, np.full(10, 0.5)
        return dyear

    def grid_only_flat_cost(self, location):
        return 1300.0

    def pv_only_saving(self, location, tariff):
        return 400.0

    def capex(self, size_kwh):
        return 4436.0 + size_kwh * 890.0     # PV 4×1109 + battery (incl inverter)

    def replacement_capex(self, size_kwh):
        return size_kwh * 890.0


def _econ():
    return EconomicParams(battery_capex=1.0, horizon_years=20, battery_life_years=10.0)


def _deg():
    return DegradationParams(replace_at_eol=False, soh_floor=0.60, calendar_life_years=10.0)


def test_s1_default_grid_cardinality():
    """S1: 12 reference + 108 MILP + 72 rules = 192 rows."""
    df = run_sweep(SweepGrid(), StubProvider(), _econ(), _deg())
    assert len(df) == 192
    assert (df["controller"] == "none").sum() == 12
    assert (df["controller"] == "milp").sum() == 108
    assert df["controller"].isin(("self_consumption", "self_consumption_tou")).sum() == 72


def test_s10_degradation_axis_mapping():
    """S10: the three MILP scenarios map to the intended (penalty, EFC) pairs."""
    df = run_sweep(SweepGrid(), StubProvider(), _econ(), _deg())
    milp = df[df["controller"] == "milp"]
    pairs = set(
        zip(milp["deg_penalty_gbp_per_kwh"].round(6), milp["cycle_life_efc"])
    )
    assert pairs == {
        (0.0, 6000.0),
        (round(890.0 / 12000.0, 6), 6000.0),   # 7.42 p/kWh, EFC 6000 (pessimistic)
        (round(890.0 / 16000.0, 6), 8000.0),   # 5.56 p/kWh, EFC 8000 (optimistic)
    }
    # Rules rows carry no penalty but still fade at the baseline EFC.
    rules = df[df["controller"].isin(("self_consumption", "self_consumption_tou"))]
    assert rules["deg_penalty_gbp_per_kwh"].isna().all()
    assert (rules["cycle_life_efc"] == 6000.0).all()


def test_s2_curve_monotone_and_exact_at_knots():
    """S2: saving_at is exact at knots and non-decreasing between them."""
    knots = knot_grid(SOC_MAX * 15 * 0 + 12.0, 7)
    dyear = StubProvider().dispatch_year("g", "agile", 0.05, "milp")
    curve = build_saving_curve(dyear, knots, SOC_WINDOW, SOC_MAX)
    for e in knots:
        assert curve.saving_at(float(e)) == pytest.approx(100.0 * np.sqrt(e))
    xs = np.linspace(knots[0], knots[-1], 50)
    ys = np.array([curve.saving_at(float(x)) for x in xs])
    assert np.all(np.diff(ys) >= -1e-9)


def test_s3_penalty_capacity_independent():
    """S3: the derived penalty is capacity-independent and grounded in EFC."""
    for cap in (5.0, 10.0, 15.0):
        assert derive_throughput_penalty(
            cap * 890.0, cap, DegradationParams(cycle_life_efc=6000.0)
        ) == pytest.approx(890.0 / 12000.0)     # 7.42 p/kWh (pessimistic)
        assert derive_throughput_penalty(
            cap * 890.0, cap, DegradationParams(cycle_life_efc=8000.0)
        ) == pytest.approx(890.0 / 16000.0)     # 5.56 p/kWh (optimistic)


def test_s4_size_zero_reference_row():
    """S4: size-0 row is PV-only, no battery capex, no replacement."""
    grid = SweepGrid(
        locations=("glasgow",), tariffs=("agile",), sizes_kwh=(0.0, 10.0),
        deg_scenarios=((None, 6000.0),), controllers=("milp",),
    )
    df = run_sweep(grid, StubProvider(), _econ(), _deg())
    ref = df[df["battery_kwh"] == 0.0].iloc[0]
    assert ref["controller"] == "none"
    assert ref["capex_gbp"] == pytest.approx(4436.0)
    assert ref["annual_saving_yr1_gbp"] == pytest.approx(400.0)
    assert ref["replacement_years"] == "[]"
    assert np.isfinite(ref["npv_gbp"])


def test_s5_surrogate_matches_full_solve_and_converges():
    """S5: surrogate run-to-fade NPV matches the full fade; gap shrinks with knots."""
    provider = StubProvider()
    deg = _deg()
    size = 10.0
    init_capex = provider.capex(size)
    repl_capex = provider.replacement_capex(size)
    econ = replace(_econ(), battery_capex=init_capex,
                   replacement_cost_factor=repl_capex / init_capex)
    e_max = 15.0 * SOC_WINDOW

    dyear = provider.dispatch_year("g", "agile", 0.05, "milp")
    fade_full = simulate_capacity_fade(
        dispatch_year=dyear, capacity_kwh_nominal=size, soc_max=SOC_MAX,
        horizon_years=econ.horizon_years, params=deg,
    )
    npv_full, _, _ = fade_npv(fade_full, _econ(), 10.0, repl_capex)

    gaps = []
    for n_knots in (7, 15):
        knots = knot_grid(e_max, n_knots)
        curve = build_saving_curve(dyear, knots, SOC_WINDOW, SOC_MAX)

        def cd(capacity_kwh, soc_max, _c=curve):
            e = capacity_kwh * (soc_max - SOC_MIN)
            return _c.saving_at(e), _c.throughput_at(e), ()

        fade_s = simulate_capacity_fade(
            dispatch_year=cd, capacity_kwh_nominal=size, soc_max=SOC_MAX,
            horizon_years=econ.horizon_years, params=deg,
        )
        npv_s, _, _ = fade_npv(fade_s, _econ(), 10.0, repl_capex)
        gaps.append(abs(npv_s.npv - npv_full.npv))

    assert gaps[1] <= gaps[0]                 # convergence
    assert gaps[1] / abs(npv_full.npv) < 0.01  # within 1% at 15 knots


def test_s9_cache_hit_zero_solves(tmp_path):
    """S9: a second run with a populated cache performs zero solves, same output."""
    provider = StubProvider()
    grid = SweepGrid(
        locations=("glasgow",), tariffs=("agile",), sizes_kwh=(0.0, 10.0),
        deg_scenarios=((None, 6000.0),), controllers=("milp",),
    )
    df1 = run_sweep(grid, provider, _econ(), _deg(), cache_dir=tmp_path)
    solves_after_first = provider.solves
    assert solves_after_first > 0

    df2 = run_sweep(grid, provider, _econ(), _deg(), cache_dir=tmp_path)
    assert provider.solves == solves_after_first          # no new solves
    assert np.allclose(df1["npv_gbp"], df2["npv_gbp"])
