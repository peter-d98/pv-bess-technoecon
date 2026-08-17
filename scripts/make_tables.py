"""Generate the dissertation results tables as LaTeX fragments.

Each table is a small function registered against a chapter, so tables can be
built a chapter at a time while the whole set stays regenerable from one
command when an assembly is re-run:

    python scripts/make_tables.py --list
    python scripts/make_tables.py --chapter 1
    python scripts/make_tables.py --table t1.2
    python scripts/make_tables.py --all

Every emitted .tex carries a provenance header (source file, its sha256, the
selection rule, the size restriction) and every run rewrites MANIFEST.tsv, so a
number in the document can always be traced back to the assembly it came from.

Fragments are meant to be \\input into the document, not compiled alone. They
require booktabs, siunitx and, for heatmapped tables, xcolor with the table
option (or colortbl).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

# Allow running as a plain script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.assemble_band_capex import band_price
from src.economics import EconomicParams, compute_npv
from src.results_data import (CELL, CONSTANT_PRICE_ASSEMBLIES, DESNZ_CAPEX,
                              PV_COST_PER_KWP, REPORTED_SIZES, best_in, load_assembly,
                              load_breakeven, load_pv_only, per_cell_optimum,
                              retail_capex)

TARIFF_LABEL = {"flat": "Flat", "e7": "Economy 7", "agile": "Agile"}
# `run-to-fade` stays the key everywhere on disk; only the display label changes.
POLICY_LABEL = {"no-replacement": "No replacement",
                "run-to-fade": r"Run to SOH$_{\min}$"}
# Same labels for use mid-sentence. Kept separate rather than lower-cased from
# POLICY_LABEL, which would corrupt the SOH acronym and the math subscript.
POLICY_LABEL_INLINE = {"no-replacement": "no replacement",
                       "run-to-fade": r"run to SOH$_{\min}$"}
CONTROLLER_LABEL = {"milp": "MILP", "self_consumption": "SC",
                    "self_consumption_tou": "SC (ToU)"}
TARIFF_ORDER = ["flat", "e7", "agile"]
LOCATION_ORDER = ["inverness", "manchester", "plymouth"]
POLICY_ORDER = ["no-replacement", "run-to-fade"]

# The Chapter 1 case study. Chosen for market representativeness, not because it
# is favourable: 5 kWh is the weakest of the three reported sizes on break-even
# ratio and Manchester the weakest of the three locations. The most favourable
# configuration in the study is shown in t1.2.
CASE = {"location": "manchester", "pv_kwp": 4.0, "battery_kwh": 5.0}

# Emitted where a value needs the dispatch re-solve that is not on disk yet.
# Deliberately conspicuous: a stray "--" reads as a legitimate empty cell.
PLACEHOLDER = "??"

# Single-hue sequential ramp anchored on the project's Okabe-Ito blue. Sequential,
# not diverging: no value in the study reaches 1.00, so a diverging scale would
# spend half its range on an empty region and put its neutral point at the edge
# of the data. The unused dark end is the finding.
RAMP = ((0.0, (0xFF, 0xFF, 0xFF)), (0.55, (0x00, 0x72, 0xB2)), (1.0, (0x06, 0x2B, 0x42)))

# WCAG relative luminance at which white and black text reach equal contrast
# against the fill. Below it, white is the more legible numeral.
LUMINANCE_CROSSOVER = 0.179

# siunitx column specifications. Rounding is fixed on the column, not on the
# table, so a document-level \sisetup cannot reformat these numbers.
MONEY_COLUMN = "S[table-format=-4.0, round-mode=places, round-precision=0]"
SIZE_COLUMN = "S[table-format=2.1, round-mode=places, round-precision=1]"


@dataclass
class TableSpec:
    """A built table, ready to render."""
    frame: pd.DataFrame
    caption: str
    label: str
    column_format: str
    selection: str
    formats: dict[str, str] = field(default_factory=dict)
    heatmap: dict[str, tuple[float, float]] = field(default_factory=dict)
    note: str | None = None
    sources: list[Path] = field(default_factory=list)
    # LaTeX header text per column, where the column name is not what should be
    # printed (multi-line headers, symbols).
    headers: dict[str, str] = field(default_factory=dict)
    # Emitted after the row at each index, to separate blocks.
    row_rules: dict[int, str] = field(default_factory=dict)
    # Emitted inside the table environment, before the tabular.
    preamble: list[str] = field(default_factory=list)
    top_rule: bool = True
    # Raw LaTeX rows emitted between the top rule and the generated column
    # header, for headers that span columns and so cannot come from `headers`.
    header_rows: list[str] = field(default_factory=list)
    # Battery sizes covered, for the provenance header. None for a table that
    # has no battery in it, where the reported sizes would be misleading.
    sizes: tuple[float, ...] | None = REPORTED_SIZES


REGISTRY: dict[str, tuple[int, str, Callable[[], TableSpec]]] = {}


def table(table_id: str, chapter: int, description: str):
    def register(fn: Callable[[], TableSpec]):
        REGISTRY[table_id] = (chapter, description, fn)
        return fn
    return register


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _ramp_colour(fraction: float) -> tuple[int, int, int]:
    fraction = min(max(fraction, 0.0), 1.0)
    for (lo, c_lo), (hi, c_hi) in zip(RAMP, RAMP[1:]):
        if fraction <= hi:
            t = 0.0 if hi == lo else (fraction - lo) / (hi - lo)
            return tuple(round(a + t * (b - a)) for a, b in zip(c_lo, c_hi))
    return RAMP[-1][1]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _cell_colour_prefix(value: float, vmin: float, vmax: float) -> str:
    fraction = (value - vmin) / (vmax - vmin) if vmax > vmin else 0.0
    rgb = _ramp_colour(fraction)
    prefix = f"\\cellcolor[HTML]{{{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}}}"
    if _relative_luminance(rgb) < LUMINANCE_CROSSOVER:
        prefix += "\\color{white}"
    return prefix


def render(spec: TableSpec, table_id: str, source_hashes: list[str]) -> str:
    # Columns suffixed __sort carry the numeric value behind a formatted string
    # cell so it can be shaded; they are not themselves rendered.
    columns = [c for c in spec.frame.columns if not c.endswith("__sort")]
    body = []
    for position, (_, row) in enumerate(spec.frame.iterrows()):
        cells = []
        for column in columns:
            value = row[column]
            if column in spec.heatmap:
                vmin, vmax = spec.heatmap[column]
                key = row.get(column + "__sort", value)
                text = spec.formats.get(column, "{}").format(value)
                cells.append(_cell_colour_prefix(float(key), vmin, vmax) + " " + text)
            elif pd.isna(value):
                cells.append("{--}")
            else:
                cells.append(spec.formats.get(column, "{}").format(value))
        body.append("  " + " & ".join(cells) + r" \\")
        if position in spec.row_rules:
            body.append("  " + spec.row_rules[position])

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = [f"% {table_id} -- generated by scripts/make_tables.py on {generated}"]
    header += [f"% source: {path} (sha256 {digest})"
               for path, digest in zip(spec.sources, source_hashes)]
    header += [f"% selection: {spec.selection}"]
    if spec.sizes is not None:
        header += [f"% sizes: {', '.join(f'{s:g}' for s in spec.sizes)} kWh"]
    header += ["% Do not edit by hand; regenerate instead."]

    head = ([r"    \toprule"] if spec.top_rule else [])
    head += [f"    {line}" for line in spec.header_rows]
    head += ["    " + " & ".join(f"{{{spec.headers.get(c, c)}}}" for c in columns) + r" \\",
             r"    \midrule"]
    note = f"  \\par\\vspace{{2pt}}\\footnotesize {spec.note}" if spec.note else None

    lines = header + [
        r"\begin{table}[htbp]",
        r"  \centering",
        f"  \\caption{{{spec.caption}}}",
        f"  \\label{{{spec.label}}}",
        *[f"  {line}" for line in spec.preamble],
        f"  \\begin{{tabular}}{{{spec.column_format}}}",
        *head,
        *["  " + line for line in body],
        r"    \bottomrule",
        r"  \end{tabular}",
    ]
    if note:
        lines.append(note)
    lines.append(r"\end{table}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Chapter 1
# --------------------------------------------------------------------------- #

def _blank_repeats(frame: pd.DataFrame, column: str, every: int) -> None:
    """Show a label only on the first row of its block; repeats are noise."""
    frame.loc[frame.index % every != 0, column] = ""


def _figure(value: float, places: int) -> str:
    """A number at a fixed number of decimal places.

    Rounding is pinned in the `\\num` options as well as in the string, so a
    document-level `\\sisetup{round-precision=2}` cannot reformat these cells --
    the same protection the S columns of t1.1 and t1.2 get from their column
    specification. siunitx also renders the grouping separator and a true minus
    sign, which a plain column would emit as a hyphen.
    """
    return (rf"\num[round-mode=places, round-precision={places}]"
            rf"{{{value:.{places}f}}}")


def _money(value: float) -> str:
    """Costs to the nearest pound, at every magnitude."""
    return _figure(value, places=0)


def _pct(value: float) -> str:
    """Percentages to one decimal place."""
    return _figure(value, places=1)


def _prose_money(value: float) -> str:
    """A pound figure for a caption or note, where no S column is doing the work.

    Braces the thousands separator so LaTeX keeps it upright, and sets the minus
    in maths mode rather than emitting a hyphen.
    """
    sign = "$-$" if value < 0 else ""
    return rf"{sign}\pounds{abs(value):,.0f}".replace(",", "{,}")


def _pv_only_payback(pv_kwp: float, saving: float, npv: float) -> str:
    """Discounted payback of the PV array alone.

    Recomputed rather than read: the sweep retains NPV and BCR but not payback.
    The economics are reconstructed from the sweep's own defaults and checked
    against the assembly's stored NPV, so a drifted assumption fails loudly
    instead of printing a plausible wrong number.
    """
    capex = pv_kwp * PV_COST_PER_KWP
    econ = EconomicParams(
        battery_capex=capex, discount_rate=0.05, horizon_years=20,
        price_escalation=0.02, battery_life_years=10.0,
        replacement_cost_factor=0.0, om_cost_per_year=0.01 * capex,
    )
    result = compute_npv(saving, econ)
    if abs(result.npv - npv) > 0.01:
        raise ValueError(f"reconstructed PV-only NPV {result.npv:.2f} does not match "
                         f"the assembly's {npv:.2f}; economics have drifted")
    years = result.discounted_payback_years
    return "Never" if not np.isfinite(years) else _figure(years, places=0)


def _case_energy() -> pd.DataFrame | None:
    """Annual energy balance for the case study, if it has been solved.

    Written by `scripts/case_study_energy.py`. The sweep retains economics and
    peak summaries but not schedules, so these rows cannot come from an assembly.
    """
    path = Path("results/case_study_energy.csv")
    return pd.read_csv(path) if path.exists() else None


def _energy_row(energy: pd.DataFrame | None, column: str, tariff: str,
                policy: str, places: int) -> str:
    """One cell of the energy block, or a placeholder if the solve is missing."""
    if energy is None:
        return PLACEHOLDER
    match = energy[(energy.tariff == tariff) & energy.policy.isin([policy, "both"])]
    if len(match) != 1:
        return PLACEHOLDER
    return _figure(match.iloc[0][column], places=places)


def _breakeven_for(breakeven: pd.DataFrame, row: pd.Series, tariff: str) -> float:
    """Break-even price of one specific configuration.

    Matched on the configuration already chosen by `dnpv` rather than optimised
    again on `breakeven_ratio`: under run-to-fade those pick different rows, and
    a table that mixed them would report a price for a system it never costed.
    """
    penalty = row.deg_penalty_gbp_per_kwh
    match = breakeven[
        (breakeven.location == row.location) & (breakeven.tariff == tariff)
        & (breakeven.pv_kwp == row.pv_kwp) & (breakeven.battery_kwh == row.battery_kwh)
        & (breakeven.controller == row.controller) & (breakeven.policy == row.policy)
        & (breakeven.deg_penalty_gbp_per_kwh.isna() if pd.isna(penalty)
           else breakeven.deg_penalty_gbp_per_kwh == penalty)]
    if len(match) != 1:
        raise ValueError(f"expected one break-even row for {tariff}/{row.policy}, "
                         f"got {len(match)}")
    return float(match.iloc[0].breakeven_gbp_per_kwh)


@table("t1.a", chapter=1, description="Whole-system viability across the 54 cases")
def build_t1_a() -> TableSpec:
    """The section-opening table, companion to figure f1.a.

    Two questions that are easy to conflate get their own columns. *NPV > 0*
    asks whether the system as a whole repays itself, which a large array can
    carry on its own; *battery worth adding* is the incremental test against the
    PV-only configuration in the same case. They do not agree, and the gap
    between 22 and 0 at 5 kWh is the point of the table.

    "Case" rather than "cell" throughout: in a battery study a cell is a
    component, and the two senses collide badly in the same sentence.
    """
    source = Path("results/sweep_scenarios_v2_band.csv")
    pv_only = load_pv_only(source)
    optimum = per_cell_optimum(load_assembly(source), ["policy", "battery_kwh"], by="dnpv")

    cases = len(pv_only)
    rows = [{"Policy": "", "System": "PV only",
             "Mean": pv_only.npv_gbp.mean(), "Best": pv_only.npv_gbp.max(),
             "Worst": pv_only.npv_gbp.min(),
             "Positive": (pv_only.npv_gbp > 0).sum(), "Worth adding": np.nan}]
    for policy in POLICY_ORDER:
        for size in REPORTED_SIZES:
            group = optimum[(optimum.policy == policy) & (optimum.battery_kwh == size)]
            if len(group) != cases:
                raise ValueError(f"expected {cases} cases for {policy}/{size:g} kWh, "
                                 f"got {len(group)}")
            rows.append({
                "Policy": POLICY_LABEL[policy],
                "System": rf"PV $+$ {size:g}\,kWh",
                "Mean": group.npv_gbp.mean(), "Best": group.npv_gbp.max(),
                "Worst": group.npv_gbp.min(),
                "Positive": (group.npv_gbp > 0).sum(),
                "Worth adding": (group.dnpv > 0).sum(),
            })

    frame = pd.DataFrame(rows)
    # Not _blank_repeats: its fixed stride assumes equal blocks, and the PV-only
    # reference row ahead of the two policy blocks offsets them by one.
    frame.loc[~frame.index.isin([1, 1 + len(REPORTED_SIZES)]), "Policy"] = ""

    count_column = "S[table-format=2.0, round-mode=places, round-precision=0]"
    # Read from the assembly rather than back off the rendered frame, whose
    # Policy column has just been blanked for display.
    crossover = optimum[(optimum.policy == "no-replacement")
                        & (optimum.battery_kwh == 5.0)]
    crossover = int((crossover.npv_gbp > 0).sum())
    return TableSpec(
        frame=frame,
        caption=(r"Whole-system viability across the "
                 rf"{cases} cases of the study (3 locations $\times$ 3 tariffs $\times$ "
                 r"6 array sizes), with controller and degradation penalty optimised within "
                 r"every case. \emph{NPV} is that of the whole system, PV included; "
                 r"\emph{positive} counts the cases repaying the whole investment, and "
                 r"\emph{worth adding} the cases in which the battery improves on the "
                 r"PV-only configuration in the same case. The two are different tests and "
                 r"give different answers."),
        label="tab:system-viability",
        column_format="ll" + MONEY_COLUMN * 3 + count_column * 2,
        selection=("all 54 cases at each battery size and lifetime policy; controller and "
                   "penalty optimised within each case by dnpv"),
        formats={"Mean": "{:.0f}", "Best": "{:.0f}", "Worst": "{:.0f}",
                 "Positive": "{:.0f}", "Worth adding": "{:.0f}"},
        headers={column: rf"\textbf{{{column}}}" for column in ("Policy", "System")}
                | {"Mean": r"\textbf{\shortstack{Mean\\NPV}}",
                   "Best": r"\textbf{\shortstack{Best\\case}}",
                   "Worst": r"\textbf{\shortstack{Worst\\case}}",
                   "Positive": rf"\textbf{{\shortstack{{NPV $>0$\\(of {cases})}}}}",
                   "Worth adding": rf"\textbf{{\shortstack{{Battery worth\\adding (of {cases})}}}}"},
        row_rules={0: r"\midrule", 3: r"\midrule"},
        top_rule=False,
        # \footnotesize left commented so the size is a one-character toggle.
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"All figures in \pounds. The PV array alone repays itself in "
              rf"{(pv_only.npv_gbp > 0).sum()} of {cases} cases; adding a 5\,kWh battery --- "
              rf"the size closest to what the domestic market installs --- leaves "
              rf"{crossover} of {cases} positive under the lifetime policy that "
              r"most favours the battery, and the mean system turns negative. The final "
              r"column is zero throughout: no battery anywhere in the study improves on its "
              r"own PV-only reference, at any size, under either policy. A positive whole-"
              r"system NPV is therefore never evidence for the battery --- it is the array "
              r"carrying a loss-making addition. The two tests are separated here and "
              r"reported apart for the rest of the chapter, the array priced on its own "
              r"and the battery on the increment."),
        sources=[source],
    )


@table("t1.0a", chapter=1, description="Viability of the PV array alone")
def build_t1_0a() -> TableSpec:
    source = Path("results/sweep_scenarios_v2_band.csv")
    # Valued on the sweep's own baseline, a grid-only household on the flat
    # tariff: that is the alternative a buyer faces, holding no hardware and on
    # a single rate. `same_tariff=True` is asked for only to carry the switch
    # term, which is reported in its own column rather than netted out.
    pv_only = load_pv_only(source, same_tariff=True)

    # One optimum per row, on NPV: the array a buyer would install. BCR is
    # reported at that same array, so the row describes a single system.
    rows = []
    for location in LOCATION_ORDER:
        for tariff in TARIFF_ORDER:
            group = pv_only[(pv_only.location == location) & (pv_only.tariff == tariff)]
            if len(group) != 6:
                raise ValueError(f"expected 6 array sizes for {location}/{tariff}, "
                                 f"got {len(group)}")
            best_npv = group.loc[group.npv_gbp.idxmax()]
            rows.append({
                "Location": location.capitalize(),
                "Tariff": TARIFF_LABEL[tariff],
                "Array": _figure(best_npv.pv_kwp, places=0),
                "NPV": _money(best_npv.npv_gbp),
                # In brackets: the same saving with the tariff switch netted out,
                # which is what the array earns on its own. Identical on flat.
                "Saving": (f"{_money(best_npv.annual_saving_yr1_gbp)} "
                           f"({_money(best_npv.saving_same_tariff)})"),
                "Payback": _pv_only_payback(best_npv.pv_kwp,
                                            best_npv.annual_saving_yr1_gbp,
                                            best_npv.npv_gbp),
                "BCR": _figure(best_npv.bcr, places=2),
            })

    frame = pd.DataFrame(rows)
    _blank_repeats(frame, "Location", every=3)

    below = pv_only[pv_only.bcr < 1.0]
    failing = " and ".join(f"{r.location.capitalize()} ({_prose_money(r.npv_gbp)})"
                           for r in below.itertuples())
    # Read from the same source the referenced table uses rather than restated,
    # so the two can never disagree about the study's best battery.
    best_battery = load_breakeven().breakeven_ratio.max()
    top_ratio = pv_only.bcr.max()
    # What the same-tariff re-valuation does to the count, quoted in the note as
    # the size of the baseline's influence rather than as a second set of rows.
    clears_same_tariff = int((pv_only.bcr_same_tariff >= 1.0).sum())

    return TableSpec(
        frame=frame,
        caption=(r"Viability of the PV array alone, before any battery. Each row reports the "
                 r"array size maximising PV-only NPV out of the six swept (1--6\,kWp) "
                 r"at \pounds1{,}109/kWp, and that array's own economics. Savings are "
                 r"measured against the study's counterfactual throughout: a household with "
                 r"no hardware on the flat tariff. The bracketed year-1 saving nets out the "
                 r"part of it that household would obtain by moving tariff alone, buying "
                 r"nothing, and so is what the array earns in its own right; the two are "
                 r"identical on flat by construction. A benefit-cost ratio "
                 r"of 1.00 is break-even, so every entry above it is an array that repays "
                 r"itself over the 20-year horizon."),
        label="tab:pv-only-viability",
        column_format="ll" + "r" * 5,
        selection=("per location and tariff, the array size maximising npv_gbp among the six "
                   "swept, reported at that size"),
        formats={column: "{}" for column in frame.columns},
        headers={column: rf"\textbf{{{column}}}" for column in ("Location", "Tariff", "BCR")}
                | {"Array": r"\textbf{\shortstack{Array\\(kWp)}}",
                   "NPV": r"\textbf{\shortstack{NPV\\(\pounds)}}",
                   "Saving": r"\textbf{\shortstack{Year-1\\saving (\pounds)}}",
                   "Payback": r"\textbf{\shortstack{Discounted\\payback (yr)}}"},
        row_rules={2: r"\midrule", 5: r"\midrule"},
        top_rule=False,
        # \footnotesize left commented so the size is a one-character toggle.
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(rf"{len(pv_only) - len(below)} of the {len(pv_only)} PV-only configurations "
              rf"clear a ratio of 1.00. The two exceptions are both 1\,kWp on Economy 7, at "
              rf"{failing}, where the array is too small to offset the tariff switch it is "
              r"charged for. Read against Table~\ref{tab:breakeven-ratio}, where the best "
              rf"battery in the study supports only {best_battery:.2f} of its own installed "
              r"price: the PV array carries the investment and the battery subtracts from "
              r"it. Every other column is on the unbracketed basis, because the "
              r"counterfactual is deliberately the household's actual position --- no "
              r"hardware, flat tariff --- and switching is one of the things installing PV "
              r"prompts. It is not, however, a return on the array, and it is what makes "
              r"the tariffs non-comparable here: netting it out reverses their order "
              rf"entirely, leaving Economy 7 strongest, Agile weakest and all "
              rf"{clears_same_tariff} configurations above 1.00. That comparison is "
              r"conditional on already being on the tariff, which for Economy 7 without "
              r"storage is not a position a household would choose. The term cancels "
              r"identically in every battery comparison, which differences two rows on the "
              r"same tariff. Finally, the array reported is the NPV optimum, not the most "
              r"capital-efficient one: capex is linear in kWp, so the ratio is value per "
              rf"kWp and peaks at 1\,kWp on flat and Agile (up to {top_ratio:.2f}), while "
              r"on Economy 7 it peaks at 4--6\,kWp, the switch charge falling as the array "
              r"grows."),
        sizes=None,
        sources=[source],
    )


@table("t1.0", chapter=1, description="Case study: Manchester, 4 kWp, 5 kWh")
def build_t1_0() -> TableSpec:
    source = Path("results/sweep_scenarios_v2_band.csv")
    assembly = load_assembly(source)
    breakeven = load_breakeven()
    energy = _case_energy()

    case = assembly[(assembly.location == CASE["location"])
                    & (assembly.pv_kwp == CASE["pv_kwp"])
                    & (assembly.battery_kwh == CASE["battery_kwh"])]
    if case.empty:
        raise ValueError(f"case study configuration {CASE} not present in {source}")

    # Controller and penalty optimised within the case, exactly as every other
    # comparison in the results; the optimum differs between lifetime policies.
    chosen = {policy: best_in(case[case.policy == policy], ["tariff"], by="dnpv")
                        .set_index("tariff")
              for policy in POLICY_ORDER}

    rows: list[dict[str, str]] = []
    headings: list[int] = []

    def add(label: str, value_of) -> None:
        rows.append({"": label, **{TARIFF_LABEL[t]: value_of(t) for t in TARIFF_ORDER}})

    def block(label: str) -> None:
        headings.append(len(rows))
        rows.append({"": rf"\textbf{{{label}}}",
                     **{TARIFF_LABEL[t]: "" for t in TARIFF_ORDER}})

    def energy_rows(policy: str) -> None:
        """Year-1 operation for one policy's chosen dispatch.

        These sit inside each block rather than in a shared header so that every
        figure is read against the controller and penalty that produced it. The
        lifetime policy cannot change year-1 operation directly; it changes it
        only where it selects a different penalty, which here is Agile alone.
        """
        tariff_of = (lambda t: "all") if policy == "reference" else (lambda t: t)
        add("Self-sufficiency, year 1 [\\%]", lambda t: _energy_row(
            energy, "self_sufficiency_pct", tariff_of(t), policy, 1))
        add("PV self-consumption, year 1 [\\%]", lambda t: _energy_row(
            energy, "self_consumption_pct", tariff_of(t), policy, 1))
        add("Grid export, year 1 [kWh]", lambda t: _energy_row(
            energy, "export_kwh", tariff_of(t), policy, 0))

    block("PV only")
    energy_rows("reference")
    add("Annual saving, year 1 [\\pounds]",
        lambda t: _money(chosen["no-replacement"].loc[t].saving_ref))
    add("NPV [\\pounds]",
        lambda t: _money(chosen["no-replacement"].loc[t].npv_ref))
    add("Discounted payback [years]", lambda t: _pv_only_payback(
        chosen["no-replacement"].loc[t].pv_kwp,
        chosen["no-replacement"].loc[t].saving_ref,
        chosen["no-replacement"].loc[t].npv_ref))

    for policy in POLICY_ORDER:
        picked = chosen[policy]
        block(POLICY_LABEL[policy])
        add("Controller", lambda t, p=picked: CONTROLLER_LABEL[p.loc[t].controller])
        add("Degradation penalty [p/kWh]", lambda t, p=picked: (
            "{--}" if pd.isna(p.loc[t].deg_penalty_gbp_per_kwh)
            else _figure(100 * p.loc[t].deg_penalty_gbp_per_kwh, places=0)))
        energy_rows(policy)
        add("Annual saving, year 1 [\\pounds]",
            lambda t, p=picked: _money(p.loc[t].annual_saving_yr1_gbp))
        add("Whole-system NPV [\\pounds]",
            lambda t, p=picked: _money(p.loc[t].npv_gbp))
        add("Battery contribution [\\pounds]",
            lambda t, p=picked: _money(p.loc[t].dnpv))
        if policy == "run-to-fade":
            add("Years of operation",
                lambda t, p=picked: _figure(p.loc[t].realised_life_years, places=0))
        else:
            add("Final SOH [\\%]",
                lambda t, p=picked: _pct(100 * p.loc[t].min_soh))
        add("Subsidy needed [\\pounds/kWh]", lambda t, p=picked: _money(
            p.loc[t].battery_capex / p.loc[t].battery_kwh
            - _breakeven_for(breakeven, p.loc[t], t)))

    frame = pd.DataFrame(rows)
    # A rule closing each section but the last, and one under every heading.
    row_rules = {index - 1: r"\midrule" for index in headings[1:]}
    row_rules |= {index: r"\midrule" for index in headings}

    placeholders = sum(row.get(TARIFF_LABEL["flat"]) == PLACEHOLDER for row in rows)
    return TableSpec(
        frame=frame,
        caption=(r"Base Case: Manchester, 4\,kWp PV, 5\,kWh battery at 0.5C, 2025 prices. "
                 r"Controller and degradation penalty are optimised within the case for each "
                 r'lifetime policy. ``Battery contribution" is the change in NPV from adding '
                 r"the battery to the identical PV array. The case is chosen as representative "
                 r"of a typical domestic installation."),
        label="tab:case-study",
        column_format="lrrr",
        selection=(f"single case {CASE['location']}/{CASE['pv_kwp']:g} kWp/"
                   f"{CASE['battery_kwh']:g} kWh; controller and penalty optimised "
                   f"within the case per policy by dnpv"
                   + (f"; {placeholders} rows PLACEHOLDER pending "
                      "scripts/case_study_energy.py" if placeholders else "")),
        formats={column: "{}" for column in frame.columns},
        headers={TARIFF_LABEL[t]: rf"\textbf{{{TARIFF_LABEL[t]}}}" for t in TARIFF_ORDER},
        row_rules=row_rules,
        top_rule=False,
        # \footnotesize left commented so the size is a one-character toggle.
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r'``Subsidy needed" is the permanent reduction in installed price at which '
              r"the battery exactly repays itself, against an observed \pounds1{,}300/kWh "
              r"(DESNZ median, 1--5.99\,kWh band). The lifetime policy changes year-1 "
              r"operation only where it selects a different penalty, which here is Agile "
              r"alone. Note that Agile returns the "
              r"largest annual saving on the lowest self-consumption: the optimiser "
              r"arbitrages rather than self-consumes, so self-sufficiency is not the "
              r"objective and does not track value."),
        sources=[source],
    )


@table("t1.0b", chapter=1, description="Closest approach to viability, by tariff")
def build_t1_0b() -> TableSpec:
    source = Path("results/sweep_scenarios_v2_band.csv")
    assembly = load_assembly(source)
    breakeven = load_breakeven()

    # Selected on whole-system NPV, not on battery contribution: the study asks
    # whether PV+BESS is worth installing, so the best system is the one a buyer
    # would actually own. Costs 80-86 pounds of battery contribution against
    # selecting on the battery's own terms, which the note records.
    best = {policy: best_in(assembly[assembly.policy == policy], ["tariff"], by="npv_gbp")
                      .set_index("tariff")
            for policy in POLICY_ORDER}

    # The claim that the best system with a battery sits in the same cell as the
    # best PV-only system is asserted, not assumed: it is what makes the PV-only
    # NPV row comparable, and it is a property of these results, not of the model.
    reference = pd.read_csv(source)
    reference = reference[reference.battery_kwh == 0]
    for policy, picked in best.items():
        for tariff in TARIFF_ORDER:
            row = picked.loc[tariff]
            top = reference[reference.tariff == tariff].nlargest(1, "npv_gbp").iloc[0]
            if (row.location, row.pv_kwp) != (top.location, top.pv_kwp):
                raise ValueError(
                    f"{tariff}/{policy}: best battery system sits at "
                    f"{row.location}/{row.pv_kwp:g} kWp but the best PV-only system is at "
                    f"{top.location}/{top.pv_kwp:g} kWp; the shared-cell claim no longer holds")

    rows: list[dict[str, str]] = []
    headings: list[int] = []

    def add(label: str, value_of) -> None:
        rows.append({"": label, **{TARIFF_LABEL[t]: value_of(t) for t in TARIFF_ORDER}})

    def block(label: str) -> None:
        headings.append(len(rows))
        rows.append({"": rf"\textbf{{{label}}}",
                     **{TARIFF_LABEL[t]: "" for t in TARIFF_ORDER}})

    anchor_policy = best["no-replacement"]
    block("Best system, PV only")
    add("Location", lambda t: anchor_policy.loc[t].location.capitalize())
    add("PV array [kWp]", lambda t: _figure(anchor_policy.loc[t].pv_kwp, places=0))
    add("NPV [\\pounds]", lambda t: _money(anchor_policy.loc[t].npv_ref))

    for policy in POLICY_ORDER:
        picked = best[policy]
        block(f"Best system with a battery --- {POLICY_LABEL_INLINE[policy]}")
        add("Battery size [kWh]",
            lambda t, p=picked: _figure(p.loc[t].battery_kwh, places=1))
        add("Controller", lambda t, p=picked: CONTROLLER_LABEL[p.loc[t].controller])
        add("Degradation penalty [p/kWh]", lambda t, p=picked: (
            "{--}" if pd.isna(p.loc[t].deg_penalty_gbp_per_kwh)
            else _figure(100 * p.loc[t].deg_penalty_gbp_per_kwh, places=0)))
        add("Whole-system NPV [\\pounds]",
            lambda t, p=picked: _money(p.loc[t].npv_gbp))
        add("Battery contribution [\\pounds]",
            lambda t, p=picked: _money(p.loc[t].dnpv))
        add("Subsidy needed [\\pounds/kWh]", lambda t, p=picked: _money(
            p.loc[t].battery_capex / p.loc[t].battery_kwh
            - _breakeven_for(breakeven, p.loc[t], t)))

    frame = pd.DataFrame(rows)
    row_rules = {index - 1: r"\midrule" for index in headings[1:]}

    return TableSpec(
        frame=frame,
        caption=(r"Closest approach to viability under each tariff: the highest whole-system "
                 r"NPV available, selected over location, array size, battery size, controller "
                 r"and degradation penalty. In every tariff and under both lifetime policies "
                 r"the best system with a battery occupies the same location and array size as "
                 r"the best PV-only system, so the two are directly comparable and the battery "
                 r"contribution is the whole of the difference between them."),
        label="tab:closest-approach",
        column_format="lrrr",
        selection=("per tariff, the maximum whole-system npv_gbp over location, pv_kwp, "
                   "battery_kwh, controller and penalty, for each lifetime policy"),
        formats={column: "{}" for column in frame.columns},
        row_rules=row_rules,
        top_rule=False,
        preamble=[r"\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"All costs in \pounds; \emph{subsidy needed} is defined as in "
              r"Table~\ref{tab:case-study}. No configuration in the study improves on its own "
              r"PV-only reference. Selecting instead on the battery's own contribution rather "
              r"than on whole-system NPV moves the closest approach to \pounds-926 "
              r"(Inverness, Agile, 6\,kWp, 2.5\,kWh, no replacement), which does not change "
              r"the conclusion. Note that the highest whole-system NPV occurs on the flat "
              r"tariff, where the battery destroys the most value, and the least damaging "
              r"battery occurs on Agile, where the system NPV is lowest."),
        sources=[source],
    )


@table("t1.1", chapter=1, description="Battery contribution to whole-system NPV")
def build_t1_1() -> TableSpec:
    source = Path("results/sweep_scenarios_v2_band.csv")
    optimum = per_cell_optimum(load_assembly(source), ["policy", "battery_kwh"], by="dnpv")

    rows = []
    for policy in POLICY_ORDER:
        for tariff in TARIFF_ORDER:
            for size in REPORTED_SIZES:
                group = optimum[(optimum.policy == policy)
                                & (optimum.tariff == tariff)
                                & (optimum.battery_kwh == size)]
                if len(group) != 18:
                    raise ValueError(
                        f"expected 18 cells for {policy}/{tariff}/{size}, got {len(group)}")
                rows.append({
                    "Policy": POLICY_LABEL[policy],
                    "Tariff": TARIFF_LABEL[tariff],
                    "Battery (kWh)": size,
                    "PV only": group.npv_ref.mean(),
                    "PV + battery": group.npv_gbp.mean(),
                    "Battery contribution": group.dnpv.mean(),
                    "Best cell": group.dnpv.max(),
                })

    frame = pd.DataFrame(rows)
    _blank_repeats(frame, "Policy", every=9)
    _blank_repeats(frame, "Tariff", every=3)

    # A light rule between tariff blocks, a full one between policy blocks.
    row_rules = {index: r"\cmidrule(lr){2-7}" for index in (2, 5, 11, 14)}
    row_rules[8] = r"\midrule"

    # Numbers are emitted unseparated; siunitx inserts the thousands separator.
    # Emitting "3,971" makes siunitx read the comma as a decimal marker.
    money = "{:.0f}"
    return TableSpec(
        frame=frame,
        caption=(r"Contribution of the battery to whole-system NPV, by tariff and battery size. "
                 r"Values are means over the 18 cells of each tariff, with controller and wear "
                 r"penalty optimised within every cell. \emph{Battery contribution} is the change "
                 r"in NPV from adding the battery to the identical PV array; \emph{best cell} is "
                 r"the least negative of the 18."),
        label="tab:battery-contribution",
        # Rounding is pinned per column rather than table-wide: a document-level
        # \sisetup with round-precision=2 otherwise prints every NPV as x.00 and
        # reads a grouped 3,971 as 3.97.
        column_format="ll" + SIZE_COLUMN + MONEY_COLUMN * 4,
        selection=("mean over the 18 cells of each tariff; controller and penalty "
                   "optimised within each cell by dnpv"),
        formats={"Battery (kWh)": "{:.1f}", "PV only": money, "PV + battery": money,
                 "Battery contribution": money, "Best cell": money},
        headers={
            "Battery (kWh)": r"\shortstack{Battery\\(kWh)}",
            "PV only": r"\shortstack{PV\\only}",
            "PV + battery": r"\shortstack{PV +\\battery}",
            "Battery contribution": r"\shortstack{Battery\\contribution}",
            "Best cell": r"\shortstack{Best\\cell}",
        },
        row_rules=row_rules,
        preamble=[r"\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"All figures in \pounds. A positive whole-system NPV is common and says nothing "
              r"about the battery: 42\% of PV+battery configurations have NPV $>0$, carried there "
              r"by the PV array alone."),
        sources=[source],
    )


@table("t1.2", chapter=1, description="Break-even battery price as a fraction of observed price")
def build_t1_2() -> TableSpec:
    optimum = per_cell_optimum(load_breakeven(), ["policy", "battery_kwh"],
                               by="breakeven_ratio")

    size_columns = [f"{size:g} kWh" for size in REPORTED_SIZES]
    rows = []
    for policy in POLICY_ORDER:
        for tariff in TARIFF_ORDER:
            row = {"Policy": POLICY_LABEL[policy], "Tariff": TARIFF_LABEL[tariff]}
            for size, column in zip(REPORTED_SIZES, size_columns):
                group = optimum[(optimum.policy == policy)
                                & (optimum.tariff == tariff)
                                & (optimum.battery_kwh == size)]
                if len(group) != 18:
                    raise ValueError(
                        f"expected 18 cells for {policy}/{tariff}/{size}, got {len(group)}")
                mean, best = group.breakeven_ratio.mean(), group.breakeven_ratio.max()
                row[column] = f"{mean:.2f} ({best:.2f})"
                row[column + "__sort"] = mean
            rows.append(row)

    frame = pd.DataFrame(rows)
    _blank_repeats(frame, "Policy", every=3)

    return TableSpec(
        frame=frame,
        caption=(r"Break-even battery price as a fraction of the observed installed price. "
                 r"1.00 is break-even: the battery exactly repays its own capex over the "
                 r"20-year horizon. Entries are the mean over the 18 cells of each tariff, with "
                 r"the best single cell in brackets. Observed prices are the DESNZ band medians, "
                 r"\pounds1{,}300/kWh at 2.5 and 5\,kWh and \pounds890/kWh at 10\,kWh."),
        label="tab:breakeven-ratio",
        column_format="llccc",
        selection=("mean over the 18 cells of each tariff (best cell in brackets); controller "
                   "and penalty optimised within each cell by breakeven_ratio"),
        formats={column: "{}" for column in size_columns},
        heatmap={column: (0.0, 1.0) for column in size_columns},
        row_rules={2: r"\midrule"},
        preamble=[r"\footnotesize"],
        note=(r"Shading is sequential over the full $0$--$1.00$ range. Nothing in the study "
              r"reaches break-even; the highest value anywhere is 0.72."),
        sources=list(CONSTANT_PRICE_ASSEMBLIES.values()),
    )


@table("t1.3", chapter=1, description="The five least-damaging batteries in the study")
def build_t1_3() -> TableSpec:
    source = Path("results/sweep_scenarios_v2_band.csv")
    optimum = per_cell_optimum(load_assembly(source), ["policy", "battery_kwh"], by="dnpv")

    # Ranked under no replacement, the upper bound on battery value, so the table
    # reports the ceiling rather than a midpoint. Selection runs over location,
    # array size, battery size, controller and penalty at once.
    top = optimum[optimum.policy == "no-replacement"].nlargest(5, "dnpv")

    # The note names one tariff, one size and one controller for all five rows.
    # Asserted rather than assumed, as in t1.0b: if a re-run breaks the pattern
    # the build fails instead of printing a caption that no longer describes it.
    uniform = {column: top[column].unique() for column in
               ("tariff", "battery_kwh", "controller", "deg_penalty_gbp_per_kwh")}
    varied = {column: values for column, values in uniform.items() if len(values) > 1}
    if varied:
        raise ValueError(f"the five best configurations no longer share "
                         f"{sorted(varied)}; the note must be rewritten")

    # Same configurations re-costed under the other policy, with controller and
    # penalty re-optimised inside each, which is the like-for-like comparison.
    faded = (optimum[optimum.policy == "run-to-fade"]
             .set_index(["location", "tariff", "pv_kwp", "battery_kwh"]))
    fade_dnpv = [faded.loc[(r.location, r.tariff, r.pv_kwp, r.battery_kwh)].dnpv
                 for r in top.itertuples()]

    frame = pd.DataFrame({
        "Location": [row.location.capitalize() for row in top.itertuples()],
        "Tariff": [TARIFF_LABEL[row.tariff] for row in top.itertuples()],
        "PV (kWp)": top.pv_kwp.to_numpy(),
        "Battery (kWh)": top.battery_kwh.to_numpy(),
        "PV only": top.npv_ref.to_numpy(),
        "PV + battery": top.npv_gbp.to_numpy(),
        "Battery contribution": top.dnpv.to_numpy(),
    })

    best_pv_only = _prose_money(optimum.npv_ref.max())
    fade_range = f"{_prose_money(max(fade_dnpv))} to {_prose_money(min(fade_dnpv))}"
    penalty = 100 * uniform["deg_penalty_gbp_per_kwh"][0]
    fade_penalties = sorted({100 * v for v in faded.loc[
        [(r.location, r.tariff, r.pv_kwp, r.battery_kwh) for r in top.itertuples()]
    ].deg_penalty_gbp_per_kwh})

    return TableSpec(
        frame=frame,
        caption=(r"The five least-damaging batteries in the study, ranked by battery "
                 r"contribution under no replacement --- the lifetime policy that most "
                 r"favours the battery. Selection is over location, array size, battery "
                 r'size, controller and degradation penalty at once. ``Battery '
                 r'contribution" is defined as in Table~\ref{tab:case-study}: the change '
                 r"in NPV from adding the battery to the identical PV array, so it is the "
                 r"difference between the two columns to its left."),
        label="tab:best-batteries",
        column_format=("ll" + "S[table-format=1.0, round-mode=places, round-precision=0]"
                       + SIZE_COLUMN + MONEY_COLUMN * 3),
        selection=("the five configurations with the greatest dnpv under no-replacement, "
                   "over location, pv_kwp, battery_kwh, controller and penalty"),
        formats={"PV (kWp)": "{:.0f}", "Battery (kWh)": "{:.1f}", "PV only": "{:.0f}",
                 "PV + battery": "{:.0f}", "Battery contribution": "{:.0f}"},
        headers={column: rf"\textbf{{{column}}}" for column in ("Location", "Tariff")}
                | {"PV (kWp)": r"\textbf{\shortstack{PV\\(kWp)}}",
                   "Battery (kWh)": r"\textbf{\shortstack{Battery\\(kWh)}}",
                   "PV only": r"\textbf{\shortstack{PV\\only}}",
                   "PV + battery": r"\textbf{\shortstack{PV +\\battery}}",
                   "Battery contribution": r"\textbf{\shortstack{Battery\\contribution}}"},
        top_rule=False,
        # \footnotesize left commented so the size is a one-character toggle.
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(rf"All figures in \pounds. Every one of the five is on Agile at "
              rf"{uniform['battery_kwh'][0]:g}\,kWh --- the smallest reported size --- "
              rf"dispatched by MILP at {penalty:.0f}\,p/kWh, so the ceiling is set by "
              r"tariff and size rather than by siting. None improves on its own PV-only "
              r"reference. Under run to SOH$_{\min}$ the same configurations return "
              rf"{fade_range} at "
              rf"{'/'.join(f'{p:.0f}' for p in fade_penalties)}\,p/kWh. Their "
              r"whole-system NPVs all fall short of the best PV-only system in the study "
              rf"({best_pv_only}, Plymouth, flat tariff, 6\,kWp), so the configuration "
              r"in which the battery does least damage is not one a buyer would choose."),
        sources=[source],
    )


@table("t1.3_mod", chapter=1,
       description="The five least-damaging batteries, as a ranking on BBCR")
def build_t1_3_mod() -> TableSpec:
    """t1.3 reshaped: a ranked list of systems, not a pair of NPV levels.

    Two changes from `build_t1_3`, both aimed at the same thing --- making the
    table a ceiling argument that reads without reference to any other object in
    the chapter:

    * The "PV only" and "PV + battery" columns are dropped. They are levels, and
      a level in this table invites the reader to compare rows on whole-system
      NPV, which the ranking is not sorted on and which mostly measures the
      array. The battery's own shortfall goes in the note instead.
    * Controller and wear penalty become columns. Every headline number in this
      section is a maximum over those two axes, and until they are printed the
      reader cannot see that the ceiling is not reached by controller-shopping.
      They come out constant, which is the point.

    Ranked on `breakeven_ratio` rather than `dnpv`, so the sort key and the
    reported quantity are the same number. Under no-replacement the two agree
    here anyway --- asserted below rather than assumed, since they need not.
    """
    breakeven = load_breakeven()
    optimum = per_cell_optimum(breakeven, ["policy", "battery_kwh"], by="breakeven_ratio")
    top = optimum[optimum.policy == "no-replacement"].nlargest(5, "breakeven_ratio")

    # The two selection rules pick the same five systems in the same order here.
    # If a re-run separates them the table has to say which one it is sorted on,
    # so the agreement is checked rather than relied on silently.
    key = ["location", "tariff", "pv_kwp", "battery_kwh"]
    by_dnpv = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    by_dnpv = by_dnpv[by_dnpv.policy == "no-replacement"].nlargest(5, "dnpv")
    if not top[key].reset_index(drop=True).equals(by_dnpv[key].reset_index(drop=True)):
        raise ValueError("ranking by breakeven_ratio and by dnpv no longer agree; "
                         "the caption must name the sort key")

    # As in t1.3: the note names one tariff, one size, one controller and one
    # penalty for all five rows, so a re-run that breaks the pattern fails here
    # rather than printing a note that no longer describes the table.
    uniform = {column: top[column].unique() for column in
               ("tariff", "battery_kwh", "controller", "deg_penalty_gbp_per_kwh")}
    varied = {column: values for column, values in uniform.items() if len(values) > 1}
    if varied:
        raise ValueError(f"the five best configurations no longer share "
                         f"{sorted(varied)}; the note must be rewritten")

    # The same five physical systems re-costed under the other policy, with
    # controller and penalty re-optimised inside each, which is the like-for-like
    # comparison and is why the selected penalty differs between the two columns.
    faded = optimum[optimum.policy == "run-to-fade"].set_index(key)
    fade_rows = faded.loc[[tuple(getattr(row, column) for column in key)
                           for row in top.itertuples()]]

    frame = pd.DataFrame({
        "Rank": np.arange(1, len(top) + 1),
        "Location": [row.location.capitalize() for row in top.itertuples()],
        "Tariff": [TARIFF_LABEL[row.tariff] for row in top.itertuples()],
        "PV (kWp)": top.pv_kwp.to_numpy(),
        "Battery (kWh)": top.battery_kwh.to_numpy(),
        "Controller": [CONTROLLER_LABEL[row.controller] for row in top.itertuples()],
        "Penalty": 100 * top.deg_penalty_gbp_per_kwh.to_numpy(),
        "BBCR": [f"{value:.2f}" for value in top.breakeven_ratio],
    })

    # Stated as an amount forgone, so the sign is carried by the word rather than
    # by a minus that would read as "forgoes minus 926".
    shortfall = _prose_money(abs(by_dnpv.dnpv.max()))
    penalty = 100 * uniform["deg_penalty_gbp_per_kwh"][0]
    # Run-to-fade is reported in the note rather than as a second column: it is
    # the same five systems re-costed, not five more systems to rank.
    fade_penalties = sorted({100 * value for value in fade_rows.deg_penalty_gbp_per_kwh})
    fade_range = (f"{fade_rows.breakeven_ratio.max():.2f} to "
                  f"{fade_rows.breakeven_ratio.min():.2f}")

    return TableSpec(
        frame=frame,
        caption=(r"The five least-damaging batteries in the study, ranked by the fraction "
                 r"of its own installed price each can support. Selection is over "
                 r"location, array size, battery size, controller and degradation penalty "
                 r"at once, under no replacement --- the lifetime policy that most "
                 r"favours the battery, so the table reports the ceiling rather than a "
                 r'midpoint. ``BBCR" is the battery benefit cost ratio: the present value '
                 r"of the battery's own benefits over the present value of its own costs, "
                 r"with the identical PV array on both sides of the comparison, so the "
                 r"array cancels. 1.00 is break-even."),
        label="tab:best-batteries",
        column_format=("cll"
                       + "S[table-format=1.0, round-mode=places, round-precision=0]"
                       + SIZE_COLUMN + "l"
                       + "S[table-format=1.0, round-mode=places, round-precision=0]"
                       + "c"),
        selection=("the five configurations with the greatest breakeven_ratio under "
                   "no-replacement, over location, pv_kwp, battery_kwh, controller and "
                   "penalty; run-to-fade re-optimised within the same physical system"),
        formats={"Rank": "{:d}", "PV (kWp)": "{:.0f}", "Battery (kWh)": "{:.1f}",
                 "Penalty": "{:.0f}", "BBCR": "{}"},
        headers={column: rf"\textbf{{{column}}}" for column in
                 ("Rank", "Location", "Tariff", "Controller")}
                | {"PV (kWp)": r"\textbf{\shortstack{PV\\(kWp)}}",
                   "Battery (kWh)": r"\textbf{\shortstack{Battery\\(kWh)}}",
                   "Penalty": r"\textbf{\shortstack{Penalty\\(p/kWh)}}",
                   "BBCR": r"\textbf{BBCR}"},
        top_rule=False,
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(rf"Every one of the five is on Agile at {uniform['battery_kwh'][0]:g}\,kWh "
              rf"--- the smallest reported size --- dispatched by MILP at "
              rf"{penalty:.0f}\,p/kWh. The controller and penalty columns are constant by "
              r"outcome rather than by construction: both were free in the selection, so "
              r"the ceiling is set by tariff and size and cannot be raised by choosing a "
              r"different controller. None of the five reaches 1.00, and the best forgoes "
              rf"{shortfall} of NPV against its own PV-only array. Under run to "
              rf"SOH$_{{\min}}$ the same five select "
              rf"{'/'.join(f'{p:.0f}' for p in fade_penalties)}\,p/kWh instead and return "
              rf"{fade_range}."),
        sources=list(CONSTANT_PRICE_ASSEMBLIES.values()),
    )


# --------------------------------------------------------------------------- #
# Chapter 5
# --------------------------------------------------------------------------- #

# Blocks run in whole-system NPV span order. The BBCR span ranks them close to
# the reverse, so no ordering here is neutral; this one is stated in the caption
# rather than left to be inferred.
AXIS_SPEC = [
    ("Location", "location", LOCATION_ORDER, lambda v: v.capitalize()),
    ("PV size", "pv_kwp", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], lambda v: rf"{v:g}\,kWp"),
    ("Battery size", "battery_kwh", list(REPORTED_SIZES), lambda v: rf"{v:g}\,kWh"),
    ("Tariff", "tariff", TARIFF_ORDER, lambda v: TARIFF_LABEL[v]),
    ("Lifetime policy", "policy", POLICY_ORDER, lambda v: POLICY_LABEL[v]),
]


@table("t5.1", chapter=5,
       description="Ceiling of every parameter setting, whole-system NPV and BBCR")
def build_t5_1() -> TableSpec:
    # Each column is maximised on its own metric. Maximising one and reporting
    # the other disagrees under run-to-fade, where the controller that loses
    # least at the observed price is not the one that supports the highest price.
    system = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="npv_gbp")
    battery = per_cell_optimum(load_breakeven(), ["policy", "battery_kwh"],
                               by="breakeven_ratio")

    rows: list[dict] = []
    rules: dict[int, str] = {}
    for name, column, order, label_of in AXIS_SPEC:
        npv = system.groupby(column).npv_gbp.max()
        ratio = battery.groupby(column).breakeven_ratio.max()
        if set(npv.index) != set(order) or set(ratio.index) != set(order):
            raise ValueError(f"{name}: swept settings do not match {order}")
        # Every setting is a maximum over all the others, so exactly one setting
        # per axis must return the global best. If that fails the grouping has
        # dropped rows and no span in the table is trustworthy.
        for series, whole in ((npv, system.npv_gbp), (ratio, battery.breakeven_ratio)):
            if not np.isclose(series.max(), whole.max()):
                raise ValueError(f"{name}: block ceiling {series.max()} is not the global "
                                 f"maximum {whole.max()}")
        rows.append({"Axis": rf"\textbf{{{name}}}", "Setting": r"\textbf{Span}",
                     "NPV": npv.max() - npv.min(), "BBCR": ratio.max() - ratio.min()})
        rows += [{"Axis": "", "Setting": label_of(level),
                  "NPV": npv[level], "BBCR": ratio[level]} for level in order]
        rules[len(rows) - 1] = r"\midrule"
    rules.pop(max(rules), None)  # the bottom rule closes the last block

    best_system = system.loc[system.npv_gbp.idxmax()]
    best_battery = battery.loc[battery.breakeven_ratio.idxmax()]
    pv_only_ceiling = load_pv_only().npv_gbp.max()
    bbcr_order = ", ".join(
        (name if name.startswith("PV") else name.lower()) for name, _, _, _ in
        sorted(AXIS_SPEC,
               key=lambda spec: -(lambda s: s.max() - s.min())(
                   battery.groupby(spec[1]).breakeven_ratio.max())))

    return TableSpec(
        frame=pd.DataFrame(rows),
        caption=(r"The best result reachable at each parameter setting, on both viability "
                 r"measures. Every entry is a maximum over all the other parameters at once, "
                 r"including the controller and the degradation penalty, so it reads as the "
                 r"most a household could achieve with that one setting imposed and every "
                 r"other choice made well. The span is the gap between an axis's best and "
                 r"worst setting, and measures how far a poor choice on that axis can be "
                 r"recovered elsewhere. Blocks are ordered by the whole-system span."),
        label="tab:axis-ceilings",
        column_format="ll" + MONEY_COLUMN
                      + "S[table-format=1.3, round-mode=places, round-precision=3]",
        selection=("maximum of each metric over every other parameter, controller and "
                   "penalty included; each column maximised on its own metric"),
        # Emitted unseparated; siunitx inserts the thousands separator, and a
        # pre-grouped "5,582" would be read as a decimal marker.
        formats={"NPV": "{:.0f}", "BBCR": "{:.3f}"},
        headers={"NPV": r"\textbf{\shortstack{Whole-system\\NPV (\pounds)}}",
                 "BBCR": r"\textbf{BBCR}",
                 "Axis": r"\textbf{Axis}", "Setting": r"\textbf{Setting}"},
        row_rules=rules,
        preamble=[r"\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"Whole-system NPV is measured against a grid-only household on the flat "
              r"tariff, so a row on another tariff carries the tariff switch as well as the "
              rf"system. The two ceilings are different systems: {_prose_money(best_system.npv_gbp)} at "
              rf"{best_system.location.capitalize()} on {TARIFF_LABEL[best_system.tariff].lower()} "
              rf"with {best_system.pv_kwp:g}\,kWp and {best_system.battery_kwh:g}\,kWh, against "
              rf"{best_battery.breakeven_ratio:.3f} at {best_battery.location.capitalize()} on "
              rf"{TARIFF_LABEL[best_battery.tariff].lower()}. Ranked on BBCR instead the order "
              rf"becomes {bbcr_order}, so the two measures disagree on which parameters matter "
              r"and, for tariff and location, on which setting is best. Every row assumes a "
              r"battery is installed, since BBCR is undefined without one; the best system "
              rf"carrying no battery at all returns {_prose_money(pv_only_ceiling)}."),
        sources=[Path("results/sweep_scenarios_v2_band.csv"),
                 *CONSTANT_PRICE_ASSEMBLIES.values()],
    )


@table("t3.1", chapter=3, description="Best controller by tariff")
def build_t3_1() -> TableSpec:
    source = Path("results/sweep_scenarios_v2_band.csv")
    assembly = load_assembly(source)

    # One configuration is a cell at one battery size. The controller is compared
    # at its own best penalty, so the MILP is never handicapped by a bad one.
    config = ["location", "tariff", "pv_kwp", "battery_kwh", "policy"]
    best_penalty = assembly.loc[assembly.groupby(config + ["controller"]).dnpv.idxmax()]
    wide = best_penalty.pivot_table(index=config, columns="controller", values="dnpv")

    # Ranked simplest first, so `idxmax` credits a tie to the plainer controller.
    # On flat that matters: SC (ToU) has no off-peak rate to charge into and
    # returns the identical schedule, so every flat comparison between them ties.
    order = ["self_consumption", "self_consumption_tou", "milp"]
    wide["winner"] = wide[order].idxmax(axis=1)
    wide["gain"] = wide[order].max(axis=1) - wide.self_consumption

    rows = []
    for policy in POLICY_ORDER:
        for tariff in TARIFF_ORDER:
            group = wide.xs(policy, level="policy").xs(tariff, level="tariff")
            if len(group) != 54:
                raise ValueError(f"expected 54 configurations for {policy}/{tariff}, "
                                 f"got {len(group)}")
            counts = group.winner.value_counts()
            rows.append({
                "Policy": POLICY_LABEL[policy],
                "Tariff": TARIFF_LABEL[tariff],
                **{CONTROLLER_LABEL[c]: counts.get(c, 0) for c in order},
                "Gain over SC": group.gain.mean(),
            })

    frame = pd.DataFrame(rows)
    _blank_repeats(frame, "Policy", every=3)

    tou = wide.self_consumption_tou - wide.self_consumption
    beats = (tou > 1e-6).groupby(level="tariff").sum()
    count_column = "S[table-format=2.0, round-mode=places, round-precision=0]"
    return TableSpec(
        frame=frame,
        caption=(r"Controller chosen by the optimisation, by tariff and lifetime policy. "
                 r"Each of the 54 configurations of a tariff (3 locations $\times$ 6 array "
                 r"sizes $\times$ 3 battery sizes) is counted once, for the controller "
                 r"returning the highest battery contribution with its degradation penalty "
                 r'optimised within the configuration. ``Gain over SC" is what that choice '
                 r"is worth in NPV against the price-blind self-consumption heuristic, "
                 r"averaged over the 54; it rises steeply with battery size, so it "
                 r"describes the grid rather than any single system."),
        label="tab:controller-choice",
        column_format="ll" + count_column * 3 + MONEY_COLUMN,
        selection=("per tariff and policy, the controller maximising dnpv in each of the 54 "
                   "configurations, with the penalty optimised within each controller"),
        formats={CONTROLLER_LABEL[c]: "{:.0f}" for c in order} | {"Gain over SC": "{:.0f}"},
        headers={column: rf"\textbf{{{column}}}" for column in
                 ["Policy", "Tariff"] + [CONTROLLER_LABEL[c] for c in order]}
                | {"Gain over SC": r"\textbf{\shortstack{Gain over\\SC}}"},
        row_rules={2: r"\midrule"},
        top_rule=False,
        # \footnotesize left commented so the size is a one-character toggle.
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"Gains in \pounds; ties are credited to the simpler controller. On flat, "
              r"SC (ToU) returns the identical schedule to plain SC in every configuration "
              r"--- with no off-peak rate there is nothing for it to charge into --- and "
              r"the MILP never beats either under no replacement, so the price-blind "
              r"heuristic is not merely adequate but optimal. The eight flat configurations "
              r"the MILP wins under run to SOH$_{\min}$ are all at 5--10\,kWh, where it "
              r"defers replacement by cycling less. SC (ToU) wins nowhere, although it does "
              rf"beat plain SC in {beats['e7']:.0f} of the 108 Economy 7 and "
              rf"{beats['agile']:.0f} of the 108 Agile configurations across both policies, "
              rf"by {_prose_money(tou[tou > 1e-6].mean())} on average where it does; the "
              r"MILP dominates it in all of them."),
        sources=[source],
    )


# Myopia losses measured by scripts/test_myopia.py at Inverness / Agile / 5 kWp /
# 10 kWh, against the daily MILP the sweep actually runs, at the penalty each
# lifetime policy selects for that cell: 0 p/kWh under no replacement (loss
# 14.28 on a 441.42 saving) and 5 p/kWh under run to fade (21.76 on 360.86).
# The larger of the two is carried here. Section 7 of docs/results_summary.md.
MYOPIA_AGILE_GBP_YR = 21.76
MYOPIA_LARGEST_GBP_YR = 30.84       # flat, where self-consumption is selected anyway
MYOPIA_AGILE_SHARE = 21.76 / 360.86

DISCOUNT_RATES = {"5.0\\%": None, "3.5\\%": Path("results/dr035")}


@table("t3.2", chapter=3, description="Myopia credit against the break-even shortfall")
def build_t3_2() -> TableSpec:
    # A credit of c GBP/yr joins the same escalating, fading stream as the saving
    # it corrects, so the whole present value scales: ratio' = ratio (1 + c/s).
    # Nothing needs re-solving, and nothing needs the replacement schedule.
    def ceilings(credit) -> list[tuple[float, int]]:
        out = []
        for src_dir in DISCOUNT_RATES.values():
            frame = per_cell_optimum(load_breakeven(src_dir=src_dir),
                                     ["policy", "battery_kwh"], by="breakeven_ratio")
            ratio = frame.breakeven_ratio * (1 + credit(frame))
            out.append((ratio.max(), int((ratio >= 1.0).sum())))
        return out

    columns = [f"{kind} {rate}" for rate in DISCOUNT_RATES for kind in ("ceiling", "reached")]
    rows: list[dict[str, str]] = []
    headings: list[int] = []

    def add(label: str, credit) -> None:
        row = {"": label}
        for rate, (ceiling, reached) in zip(DISCOUNT_RATES, ceilings(credit)):
            row[f"ceiling {rate}"] = _figure(ceiling, places=3)
            row[f"reached {rate}"] = _figure(reached, places=0)
        rows.append(row)

    def block(label: str) -> None:
        headings.append(len(rows))
        rows.append({"": rf"\textbf{{{label}}}", **{c: "" for c in columns}})

    add("None --- as reported", lambda f: 0.0)
    block("Uniform credit, every configuration")
    add(rf"\quad $+$\pounds{MYOPIA_AGILE_GBP_YR:.2f}/yr --- measured on Agile",
        lambda f: MYOPIA_AGILE_GBP_YR / f.dsaving_yr1_hi)
    add(rf"\quad $+$\pounds{MYOPIA_LARGEST_GBP_YR:.2f}/yr --- largest measured",
        lambda f: MYOPIA_LARGEST_GBP_YR / f.dsaving_yr1_hi)
    add(rf"\quad $+$\pounds{2 * MYOPIA_AGILE_GBP_YR:.2f}/yr --- twice the Agile loss",
        lambda f: 2 * MYOPIA_AGILE_GBP_YR / f.dsaving_yr1_hi)
    block("Proportional credit, every configuration")
    add(rf"\quad $+${100 * MYOPIA_AGILE_SHARE:.1f}\% of saving --- measured on Agile",
        lambda f: MYOPIA_AGILE_SHARE)
    add(rf"\quad $+${200 * MYOPIA_AGILE_SHARE:.1f}\% of saving --- twice measured",
        lambda f: 2 * MYOPIA_AGILE_SHARE)

    frame = pd.DataFrame(rows, columns=["", *columns])
    row_rules = {index - 1: r"\midrule" for index in headings}
    row_rules |= {index: r"\midrule" for index in headings}

    return TableSpec(
        frame=frame,
        caption=(r"Effect of crediting the battery with the day-ahead myopia loss. The "
                 r"production controller optimises one day at a time, so it under-uses "
                 r"the battery relative to perfect annual foresight; the loss is therefore "
                 r"owed \emph{to} the battery. Entries are the highest break-even ratio "
                 r"anywhere in the study once the credit is applied to all 324 "
                 r"configurations, and the number of those configurations reaching 1.00. "
                 r"A credit joins the same escalating, fading stream as the saving it "
                 r"corrects, so the present value scales with it and no re-solve is needed."),
        label="tab:myopia-bound",
        column_format="lrrrr",
        selection=("break-even ratio maximised over controller and penalty within each "
                   "cell, then over all 324 configurations, at two discount rates"),
        formats={column: "{}" for column in frame.columns},
        headers={"": "", **{f"ceiling {r}": rf"\textbf{{\shortstack{{Ceiling\\{r}}}}}"
                            for r in DISCOUNT_RATES},
                 **{f"reached {r}": rf"\textbf{{\shortstack{{At 1.00\\{r}}}}}"
                    for r in DISCOUNT_RATES}},
        row_rules=row_rules,
        top_rule=False,
        preamble=[r"%\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"3.5\% is the HM Treasury Green Book rate, reassembled from the same "
              r"dispatch cache; the discount rate does not enter dispatch. The uniform "
              rf"credit is the cruder bound: at twice the Agile loss it hands the median "
              rf"configuration a fifth of its entire annual saving, against a measured "
              rf"myopia share of {100 * MYOPIA_AGILE_SHARE:.1f}\%, and it is the "
              r"small-saving configurations it flatters. The proportional rows are the "
              r"defensible bound and clear 1.00 at both rates. Both Agile figures are "
              r"measured at the penalty the sweep selects for that cell, so each audits "
              r"the dispatch actually reported."),
        sources=[Path("results/_band_c1300.csv"), Path("results/_band_c890.csv"),
                 Path("results/dr035/_band_c1300.csv"), Path("results/dr035/_band_c890.csv")],
    )


# --------------------------------------------------------------------------- #
# Chapter 4
# --------------------------------------------------------------------------- #

# The two axes of t4.1, as variant directories under results/sens. None is the
# base case: 5% real discount, 2% real escalation. Every other cell is a
# reassembly of the same dispatch cache at different economics, written by
# scripts/make_sensitivity_assemblies.py; neither parameter enters dispatch.
GRID_RATES = {"3.5": "dr035", "5.0": None, "7.0": "dr070"}
GRID_ESCALATIONS = {"0": "esc000", "2": None, "4": "esc040"}


def _grid_dir(rate: str, escalation: str) -> Path | None:
    """The assembly directory for one cell of the grid, or None for the base."""
    parts = [p for p in (GRID_RATES[rate], GRID_ESCALATIONS[escalation]) if p is not None]
    return Path("results/sens") / "_".join(parts) if parts else None


@table("t4.1", chapter=4, description="Conditions for viability: discount rate x escalation")
def build_t4_1() -> TableSpec:
    ceilings: dict[tuple[str, str], pd.Series] = {}
    fade: dict[tuple[str, str], float] = {}
    reached = 0
    for rate in GRID_RATES:
        for escalation in GRID_ESCALATIONS:
            frame = per_cell_optimum(load_breakeven(src_dir=_grid_dir(rate, escalation)),
                                     ["policy", "battery_kwh"], by="breakeven_ratio")
            if len(frame) != 324:
                raise ValueError(f"expected 324 configurations at {rate}/{escalation}, "
                                 f"got {len(frame)}")
            ceilings[rate, escalation] = frame.loc[frame.breakeven_ratio.idxmax()]
            fade[rate, escalation] = float(
                frame[frame.policy == "run-to-fade"].breakeven_ratio.max())
            reached += int((frame.breakeven_ratio >= 1.0).sum())

    # The note names one configuration for all nine cells. Asserted rather than
    # assumed, as in t1.0b and t1.3: if a reassembly moves the ceiling elsewhere
    # the build fails instead of printing a caption that no longer describes it.
    describe = ["location", "tariff", "pv_kwp", "battery_kwh", "controller", "policy"]
    corners = {tuple(row[c] for c in describe) for row in ceilings.values()}
    if len(corners) != 1:
        raise ValueError(f"the ceiling no longer sits in one configuration across the "
                         f"grid ({len(corners)} distinct); the note must be rewritten")

    columns = [rf"{e}\%" for e in GRID_ESCALATIONS]
    rows = []
    for rate in GRID_RATES:
        row = {"Discount rate": rf"{rate}\%"}
        for escalation, column in zip(GRID_ESCALATIONS, columns):
            best = ceilings[rate, escalation].breakeven_ratio
            row[column] = f"{best:.2f} ({fade[rate, escalation]:.2f})"
            row[column + "__sort"] = best
        rows.append(row)
    frame = pd.DataFrame(rows)

    best_corner = ceilings["3.5", "4"]
    worst_corner = ceilings["7.0", "0"]
    base = ceilings["5.0", "2"]
    anchor = ceilings["3.5", "2"]
    # The price the ceiling is measured against, read from the same band table
    # the ratio is divided by rather than restated.
    observed = band_price(best_corner.battery_kwh)
    return TableSpec(
        frame=frame,
        caption=(r"Conditions for viability: the highest break-even ratio anywhere in the "
                 r"study at each combination of real discount rate and real electricity "
                 r"price escalation. 1.00 is break-even, so each entry is the fraction of "
                 r"its own installed price that the best battery in the study can support "
                 r"under those economics. The leading figure is under no replacement, the "
                 r"lifetime policy that most favours the battery; the bracketed figure is "
                 r"under run to SOH$_{\min}$. The base case is the centre cell, 5.0\% and "
                 r"2\%; 3.5\% is the HM Treasury Green Book rate."),
        label="tab:viability-conditions",
        column_format="lccc",
        selection=("break-even ratio maximised over controller and penalty within each "
                   "cell, then over all 324 configurations, at each of nine "
                   "discount rate x escalation reassemblies"),
        formats={column: "{}" for column in columns},
        heatmap={column: (0.0, 1.0) for column in columns},
        headers={"Discount rate": r"\textbf{\shortstack{Discount\\rate}}"}
                | {column: rf"\textbf{{\shortstack{{Escalation\\{column}}}}}"
                   for column in columns},
        preamble=[r"\footnotesize"],
        note=(rf"Shading is sequential over the full $0$--$1.00$ range, as in "
              rf"Table~\ref{{tab:breakeven-ratio}}. Of the {9 * 324:,} configurations the "
              rf"grid covers, {reached} reach 1.00. The ceiling sits in the same "
              rf"configuration in all nine cells --- {best_corner.location.capitalize()}, "
              rf"{TARIFF_LABEL[best_corner.tariff]}, {best_corner.pv_kwp:g}\,kWp, "
              rf"{best_corner.battery_kwh:g}\,kWh, "
              rf"{CONTROLLER_LABEL[best_corner.controller]} --- so the grid moves the "
              rf"answer without moving where it is found. The favourable corner is the "
              rf"quotable one: at the Green Book rate with double the base escalation the "
              rf"best battery in the study supports "
              rf"{_prose_money(best_corner.breakeven_gbp_per_kwh)}/kWh of an observed "
              rf"{_prose_money(observed)}/kWh, a shortfall of "
              rf"{_prose_money(observed - best_corner.breakeven_gbp_per_kwh)}/kWh that no "
              rf"defensible discounting closes. The two axes are of comparable strength "
              rf"--- 0--4\% escalation moves the ceiling by "
              rf"{ceilings['5.0', '4'].breakeven_ratio - ceilings['5.0', '0'].breakeven_ratio:.2f} "
              rf"at the base rate, 7--3.5\% discounting by "
              rf"{anchor.breakeven_ratio - ceilings['7.0', '2'].breakeven_ratio:.2f} at "
              rf"the base escalation --- and they compound rather than cancel, spanning "
              rf"{worst_corner.breakeven_ratio:.2f} to {best_corner.breakeven_ratio:.2f} "
              rf"against {base.breakeven_ratio:.2f} at the base case. Read against "
              rf"Table~\ref{{tab:myopia-bound}}: the myopia credit is the one correction "
              rf"that carries a configuration past 1.00, and it does so at the base "
              rf"escalation."),
        sources=[path for rate in GRID_RATES for escalation in GRID_ESCALATIONS
                 for path in (
                     (_grid_dir(rate, escalation) or Path("results")) / name.name
                     for name in CONSTANT_PRICE_ASSEMBLIES.values())],
    )


# --------------------------------------------------------------------------- #
# Chapter 4
# --------------------------------------------------------------------------- #

CAPEX_BASIS = Path("results/capex_basis/basis.csv")


def _capex_specifications() -> dict[str, tuple[float, float | None]]:
    """The three specifications compared, as (fixed GBP, GBP/kWh).

    A `None` per-kWh term means the adopted band schedule, which prices each size
    from its own DESNZ capacity band and carries no fixed term at all.
    """
    return {"DESNZ banded": (0.0, None),
            "DESNZ fitted": DESNZ_CAPEX,
            "Retail list": retail_capex()}


@table("t4.2", chapter=4, description="Viability under three capex specifications")
def build_t4_2() -> TableSpec:
    basis = pd.read_csv(CAPEX_BASIS)
    basis = basis[basis.battery_kwh.isin(REPORTED_SIZES)].copy()

    rows = []
    for policy in POLICY_ORDER:
        subset = basis[basis.policy == policy]
        for name, (fixed, per_kwh) in _capex_specifications().items():
            price = (subset.battery_kwh.map(band_price) if per_kwh is None else per_kwh)
            # The multiples k_F and k_c carry the replacement outflows and the
            # terminal residual, so this is the present value of the whole cost.
            cost = subset.k_F * fixed + subset.k_c * price * subset.battery_kwh
            scored = subset.assign(bbcr=subset.G / cost, shortfall=cost - subset.G)

            # Size, controller and penalty optimised within each cell on the cash
            # shortfall, which is what a buyer maximising NPV would do. Every
            # figure in a row then describes that one configuration. Optimising
            # on BBCR instead picks a larger pack under the fitted
            # specifications, because a fixed cost spread over more kWh lifts the
            # ratio while widening the gap in pounds.
            best = scored.loc[scored.groupby(CELL).shortfall.idxmin()]
            if len(best) != 54:
                raise ValueError(f"expected 54 cells for {policy}/{name}, got {len(best)}")
            top = best.loc[best.shortfall.idxmin()]
            if top.shortfall <= 0:
                raise ValueError(f"{policy}/{name} reaches break-even; the caption "
                                 "claims no configuration does")

            counts = best.battery_kwh.value_counts()
            rows.append({
                "Policy": POLICY_LABEL[policy],
                "Specification": name,
                "Best size": ", ".join(f"{size:g} ({count})" for size, count
                                       in counts.sort_values(ascending=False).items()),
                "BBCR": top.bbcr,
                "Shortfall": top.shortfall,
                "__cell": f"{top.location}/{top.tariff}/{top.pv_kwp:g}",
            })

    # The note names one cell for all six rows; assert it rather than assume it.
    cells = {row.pop("__cell") for row in rows}
    if len(cells) != 1:
        raise ValueError(f"the best cell is no longer shared: {sorted(cells)}; "
                         "the note must be rewritten")
    location, tariff, array = cells.pop().split("/")

    frame = pd.DataFrame(rows)
    _blank_repeats(frame, "Policy", every=3)

    return TableSpec(
        frame=frame,
        caption=(r"Battery viability under three capex specifications. Size, controller and "
                 r"degradation penalty are chosen within each of the 54 cells to minimise "
                 r"the cash shortfall, as a buyer maximising NPV would; the best of the 54 "
                 r"is reported. BBCR is that configuration's benefit--cost ratio: discounted "
                 r"benefit over the present value of cost, so 1.00 is break-even. No battery "
                 r"is worth adding under any specification or either policy. Best size is "
                 r"set by the price schedule, not by the battery: banding rewards the "
                 r"10\,kWh price drop, the fitted specifications reward amortising a fixed "
                 r"install cost."),
        label="tab:capex-specifications",
        column_format=("ll l S[table-format=1.3, round-mode=places, round-precision=3] "
                       + MONEY_COLUMN),
        selection=("per specification and policy, the smallest shortfall over the 54 "
                   "cells, with size, controller and penalty optimised within each cell"),
        formats={"BBCR": "{:.3f}", "Shortfall": "{:.0f}"},
        headers={"Best size": r"\shortstack[l]{Best size\\kWh (cells)}",
                 "Shortfall": r"\shortstack{Shortfall\\\pounds}"},
        row_rules={2: r"\midrule"},
        preamble=[r"\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(rf"Best size lists the sizes chosen across the 54 cells, most common first. "
              rf"Every row's best cell is {location.capitalize()}, {tariff.capitalize()}, "
              rf"{array}\,kWp, MILP-dispatched. Selecting on BBCR instead would choose a "
              r"larger pack --- 10\,kWh rather than 5 under the fitted specifications --- "
              r"and report a higher ratio against a wider cash gap. A replacement pays the "
              r"install overhead in full."),
        sources=[CAPEX_BASIS],
    )


# --------------------------------------------------------------------------- #
# Appendix
# --------------------------------------------------------------------------- #

# One table per tariff rather than one table of 54 rows. A 54-row block does not
# fit a page, and the alternative -- a longtable -- needs a package the document
# does not load, so the split is what keeps these tables ordinary floats built
# from booktabs and siunitx like every other table here. Tariff is also the axis
# the result actually separates on.
APPENDIX_LABEL = {"flat": "tab:appendix-flat", "e7": "tab:appendix-e7",
                  "agile": "tab:appendix-agile"}


def _delta_column(policy: str, size: float) -> str:
    """Frame column holding one policy and size of battery contribution."""
    return f"{policy} {size:g}"


def _appendix_tariff(tariff: str) -> TableSpec:
    """Every reported configuration of one tariff, one row per case.

    Two things are here that are nowhere else. The PV-only NPV of every array
    appears: t1.0a reports only the NPV-maximising array of each location and
    tariff and t1.a only aggregates, so text wanting a particular array's
    PV-only figure has had nothing to cite. And every reported configuration is
    printed, so the 0-of-54 headline can be checked case by case rather than
    taken on the aggregates.

    Battery columns carry `dnpv`, not whole-system NPV: the two differ by the
    PV-only column already on the row, so printing both would spend six columns
    on a sum the reader can do. The caption says so.
    """
    source = Path("results/sweep_scenarios_v2_band.csv")
    pv_only = load_pv_only(source)
    optimum = per_cell_optimum(load_assembly(source), ["policy", "battery_kwh"], by="dnpv")

    # The claim these tables exist to let a reader check. Asserted here so a
    # re-assembly that overturned it could not print a caption denying it.
    if (optimum.dnpv > 0).any():
        raise ValueError("a battery improves on its PV-only reference; the caption "
                         "and the study's headline both say none does")

    pv_only = pv_only[pv_only.tariff == tariff]
    optimum = optimum[optimum.tariff == tariff]
    keyed = optimum.set_index(CELL + ["policy", "battery_kwh"])
    expected = len(pv_only) * len(POLICY_ORDER) * len(REPORTED_SIZES)
    if len(keyed) != expected or not keyed.index.is_unique:
        raise ValueError(f"expected {expected} uniquely keyed {tariff} configurations, "
                         f"got {len(keyed)}")

    rows = []
    for location in LOCATION_ORDER:
        for pv_kwp in sorted(pv_only.pv_kwp.unique()):
            reference = pv_only[(pv_only.location == location) & (pv_only.pv_kwp == pv_kwp)]
            if len(reference) != 1:
                raise ValueError(f"expected one PV-only row for {location}/{tariff}/"
                                 f"{pv_kwp:g} kWp, got {len(reference)}")
            row = {"Location": location.capitalize(),
                   "kWp": _figure(pv_kwp, places=0),
                   "PV only": _money(reference.iloc[0].npv_gbp)}
            for policy in POLICY_ORDER:
                for size in REPORTED_SIZES:
                    row[_delta_column(policy, size)] = _money(
                        keyed.at[(location, tariff, pv_kwp, policy, size), "dnpv"])
            rows.append(row)

    frame = pd.DataFrame(rows)
    _blank_repeats(frame, "Location", every=len(pv_only) // len(LOCATION_ORDER))

    # 1-indexed position of the first battery column, for the spanned header.
    first_delta = 4
    spans = [(first_delta + offset * len(REPORTED_SIZES),
              first_delta + (offset + 1) * len(REPORTED_SIZES) - 1, POLICY_LABEL[policy])
             for offset, policy in enumerate(POLICY_ORDER)]

    best = optimum.loc[optimum.dnpv.idxmax()]
    worst = optimum.loc[optimum.dnpv.idxmin()]
    below = pv_only[pv_only.npv_gbp <= 0]
    exceptions = ""
    if len(below):
        named = " and ".join(f"{r.location.capitalize()} at {r.pv_kwp:g}\\,kWp "
                             f"({_prose_money(r.npv_gbp)})" for r in below.itertuples())
        exceptions = f", the {'exception' if len(below) == 1 else 'exceptions'} {named}"
    # Reported per size, never pooled: a count summed over sizes would answer no
    # question a buyer has, since a buyer installs one battery.
    positive = {(policy, size): int((group.npv_gbp > 0).sum())
                for (policy, size), group in optimum.groupby(["policy", "battery_kwh"])}
    counts = {policy: " / ".join(str(positive[(policy, size)]) for size in REPORTED_SIZES)
              for policy in POLICY_ORDER}
    # Controller is optimised per row but not printed: six more columns to say
    # what one sentence says, since on this tariff it is nearly constant.
    # Spelt out rather than CONTROLLER_LABEL's abbreviation: the note is prose,
    # and nothing in these tables defines the short form.
    modal = optimum.controller.value_counts().idxmax()
    modal_name = {"milp": "MILP", "self_consumption": "Self-consumption"}[modal]
    per_policy = len(optimum) // len(POLICY_ORDER)
    modal_counts = [int(((optimum.policy == policy) & (optimum.controller == modal)).sum())
                    for policy in POLICY_ORDER]
    if modal_counts == [per_policy] * len(POLICY_ORDER):
        chosen = f"every one of the {per_policy} configurations under both policies"
    else:
        chosen = " and ".join(
            f"{count} of {per_policy} under {POLICY_LABEL_INLINE[policy]}"
            for policy, count in zip(POLICY_ORDER, modal_counts))

    return TableSpec(
        frame=frame,
        caption=(rf"Every reported configuration on the {TARIFF_LABEL[tariff]} tariff. Each "
                 r"row is one case --- one location and array size --- giving the NPV of "
                 r"that array with no battery, then the change in whole-system NPV from "
                 r"adding a battery of each reported size under each lifetime policy. "
                 r"\emph{Whole-system NPV is the sum of the PV-only column and the battery "
                 r"column}, which is why it is not printed again. Controller and degradation "
                 r"penalty are optimised within every case, so each entry is the best that "
                 rf"battery can do there. With Tables~\ref{{{APPENDIX_LABEL['flat']}}}--"
                 rf"\ref{{{APPENDIX_LABEL['agile']}}} this is the whole reported result set; "
                 r"the tables of Chapter~1 summarise it."),
        label=APPENDIX_LABEL[tariff],
        column_format="l" + "r" * (2 + 1 + len(POLICY_ORDER) * len(REPORTED_SIZES)),
        selection=(f"every {tariff} case at every reported size and policy, controller and "
                   "penalty optimised within each case by dnpv; the PV-only column is the "
                   "zero-battery reference row of the same case"),
        formats={column: "{}" for column in frame.columns},
        headers={"Location": r"\textbf{Location}", "kWp": r"\textbf{kWp}",
                 "PV only": r"\textbf{\shortstack{PV only\\NPV}}"}
                | {_delta_column(policy, size): rf"\textbf{{{size:g}}}"
                   for policy in POLICY_ORDER for size in REPORTED_SIZES},
        header_rows=[
            "& & & "
            + " & ".join(rf"\multicolumn{{{len(REPORTED_SIZES)}}}{{c}}{{\textbf{{{label}}}}}"
                         for _, _, label in spans) + r" \\",
            " ".join(rf"\cmidrule(lr){{{start}-{end}}}" for start, end, _ in spans),
        ],
        row_rules={position: r"\addlinespace"
                   for position in range(len(pv_only) // len(LOCATION_ORDER) - 1,
                                         len(frame) - 1,
                                         len(pv_only) // len(LOCATION_ORDER))},
        preamble=[r"\footnotesize",
                  r"\sisetup{group-separator={,}, group-minimum-digits=4}"],
        note=(r"All figures in \pounds. The battery columns, headed by size in kWh, are the "
              r"change from the PV-only configuration in the same row, so a whole-system NPV "
              rf"is a column plus the PV-only column beside it. None of the {len(keyed)} "
              rf"entries is positive: the least damaging is {_prose_money(best.dnpv)} at "
              rf"{best.location.capitalize()}, {best.pv_kwp:g}\,kWp, {best.battery_kwh:g}"
              rf"\,kWh under {POLICY_LABEL_INLINE[best.policy]}, the worst "
              rf"{_prose_money(worst.dnpv)}. Summing the columns leaves "
              rf"{counts['no-replacement']} of {len(frame)} cases at "
              rf"{', '.join(f'{size:g}' for size in REPORTED_SIZES)}\,kWh positive under no "
              rf"replacement and {counts['run-to-fade']} under run to "
              r"\mbox{SOH$_{\min}$} --- each count a different battery, and so not to be "
              r"added together. A positive whole-system NPV means only that the array is "
              rf"large enough to carry the loss: the array alone is positive in "
              rf"{len(pv_only) - len(below)} of the {len(frame)} cases{exceptions}. "
              rf"{modal_name} dispatch is selected in {chosen}."),
        sources=[source],
    )


@table("tA.1", chapter=6, description="Appendix: every reported configuration, flat")
def build_tA_1() -> TableSpec:
    return _appendix_tariff("flat")


@table("tA.2", chapter=6, description="Appendix: every reported configuration, Economy 7")
def build_tA_2() -> TableSpec:
    return _appendix_tariff("e7")


@table("tA.3", chapter=6, description="Appendix: every reported configuration, Agile")
def build_tA_3() -> TableSpec:
    return _appendix_tariff("agile")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:12]


def _write(table_id: str, out_dir: Path) -> tuple[Path, str]:
    _, _, build = REGISTRY[table_id]
    spec = build()
    hashes = [_sha256(path) for path in spec.sources]
    out_path = out_dir / f"{table_id.replace('.', '_')}.tex"
    out_path.write_text(render(spec, table_id, hashes))
    return out_path, "+".join(hashes) or "n/a"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapter", type=int, help="build every table in this chapter")
    group.add_argument("--table", help="build one table by id, e.g. t1.2")
    group.add_argument("--all", action="store_true", help="build every registered table")
    group.add_argument("--list", action="store_true", help="list registered tables")
    parser.add_argument("--out-dir", type=Path, default=Path("results/tables"))
    args = parser.parse_args()

    if args.list:
        for table_id, (chapter, description, _) in sorted(REGISTRY.items()):
            print(f"  ch{chapter}  {table_id:<6} {description}")
        return

    if args.table:
        if args.table not in REGISTRY:
            raise SystemExit(f"unknown table {args.table!r}; try --list")
        wanted = [args.table]
    elif args.chapter:
        wanted = [t for t, (c, _, _) in REGISTRY.items() if c == args.chapter]
        if not wanted:
            raise SystemExit(f"no tables registered for chapter {args.chapter}")
    else:
        wanted = list(REGISTRY)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for table_id in sorted(wanted):
        out_path, source_hash = _write(table_id, args.out_dir)
        chapter, description, _ = REGISTRY[table_id]
        manifest.append((table_id, str(chapter), source_hash, str(out_path), description))
        print(f"  {table_id:<6} -> {out_path}  (source {source_hash})")

    manifest_path = args.out_dir / "MANIFEST.tsv"
    existing = {}
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines()[1:]:
            parts = line.split("\t")
            if parts:
                existing[parts[0]] = parts
    for entry in manifest:
        existing[entry[0]] = list(entry)
    lines = ["table_id\tchapter\tsource_sha256\toutput\tdescription"]
    lines += ["\t".join(existing[k]) for k in sorted(existing)]
    manifest_path.write_text("\n".join(lines) + "\n")
    print(f"  manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
