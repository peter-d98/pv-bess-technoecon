"""Parameter-sweep harness for the PV-BESS study (Spec 05).

Orchestrates the systematic sweep over location x tariff x PV size x battery
size x in-dispatch degradation penalty, plus a controller axis (MILP vs
rules-based), and emits a tidy long-format results table.

The tractability trick is the **saving(E_usable) surrogate**: the annual dispatch
outcome depends on the battery only through its usable energy
``E_usable = capacity_kwh * SOH * (soc_max - soc_min)`` and power. Spec 06 fixes
nameplate power at 0.5C of nominal capacity and holds it through fade, so each
nominal battery size has its own curve. Run-to-fade is evaluated by lookup on
that curve rather than by re-solving each year.

This module is pure orchestration/logic: the dispatch itself (MILP solve or rules
simulation) is **dependency-injected** via a ``provider`` object, so the harness
is unit-testable without CVXPY (mirroring the Spec 02 pattern). ``scripts/run_sweep``
supplies the real provider.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
import pandas as pd

from src.degradation import DegradationParams, simulate_capacity_fade
from src.economics import EconomicParams, NPVResult, compute_npv
from src.peaks import PEAK_THRESHOLDS_KW, peak_reduction_kw, threshold_key

# A dispatch closure: (capacity_kwh, soc_max) -> (saving, throughput_kwh, soc_series)
# The real provider returns a fourth element, the solved year's peak-import
# summary (Spec 06 §4.6); stub/curve closures may omit it.
DispatchYear = Callable[[float, float], tuple]


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

    locations: tuple[str, ...] = ("inverness", "manchester", "plymouth")
    tariffs: tuple[str, ...] = ("flat", "e7", "agile")
    pv_sizes_kwp: tuple[float, ...] = (2.0, 4.0, 5.0, 6.0)
    sizes_kwh: tuple[float, ...] = (0.0, 2.5, 5.0, 10.0)
    deg_scenarios: tuple[tuple[float | None, float], ...] = (
        (0.0, 6000.0),
        (0.01, 6000.0),
        (0.03, 6000.0),
        (0.05, 6000.0),
        (0.07, 6000.0),
    )
    controllers: tuple[str, ...] = ("milp", "self_consumption", "self_consumption_tou")
    c_rate: float = 0.5
    export: str = "matched"
    n_knots: int = 9
    # Lowest state of health each curve is solved down to, as a fraction of
    # nominal capacity. The no-replacement policy runs one battery for the whole
    # horizon and so queries well below the 0.60 replacement floor; solving to
    # this depth keeps every queried capacity inside the knot range instead of
    # flat-extrapolating below the lowest solved point (Spec 06 §4.5).
    min_soh_coverage: float = 0.15
    lifetime_policies: tuple[str, ...] = ("run-to-fade", "no-replacement")


class SweepProvider(Protocol):
    """Injected dispatch/data provider (the runner supplies the real one)."""

    soc_min: float
    soc_max: float

    def resolve_penalty(self, penalty: float | None, cycle_life_efc: float) -> float: ...
    def dispatch_year(
        self, location: str, tariff: str, pv_kwp: float, nominal_size_kwh: float,
        penalty: float, controller: str,
    ) -> DispatchYear: ...
    def grid_only_flat_cost(self, location: str) -> float: ...
    def pv_only_saving(self, location: str, tariff: str, pv_kwp: float) -> float: ...
    def pv_only_peaks(self, location: str, tariff: str, pv_kwp: float) -> dict: ...
    def capex(self, pv_kwp: float, size_kwh: float) -> float: ...
    def pv_om_cost(self, pv_kwp: float) -> float: ...
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
    # Per-knot grid-import peak summaries, aligned with ``e_usable``. Retained
    # (and cached) so deferred peak analysis needs no re-solve.
    peaks: tuple = ()

    @property
    def fresh_peaks(self) -> dict | None:
        """Peak summary at the highest (fresh-battery) knot."""
        return self.peaks[-1] if self.peaks else None

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
    peaks: list[dict | None] = []
    for i, e in enumerate(e_usable_knots):
        capacity = e / soc_window
        result = dispatch_year(capacity, soc_max)
        saving, throughput, soc_series = result[0], result[1], result[2]
        savings[i] = saving
        throughputs[i] = throughput
        mean_socs[i] = float(np.mean(soc_series)) if len(soc_series) else np.nan
        peaks.append(result[3] if len(result) > 3 else None)
    return SavingCurve(
        e_usable=np.asarray(e_usable_knots, dtype=float),
        saving=savings,
        throughput=throughputs,
        mean_soc=mean_socs,
        peaks=tuple(peaks),
    )


def fade_npv(
    fade,
    econ: EconomicParams,
    replacement_capex: float,
) -> tuple[NPVResult, int, float]:
    """Run-to-fade NPV with full replacements and realised-life residual.

    Value the NPV on the life the fade simulation actually realised (first
    replacement year minus one). A replacement is paid in full when installed;
    at the horizon, its unused life is credited straight-line using that same
    realised life. The original faded battery receives no residual if no
    replacement occurs within the horizon.
    """
    horizon = econ.horizon_years
    if fade.replacement_years:
        realised_life = fade.replacement_years[0] - 1
    else:
        realised_life = horizon + 1  # survives the horizon; no replacement

    terminal_residual = 0.0
    if fade.replacement_years and realised_life < horizon:
        install_t = ((horizon - 1) // realised_life) * realised_life
        age_at_horizon = horizon - install_t
        remaining_years = max(0, realised_life - age_at_horizon)
        terminal_residual = replacement_capex * remaining_years / realised_life

    econ_fade = replace(econ, battery_life_years=realised_life)
    result = compute_npv(
        fade.saving_stream,
        econ_fade,
        terminal_residual_value=terminal_residual,
    )
    return result, realised_life, terminal_residual


def no_replacement_npv(fade, econ: EconomicParams) -> NPVResult:
    """NPV of keeping the original battery for the entire horizon (Spec 06 §4.5).

    Nothing is bought after the initial system, so there is no replacement
    outflow; and the pack still in service at the horizon is past its assumed
    useful life, so it is credited no terminal residual. Operating below the
    0.60 SOH floor is an explicit sensitivity, not a warranty claim.
    """
    econ_nr = replace(
        econ,
        battery_life_years=float(econ.horizon_years),
        replacement_cost_factor=0.0,
        include_residual_value=False,
    )
    return compute_npv(fade.saving_stream, econ_nr)


def _validate_no_replacement(fade, scenario: str) -> float:
    """Reject a no-replacement trajectory that the fade law drives to SOH <= 0.

    The additive fade law was written alongside replacement, so over 20 years it
    can reach non-positive capacity. Clipping SOH or solving a battery with
    non-positive capacity would silently invent a result, so fail loudly instead
    (``soh_floor=0`` makes the fade simulator flag this as a replacement).
    """
    min_soh = float(min(fade.soh_trajectory))
    if fade.replacement_years or min_soh <= 0.0:
        raise ValueError(
            f"{scenario}: no-replacement trajectory reaches SOH <= 0 within the "
            f"horizon (min SOH {min_soh:.4f}). The additive fade law needs a "
            "physical lower bound before this scenario can be valued."
        )
    return min_soh


def _validate_knot_coverage(
    min_soh: float, min_soh_coverage: float, policy: str, scenario: str
) -> None:
    """Ensure every capacity the policy queries lies within the solved knots."""
    if min_soh < min_soh_coverage - 1e-9:
        raise ValueError(
            f"{scenario}: {policy} reaches SOH {min_soh:.4f}, below the solved "
            f"knot floor of {min_soh_coverage:.4f} SOH. Lower "
            "SweepGrid.min_soh_coverage and re-solve; the curve must not be "
            "flat-extrapolated below its lowest solved knot."
        )



def _curve_cache_path(
    cache_dir: Path, location: str, tariff: str, pv_kwp: float,
    nominal_size_kwh: float, controller: str, tag: str, power_kw: float,
) -> Path:
    name = (
        f"{location}_{tariff}_pv{pv_kwp:g}_bat{nominal_size_kwh:g}_"
        f"{controller}_{tag}_pw{power_kw:g}.pkl"
    )
    return cache_dir / name


def run_sweep(
    grid: SweepGrid,
    provider: SweepProvider,
    econ_base: EconomicParams,
    deg_base: DegradationParams,
    *,
    cache_dir: str | Path | None = None,
) -> "SweepResult":
    """Run the sweep and return the tidy scenario table plus retained peak events.

    Scenario rows: size-0 PV-only reference rows (one per location x tariff x PV
    size), MILP battery rows crossed with the penalty axis, and rules-controller
    battery rows (penalty not crossed). Every battery scenario is valued under
    **both** lifetime policies (run-to-fade-and-replace, no-replacement) from the
    same dispatch curves, so the second policy costs no extra MILP solves.
    """
    soc_window = provider.soc_max - provider.soc_min
    sizes_pos = [s for s in grid.sizes_kwh if s > 0]

    cache_dir = Path(cache_dir) if cache_dir is not None else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    curve_mem: dict[tuple, SavingCurve] = {}

    def get_curve(
        location, tariff, pv_kwp, nominal_size, controller, tag, penalty_value
    ) -> SavingCurve:
        power_kw = grid.c_rate * nominal_size
        key = (location, tariff, pv_kwp, nominal_size, controller, tag, power_kw)
        if key in curve_mem:
            return curve_mem[key]
        if cache_dir is not None:
            path = _curve_cache_path(
                cache_dir, location, tariff, pv_kwp, nominal_size,
                controller, tag, power_kw,
            )
            if path.exists():
                curve = pickle.loads(path.read_bytes())
                curve_mem[key] = curve
                return curve
        dyear = provider.dispatch_year(
            location, tariff, pv_kwp, nominal_size, penalty_value, controller
        )
        e_max = nominal_size * soc_window
        knots = knot_grid(e_max, grid.n_knots, e_lo=e_max * grid.min_soh_coverage)
        curve = build_saving_curve(dyear, knots, soc_window, provider.soc_max)
        curve_mem[key] = curve
        if cache_dir is not None:
            _curve_cache_path(
                cache_dir, location, tariff, pv_kwp, nominal_size,
                controller, tag, power_kw,
            ).write_bytes(pickle.dumps(curve))
        return curve

    rows: list[dict] = []
    peak_rows: list[dict] = []

    for location in grid.locations:
        grid_only_flat = provider.grid_only_flat_cost(location)
        for tariff in grid.tariffs:
            for pv_kwp in grid.pv_sizes_kwp:
                om_cost = provider.pv_om_cost(pv_kwp)
                # Matching no-battery case for peak reduction: same location,
                # tariff, PV size, and demand (Spec 06 §4.6).
                ref_peaks = provider.pv_only_peaks(location, tariff, pv_kwp)
                # --- size-0 PV-only reference row -------------------------------
                if 0.0 in grid.sizes_kwh:
                    ref_saving = provider.pv_only_saving(location, tariff, pv_kwp)
                    ref_capex = provider.capex(pv_kwp, 0.0)
                    econ_ref = replace(
                        econ_base, battery_capex=ref_capex,
                        replacement_cost_factor=0.0, om_cost_per_year=om_cost,
                    )
                    npv_ref = compute_npv(ref_saving, econ_ref)
                    rows.append(
                        _row(
                            location, tariff, grid.export, pv_kwp, 0.0, 0.0,
                            np.nan, "none", econ_ref, "reference",
                            operating=grid_only_flat - ref_saving,
                            grid_only_flat=grid_only_flat, saving_yr1=ref_saving,
                            npv=npv_ref.npv, bcr=npv_ref.bcr,
                            realised_life=np.nan, replacement_years="[]",
                            min_soh=np.nan, min_saving=ref_saving, efc_yr1=0.0,
                            throughput_yr1=0.0, mean_soc=np.nan,
                            capex=ref_capex, cycle_life_efc=np.nan,
                            peaks=ref_peaks,
                            peak_reduction=peak_reduction_kw(ref_peaks, ref_peaks),
                        )
                    )
                    peak_rows.extend(
                        _peak_event_rows(
                            location, tariff, pv_kwp, 0.0, 0.0, np.nan, "none",
                            ref_peaks,
                        )
                    )

                # --- battery rows ------------------------------------------------
                for controller in grid.controllers:
                    scenarios = (
                        grid.deg_scenarios
                        if controller == "milp"
                        else ((None, deg_base.cycle_life_efc),)
                    )
                    for penalty_spec, cycle_life_efc in scenarios:
                        if controller == "milp":
                            penalty_value = provider.resolve_penalty(
                                penalty_spec, cycle_life_efc
                            )
                            tag = f"{penalty_value:.4f}"
                            penalty_label = penalty_value
                        else:
                            penalty_value = 0.0
                            tag = "rules"
                            penalty_label = np.nan
                        deg_scn = replace(
                            deg_base, cycle_life_efc=cycle_life_efc
                        )
                        for size in sizes_pos:
                            power_kw = grid.c_rate * size
                            curve = get_curve(
                                location, tariff, pv_kwp, size, controller,
                                tag, penalty_value,
                            )
                            init_capex = provider.capex(pv_kwp, size)
                            repl_capex = provider.replacement_capex(size)
                            econ = replace(
                                econ_base,
                                battery_capex=init_capex,
                                replacement_cost_factor=repl_capex / init_capex,
                                om_cost_per_year=om_cost,
                            )
                            scenario = (
                                f"{location}/{tariff}/{pv_kwp:g}kWp/{size:g}kWh/"
                                f"{controller}/{tag}"
                            )

                            def curve_dispatch(capacity_kwh, soc_max, _curve=curve):
                                e = capacity_kwh * (soc_max - provider.soc_min)
                                return _curve.saving_at(e), _curve.throughput_at(e), ()

                            def simulate(params):
                                return simulate_capacity_fade(
                                    dispatch_year=curve_dispatch,
                                    capacity_kwh_nominal=size,
                                    soc_max=provider.soc_max,
                                    horizon_years=econ.horizon_years,
                                    params=params,
                                )

                            e_yr1 = size * soc_window
                            throughput_yr1 = curve.throughput_at(e_yr1)
                            efc_yr1 = throughput_yr1 / (2.0 * size)
                            peaks = curve.fresh_peaks
                            reduction = peak_reduction_kw(ref_peaks, peaks)

                            def emit(policy, npv, bcr, fade, realised_life):
                                rows.append(
                                    _row(
                                        location, tariff, grid.export, pv_kwp,
                                        size, power_kw, penalty_label, controller,
                                        econ, policy,
                                        operating=(
                                            grid_only_flat - fade.saving_stream[0]
                                        ),
                                        grid_only_flat=grid_only_flat,
                                        saving_yr1=fade.saving_stream[0],
                                        npv=npv, bcr=bcr,
                                        realised_life=realised_life,
                                        replacement_years=str(
                                            fade.replacement_years
                                        ),
                                        min_soh=min(fade.soh_trajectory),
                                        min_saving=min(fade.saving_stream),
                                        efc_yr1=efc_yr1,
                                        throughput_yr1=throughput_yr1,
                                        mean_soc=curve.mean_soc_at(e_yr1),
                                        capex=init_capex,
                                        cycle_life_efc=cycle_life_efc,
                                        peaks=peaks, peak_reduction=reduction,
                                    )
                                )

                            if "run-to-fade" in grid.lifetime_policies:
                                fade = simulate(deg_scn)
                                _validate_knot_coverage(
                                    min(fade.soh_trajectory),
                                    grid.min_soh_coverage,
                                    "run-to-fade", scenario,
                                )
                                npv_res, realised_life, _residual = fade_npv(
                                    fade, econ, repl_capex
                                )
                                emit(
                                    "run-to-fade", npv_res.npv, npv_res.bcr,
                                    fade, realised_life,
                                )

                            if "no-replacement" in grid.lifetime_policies:
                                # soh_floor=0 never triggers a replacement for a
                                # positive-SOH trajectory, so SOH, EFC, and
                                # calendar age are never reset.
                                fade_nr = simulate(
                                    replace(
                                        deg_scn, replace_at_eol=False,
                                        soh_floor=0.0,
                                    )
                                )
                                min_soh_nr = _validate_no_replacement(
                                    fade_nr, scenario
                                )
                                _validate_knot_coverage(
                                    min_soh_nr, grid.min_soh_coverage,
                                    "no-replacement", scenario,
                                )
                                npv_nr = no_replacement_npv(fade_nr, econ)
                                emit(
                                    "no-replacement", npv_nr.npv, npv_nr.bcr,
                                    fade_nr, np.nan,
                                )

                            peak_rows.extend(
                                _peak_event_rows(
                                    location, tariff, pv_kwp, size, power_kw,
                                    penalty_label, controller, peaks,
                                )
                            )

    return SweepResult(
        scenarios=pd.DataFrame(rows), peak_events=pd.DataFrame(peak_rows)
    )


@dataclass(frozen=True)
class SweepResult:
    """The sweep's two artefacts: the scenario table and retained peak events."""

    scenarios: pd.DataFrame
    peak_events: pd.DataFrame


def _peak_columns(peaks: dict | None, peak_reduction: float) -> dict:
    """Peak summary columns, blank when the provider retained no schedule."""
    columns: dict = {
        "peak_import_kw": np.nan,
        "peak_import_time": "",
        "p95_import_kw": np.nan,
        "p99_import_kw": np.nan,
    }
    for threshold in PEAK_THRESHOLDS_KW:
        columns[threshold_key(threshold)] = np.nan
    if peaks:
        for name in list(columns):
            if name in peaks:
                columns[name] = peaks[name]
    columns["peak_reduction_kw"] = peak_reduction
    return columns


def _peak_event_rows(
    location, tariff, pv_kwp, battery_kwh, power_kw, deg_penalty, controller,
    peaks: dict | None,
) -> list[dict]:
    """Long-format top grid-import half-hours, keyed by scenario."""
    if not peaks:
        return []
    key = {
        "location": location,
        "tariff": tariff,
        "pv_kwp": pv_kwp,
        "battery_kwh": battery_kwh,
        "power_kw": power_kw,
        "deg_penalty_gbp_per_kwh": deg_penalty,
        "controller": controller,
    }
    return [{**key, **event} for event in peaks["top_events"]]


def _row(
    location, tariff, export, pv_kwp, battery_kwh, power_kw, deg_penalty, controller,
    econ, policy, *, operating, grid_only_flat, saving_yr1, npv, bcr,
    realised_life, replacement_years, min_soh, min_saving, efc_yr1,
    throughput_yr1, mean_soc, capex, cycle_life_efc, peaks=None,
    peak_reduction=np.nan,
) -> dict:
    row = {
        "location": location,
        "tariff": tariff,
        "export": export,
        "pv_kwp": pv_kwp,
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
    row.update(_peak_columns(peaks, peak_reduction))
    return row
