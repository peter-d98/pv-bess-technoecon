"""Canonical definitions for reading assembled sweep tables into results form.

Every results table and figure loads its data through this module, so the two
quantities the results chapters rest on are defined exactly once:

    dnpv             NPV(PV + battery) - NPV(PV only) in the same cell.
                     The incremental test. Not "NPV > 0", which a large PV array
                     can carry on its own.
    breakeven_ratio  break-even battery price / observed band price. 1.00 is
                     break-even, so the ratio is directly comparable to a quote.

A *cell* is one (location, tariff, pv_kwp) combination: 18 per tariff, 54 in all.

Break-even needs both constant-price assemblies, not the band table. NPV is
linear in the battery price, but under run-to-fade the discounted replacement
outflows and the terminal residual credit scale with price too, so the price at
which the battery exactly repays itself cannot be recovered from the initial
capex of a single assembly. It is interpolated from the two runs instead.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.assemble_band_capex import band_price
from src.data_loader import load_all
from src.economics import EconomicParams, compute_npv
from src.locations import get_location, resolve_paths, resolve_pv_size_path
from src.tariffs import build_tariff

# Sizes reported in the results chapters. 0.5 kWh lies below the lowest published
# DESNZ band; 1 kWh sits at the band edge where a band median least represents a
# real installation, so it is shown only in the capex-specification chapter.
REPORTED_SIZES = (2.5, 5.0, 10.0)

# DESNZ 2025/26 inflation-adjusted median, 4-10 kW domestic band. Linear: the
# fitted fixed term is indistinguishable from zero.
PV_COST_PER_KWP = 1109.0

CELL = ["location", "tariff", "pv_kwp"]

# The two fixed-plus-variable battery capex specifications the results chapters
# compare against the adopted band schedule, as (fixed GBP, GBP/kWh). DESNZ is
# the low-F corner of the OLS readings of the published table; the retail list is
# an installer's published price table, fitted here rather than transcribed so
# the figures and tables regenerate from the quoted prices.
DESNZ_CAPEX = (2959.0, 475.0)
RETAIL_PRICE_LIST = ((2.6, 3000.0), (5.0, 4000.0), (9.5, 6250.0), (13.0, 8500.0))


def retail_capex() -> tuple[float, float]:
    """Fixed and variable terms of the retail price list, by least squares."""
    sizes, prices = zip(*RETAIL_PRICE_LIST)
    variable, fixed = np.polyfit(np.array(sizes), np.array(prices), 1)
    return float(fixed), float(variable)

# Columns identifying one scenario row across assemblies.
SCENARIO_KEY = [
    "location", "tariff", "export", "pv_kwp", "battery_kwh", "power_kw",
    "deg_penalty_gbp_per_kwh", "cycle_life_efc", "controller",
    "discount_rate", "price_escalation", "policy",
]

BAND_ASSEMBLY = Path("results/sweep_scenarios_v2_band.csv")
CONSTANT_PRICE_ASSEMBLIES = {1300.0: Path("results/_band_c1300.csv"),
                             890.0: Path("results/_band_c890.csv")}

# The sweep's own inputs, needed by the same-tariff baseline below. Mirrors the
# defaults in scripts/run_sweep.py.
DATA_DIR = Path("data")
DEMAND_PROFILE = DATA_DIR / "demand_halfhourly_2025.csv"
SWEEP_YEAR = 2025
HOURS_PER_STEP = 0.5

# PV-only economics as the sweep values them: no replacement, no fade, O&M a
# fixed share of capex. Asserted against the assembly's own NPV wherever used.
PV_DISCOUNT_RATE = 0.05
PV_PRICE_ESCALATION = 0.02
PV_HORIZON_YEARS = 20
PV_OM_FRAC = 0.01


def pv_economics(pv_kwp: float) -> EconomicParams:
    """Valuation parameters for a PV array with no battery."""
    capex = pv_kwp * PV_COST_PER_KWP
    return EconomicParams(
        battery_capex=capex, discount_rate=PV_DISCOUNT_RATE,
        horizon_years=PV_HORIZON_YEARS, price_escalation=PV_PRICE_ESCALATION,
        battery_life_years=10.0, replacement_cost_factor=0.0,
        om_cost_per_year=PV_OM_FRAC * capex,
    )


@lru_cache(maxsize=None)
def grid_only_cost(location: str, tariff: str) -> float:
    """Annual bill of a household with no PV and no battery, on one tariff.

    Every kWh of demand is imported at the prevailing half-hourly price, so this
    is a price-times-demand sum with no dispatch behind it -- the quantity
    `grid_only_cost` in scripts/run_stage2.py, evaluated on the sweep's own
    profiles and prices.
    """
    spec = get_location(location)
    _, agile_path = resolve_paths(spec, DATA_DIR)
    data = load_all(resolve_pv_size_path(spec, DATA_DIR, 4.0), DEMAND_PROFILE,
                    agile_path, year=SWEEP_YEAR)
    import_price, _ = build_tariff(tariff, data.index, spec.rates,
                                   agile_path=agile_path, export="matched")
    return float((data["demand_kw"] * import_price.to_numpy()).sum() * HOURS_PER_STEP)


def _attach_reference(raw: pd.DataFrame) -> pd.DataFrame:
    """Return battery rows carrying their cell's PV-only reference.

    The PV-only row itself is dropped, so `npv_ref` and `saving_ref` are the only
    route to it: a caller that filters this frame for `battery_kwh == 0` will find
    nothing rather than silently reading a battery row.
    """
    reference = (raw.loc[raw.battery_kwh == 0,
                         CELL + ["npv_gbp", "annual_saving_yr1_gbp"]]
                    .rename(columns={"npv_gbp": "npv_ref",
                                     "annual_saving_yr1_gbp": "saving_ref"}))
    if len(reference) != 54:
        raise ValueError(f"expected 54 PV-only reference rows, found {len(reference)}")

    battery = raw.loc[raw.battery_kwh > 0].merge(
        reference, on=CELL, how="left", validate="many_to_one")
    if battery.npv_ref.isna().any():
        raise ValueError("battery rows with no matching PV-only reference cell")

    battery = battery.copy()
    battery["dnpv"] = battery.npv_gbp - battery.npv_ref
    battery["dsaving_yr1"] = battery.annual_saving_yr1_gbp - battery.saving_ref
    return battery


def load_assembly(path: Path = BAND_ASSEMBLY,
                  sizes: tuple[float, ...] | None = REPORTED_SIZES) -> pd.DataFrame:
    """Load one assembly and attach `npv_ref`, `dnpv` and `battery_capex`.

    Pass `sizes=None` to keep every size in the table.
    """
    battery = _attach_reference(pd.read_csv(path))
    if sizes is not None:
        battery = battery.loc[battery.battery_kwh.isin(sizes)].copy()

    battery["battery_capex"] = battery.battery_kwh * battery.battery_kwh.map(band_price)

    # The assembly's own capex column must reconcile to band price x size plus
    # linear PV, or the derived battery capex above is measuring something else.
    implied = battery.capex_gbp - battery.pv_kwp * PV_COST_PER_KWP
    residual = (implied - battery.battery_capex).abs().max()
    if residual > 1e-6:
        raise ValueError(f"battery capex does not reconcile to band price x size (max {residual})")

    return battery


def load_pv_only(path: Path = BAND_ASSEMBLY, same_tariff: bool = False) -> pd.DataFrame:
    """The PV-only reference rows: one per cell, no battery.

    `load_assembly` drops these on purpose, so the PV-only results reach them
    through their own entry point rather than by filtering the raw table.

    `same_tariff=True` adds the columns re-valued against a grid-only household
    *on the row's own tariff* rather than on flat; see `_attach_same_tariff`.
    Off by default, so a caller that has not thought about the baseline keeps
    the sweep's own numbers rather than silently getting different ones.
    """
    pv_only = pd.read_csv(path)
    pv_only = pv_only.loc[pv_only.battery_kwh == 0].copy()
    if len(pv_only) != 54:
        raise ValueError(f"expected 54 PV-only rows, found {len(pv_only)}")

    residual = (pv_only.capex_gbp - pv_only.pv_kwp * PV_COST_PER_KWP).abs().max()
    if residual > 1e-6:
        raise ValueError(f"PV-only capex is not linear at {PV_COST_PER_KWP:g}/kWp "
                         f"(max {residual})")
    return _attach_same_tariff(pv_only) if same_tariff else pv_only


def _attach_same_tariff(pv_only: pd.DataFrame) -> pd.DataFrame:
    """Re-value PV-only rows against a same-tariff grid-only household.

    The sweep prices every saving against a *flat-tariff* grid-only baseline
    (`pv_only_saving` in scripts/run_sweep.py), so a row on Agile or Economy 7
    is credited -- or charged -- for the tariff switch as well as for the array.
    On this demand profile the switch alone is worth +112 to +231 GBP/yr on
    Agile and -170 to -204 GBP/yr on Economy 7, which is not an effect of
    installing anything. Netting it out is what makes paybacks and benefit-cost
    ratios comparable across tariffs.

    The correction is a constant per (location, tariff), so it shifts a cell's
    whole size curve without moving the NPV-maximising array. No re-solve is
    involved: demand is identical in every scenario and neither leg has PV.

    Adds `tariff_switch_gbp` (positive where the switch alone saves money) and
    the re-valued `saving_same_tariff`, `npv_same_tariff`, `bcr_same_tariff`
    and `payback_same_tariff`.
    """
    flat = pv_only.location.map(lambda name: grid_only_cost(name, "flat"))
    drift = (flat - pv_only.grid_only_flat_cost_gbp).abs().max()
    if drift > 0.01:
        raise ValueError(f"recomputed flat grid-only cost differs from the assembly's "
                         f"by {drift:.2f}; profiles or prices have drifted")

    own = [grid_only_cost(row.location, row.tariff) for row in pv_only.itertuples()]
    pv_only = pv_only.assign(tariff_switch_gbp=flat.to_numpy() - own)
    pv_only["saving_same_tariff"] = (pv_only.annual_saving_yr1_gbp
                                     - pv_only.tariff_switch_gbp)

    # The uncorrected valuation is reconstructed first and checked against the
    # assembly, so a drifted economic assumption fails loudly rather than
    # producing a plausible correction on top of the wrong base.
    for column, saving in (("", pv_only.annual_saving_yr1_gbp),
                           ("same_tariff", pv_only.saving_same_tariff)):
        valued = [compute_npv(value, pv_economics(kwp))
                  for value, kwp in zip(saving, pv_only.pv_kwp)]
        if not column:
            gap = max(abs(r.npv - npv) for r, npv in zip(valued, pv_only.npv_gbp))
            if gap > 0.01:
                raise ValueError(f"reconstructed PV-only NPV differs from the assembly's "
                                 f"by {gap:.2f}; economics have drifted")
            continue
        pv_only["npv_same_tariff"] = [r.npv for r in valued]
        pv_only["bcr_same_tariff"] = [r.bcr for r in valued]
        pv_only["payback_same_tariff"] = [r.discounted_payback_years for r in valued]
    return pv_only


def attach_system_npv(battery: pd.DataFrame, pv_only: pd.DataFrame) -> pd.DataFrame:
    """Attach the same-tariff PV-only level and the whole-system NPV on it.

    `dnpv` is a difference between two rows of the same cell, both priced against
    the same flat grid-only baseline, so the tariff-switch constant cancels out of
    it entirely. Re-valuing the whole system is therefore a shift of the level
    alone: `npv_same_tariff` replaces `npv_ref`, and the increment is untouched.

    `pv_only` must come from `load_pv_only(same_tariff=True)`. The uncorrected
    reference is checked against `npv_ref` on the way through, so a frame built
    from a different assembly fails here rather than shifting by the wrong amount.
    """
    reference = pv_only[CELL + ["npv_same_tariff", "npv_gbp"]].rename(
        columns={"npv_gbp": "npv_ref_check"})
    merged = battery.merge(reference, on=CELL, how="left", validate="many_to_one")
    if merged.npv_same_tariff.isna().any():
        raise ValueError("battery rows with no matching same-tariff reference cell")

    drift = (merged.npv_ref - merged.npv_ref_check).abs().max()
    if drift > 0.01:
        raise ValueError(f"PV-only reference differs between the two frames by "
                         f"{drift:.2f}; they do not come from the same assembly")

    merged["npv_system"] = merged.npv_same_tariff + merged.dnpv
    return merged.drop(columns="npv_ref_check")


def load_breakeven(sizes: tuple[float, ...] | None = REPORTED_SIZES,
                   src_dir: Path | None = None) -> pd.DataFrame:
    """Break-even battery price per scenario, interpolated between the two runs.

    Adds `breakeven_gbp_per_kwh` (the most a buyer could pay per kWh and not lose
    money) and `breakeven_ratio` (that price over the size's observed band price).

    `src_dir` reads the same pair of constant-price files from another directory,
    which is how an alternative discount rate is compared: the rate does not enter
    dispatch, so a reassembly of the same cache differs only in the economics.
    """
    high, low = 1300.0, 890.0
    assemblies = ({price: src_dir / path.name
                   for price, path in CONSTANT_PRICE_ASSEMBLIES.items()}
                  if src_dir is not None else CONSTANT_PRICE_ASSEMBLIES)
    frames = {price: _attach_reference(pd.read_csv(path))
              for price, path in assemblies.items()}

    merged = frames[high].merge(frames[low], on=SCENARIO_KEY, suffixes=("_hi", "_lo"),
                                validate="one_to_one")
    if len(merged) != len(frames[high]):
        raise ValueError("the two constant-price assemblies do not describe the same grid")

    # dnpv is linear in price; solve dnpv(P) = 0.
    slope = (merged.dnpv_lo - merged.dnpv_hi) / (low - high)
    merged["breakeven_gbp_per_kwh"] = high - merged.dnpv_hi / slope
    merged["breakeven_ratio"] = (merged.breakeven_gbp_per_kwh
                                 / merged.battery_kwh.map(band_price))

    if sizes is not None:
        merged = merged.loc[merged.battery_kwh.isin(sizes)].copy()
    return merged


def best_in(df: pd.DataFrame, group: list[str], by: str) -> pd.DataFrame:
    """One row per group: the maximum of `by`.

    `by` is required because the choice changes the answer. Maximising `npv_gbp`
    picks the best system a buyer could own; maximising `dnpv` picks where the
    battery does least damage; maximising `breakeven_ratio` picks the price the
    battery can best support. Under no-replacement the last two agree, because
    only the initial capex scales with price. Under run-to-fade they do not.
    """
    return df.loc[df.groupby(group, sort=False)[by].idxmax()]


def per_cell_optimum(df: pd.DataFrame, group: list[str], by: str) -> pd.DataFrame:
    """Optimise controller and wear penalty within each cell, then group.

    Every like-for-like comparison in the results does this first, so no cell is
    penalised for a controller or penalty choice made elsewhere.
    """
    cell_group = CELL + [c for c in group if c not in CELL]
    return best_in(df, cell_group, by=by)
