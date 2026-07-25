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

from src.degradation import (
    DegradationParams,
    FadeResult,
    derive_throughput_penalty,
    simulate_capacity_fade,
)
from src.economics import EconomicParams
from src.sweep import (
    SweepGrid,
    build_saving_curve,
    fade_npv,
    knot_grid,
    no_replacement_npv,
    run_sweep,
)

SOC_MIN, SOC_MAX = 0.10, 0.90
SOC_WINDOW = SOC_MAX - SOC_MIN

NO_BATTERY_PEAK_KW = 8.0

RULES_CONTROLLERS = ("self_consumption", "self_consumption_tou")


def _stub_peaks(peak_kw: float) -> dict:
    """Minimal peak summary in the shape the real provider returns."""
    return {
        "peak_import_kw": peak_kw,
        "peak_import_time": "2023-01-15T18:00:00",
        "p95_import_kw": 0.5 * peak_kw,
        "p99_import_kw": 0.8 * peak_kw,
        "count_above_2kw": 100,
        "count_above_3kw": 50,
        "count_above_5kw": 10,
        "count_above_7kw": 1,
        "top_events": [
            {
                "rank": i + 1,
                "time": f"2023-01-1{i}T18:00:00",
                "grid_import_kw": peak_kw - i,
                "demand_kw": 1.0,
                "pv_kw": 0.0,
                "p_charge_kw": 0.0,
                "p_discharge_kw": 0.0,
            }
            for i in range(2)
        ],
    }


class StubProvider:
    """Closed-form dispatch: saving = 100*sqrt(E_usable), throughput = 200*E_usable."""

    def __init__(self):
        self.soc_min = SOC_MIN
        self.soc_max = SOC_MAX
        self.solves = 0
        self.dispatch_requests = []

    def resolve_penalty(self, penalty, cycle_life_efc):
        if penalty is not None:
            return float(penalty)
        return 890.0 / (cycle_life_efc * 2.0)   # derived, capacity-independent

    def dispatch_year(
        self, location, tariff, pv_kwp, nominal_size_kwh, penalty, controller
    ):
        self.dispatch_requests.append((pv_kwp, nominal_size_kwh, controller, penalty))

        def dyear(capacity_kwh, soc_max):
            self.solves += 1
            e = capacity_kwh * (soc_max - self.soc_min)
            return (
                100.0 * np.sqrt(e), 200.0 * e, np.full(10, 0.5),
                _stub_peaks(NO_BATTERY_PEAK_KW - 0.1 * e),
            )
        return dyear

    def grid_only_flat_cost(self, location):
        return 1300.0

    def pv_only_saving(self, location, tariff, pv_kwp):
        return 100.0 * pv_kwp

    def pv_only_peaks(self, location, tariff, pv_kwp):
        return _stub_peaks(NO_BATTERY_PEAK_KW)

    def capex(self, pv_kwp, size_kwh):
        return pv_kwp * 1109.0 + size_kwh * 890.0

    def pv_om_cost(self, pv_kwp):
        return 0.01 * pv_kwp * 1109.0

    def replacement_capex(self, size_kwh):
        return size_kwh * 890.0


def _econ():
    return EconomicParams(battery_capex=1.0, horizon_years=20, battery_life_years=10.0)


def _deg():
    return DegradationParams(replace_at_eol=False, soh_floor=0.60, calendar_life_years=10.0)


def test_s1_default_grid_cardinality():
    """Spec 06 A3: 54 reference rows + 2 lifetime policies x (1620 MILP + 540 rules)."""
    df = run_sweep(SweepGrid(), StubProvider(), _econ(), _deg()).scenarios
    assert len(df) == 4374
    assert (df["controller"] == "none").sum() == 54
    assert (df["controller"] == "milp").sum() == 3240
    rules = df["controller"].isin(RULES_CONTROLLERS)
    assert rules.sum() == 1080
    assert (df["policy"] == "run-to-fade").sum() == 2160
    assert (df["policy"] == "no-replacement").sum() == 2160


def test_s10_degradation_axis_mapping():
    """The six Spec 06 MILP penalties all use 6000 EFC fade."""
    df = run_sweep(SweepGrid(), StubProvider(), _econ(), _deg()).scenarios
    milp = df[df["controller"] == "milp"]
    pairs = set(
        zip(milp["deg_penalty_gbp_per_kwh"].round(6), milp["cycle_life_efc"])
    )
    assert pairs == {
        (0.0, 6000.0),
        (0.01, 6000.0),
        (0.03, 6000.0),
        (0.05, 6000.0),
        (0.07, 6000.0),
        (0.09, 6000.0),
    }
    # Rules rows carry no penalty but still fade at the baseline EFC.
    rules = df[df["controller"].isin(("self_consumption", "self_consumption_tou"))]
    assert rules["deg_penalty_gbp_per_kwh"].isna().all()
    assert (rules["cycle_life_efc"] == 6000.0).all()


def test_s2_curve_monotone_and_exact_at_knots():
    """S2: saving_at is exact at knots and non-decreasing between them."""
    knots = knot_grid(SOC_MAX * 15 * 0 + 12.0, 7)
    dyear = StubProvider().dispatch_year("g", "agile", 4.0, 10.0, 0.05, "milp")
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
        locations=("glasgow",), tariffs=("agile",), pv_sizes_kwp=(4.0,),
        sizes_kwh=(0.0, 10.0),
        deg_scenarios=((None, 6000.0),), controllers=("milp",),
    )
    df = run_sweep(grid, StubProvider(), _econ(), _deg()).scenarios
    ref = df[df["battery_kwh"] == 0.0].iloc[0]
    assert ref["controller"] == "none"
    assert ref["pv_kwp"] == 4.0
    assert ref["capex_gbp"] == pytest.approx(4436.0)
    assert ref["annual_saving_yr1_gbp"] == pytest.approx(400.0)
    assert ref["replacement_years"] == "[]"
    assert np.isfinite(ref["npv_gbp"])


def test_s5_surrogate_matches_full_solve_and_converges():
    """S5: surrogate run-to-fade NPV matches the full fade; gap shrinks with knots."""
    provider = StubProvider()
    deg = _deg()
    size = 10.0
    init_capex = provider.capex(4.0, size)
    repl_capex = provider.replacement_capex(size)
    econ = replace(_econ(), battery_capex=init_capex,
                   replacement_cost_factor=repl_capex / init_capex)
    e_max = 15.0 * SOC_WINDOW

    dyear = provider.dispatch_year("g", "agile", 4.0, size, 0.05, "milp")
    fade_full = simulate_capacity_fade(
        dispatch_year=dyear, capacity_kwh_nominal=size, soc_max=SOC_MAX,
        horizon_years=econ.horizon_years, params=deg,
    )
    npv_full, _, _ = fade_npv(fade_full, econ, repl_capex)

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
        npv_s, _, _ = fade_npv(fade_s, econ, repl_capex)
        gaps.append(abs(npv_s.npv - npv_full.npv))

    assert gaps[1] <= gaps[0]                 # convergence
    assert gaps[1] / abs(npv_full.npv) < 0.01  # within 1% at 15 knots


def test_spec06_fade_npv_uses_realised_life_for_terminal_residual():
    replacement_capex = 8900.0
    initial_capex = 13336.0
    econ = EconomicParams(
        battery_capex=initial_capex,
        discount_rate=0.0,
        horizon_years=20,
        price_escalation=0.0,
        battery_life_years=10.0,
        replacement_cost_factor=replacement_capex / initial_capex,
    )
    fade = FadeResult(
        saving_stream=[0.0] * 20,
        soh_trajectory=[1.0] * 20,
        effective_life_years=15.0,
        replacement_years=[16],
        efc_per_year=[0.0] * 20,
    )

    result, realised_life, terminal_residual = fade_npv(
        fade, econ, replacement_capex
    )

    assert realised_life == 15
    assert result.cashflows.loc[result.cashflows["year"] == 15, "capex"].item() == pytest.approx(replacement_capex)
    assert terminal_residual == pytest.approx(replacement_capex * 10.0 / 15.0)
    assert result.cashflows.loc[result.cashflows["year"] == 20, "benefit"].item() == pytest.approx(terminal_residual)


def test_s9_cache_hit_zero_solves(tmp_path):
    """S9: a second run with a populated cache performs zero solves, same output."""
    provider = StubProvider()
    grid = SweepGrid(
        locations=("glasgow",), tariffs=("agile",), pv_sizes_kwp=(4.0,),
        sizes_kwh=(0.0, 10.0),
        deg_scenarios=((None, 6000.0),), controllers=("milp",),
    )
    df1 = run_sweep(grid, provider, _econ(), _deg(), cache_dir=tmp_path).scenarios
    solves_after_first = provider.solves
    assert solves_after_first > 0

    df2 = run_sweep(grid, provider, _econ(), _deg(), cache_dir=tmp_path).scenarios
    assert provider.solves == solves_after_first          # no new solves
    assert np.allclose(df1["npv_gbp"], df2["npv_gbp"])


def _single_cell_grid(**overrides) -> SweepGrid:
    base = dict(
        locations=("glasgow",), tariffs=("agile",), pv_sizes_kwp=(4.0,),
        sizes_kwh=(0.0, 10.0), deg_scenarios=((0.05, 6000.0),),
        controllers=("milp",),
    )
    base.update(overrides)
    return SweepGrid(**base)


def test_s11_no_replacement_keeps_one_battery_and_shares_the_curve():
    """A8: no SOH reset, no replacement year, and no extra dispatch curves."""
    provider = StubProvider()
    df = run_sweep(_single_cell_grid(), provider, _econ(), _deg()).scenarios
    battery = df[df["battery_kwh"] == 10.0]

    assert set(battery["policy"]) == {"run-to-fade", "no-replacement"}
    fade_row = battery[battery["policy"] == "run-to-fade"].iloc[0]
    keep_row = battery[battery["policy"] == "no-replacement"].iloc[0]

    assert fade_row["replacement_years"] != "[]"      # the fade policy replaces
    assert keep_row["replacement_years"] == "[]"      # the keep policy never does
    assert keep_row["min_soh"] < 0.60                 # runs past the floor
    assert keep_row["min_soh"] < fade_row["min_soh"]  # so SOH was never reset
    assert np.isnan(keep_row["realised_life_years"])
    # Year 1 is the same fresh battery under both policies.
    assert keep_row["annual_saving_yr1_gbp"] == pytest.approx(
        fade_row["annual_saving_yr1_gbp"]
    )
    # One curve serves both policies, so the second costs no solves.
    assert len(provider.dispatch_requests) == 1


def test_s12_no_replacement_npv_charges_no_replacement_and_no_residual():
    """A8: only the year-0 capex; nothing bought later, nothing credited back."""
    econ = EconomicParams(
        battery_capex=1000.0, discount_rate=0.0, horizon_years=20,
        price_escalation=0.0, battery_life_years=6.0, replacement_cost_factor=1.0,
    )
    fade = FadeResult(
        saving_stream=[0.0] * 20, soh_trajectory=[0.5] * 20,
        effective_life_years=20.0, replacement_years=[], efc_per_year=[0.0] * 20,
    )

    result = no_replacement_npv(fade, econ)

    later = result.cashflows[result.cashflows["year"] > 0]
    assert later["capex"].abs().sum() == pytest.approx(0.0)
    assert later["benefit"].abs().sum() == pytest.approx(0.0)
    assert result.npv == pytest.approx(-1000.0)


def test_s13_no_replacement_rejects_non_positive_soh():
    """A8: a fade trajectory reaching SOH <= 0 must fail, not be clipped."""
    deg = replace(_deg(), calendar_life_years=3.0)
    grid = _single_cell_grid(lifetime_policies=("no-replacement",))

    with pytest.raises(ValueError, match=r"SOH <= 0"):
        run_sweep(grid, StubProvider(), _econ(), deg)


def test_s14_rejects_capacity_below_the_lowest_solved_knot():
    """A8: queried capacities must be covered, never flat-extrapolated."""
    grid = _single_cell_grid(
        lifetime_policies=("no-replacement",), min_soh_coverage=0.80
    )

    with pytest.raises(ValueError, match=r"below the solved"):
        run_sweep(grid, StubProvider(), _econ(), _deg())


def test_s15_peak_summaries_and_events_are_retained():
    """A9: peak columns per scenario, top events per scenario, signed reduction."""
    result = run_sweep(_single_cell_grid(), StubProvider(), _econ(), _deg())
    df = result.scenarios

    battery = df[(df["battery_kwh"] == 10.0) & (df["policy"] == "run-to-fade")].iloc[0]
    fresh_usable = 10.0 * SOC_WINDOW
    expected_peak = NO_BATTERY_PEAK_KW - 0.1 * fresh_usable
    assert battery["peak_import_kw"] == pytest.approx(expected_peak)
    assert battery["peak_reduction_kw"] == pytest.approx(
        NO_BATTERY_PEAK_KW - expected_peak
    )
    assert battery["count_above_3kw"] == 50
    assert battery["peak_import_time"] == "2023-01-15T18:00:00"

    reference = df[df["battery_kwh"] == 0.0].iloc[0]
    assert reference["peak_import_kw"] == pytest.approx(NO_BATTERY_PEAK_KW)
    assert reference["peak_reduction_kw"] == pytest.approx(0.0)

    events = result.peak_events
    assert set(events["battery_kwh"]) == {0.0, 10.0}
    # Events are retained once per scenario, not once per lifetime policy.
    assert list(events.groupby("battery_kwh").size()) == [2, 2]
    assert {"time", "grid_import_kw", "demand_kw", "pv_kw"} <= set(events.columns)


def test_spec06_power_is_half_c_and_fixed_per_nominal_size():
    provider = StubProvider()
    grid = SweepGrid(
        locations=("glasgow",), tariffs=("agile",), pv_sizes_kwp=(4.0,),
        sizes_kwh=(0.0, 2.5, 5.0, 10.0),
        deg_scenarios=((0.05, 6000.0),), controllers=("milp",),
    )
    df = run_sweep(grid, provider, _econ(), _deg()).scenarios
    battery_rows = df[df["battery_kwh"] > 0]

    assert np.allclose(
        battery_rows["power_kw"], 0.5 * battery_rows["battery_kwh"]
    )
    requested_sizes = {request[1] for request in provider.dispatch_requests}
    assert requested_sizes == {2.5, 5.0, 10.0}


def test_spec06_cache_separates_pv_and_nominal_battery_sizes(tmp_path):
    provider = StubProvider()
    grid = SweepGrid(
        locations=("glasgow",), tariffs=("agile",), pv_sizes_kwp=(2.0, 4.0),
        sizes_kwh=(2.5, 5.0), deg_scenarios=((0.05, 6000.0),),
        controllers=("milp",), n_knots=3,
    )
    run_sweep(grid, provider, _econ(), _deg(), cache_dir=tmp_path)

    assert provider.solves == 2 * 2 * 3
    names = {path.name for path in tmp_path.glob("*.pkl")}
    assert len(names) == 4
    assert any("pv2_bat2.5" in name for name in names)
    assert any("pv4_bat5" in name for name in names)
