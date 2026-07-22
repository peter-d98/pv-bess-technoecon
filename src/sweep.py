"""Parameter-sweep harness for the PV-BESS study (Spec 05).

Orchestrates the systematic sweep over location x tariff x battery size x
in-dispatch degradation penalty, plus a controller axis (MILP vs rules-based),
and emits a tidy long-format results table for Spec 06 to plot.

The tractability trick is the **saving(E_usable) surrogate**: the annual dispatch
outcome depends on the battery only through its usable energy
``E_usable = capacity_kwh * SOH * (soc_max - soc_min)`` and its (fixed) power, and
the derived in-dispatch penalty is independent of nominal capacity. So one curve
per (location, tariff, penalty, controller) legitimately serves every battery
size *and* every fade year. Run-to-fade is then evaluated by curve lookup rather
than by re-solving each year (reusing :func:`src.degradation.simulate_capacity_fade`).

This module is pure orchestration/logic: the dispatch itself (MILP solve or rules
simulation) is **dependency-injected** via a ``provider`` object, so the harness
is unit-testable without CVXPY (mirroring the Spec 02 pattern). ``scripts/run_sweep``
supplies the real provider.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol, Sequence

import numpy as np
import pandas as pd

from src.degradation import DegradationParams, simulate_capacity_fade
from src.economics import EconomicParams, NPVResult, compute_npv

# A dispatch closure: (capacity_kwh, soc_max) -> (saving, throughput_kwh, soc_series)
DispatchYear = Callable[[float, float], tuple[float, float, Sequence[float]]]


@dataclass(frozen=True)
class SweepGrid:
    """The parameter grid.

    ``deg_scenarios`` is the degradation axis: each entry is
    ``(penalty_spec, cycle_life_efc)`` where ``penalty_spec`` is the in-dispatch
    wear price (``None`` = derive it from *this* scenario's EFC and the battery
    cost, ``0.0`` = ignore wear in dispatch) and ``cycle_life_efc`` is the
    end-of-life equivalent-full-cycle count driving capacity fade / replacement.

    Defaults (grounded in the £890/kWh battery incl. inverter):
      * ``(0.0, 6000)``   — wear ignored in dispatch, but the pack still fades at
        6000 EFC (isolates the *value* of degradation-aware dispatch vs the next);
      * ``(None, 6000)``  — 7.42 p/kWh, internally consistent **pessimistic** case
        (the baseline);
      * ``(None, 8000)``  — 5.56 p/kWh, internally consistent **optimistic** case.
    """

    locations: tuple[str, ...] = ("inverness", "glasgow", "manchester", "plymouth")
    tariffs: tuple[str, ...] = ("flat", "e7", "agile")
    sizes_kwh: tuple[float, ...] = (0.0, 5.0, 10.0, 15.0)
    deg_scenarios: tuple[tuple[float | None, float], ...] = (
        (0.0, 6000.0), (None, 6000.0), (None, 8000.0)
    )
    controllers: tuple[str, ...] = ("milp", "self_consumption", "self_consumption_tou")
    power_kw: float = 3.0
    export: str = "matched"
    n_knots: int = 9


class SweepProvider(Protocol):
    """Injected dispatch/data provider (the runner supplies the real one)."""

    soc_min: float
    soc_max: float

    def resolve_penalty(self, penalty: float | None, cycle_life_efc: float) -> float: ...
    def dispatch_year(
        self, location: str, tariff: str, penalty: float, controller: str
    ) -> DispatchYear: ...
    def grid_only_flat_cost(self, location: str) -> float: ...
    def pv_only_saving(self, location: str, tariff: str) -> float: ...
    def capex(self, size_kwh: float) -> float: ...
    def replacement_capex(self, size_kwh: float) -> float: ...


@dataclass(frozen=True)
class SavingCurve:
    """Monotone lookup of annual outcomes vs usable energy (kWh).

    Linear interpolation (``np.interp``) is exact at the knots and preserves the
    monotonicity of the sampled saving. Queries outside the knot range are
    clamped (flat extrapolation), which never happens for the in-range fade
    trajectories but guards against tiny numerical overshoot.
    """

    e_usable: np.ndarray
    saving: np.ndarray
    throughput: np.ndarray
    mean_soc: np.ndarray

    def _at(self, values: np.ndarray, e: float) -> float:
        e_clamped = float(np.clip(e, self.e_usable[0], self.e_usable[-1]))
        return float(np.interp(e_clamped, self.e_usable, values))

    def saving_at(self, e_usable: float) -> float:
        return self._at(self.saving, e_usable)

    def throughput_at(self, e_usable: float) -> float:
        return self._at(self.throughput, e_usable)

    def mean_soc_at(self, e_usable: float) -> float:
        return self._at(self.mean_soc, e_usable)


def knot_grid(e_max: float, n_knots: int, e_lo: float = 0.5) -> np.ndarray:
    """Knot abscissae over ``[e_lo, e_max]``, denser at the low end.

    The low-end density matters because the NPV frontier turns over there (small
    packs), and the faded trajectories of every pack spend time at low usable
    energy.
    """
    if n_knots < 2:
        raise ValueError("n_knots must be >= 2.")
    lin = np.linspace(0.0, 1.0, n_knots)
    return e_lo + (e_max - e_lo) * (lin ** 1.5)


def build_saving_curve(
    dispatch_year: DispatchYear,
    e_usable_knots: np.ndarray,
    soc_window: float,
    soc_max: float,
) -> SavingCurve:
    """Sample ``dispatch_year`` at each usable-energy knot and build the curve.

    ``dispatch_year`` is called with the nominal capacity that yields the knot's
    usable energy at the reference SOC window (``capacity = E_usable / window``)
    and the reference ``soc_max``, and its full-SOH (fresh-battery)
    saving/throughput are recorded.
    """
    savings = np.empty(len(e_usable_knots))
    throughputs = np.empty(len(e_usable_knots))
    mean_socs = np.empty(len(e_usable_knots))
    for i, e in enumerate(e_usable_knots):
        capacity = e / soc_window
        saving, throughput, soc_series = dispatch_year(capacity, soc_max)
        savings[i] = saving
        throughputs[i] = throughput
        mean_socs[i] = float(np.mean(soc_series)) if len(soc_series) else np.nan
    return SavingCurve(
        e_usable=np.asarray(e_usable_knots, dtype=float),
        saving=savings,
        throughput=throughputs,
        mean_soc=mean_socs,
    )


def fade_npv(
    fade,
    econ: EconomicParams,
    warranty_years: float,
    replacement_capex: float,
) -> tuple[NPVResult, int, float]:
    """Run-to-fade NPV with realised life and warranty-residual credit.

    Replicates the ``scripts/run_stage2`` fade-NPV convention: value the NPV on
    the life the fade simulation actually realised (first replacement year minus
    one), and credit the unconsumed *warranty* value (straight-line) of whatever
    battery is in service at the horizon end.
    """
    horizon = econ.horizon_years
    if fade.replacement_years:
        realised_life = fade.replacement_years[0] - 1
    else:
        realised_life = horizon + 1  # survives the horizon; no replacement

    if fade.replacement_years and realised_life < horizon:
        k_last = (horizon - 1) // realised_life
        install_t = k_last * realised_life
    else:
        install_t = 0
    age_at_horizon = horizon - install_t
    remaining_frac = max(0.0, (warranty_years - age_at_horizon) / warranty_years)
    terminal_residual = replacement_capex * remaining_frac

    econ_fade = replace(econ, battery_life_years=realised_life)
    result = compute_npv(
        fade.saving_stream, econ_fade, terminal_residual_value=terminal_residual
    )
    return result, realised_life, terminal_residual


def _curve_cache_path(
    cache_dir: Path, location: str, tariff: str, controller: str,
    tag: str, power_kw: float,
) -> Path:
    name = f"{location}_{tariff}_{controller}_{tag}_pw{power_kw:g}.pkl"
    return cache_dir / name


def run_sweep(
    grid: SweepGrid,
    provider: SweepProvider,
    econ_base: EconomicParams,
    deg_base: DegradationParams,
    *,
    cache_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Run the sweep and return the tidy scenario table (§4.5 schema).

    One row per baseline scenario: size-0 PV-only reference rows (one per
    location x tariff), MILP battery rows crossed with the penalty axis, and
    rules-controller battery rows (penalty not crossed). Run-to-fade is the
    baseline for every battery row.
    """
    soc_window = provider.soc_max - provider.soc_min
    e_max = max(grid.sizes_kwh) * soc_window
    knots = knot_grid(e_max, grid.n_knots)
    sizes_pos = [s for s in grid.sizes_kwh if s > 0]

    cache_dir = Path(cache_dir) if cache_dir is not None else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    curve_mem: dict[tuple, SavingCurve] = {}

    def get_curve(location, tariff, controller, tag, penalty_value) -> SavingCurve:
        key = (location, tariff, controller, tag, grid.power_kw)
        if key in curve_mem:
            return curve_mem[key]
        if cache_dir is not None:
            path = _curve_cache_path(cache_dir, location, tariff, controller, tag, grid.power_kw)
            if path.exists():
                curve = pickle.loads(path.read_bytes())
                curve_mem[key] = curve
                return curve
        dyear = provider.dispatch_year(location, tariff, penalty_value, controller)
        curve = build_saving_curve(dyear, knots, soc_window, provider.soc_max)
        curve_mem[key] = curve
        if cache_dir is not None:
            _curve_cache_path(
                cache_dir, location, tariff, controller, tag, grid.power_kw
            ).write_bytes(pickle.dumps(curve))
        return curve

    rows: list[dict] = []

    for location in grid.locations:
        grid_only_flat = provider.grid_only_flat_cost(location)
        for tariff in grid.tariffs:
            # --- size-0 PV-only reference row -----------------------------------
            if 0.0 in grid.sizes_kwh:
                ref_saving = provider.pv_only_saving(location, tariff)
                ref_capex = provider.capex(0.0)
                econ_ref = replace(
                    econ_base, battery_capex=ref_capex, replacement_cost_factor=0.0
                )
                npv_ref = compute_npv(ref_saving, econ_ref)
                rows.append(
                    _row(
                        location, tariff, grid.export, 0.0, grid.power_kw, np.nan,
                        "none", econ_base, "reference",
                        operating=grid_only_flat - ref_saving,
                        grid_only_flat=grid_only_flat, saving_yr1=ref_saving,
                        npv=npv_ref.npv, bcr=npv_ref.bcr, realised_life=np.nan,
                        replacement_years="[]", min_soh=np.nan, min_saving=ref_saving,
                        efc_yr1=0.0, throughput_yr1=0.0, mean_soc=np.nan,
                        capex=ref_capex, cycle_life_efc=np.nan,
                    )
                )

            # --- battery rows ----------------------------------------------------
            for controller in grid.controllers:
                if controller == "milp":
                    scenarios = grid.deg_scenarios
                else:
                    # Rules controllers are penalty-free simulations; they are not
                    # crossed with the degradation axis and fade at the baseline EFC.
                    scenarios = ((None, deg_base.cycle_life_efc),)
                for penalty_spec, cycle_life_efc in scenarios:
                    if controller == "milp":
                        penalty_value = provider.resolve_penalty(penalty_spec, cycle_life_efc)
                        tag = f"{penalty_value:.4f}"
                        penalty_label = penalty_value
                    else:
                        penalty_value = 0.0
                        tag = "rules"
                        penalty_label = np.nan
                    curve = get_curve(location, tariff, controller, tag, penalty_value)
                    deg_scn = replace(deg_base, cycle_life_efc=cycle_life_efc)
                    for size in sizes_pos:
                        init_capex = provider.capex(size)
                        repl_capex = provider.replacement_capex(size)
                        econ = replace(
                            econ_base,
                            battery_capex=init_capex,
                            replacement_cost_factor=repl_capex / init_capex,
                        )

                        def curve_dispatch(capacity_kwh, soc_max, _curve=curve):
                            e = capacity_kwh * (soc_max - provider.soc_min)
                            return _curve.saving_at(e), _curve.throughput_at(e), ()

                        fade = simulate_capacity_fade(
                            dispatch_year=curve_dispatch,
                            capacity_kwh_nominal=size,
                            soc_max=provider.soc_max,
                            horizon_years=econ.horizon_years,
                            params=deg_scn,
                        )
                        npv_res, realised_life, _resid = fade_npv(
                            fade, econ, econ_base.battery_life_years, repl_capex
                        )

                        e_yr1 = size * soc_window
                        throughput_yr1 = curve.throughput_at(e_yr1)
                        efc_yr1 = throughput_yr1 / (2.0 * size)
                        saving_yr1 = fade.saving_stream[0]
                        rows.append(
                            _row(
                                location, tariff, grid.export, size, grid.power_kw,
                                penalty_label, controller, econ, "run-to-fade",
                                operating=grid_only_flat - saving_yr1,
                                grid_only_flat=grid_only_flat, saving_yr1=saving_yr1,
                                npv=npv_res.npv, bcr=npv_res.bcr,
                                realised_life=realised_life,
                                replacement_years=str(fade.replacement_years),
                                min_soh=min(fade.soh_trajectory),
                                min_saving=min(fade.saving_stream),
                                efc_yr1=efc_yr1, throughput_yr1=throughput_yr1,
                                mean_soc=curve.mean_soc_at(e_yr1), capex=init_capex,
                                cycle_life_efc=cycle_life_efc,
                            )
                        )

    return pd.DataFrame(rows)


def _row(
    location, tariff, export, battery_kwh, power_kw, deg_penalty, controller,
    econ, policy, *, operating, grid_only_flat, saving_yr1, npv, bcr,
    realised_life, replacement_years, min_soh, min_saving, efc_yr1,
    throughput_yr1, mean_soc, capex, cycle_life_efc,
) -> dict:
    return {
        "location": location,
        "tariff": tariff,
        "export": export,
        "battery_kwh": battery_kwh,
        "power_kw": power_kw,
        "deg_penalty_gbp_per_kwh": deg_penalty,
        "cycle_life_efc": cycle_life_efc,
        "controller": controller,
        "discount_rate": econ.discount_rate,
        "price_escalation": econ.price_escalation,
        "policy": policy,
        "operating_cost_gbp": operating,
        "grid_only_flat_cost_gbp": grid_only_flat,
        "annual_saving_yr1_gbp": saving_yr1,
        "npv_gbp": npv,
        "bcr": bcr,
        "realised_life_years": realised_life,
        "replacement_years": replacement_years,
        "min_soh": min_soh,
        "min_saving_gbp": min_saving,
        "efc_yr1": efc_yr1,
        "throughput_yr1_kwh": throughput_yr1,
        "mean_soc": mean_soc,
        "capex_gbp": capex,
    }
