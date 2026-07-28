"""Assembled-experiment oracles for Spec 06 (V3-V15 of the verification plan).

These are the checks that need the completed 2160-curve cache and the assembled
tables rather than a stub dispatch. ``results/`` is gitignored, so the whole module
skips when the artefacts are absent (a fresh clone, or before the experiment runs)
and exercises them automatically when they are present.

The unit-level oracles live elsewhere: E1-E4 in ``test_economics.py``, the surrogate
and lifetime-policy oracles in ``test_sweep.py``, peak metrics in ``test_peaks.py``.
"""

from __future__ import annotations

import pickle
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CACHE_DIR = REPO_ROOT / "results" / "cache" / "sweep_v2"
SCENARIOS = REPO_ROOT / "results" / "sweep_scenarios_v2.csv"
PEAKS = REPO_ROOT / "results" / "sweep_peak_events_v2.csv"

pytestmark = pytest.mark.skipif(
    not (CACHE_DIR.is_dir() and SCENARIOS.exists() and PEAKS.exists()),
    reason="Spec 06 experiment artefacts not present (results/ is gitignored)",
)

LOCS = ("inverness", "manchester", "plymouth")
TARS = ("flat", "e7", "agile")
PVS = (1, 2, 3, 4, 5, 6)
SIZES = (0.5, 1, 2.5, 5, 10)
PENS = (0.0, 0.01, 0.03, 0.05, 0.07, 0.09)
PV_COST_PER_KWP = 1109.0
SOH_FLOOR = 0.08

CURVE_NAME = re.compile(
    r"^(?P<loc>[a-z]+)_(?P<tar>[a-z0-9]+)_pv(?P<pv>[\d.]+)_bat(?P<bat>[\d.]+)_"
    r"(?P<ctrl>milp|self_consumption_tou|self_consumption)_(?P<tag>[\d.]+|rules)_"
    r"pw(?P<pw>[\d.]+)\.pkl$"
)


@pytest.fixture(scope="module")
def scenarios() -> pd.DataFrame:
    return pd.read_csv(SCENARIOS)


@pytest.fixture(scope="module")
def peaks() -> pd.DataFrame:
    return pd.read_csv(PEAKS)


@pytest.fixture(scope="module")
def curves() -> list[tuple]:
    """(location, tariff, pv, size, controller, tag, SavingCurve) for every cached curve."""
    out = []
    for path in sorted(CACHE_DIR.glob("*.pkl")):
        match = CURVE_NAME.match(path.name)
        assert match, f"unparseable cache filename: {path.name}"
        out.append((match["loc"], match["tar"], float(match["pv"]), float(match["bat"]),
                    match["ctrl"], match["tag"], pickle.loads(path.read_bytes())))
    return out


def _expected_curve_names() -> set[str]:
    names = set()
    for loc in LOCS:
        for tar in TARS:
            for pv in PVS:
                for size in SIZES:
                    power = size * 0.5
                    stem = f"{loc}_{tar}_pv{pv:g}_bat{size:g}"
                    for pen in PENS:
                        names.add(f"{stem}_milp_{pen:.4f}_pw{power:g}.pkl")
                    for ctrl in ("self_consumption", "self_consumption_tou"):
                        names.add(f"{stem}_{ctrl}_rules_pw{power:g}.pkl")
    return names


def test_v14_partition_is_disjoint_and_complete():
    """The 12-way `i mod 12` split covers every job exactly once."""
    jobs = []
    for loc in LOCS:
        for pv in PVS:
            for tar in TARS:
                for pen in PENS:
                    jobs.append(("milp", loc, pv, tar, pen))
                jobs.append(("rules", loc, pv, tar, "na"))
    assert len(jobs) == 378
    parts = {m: [j for i, j in enumerate(jobs) if i % 12 == m] for m in range(12)}
    union = [j for m in range(12) for j in parts[m]]
    assert len(union) == len(set(union)) == 378
    assert sorted(union) == sorted(jobs)


def test_v4_cache_is_exactly_the_expected_curve_set():
    actual = {p.name for p in CACHE_DIR.glob("*.pkl")}
    expected = _expected_curve_names()
    assert len(expected) == 2160
    assert actual == expected, (
        f"{len(expected - actual)} missing, {len(actual - expected)} unexpected"
    )


def test_v15_every_curve_is_structurally_valid(curves):
    for loc, tar, pv, size, ctrl, tag, curve in curves:
        n = len(curve.e_usable)
        assert len(curve.saving) == len(curve.throughput) == len(curve.mean_soc) == n
        assert np.all(np.isfinite(curve.saving))
        assert np.all(np.isfinite(curve.throughput))
        assert np.all(np.isfinite(curve.mean_soc))
        assert np.all(np.diff(curve.e_usable) > 0)
        assert np.all((curve.mean_soc >= 0) & (curve.mean_soc <= 1))
        assert np.all(curve.throughput >= 0)


def test_v15b_saving_rises_with_battery_size(curves):
    groups = defaultdict(list)
    for loc, tar, pv, size, ctrl, tag, curve in curves:
        groups[(loc, tar, pv, ctrl, tag)].append((size, curve))
    for key, items in groups.items():
        items.sort(key=lambda t: t[0])
        savings = [c.saving[-1] for _, c in items]
        for lower, higher in zip(savings, savings[1:]):
            assert higher >= lower - 1e-3, f"saving falls with size at {key}"


def test_v15c_throughput_does_not_rise_with_wear_penalty(curves):
    """Higher wear price must not increase cycling; MILP solver noise tolerated."""
    groups = defaultdict(dict)
    for loc, tar, pv, size, ctrl, tag, curve in curves:
        if ctrl == "milp":
            groups[(loc, tar, pv, size)][float(tag)] = curve
    for key, by_penalty in groups.items():
        thr = [by_penalty[p].throughput[-1] for p in sorted(by_penalty)]
        for lower, higher in zip(thr, thr[1:]):
            assert (higher - lower) / lower < 1e-4, f"throughput rises with penalty at {key}"


def test_v3_scenario_cardinality_and_key_uniqueness(scenarios):
    assert len(scenarios) == 4374
    key = ["location", "tariff", "pv_kwp", "battery_kwh",
           "deg_penalty_gbp_per_kwh", "controller", "policy"]
    assert not scenarios.duplicated(subset=key).any()
    assert (scenarios.battery_kwh == 0).sum() == 54


def test_v5_pv_capex_linear_and_saving_rises_with_pv_size(scenarios):
    pv_only = scenarios[scenarios.battery_kwh == 0]
    expected = pv_only.pv_kwp * PV_COST_PER_KWP
    assert (pv_only.capex_gbp - expected).abs().max() < 1e-6
    table = pv_only.pivot_table(index=["location", "tariff"], columns="pv_kwp",
                                values="annual_saving_yr1_gbp")
    assert len(table) == 9
    assert table.apply(lambda row: np.all(np.diff(row.values) > 0), axis=1).all()


def test_v7_penalty_axis_exact_and_rules_penalty_free(scenarios):
    milp = scenarios[scenarios.controller == "milp"]
    rules = scenarios[scenarios.controller.isin(["self_consumption", "self_consumption_tou"])]
    assert sorted(milp.deg_penalty_gbp_per_kwh.dropna().unique()) == list(PENS)
    assert set(milp.cycle_life_efc.unique()) == {6000.0}
    assert set(rules.cycle_life_efc.unique()) == {6000.0}
    assert rules.deg_penalty_gbp_per_kwh.isna().all()


def test_v8_power_is_half_c_for_every_nominal_size(scenarios):
    battery = scenarios[scenarios.battery_kwh > 0]
    assert (battery.power_kw - 0.5 * battery.battery_kwh).abs().max() < 1e-9
    assert set(zip(battery.battery_kwh, battery.power_kw)) == {
        (0.5, 0.25), (1.0, 0.5), (2.5, 1.25), (5.0, 2.5), (10.0, 5.0)
    }


def test_v9_no_replacement_policy(scenarios):
    """No outflow, no SOH reset, positive SOH, every query inside the solved knots."""
    no_repl = scenarios[(scenarios.policy == "no-replacement") & (scenarios.battery_kwh > 0)]
    listed = no_repl.replacement_years.astype(str).str.strip()
    assert not (no_repl.replacement_years.notna() & listed.ne("") & listed.ne("[]")).any()
    assert (no_repl.min_soh > 0).all()
    assert (no_repl.min_soh >= SOH_FLOOR).all()
    # The policy is only meaningful if it actually runs below the 0.60 replacement floor.
    assert (no_repl.min_soh < 0.60).any()


def test_v3b_structural_invariants(scenarios):
    d = scenarios
    assert (d.p95_import_kw <= d.p99_import_kw + 1e-9).all()
    assert (d.p99_import_kw <= d.peak_import_kw + 1e-9).all()
    assert (d.count_above_2kw >= d.count_above_3kw).all()
    assert (d.count_above_3kw >= d.count_above_5kw).all()
    assert (d.count_above_5kw >= d.count_above_7kw).all()
    assert ((d.mean_soc >= 0) | d.mean_soc.isna()).all()
    assert ((d.mean_soc <= 1) | d.mean_soc.isna()).all()
    assert (d.efc_yr1.dropna() >= 0).all()
    assert (d.throughput_yr1_kwh.dropna() >= 0).all()
    assert (d.capex_gbp > 0).all()

    pv_only = d[d.battery_kwh == 0].set_index(["location", "tariff", "pv_kwp"])
    battery = d[d.battery_kwh > 0].join(
        pv_only.annual_saving_yr1_gbp.rename("ref"), on=["location", "tariff", "pv_kwp"]
    )
    assert (battery.annual_saving_yr1_gbp >= battery.ref - 1e-6).all()


def test_v11_peak_events_cardinality_ranking_and_balance(peaks):
    assert len(peaks) == 110700
    key = ["location", "tariff", "pv_kwp", "battery_kwh",
           "deg_penalty_gbp_per_kwh", "controller"]
    grouped = peaks.groupby(key, dropna=False)
    assert grouped.ngroups == 2214
    assert (grouped.size() == 50).all()
    assert not grouped["rank"].apply(
        lambda s: sorted(s.tolist()) != list(range(1, 51))
    ).any()
    assert not grouped.apply(
        lambda g: (g.sort_values("rank").grid_import_kw.diff().dropna() > 1e-9).any(),
        include_groups=False,
    ).any()
    # Retained import must be the solved p_import_kw, not a gross-deficit approximation.
    residual = peaks.grid_import_kw - (
        peaks.demand_kw - peaks.pv_kw + peaks.p_charge_kw - peaks.p_discharge_kw
    )
    assert residual.abs().max() < 1e-6
