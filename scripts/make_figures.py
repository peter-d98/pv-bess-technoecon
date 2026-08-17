"""Generate the dissertation results figures.

Mirrors scripts/make_tables.py: figures are registered against a chapter so they
can be built a chapter at a time, and both consume the same definitions from
src/results_data.py so a figure can never drift from the table beside it.

    python scripts/make_figures.py --list
    python scripts/make_figures.py --chapter 1
    python scripts/make_figures.py --figure f1.2

Colours follow the Okabe-Ito palette already used by the input-data figures in
data/graphs, and the serif face matches the document.
"""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Allow running as a plain script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.assemble_band_capex import band_price
# Imported rather than restated: the myopia credit is a measured quantity, and
# the tornado bar must be the same number the controller chapter tabulates.
from scripts.make_tables import MYOPIA_AGILE_SHARE
from scripts.make_sensitivity_assemblies import REPLACEMENT_DECLINE_RATES
from src.data_loader import load_all
from src.locations import get_location, resolve_paths, resolve_pv_size_path
from src.results_data import (CONSTANT_PRICE_ASSEMBLIES, DESNZ_CAPEX, PV_COST_PER_KWP,
                              REPORTED_SIZES, attach_system_npv, best_in, load_assembly,
                              load_breakeven, load_pv_only, per_cell_optimum, retail_capex)

# The PV-only export volume is read straight from the profiles; these mirror the
# runner's own defaults (scripts/run_sweep.py).
DATA_DIR = Path("data")
DEMAND_PROFILE = DATA_DIR / "demand_halfhourly_2025.csv"
SWEEP_YEAR = 2025
HOURS_PER_STEP = 0.5

# Okabe-Ito: distinguishable under all common CVD types and in greyscale print.
COLOR_NO_REPLACEMENT = "#0072B2"
COLOR_RUN_TO_FADE = "#D55E00"
COLOR_LEVEL = "#0072B2"
COLOR_LOSS = "#D55E00"
INK_MUTED = "#4D4D4D"

TARIFF_LABEL = {"flat": "Flat", "e7": "Economy 7", "agile": "Agile"}
TARIFF_ORDER = ["flat", "e7", "agile"]
LOCATION_ORDER = ["inverness", "manchester", "plymouth"]
# Where the tariff is the series rather than the panel it needs its own hues.
# Categorical, unlike the ordered battery-size ramp below, so the three are fully
# distinct; marker and dash repeat the encoding for greyscale print.
TARIFF_STYLE = {"flat": ("#E69F00", "o", "-"), "e7": ("#009E73", "s", "--"),
                "agile": ("#0072B2", "^", (0, (5, 1.5)))}
CONTROLLER_LABEL = {"milp": "MILP", "self_consumption": "SC",
                    "self_consumption_tou": "SC (ToU)"}
# `run-to-fade` stays the key everywhere on disk; only the display label changed.
POLICY_STYLE = {
    "no-replacement": ("No replacement", COLOR_NO_REPLACEMENT, "o", "-"),
    "run-to-fade": (r"Run to SOH$_\mathrm{min}$", COLOR_RUN_TO_FADE, "s", "--"),
}

# Okabe-Ito continued: the dispatch figure needs four hues beyond the policy pair.
COLOR_PV = "#E69F00"
COLOR_CHARGE = "#56B4E9"
COLOR_DISCHARGE = "#009E73"
COLOR_SOC = "#0072B2"
COLOR_PRICE = "#CC79A7"

# Where the controller is the series rather than the panel it needs an identity
# hue. Light/mid/dark as well as three dash patterns, so the overlaid state-of-
# charge traces separate in greyscale print; none of the three is a dispatch hue
# above, so a controller identity is never mistaken for a power flow.
CONTROLLER_STYLE = {
    "self_consumption": ("#0072B2", "-"),
    "self_consumption_tou": ("#D55E00", (0, (5, 1.5))),
    "milp": ("#111111", (0, (1.5, 1.2))),
}
CONTROLLER_ORDER = ["self_consumption", "self_consumption_tou", "milp"]

# Battery size is ordered, so it gets a single-hue sequential ramp rather than
# three categorical hues, anchored on the project blue. Markers repeat the
# encoding for greyscale print.
SIZE_STYLE = {2.5: ("#7FB9DC", "o"), 5.0: ("#0072B2", "s"), 10.0: ("#04314F", "^")}

# PV array size is ordered too, and needs its own ramp where it is the series
# rather than the panel. Anchored on the PV orange above, not the battery blue,
# so the two size variables are never confusable in the same chapter.
PV_RAMP_ENDS = ((247, 214, 158), (94, 58, 0))

# Location is ordered as well -- north to south, and so in irradiance -- so it
# takes a ramp rather than three categorical hues, light to dark with increasing
# sun. Green, because orange is spent on array size and blue on battery size, so
# no two ordered variables in the chapter share a hue. Markers repeat the
# encoding for greyscale print. The ramp is the point of f5.2: if the response
# followed irradiance the lines would stack in ramp order, and they do not.
LOCATION_STYLE = {"inverness": ("#66C2A4", "o"), "manchester": ("#009E73", "s"),
                  "plymouth": ("#00513B", "^")}


def _pv_size_ramp(levels: int) -> list[str]:
    """`levels` hex colours along the PV ramp, light to dark."""
    return ["#%02X%02X%02X" % tuple(
                round(lo + (hi - lo) * step / max(levels - 1, 1))
                for lo, hi in zip(*PV_RAMP_ENDS))
            for step in range(levels)]

# Written by scripts/case_study_energy.py, which solves the base-case dispatches.
CASE_SCHEDULES = Path("results/case_study_schedules.csv")
SEASON_MONTHS = {"Winter": (12, 1, 2), "Summer": (6, 7, 8)}

# Written by scripts/case_study_fade.py, which replays the base-case fade.
CASE_FADE = Path("results/case_study_fade.csv")
SOH_FLOOR_PCT = 60.0

DISCOUNT_RATE = 0.05
PRICE_ESCALATION = 0.02
HORIZON_YEARS = 20

# Written by scripts/make_sensitivity_assemblies.py: one re-valuation of the same
# dispatch cache per sensitivity axis, each a pair of constant-price assemblies.
SENS_DIR = Path("results/sens")

# The closest approach to viability under each lifetime policy, held fixed
# across every bar of its tornado. Not re-optimised under each variant: a bar
# has to measure the assumption, not a change of controller or wear penalty.
# Both are the same physical system; only the wear penalty the sweep selects
# differs, so the two figures are read against each other directly.
TORNADO_CELL = {"location": "inverness", "tariff": "agile", "pv_kwp": 6.0,
                "battery_kwh": 2.5, "controller": "milp",
                "deg_penalty_gbp_per_kwh": 0.01, "policy": "no-replacement"}
TORNADO_CELL_FADE = TORNADO_CELL | {"deg_penalty_gbp_per_kwh": 0.03,
                                    "policy": "run-to-fade"}

# Shared by both battery tornados so their bars are directly comparable.
TORNADO_XLIM = (0.24, 1.50)

# The PV-only tornado prices the best PV system in the study -- the analogue of
# the closest-approach cell, and the system the PV headline actually names.
PV_TORNADO_CELL = {"location": "plymouth", "tariff": "flat", "pv_kwp": 6.0}
PV_TORNADO_XLIM = (0.62, 2.80)
PV_OM_FRAC = 0.01
PV_CAPEX_SPREAD = 0.20
# Flat and Economy 7 export at the flat SEG rate (Spec 03). The base is the
# national average in data/location_data_summary.md; the range is the spread of
# supplier SEG offers and is the one input here with no repository provenance.
SEG_BASE = 0.13
SEG_RANGE = (0.05, 0.16)
# Module output decline, absent from the model entirely: the sweep repeats year
# 1 for the whole horizon. Mid-range for c-Si.
PV_DEGRADATION = 0.005
PV_HORIZONS = {"hor15": 15, "hor25": 25}



@dataclass
class Figure:
    chapter: int
    description: str
    build: Callable[[Path], Path]


REGISTRY: dict[str, Figure] = {}


def figure(figure_id: str, chapter: int, description: str):
    def register(fn: Callable[[Path], Path]):
        REGISTRY[figure_id] = Figure(chapter, description, fn)
        return fn
    return register


def _configure_style() -> None:
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = [
        "Computer Modern Serif", "Computer Modern Roman", "CMU Serif", "DejaVu Serif",
    ]


def _style_axes(ax) -> None:
    ax.grid(True, axis="y", color="0.9", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("0.6")
    ax.tick_params(labelsize=9.5, color="0.6")


def _save(fig, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / name
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------- #
# Chapter 1
# --------------------------------------------------------------------------- #

@figure("f1.a", chapter=1,
        description="Whole-system NPV decomposed into PV and battery contributions")
def build_f1_a(out_dir: Path) -> Path:
    """The section-opening figure: does the system a buyer would install pay?

    Every other Chapter 1 figure answers one half of that. f1.0 prices the array
    alone and f1.1 prices the battery increment; this one stacks the two so the
    level and the increment are read off the same axis, and the whole-system NPV
    is the bottom edge of the stack rather than a separate series.

    Colour follows f1.2 rather than POLICY_STYLE: the hues encode value in and
    value out, as in the waterfall, and the lifetime policy is carried by the
    fill alpha and the marker instead. Using the policy hues here would put the
    same two colours on two different meanings within one chapter.
    """
    _configure_style()
    # Same-tariff baseline: the sweep prices every saving against a flat-tariff
    # grid-only household, which credits an Agile row for the tariff switch and
    # charges an E7 row for it. That is not an effect of installing anything, so
    # it is netted out of the level before the battery is stacked on it.
    pv_only = load_pv_only(same_tariff=True)
    optimum = attach_system_npv(
        per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv"), pv_only)

    positions = np.arange(len(REPORTED_SIZES) + 1)
    # The deduction is drawn narrower than the level it comes out of, and inside
    # it, so the PV block stays visible as a flank at every column. Hung from the
    # top of that block rather than stacked beside it, the bottom edge of the
    # deduction is the whole-system NPV.
    width, inner = 0.66, 0.36
    fig, axes = plt.subplots(1, 3, figsize=(7.6, 3.7), sharey=True)

    for ax, tariff in zip(axes, TARIFF_ORDER):
        panel = optimum[optimum.tariff == tariff]

        # The same 18 PV arrays sit under every column, so the blue bar is drawn
        # at all four positions rather than only over the PV-only one: holding it
        # visibly constant is what makes the deduction beside it legible. Checked
        # against the PV-only rows, so a drifted reference fails loudly.
        pv_level = float(panel.npv_same_tariff.mean())
        expected = float(pv_only.loc[pv_only.tariff == tariff, "npv_same_tariff"].mean())
        if abs(pv_level - expected) > 0.01:
            raise ValueError(f"{tariff}: mean PV reference {pv_level:.2f} does not match "
                             f"the PV-only assembly's {expected:.2f}")

        ax.bar(positions, pv_level, width=width, color=COLOR_LEVEL, edgecolor="white",
               linewidth=0.8, label="PV contribution" if tariff == TARIFF_ORDER[0] else None)

        for index, size in enumerate(REPORTED_SIZES):
            position = index + 1
            means = {policy: float(panel.loc[(panel.policy == policy)
                                             & (panel.battery_kwh == size), "dnpv"].mean())
                     for policy in POLICY_STYLE}
            upper, lower = means["no-replacement"], means["run-to-fade"]
            first = tariff == TARIFF_ORDER[0] and index == 0

            # No edge on the inner bars: a white one reads as a gap and splits
            # the PV block behind them into two unrelated bars.
            ax.bar(position, upper, bottom=pv_level, width=inner, color=COLOR_LOSS,
                   linewidth=0, zorder=3,
                   label="Battery contribution" if first else None)
            ax.bar(position, lower - upper, bottom=pv_level + upper, width=inner,
                   color=COLOR_LOSS, alpha=0.38, linewidth=0, zorder=3,
                   label=r"Additional loss, run to SOH$_\mathrm{min}$" if first else None)

        # Neutral, because the net is a result rather than a fourth series. The
        # PV-only column carries one marker: with no battery the policies coincide.
        nets = {policy: [pv_level] + [pv_level + float(
                    panel.loc[(panel.policy == policy) & (panel.battery_kwh == size), "dnpv"]
                    .mean()) for size in REPORTED_SIZES]
                for policy in POLICY_STYLE}
        label_of = {"no-replacement": "Whole-system NPV — no replacement",
                    "run-to-fade": r"— run to SOH$_\mathrm{min}$"}
        ax.scatter(positions, nets["no-replacement"], s=42, color=INK_MUTED, marker="o",
                   zorder=4, label=label_of["no-replacement"]
                   if tariff == TARIFF_ORDER[0] else None)
        ax.scatter(positions[1:], nets["run-to-fade"][1:], s=42, facecolors="white",
                   edgecolors=INK_MUTED, linewidths=1.3, marker="s", zorder=4,
                   label=label_of["run-to-fade"] if tariff == TARIFF_ORDER[0] else None)

        # The value each column resolves to, set above the block rather than by
        # the marker, where the +2.5 kWh pair would collide. Under no
        # replacement, matching the filled marker: the run-to-fade square is
        # always below it, so one number per column reads as the upper bound.
        # No pound sign: with one it is wider than its own column and the
        # negative labels collide. The axis already carries the unit.
        for position, net in zip(positions, nets["no-replacement"]):
            ax.annotate(f"{'−' if net < 0 else ''}{abs(net):,.0f}",
                        xy=(position, pv_level), xytext=(0, 7),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=9, color=INK_MUTED)

        ax.axhline(0.0, color=INK_MUTED, linewidth=1.2)
        _style_axes(ax)
        ax.set_xticks(positions)
        ax.set_xticklabels(["PV\nonly", *(f"+{s:g}" for s in REPORTED_SIZES)])
        ax.set_xlabel("Battery added (kWh)", fontsize=10)
        ax.set_title(TARIFF_LABEL[tariff], fontsize=11, loc="left", pad=16)


    axes[0].set_ylabel("Whole-system NPV (£)", fontsize=10)
    axes[0].yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    # Headroom above the tallest PV block, so the one direct label does not run
    # into the panel title.
    top = float(optimum.groupby("tariff").npv_same_tariff.mean().max())
    bottom = float(optimum.groupby(
        ["tariff", "policy", "battery_kwh"]).npv_system.mean().min())
    axes[0].set_ylim(bottom * 1.14, top * 1.30)

    # Explicit order: matplotlib fills the legend column-wise, which otherwise
    # separates the two net markers and splits the three bar entries.
    handles, labels = axes[0].get_legend_handles_labels()
    order = [labels.index(name) for name in
             ("PV contribution", "Battery contribution",
              r"Additional loss, run to SOH$_\mathrm{min}$",
              "Whole-system NPV — no replacement", r"— run to SOH$_\mathrm{min}$")]
    # Two columns, so the column-major fill keeps the three bar entries together
    # and the two net markers together.
    fig.legend([handles[i] for i in order], [labels[i] for i in order],
               frameon=False, fontsize=9.5, ncol=2, loc="lower center",
               bbox_to_anchor=(0.5, -0.17), handletextpad=0.5, columnspacing=2.4)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_a_system_npv_decomposition.png")


@figure("f1.b", chapter=1,
        description="Whole-system NPV, every reported configuration, decomposed")
def build_f1_b(out_dir: Path) -> Path:
    """f1.a without the averaging: one segment per configuration, nothing pooled.

    f1.a stacks a mean PV level and a mean battery deduction, which answers "what
    does a typical system return" --- a question this study does not ask. Here
    every one of the 162 reported configurations is drawn on its own, so the
    figure carries a distribution rather than a summary and the reader can find
    the exception if there is one. There is not one, and that is the finding: no
    segment anywhere points right.

    Each segment starts at its cell's PV-only NPV (hollow marker) and ends at the
    whole-system NPV of the same cell with the battery added (filled marker), so
    a dot answers the buyer's question --- does this bundle pay --- and the
    segment beside it answers the study's --- which half paid for it. The pale
    extension is the further loss under run to SOH_min, as in f1.a.

    Levels carry the sweep's own flat grid-only baseline, so a row on Agile or
    Economy 7 is credited or charged for the tariff switch. That term is a
    constant per (location, tariff): it shifts a whole row sideways and cancels
    out of every segment length, which is why no correction is applied here.
    """
    _configure_style()
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")

    # Rows run tariff-major, size-minor, top to bottom, with a gap between tariff
    # blocks. Tariff gets the outer grouping because it is the axis that separates
    # these rows; battery size orders them within a block.
    rows, y = {}, 0.0
    for tariff in TARIFF_ORDER:
        for size in REPORTED_SIZES:
            rows[(tariff, size)] = y
            y -= 1.0
        y -= 0.65

    pv_sizes = sorted(optimum.pv_kwp.unique())
    # Segments at one row would overplot into a single line, so the six arrays are
    # fanned across the row band in size order. The fan is an encoding, not
    # decoration: within a row, vertical position is the array size. Kept well
    # inside the 1.0 row pitch so the band reads as one group.
    fan = np.linspace(0.24, -0.24, len(pv_sizes))

    fig, axes = plt.subplots(1, 3, figsize=(7.9, 5.0), sharex=True, sharey=True)

    for ax, location in zip(axes, LOCATION_ORDER):
        panel = optimum[optimum.location == location]
        for (tariff, size), base in rows.items():
            block = panel[(panel.tariff == tariff) & (panel.battery_kwh == size)]
            level = block.set_index(["policy", "pv_kwp"])
            for offset, pv_kwp in zip(fan, pv_sizes):
                reference = level.loc[("no-replacement", pv_kwp), "npv_ref"]
                upper = level.loc[("no-replacement", pv_kwp), "npv_gbp"]
                lower = level.loc[("run-to-fade", pv_kwp), "npv_gbp"]
                row = base + offset
                first = (location == LOCATION_ORDER[0]
                         and (tariff, size) == (TARIFF_ORDER[0], REPORTED_SIZES[0])
                         and pv_kwp == pv_sizes[0])

                # Drawn thinner than the segment it continues, and terminated in
                # its own open marker: at equal weight the pale run reads as a
                # second line belonging to the row below rather than as the same
                # segment carried further left.
                ax.plot([lower, upper], [row, row], color=COLOR_LOSS, alpha=0.45,
                        linewidth=0.9, solid_capstyle="butt", zorder=2,
                        label=r"Further loss, run to SOH$_\mathrm{min}$" if first else None)
                ax.scatter([lower], [row], s=9, facecolors="white", alpha=0.95,
                           edgecolors=COLOR_LOSS, linewidths=0.7, marker="s", zorder=3)
                ax.plot([upper, reference], [row, row], color=COLOR_LOSS,
                        linewidth=1.9, solid_capstyle="butt", zorder=4,
                        label="Lost to the battery" if first else None)
                ax.scatter([reference], [row], s=14, facecolors="white",
                           edgecolors=COLOR_LEVEL, linewidths=1.1, zorder=5,
                           label="PV only" if first else None)
                ax.scatter([upper], [row], s=14, color=COLOR_LOSS, zorder=6,
                           label="PV + battery" if first else None)

        ax.axvline(0.0, color=INK_MUTED, linewidth=1.1, zorder=1)
        _style_axes(ax)
        ax.grid(False, axis="y")
        ax.grid(True, axis="x", color="0.9", linewidth=0.6)
        # Two-level row label: sizes on the spine, tariff set outside it. Putting
        # the sizes inside the panel instead collides with the longest segments.
        ax.set_yticks(list(rows.values()))
        ax.set_yticklabels([f"{size:g} kWh" for _, size in rows], fontsize=8.5,
                           color=INK_MUTED)
        ax.tick_params(axis="y", length=0)
        ax.set_xlabel("NPV (£)", fontsize=10)
        ax.set_title(location.capitalize(), fontsize=11, loc="left", pad=6)

    for tariff in TARIFF_ORDER:
        axes[0].annotate(TARIFF_LABEL[tariff],
                         xy=(-0.255, rows[(tariff, REPORTED_SIZES[1])]),
                         xycoords=("axes fraction", "data"), ha="center", va="center",
                         fontsize=10.5, rotation=90, annotation_clip=False)
    axes[0].set_ylim(min(rows.values()) - 0.9, max(rows.values()) + 0.9)
    for ax in axes:
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    handles, labels = axes[0].get_legend_handles_labels()
    order = [labels.index(name) for name in
             ("PV only", "PV + battery", "Lost to the battery",
              r"Further loss, run to SOH$_\mathrm{min}$")]
    fig.legend([handles[i] for i in order], [labels[i] for i in order], frameon=False,
               fontsize=9.5, ncol=4, loc="lower center", bbox_to_anchor=(0.5, -0.06),
               handletextpad=0.5, columnspacing=1.8)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_b_system_npv_segments.png")


def _system_npv_by_size(policy: str) -> dict[float, np.ndarray]:
    """Whole-system NPV over the 54 cells, at each battery size and at none.

    Keyed by battery size, with 0 for the PV-only reference, which is the same
    54 arrays under either policy because there is no battery to replace.
    """
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    optimum = optimum[optimum.policy == policy]

    series = {0.0: load_pv_only().npv_gbp.to_numpy()}
    for size in REPORTED_SIZES:
        values = optimum.loc[optimum.battery_kwh == size, "npv_gbp"].to_numpy()
        if len(values) != 54:
            raise ValueError(f"expected 54 cells at {size:g} kWh, got {len(values)}")
        series[size] = values
    return series


# Grey for the no-battery reference: it is the baseline every other curve is read
# against, not a fourth step on the size ramp.
REFERENCE_STYLE = ("#6E6E6E", "PV only")


@figure("f1.c", chapter=1,
        description="Distribution of whole-system NPV over the 54 cells, by battery size")
def build_f1_c(out_dir: Path) -> Path:
    """The verdict as a distribution: what share of systems pay, and by how much.

    An empirical CDF loses nothing --- it *is* the 54 cells, ordered --- so the
    figure answers "under what conditions" without pooling anything into a mean.
    Three readings, in the order they are meant to be taken:

      1. Where a curve crosses the zero line is the share of cells that pay, so
         the counts of tab:whole-system-viability are read off continuously
         rather than tabulated at four sizes.
      2. The curves march left as the battery grows, at every quantile, not only
         at the mean or the best case.
      3. The horizontal gap between a curve and the grey reference is the price
         of the battery in NPV, at matched quantiles of the same 54 cells.

    Cell identity is deliberately given up here --- which location, which tariff
    --- because that is what the parameter-axis section is for. This figure is
    the verdict; the axes decompose it.
    """
    _configure_style()
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.6), sharey=True, sharex=True)

    for ax, (policy, (label, _, _, _)) in zip(axes, POLICY_STYLE.items()):
        series = _system_npv_by_size(policy)
        for size, values in series.items():
            colour, name = (REFERENCE_STYLE if size == 0
                            else (SIZE_STYLE[size][0], f"+{size:g} kWh"))
            ordered = np.sort(values)
            share = np.arange(1, len(ordered) + 1) / len(ordered)
            # Stepped and closed at both ends, so the curve reads as a share of a
            # finite set of 54 rather than as a smooth density.
            ax.plot(np.concatenate([ordered[:1], ordered]),
                    np.concatenate([[0.0], share]),
                    drawstyle="steps-post", color=colour, linewidth=1.9,
                    label=name if policy == "no-replacement" else None)

            # The count at break-even, set on the curve it belongs to. This is
            # the number tab:whole-system-viability reports.
            below = int((ordered <= 0).sum())
            ax.scatter([0.0], [below / len(ordered)], s=26, color=colour, zorder=5)
            ax.annotate(f"{len(ordered) - below}", xy=(0.0, below / len(ordered)),
                        xytext=(5, -3), textcoords="offset points", ha="left",
                        va="top", fontsize=8.5, color=colour)

        ax.axvline(0.0, color=INK_MUTED, linewidth=1.1, zorder=1)
        _style_axes(ax)
        ax.grid(True, axis="x", color="0.9", linewidth=0.6)
        ax.set_xlabel("Whole-system NPV (£)", fontsize=10)
        ax.set_title(label, fontsize=11, loc="left", pad=6)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    axes[0].set_ylabel("Share of the 54 cells\nat or below", fontsize=10)
    axes[0].set_ylim(-0.02, 1.02)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4, loc="lower center",
               bbox_to_anchor=(0.5, -0.10), handletextpad=0.5, columnspacing=2.0)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_c_system_npv_ecdf.png")


@figure("f1.d", chapter=1,
        description="Whole-system NPV of every cell, by battery size and tariff")
def build_f1_d(out_dir: Path) -> Path:
    """The same 54 cells as f1.c, kept as points rather than ordered into a curve.

    Trades the direct reading of "what share pays" for cell identity: colouring
    by tariff keeps the strongest axis of the battery verdict visible inside the
    verdict figure, which the ECDF gives up. One row per battery size and policy,
    zero marked, no aggregate marker --- a mean over these rows would answer a
    question the study does not ask.
    """
    _configure_style()
    rows, y = [], 0.0
    for size in (0.0, *REPORTED_SIZES):
        for policy in (["no-replacement"] if size == 0 else list(POLICY_STYLE)):
            rows.append((size, policy, y))
            y -= 1.0
        y -= 0.4

    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    # Jitter within the row band, deterministic so the figure is reproducible.
    rng = np.random.default_rng(0)

    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    pv_only = load_pv_only()

    for size, policy, base in rows:
        block = (pv_only if size == 0 else
                 optimum[(optimum.battery_kwh == size) & (optimum.policy == policy)])
        for tariff, (colour, marker, _) in TARIFF_STYLE.items():
            values = block.loc[block.tariff == tariff, "npv_gbp"].to_numpy()
            offset = base + rng.uniform(-0.26, 0.26, len(values))
            first = (size, policy) == (0.0, "no-replacement")
            ax.scatter(values, offset, s=17, color=colour, marker=marker,
                       alpha=0.85, linewidths=0,
                       label=TARIFF_LABEL[tariff] if first else None)

    ax.axvline(0.0, color=INK_MUTED, linewidth=1.1, zorder=1)
    _style_axes(ax)
    ax.grid(False, axis="y")
    ax.grid(True, axis="x", color="0.9", linewidth=0.6)
    ax.set_yticks([base for _, _, base in rows])
    ax.set_yticklabels(["PV only" if size == 0 else
                        (f"+{size:g} kWh, no repl." if policy == "no-replacement"
                         else r"+%g kWh, run to SOH$_\mathrm{min}$" % size)
                        for size, policy, _ in rows], fontsize=9)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Whole-system NPV (£)", fontsize=10)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax.set_ylim(min(base for _, _, base in rows) - 0.8,
                max(base for _, _, base in rows) + 0.8)

    ax.legend(frameon=False, fontsize=9.5, ncol=3, loc="lower left",
              bbox_to_anchor=(0.0, -0.30), handletextpad=0.2, columnspacing=1.6)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_d_system_npv_dots.png")


@figure("f1.e", chapter=1,
        description="The two viability tests on one axis: the array priced alone, "
                    "the battery priced as an increment")
def build_f1_e(out_dir: Path) -> Path:
    """The section opener as a count, without pooling the count.

    Answers the four questions the chapter opens on -- how many configurations,
    how many whole systems pay, how many arrays pay, how many batteries pay --
    while keeping each count on the row whose design choice produced it. A single
    pooled "positive NPV" figure is the one number this figure refuses to draw:
    the whole-system count runs from 9 to 36 across the six rows, so pooling to
    120 of 324 would read as a third of batteries paying back when the
    incremental count is zero on every row.

    Two quantities, so two panels rather than one shared row band: the array is a
    level and the battery is an increment, and stacking them on one set of rows
    would invite the difference to be read as a distance along the axis. They
    share the £ axis and the zero line instead, which is the comparison that is
    actually meaningful -- the array's spread sits mostly right of zero and the
    battery's lies wholly left of it.

    Points are coloured by PV size, not tariff as in f1.d. The battery increment
    depends on the array it is added to -- it improves monotonically from 1 to
    6 kWp, by £352 at 2.5 kWh and £970 at 10 kWh -- and a figure that keeps all
    54 cases as points but hides which array they belong to would show that
    dependence as unexplained width. Sequential rather than categorical, because
    array size is ordered, and the ramp is monotone in luminance so it survives
    greyscale print without needing six markers.

    The PV-only panel carries the sweep's own flat grid-only baseline, matching
    tab:system-viability at 52 of 54. On a same-tariff baseline all 54 are
    positive; the difference is the tariff switch, not the array, and §8 of
    docs/results_summary.md prices it separately.
    """
    _configure_style()
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    pv_only = load_pv_only()
    cases = len(pv_only)

    pv_sizes = sorted(optimum.pv_kwp.unique())
    ramp = _pv_size_ramp(len(pv_sizes))

    rows, y = [], 0.0
    for size in REPORTED_SIZES:
        for policy in POLICY_STYLE:
            rows.append((size, policy, y))
            y -= 1.0
        y -= 0.35

    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(7.4, 5.2), sharex=True,
        gridspec_kw={"height_ratios": [1, len(rows)], "hspace": 0.28})
    # Deterministic, so the figure is reproducible run to run.
    rng = np.random.default_rng(0)

    def draw(ax, block, base, column):
        for colour, pv_kwp in zip(ramp, pv_sizes):
            for tariff, (_, marker, _) in TARIFF_STYLE.items():
                rows_here = block[(block.pv_kwp == pv_kwp) & (block.tariff == tariff)]
                values = rows_here[column].to_numpy()
                ax.scatter(values, base + rng.uniform(-0.26, 0.26, len(values)), s=17,
                           color=colour, marker=marker, alpha=0.9, linewidths=0)

    draw(top, pv_only, 0.0, "npv_gbp")
    for size, policy, base in rows:
        block = optimum[(optimum.battery_kwh == size) & (optimum.policy == policy)]
        if len(block) != cases:
            raise ValueError(f"expected {cases} cases at {size:g} kWh / {policy}, "
                             f"got {len(block)}")
        draw(bottom, block, base, "dnpv")

    # The counts, set in the right margin rather than on the points: they are the
    # figure's headline, and a reader who wants only the verdict reads this column
    # and stops. Both tests on every battery row, so the gap between them is read
    # off one row rather than assembled across two figures.
    def count_column(ax, base, offset, text, weight="normal"):
        ax.annotate(text, xy=(offset, base), xycoords=("axes fraction", "data"),
                    ha="center", va="center", fontsize=8.5, color=INK_MUTED,
                    fontweight=weight, annotation_clip=False)

    SYSTEM_X, BATTERY_X = 1.055, 1.20
    count_column(top, 0.0, SYSTEM_X, f"{int((pv_only.npv_gbp > 0).sum())} of {cases}")
    for size, policy, base in rows:
        block = optimum[(optimum.battery_kwh == size) & (optimum.policy == policy)]
        count_column(bottom, base, SYSTEM_X, f"{int((block.npv_gbp > 0).sum())} of {cases}")
        count_column(bottom, base, BATTERY_X, f"{int((block.dnpv > 0).sum())} of {cases}",
                     weight="bold")

    for label, x in (("System\npays", SYSTEM_X), ("Battery\nworth adding", BATTERY_X)):
        top.annotate(label, xy=(x, 1.42), xycoords="axes fraction", ha="center",
                     va="bottom", fontsize=8.5, color=INK_MUTED, annotation_clip=False)
    # No battery on the PV-only row, so the incremental test does not apply to it.
    count_column(top, 0.0, BATTERY_X, "--")

    for ax in (top, bottom):
        ax.axvline(0.0, color=INK_MUTED, linewidth=1.1, zorder=1)
        _style_axes(ax)
        ax.grid(False, axis="y")
        ax.grid(True, axis="x", color="0.9", linewidth=0.6)
        ax.tick_params(axis="y", length=0)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    top.set_yticks([0.0])
    top.set_yticklabels(["PV only"], fontsize=9)
    top.set_ylim(-0.55, 0.55)
    top.set_title(f"The array priced alone: NPV of {cases} arrays",
                  fontsize=10.5, loc="left", pad=6)

    bottom.set_yticks([base for _, _, base in rows])
    bottom.set_yticklabels(
        [f"+{size:g} kWh, no repl." if policy == "no-replacement"
         else r"+%g kWh, run to SOH$_\mathrm{min}$" % size for size, policy, _ in rows],
        fontsize=9)
    bottom.set_ylim(min(base for _, _, base in rows) - 0.7,
                    max(base for _, _, base in rows) + 0.7)
    bottom.set_title(f"The battery priced as an increment: "
                     rf"$\Delta$NPV of {len(rows) * cases} configurations",
                     fontsize=10.5, loc="left", pad=6)
    bottom.set_xlabel("NPV, and change in NPV against the array alone (£)", fontsize=10)

    # Swatches rather than markers: the ramp is one ordered variable, and six
    # legend markers would read as six unrelated series.
    handles = [plt.Line2D([], [], marker="s", linestyle="none", markersize=6.5,
                          markerfacecolor=colour, markeredgecolor="none",
                          label=f"{pv_kwp:g}")
               for colour, pv_kwp in zip(ramp, pv_sizes)]
    size_legend = fig.legend(handles=handles, title="PV array (kWp)", frameon=False,
                            fontsize=9, title_fontsize=9, ncol=len(pv_sizes),
                            loc="lower center", bbox_to_anchor=(0.30, -0.10),
                            handletextpad=0.1, columnspacing=0.9)
    fig.add_artist(size_legend)

    # Tariff separates each row into visible clusters -- flat is £2,400 worse than
    # Agile at 5 kWh -- so it is encoded rather than left as unexplained width.
    # Marker, not a second hue: colour is already spent on the ordered PV ramp.
    tariff_handles = [plt.Line2D([], [], marker=marker, linestyle="none", markersize=6,
                                 color=INK_MUTED, label=TARIFF_LABEL[tariff])
                      for tariff, (_, marker, _) in TARIFF_STYLE.items()]
    fig.legend(handles=tariff_handles, title="Tariff", frameon=False, fontsize=9,
               title_fontsize=9, ncol=len(tariff_handles), loc="lower center",
               bbox_to_anchor=(0.75, -0.10), handletextpad=0.1, columnspacing=1.0)

    return _save(fig, out_dir, "f1_e_two_tests.png")


@figure("f1.0", chapter=1, description="PV-only NPV against array size, by location and tariff")
def build_f1_0(out_dir: Path) -> Path:
    _configure_style()
    # The sweep's own baseline: a grid-only household on the flat tariff, which
    # is the alternative a buyer actually faces. A row on another tariff
    # therefore carries the tariff switch as well as the array; that term is
    # separated in tab:pv-only-viability rather than netted out here.
    pv_only = load_pv_only()
    sizes = sorted(pv_only.pv_kwp.unique())

    fig, axes = plt.subplots(1, 3, figsize=(7.6, 3.2), sharey=True)

    for ax, location in zip(axes, LOCATION_ORDER):
        panel = pv_only[pv_only.location == location]
        for tariff, (colour, marker, dashes) in TARIFF_STYLE.items():
            track = panel[panel.tariff == tariff].sort_values("pv_kwp")
            ax.plot(track.pv_kwp, track.npv_gbp, color=colour, linewidth=1.9,
                    linestyle=dashes, marker=marker, markersize=5,
                    label=TARIFF_LABEL[tariff] if location == LOCATION_ORDER[0] else None)

            # Ringed because it is the figure's second reading: on Agile the
            # optimum is interior, so more array is worse, while flat and
            # Economy 7 are still climbing at the largest size swept.
            peak = track.loc[track.npv_gbp.idxmax()]
            ax.scatter([peak.pv_kwp], [peak.npv_gbp], s=95, facecolors="none",
                       edgecolors=colour, linewidths=1.4, zorder=4)

        ax.axhline(0.0, color=INK_MUTED, linewidth=1.0)
        _style_axes(ax)
        ax.set_xticks(sizes)
        ax.set_xlabel("PV array (kWp)", fontsize=10)
        ax.set_title(location.capitalize(), fontsize=11, loc="left", pad=6)

    axes[0].set_ylabel("PV-only NPV (£)", fontsize=10)
    axes[0].yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    # The ring takes its series' colour on the data but a neutral one in the
    # legend, where a coloured exemplar would read as a fourth tariff.
    axes[0].scatter([], [], s=95, facecolors="none", edgecolors=INK_MUTED,
                    linewidths=1.4, label="Highest observed NPV")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, -0.07),
               handletextpad=0.5, columnspacing=2.0)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_0_pv_only_npv.png")


@figure("f1.1", chapter=1, description="Battery contribution to NPV by size, tariff and policy")
def build_f1_1(out_dir: Path) -> Path:
    _configure_style()
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")

    positions = np.arange(len(REPORTED_SIZES))
    fig, axes = plt.subplots(1, 3, figsize=(7.6, 3.1), sharey=True)

    for ax, tariff in zip(axes, TARIFF_ORDER):
        for policy, (label, colour, marker, dashes) in POLICY_STYLE.items():
            subset = optimum[(optimum.tariff == tariff) & (optimum.policy == policy)]
            grouped = subset.groupby("battery_kwh").dnpv
            means = np.array([grouped.mean()[s] for s in REPORTED_SIZES])
            highs = np.array([grouped.max()[s] for s in REPORTED_SIZES])
            lows = np.array([grouped.min()[s] for s in REPORTED_SIZES])

            ax.fill_between(positions, lows, highs, color=colour, alpha=0.13, linewidth=0)
            ax.plot(positions, means, color=colour, linewidth=2.0, linestyle=dashes,
                    marker=marker, markersize=5, label=label)

        ax.axhline(0.0, color=INK_MUTED, linewidth=1.0)
        _style_axes(ax)
        ax.set_xticks(positions)
        ax.set_xticklabels([f"{s:g}" for s in REPORTED_SIZES])
        ax.set_xlabel("Battery size (kWh)", fontsize=10)
        ax.set_title(TARIFF_LABEL[tariff], fontsize=11, loc="left", pad=6)

    axes[0].set_ylabel("Change in NPV from\nadding the battery (£)", fontsize=10)
    axes[0].yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    axes[0].legend(frameon=False, fontsize=9, loc="lower left", handlelength=2.2)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_1_battery_contribution.png")


@figure("f1.2", chapter=1, description="Why the closest-approach battery still fails")
def build_f1_2(out_dir: Path) -> Path:
    _configure_style()
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    best = optimum[optimum.policy == "no-replacement"].nlargest(1, "dnpv").iloc[0]
    year_one = best.dsaving_yr1

    years = np.arange(1, HORIZON_YEARS + 1)
    escalation = (1 + PRICE_ESCALATION) ** (years - 1)
    undiscounted = year_one * escalation.sum()
    discounted = year_one * (escalation / (1 + DISCOUNT_RATE) ** years).sum()
    delivered = best.dnpv + best.battery_capex

    steps = [
        ("Undiscounted\nsaving, 20 yr", 0.0, undiscounted, COLOR_LEVEL),
        ("Lost to\ndiscounting", discounted, undiscounted, COLOR_LOSS),
        ("Lost to\ncapacity fade", delivered, discounted, COLOR_LOSS),
        ("Battery\ncapex", best.dnpv, delivered, COLOR_LOSS),
        ("Battery\ncontribution", min(best.dnpv, 0.0), max(best.dnpv, 0.0), COLOR_LOSS),
    ]

    fig, ax = plt.subplots(figsize=(7.0, 3.9))
    for index, (label, bottom, top, colour) in enumerate(steps):
        ax.bar(index, top - bottom, bottom=bottom, width=0.58, color=colour,
               edgecolor="white", linewidth=0.8)
        if index < len(steps) - 1:
            ax.plot([index + 0.29, index + 0.71], [steps[index + 1][2]] * 2,
                    color=INK_MUTED, linewidth=0.8, linestyle=(0, (2, 2)))

    # Level bars are labelled outside the bar; the three deductions are labelled
    # inside, which is the only placement that does not collide at this aspect.
    annotations = [undiscounted, discounted - undiscounted, delivered - discounted,
                   best.dnpv - delivered, best.dnpv]
    for index, (value, (_, bottom, top, _)) in enumerate(zip(annotations, steps)):
        inside = 0 < index < len(steps) - 1
        if inside:
            ax.annotate(f"{value:+,.0f}", xy=(index, (bottom + top) / 2), ha="center",
                        va="center", fontsize=9.5, color="white")
        else:
            ax.annotate(f"{value:+,.0f}", xy=(index, top if value >= 0 else bottom),
                        xytext=(0, 6 if value >= 0 else -15), textcoords="offset points",
                        ha="center", fontsize=9.5, color=INK_MUTED)

    ax.axhline(0.0, color=INK_MUTED, linewidth=1.0)
    _style_axes(ax)
    ax.set_ylim(min(best.dnpv, 0) * 1.55, undiscounted * 1.12)
    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([label for label, *_ in steps], fontsize=9.5)
    ax.set_ylabel("Value of the battery (£)", fontsize=10)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax.set_title(
        f"{best.location.title()}, {TARIFF_LABEL[best.tariff]}, {best.pv_kwp:g} kWp PV, "
        f"{best.battery_kwh:g} kWh battery, never replaced",
        fontsize=11, loc="left", pad=8)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_2_closest_approach.png")


@figure("f1.3", chapter=1, description="Break-even battery price against the observed price")
def build_f1_3(out_dir: Path) -> Path:
    _configure_style()
    # Break-even is maximised within each cell, not dnpv: under run-to-fade the
    # purchase price also sets the replacement outflow, so the controller that
    # loses least at the observed price is not always the one that supports the
    # highest price.
    optimum = per_cell_optimum(load_breakeven(), ["policy", "battery_kwh"],
                               by="breakeven_ratio")

    offset = 0.17
    fig, axes = plt.subplots(3, 1, figsize=(7.0, 5.4), sharex=True)

    for ax, tariff in zip(axes, TARIFF_ORDER):
        for policy, (label, colour, marker, _) in POLICY_STYLE.items():
            subset = optimum[(optimum.tariff == tariff) & (optimum.policy == policy)]
            sign = 1 if policy == "no-replacement" else -1
            for index, size in enumerate(REPORTED_SIZES):
                ratios = subset.loc[subset.battery_kwh == size, "breakeven_ratio"]
                y = (len(REPORTED_SIZES) - 1 - index) + sign * offset
                # Range line first, so the 18 cell markers sit on top of it.
                ax.plot([ratios.min(), ratios.max()], [y, y],
                        color=colour, linewidth=1.0, alpha=0.45, zorder=1)
                ax.scatter(ratios, np.full(len(ratios), y), s=15, color=colour,
                           alpha=0.5, linewidths=0, zorder=2)
                ax.scatter([ratios.mean()], [y], s=62, color=colour, marker=marker,
                           edgecolors="white", linewidths=1.4, zorder=3,
                           label=label if index == 0 else None)

        ax.axvline(1.0, color=INK_MUTED, linewidth=1.2)
        # Band each size, so the paired policy markers cannot read as two sizes.
        for boundary in np.arange(len(REPORTED_SIZES) - 1) + 0.5:
            ax.axhline(boundary, color="0.75", linewidth=0.7,
                       linestyle=(0, (3, 3)), zorder=0)
        _style_axes(ax)
        ax.grid(False, axis="y")
        ax.grid(True, axis="x", color="0.9", linewidth=0.6)
        ax.set_ylim(-0.5, len(REPORTED_SIZES) - 0.5)
        ax.set_yticks(range(len(REPORTED_SIZES)))
        ax.set_yticklabels([f"{s:g} kWh" for s in reversed(REPORTED_SIZES)])
        ax.set_title(TARIFF_LABEL[tariff], fontsize=11, loc="left", pad=6)

    axes[0].set_xlim(0.0, 1.06)
    axes[0].xaxis.set_major_formatter(lambda v, _: f"{v:.2f}")
    # Labelled once, on the top panel only: the rule is the same in all three.
    axes[0].annotate("break-even", xy=(1.0, len(REPORTED_SIZES) - 0.5),
                     xytext=(-4, 2), textcoords="offset points", ha="right",
                     va="bottom", fontsize=9.5, color=INK_MUTED)
    axes[-1].set_xlabel("Battery benefit cost ratio", fontsize=10)

    # The one direct label: the closest any configuration comes to break-even.
    peak = optimum.loc[optimum.breakeven_ratio.idxmax()]
    axes[TARIFF_ORDER.index(peak.tariff)].annotate(
        f"{peak.breakeven_ratio:.2f}",
        xy=(peak.breakeven_ratio,
            len(REPORTED_SIZES) - 1 - REPORTED_SIZES.index(peak.battery_kwh) + offset),
        xytext=(7, 0), textcoords="offset points", ha="left", va="center",
        fontsize=9.5, color=INK_MUTED)

    # Below the axis label: every in-panel position collides with the flat rows.
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=2,
               loc="lower center", bbox_to_anchor=(0.5, -0.035),
               handletextpad=0.3, columnspacing=2.0)

    fig.tight_layout()
    return _save(fig, out_dir, "f1_3_breakeven_ratio.png")


# --------------------------------------------------------------------------- #
# Chapter 2
# --------------------------------------------------------------------------- #

def _agile_controller_case() -> pd.DataFrame:
    """The three base-case Agile schedules f2.0 compares, one per controller."""
    schedules = pd.read_csv(CASE_SCHEDULES, parse_dates=["timestamp"],
                            index_col="timestamp")
    case = schedules[(schedules.tariff == "agile")
                     & (schedules.policy == "no-replacement")]
    missing = set(CONTROLLER_ORDER) - set(case.controller)
    if missing:
        raise ValueError(f"no Agile schedule for {sorted(missing)}; "
                         f"run scripts/case_study_energy.py")
    return case


@figure("f2.0", chapter=2,
        description="Winter-day dispatch under all three controllers, base case on Agile")
def build_f2_0(out_dir: Path) -> Path:
    """The controller comparison the section rests on: one day, one system.

    A winter day rather than a summer one because the three controllers diverge
    on where charge comes from. In summer the PV surplus fills the battery
    before any of them has a decision to make, and SC and SC (ToU) collapse onto
    each other; in winter the charge has to come off the grid, which is exactly
    where the three separate — SC never grid-charges, SC (ToU) charges in the
    tariff's cheap window, the MILP charges at the day's price minimum.
    """
    case = _agile_controller_case()
    # The season's median-generation day, as in f2.1, so the panel is neither a
    # brightest- nor a dullest-day special case. Generation does not depend on
    # the controller, so any one of the three fixes the day for all of them.
    reference = case[case.controller == "milp"]
    daily_pv = reference.pv_kw.resample("D").sum() * HOURS_PER_STEP
    winter = daily_pv[daily_pv.index.month.isin(SEASON_MONTHS["Winter"])]
    chosen = (winter - winter.median()).abs().idxmin()
    return _controller_dispatch(out_dir, case, chosen,
                                "f2_0_controller_dispatch.png")


def _controller_dispatch(out_dir: Path, case: pd.DataFrame, chosen, name: str) -> Path:
    """Draw the three-controller dispatch comparison for one day."""
    _configure_style()

    def closed(values) -> np.ndarray:
        """Repeat the 23:30 value at 24:00 so the last half-hour is drawn."""
        array = np.asarray(values, dtype=float)
        return np.append(array, array[-1])

    fig, axes = plt.subplots(
        5, 1, figsize=(7.0, 8.4), sharex=True,
        gridspec_kw={"height_ratios": [1.5, 1.5, 1.5, 1.15, 0.85]})

    days = {controller: case[(case.controller == controller)
                             & (case.index.date == chosen.date())]
            for controller in CONTROLLER_ORDER}

    for axis, controller in zip(axes, CONTROLLER_ORDER):
        day = days[controller]
        hours = np.append(day.index.hour + day.index.minute / 60.0, 24.0)

        axis.fill_between(hours, closed(day.pv_kw), step="post", color=COLOR_PV,
                          alpha=0.30, linewidth=0, label="PV generation")
        axis.plot(hours, closed(day.pv_kw), drawstyle="steps-post",
                  color=COLOR_PV, linewidth=1.3)
        # Above the fills: the point of the panel is which flows meet demand.
        axis.plot(hours, closed(day.demand_kw), drawstyle="steps-post",
                  color=INK_MUTED, linewidth=1.3, label="Demand", zorder=4)

        # One signed series, as in f2.1: charging draws the battery below the
        # axis, so the two directions cannot read as two unrelated quantities.
        battery = closed(day.p_discharge_kw - day.p_charge_kw)
        axis.fill_between(hours, battery.clip(min=0), step="post",
                          color=COLOR_DISCHARGE, linewidth=0,
                          label="Battery discharge")
        axis.fill_between(hours, battery.clip(max=0), step="post",
                          color=COLOR_CHARGE, linewidth=0, label="Battery charge")
        axis.axhline(0.0, color=INK_MUTED, linewidth=0.8)
        axis.set_ylabel("Power (kW)", fontsize=10)
        # Titled in the controller's own identity hue, which is also the hue of
        # its trace in the state-of-charge panel below.
        axis.set_title(CONTROLLER_LABEL[controller], fontsize=11, loc="left",
                       pad=5, color=CONTROLLER_STYLE[controller][0])

    # Shared by hand rather than by `sharey`, so the three panels stay
    # comparable in height without the outer two losing their tick labels.
    limits = [(day.p_discharge_kw - day.p_charge_kw).agg(["min", "max"])
              for day in days.values()]
    span = max(max(abs(v) for v in pair) for pair in limits)
    ceiling = max(span, max(day.demand_kw.max() for day in days.values()),
                  max(day.pv_kw.max() for day in days.values()))
    for axis in axes[:3]:
        axis.set_ylim(-span * 1.15, ceiling * 1.12)

    soc_axis = axes[3]
    for controller in CONTROLLER_ORDER:
        day = days[controller]
        hours = np.append(day.index.hour + day.index.minute / 60.0, 24.0)
        colour, dash = CONTROLLER_STYLE[controller]
        soc_axis.plot(hours, closed(day.soc * 100), drawstyle="steps-post",
                      color=colour, linestyle=dash, linewidth=1.6,
                      label=CONTROLLER_LABEL[controller])
    soc_axis.set_ylabel("State of\ncharge (%)", fontsize=10)
    # Headroom above the 90% ceiling rather than a tighter limit: the key has to
    # sit clear of three traces that between them cross most of the panel.
    soc_axis.set_ylim(0, 132)
    soc_axis.set_yticks([0, 50, 100])
    soc_axis.legend(frameon=False, fontsize=9, loc="upper left", ncol=3,
                    handlelength=2.4, columnspacing=1.4, borderpad=0.2)

    # Prices do not depend on the controller, so the panel is drawn once.
    price_axis = axes[4]
    day = days["milp"]
    hours = np.append(day.index.hour + day.index.minute / 60.0, 24.0)
    price_axis.plot(hours, closed(day.import_price * 100), drawstyle="steps-post",
                    color=COLOR_PRICE, linewidth=1.5, label="Import")
    price_axis.plot(hours, closed(day.export_price * 100), drawstyle="steps-post",
                    color=COLOR_PRICE, linewidth=1.3, linestyle=(0, (3, 2)),
                    alpha=0.75, label="Export")
    price_axis.set_ylabel("Price\n(p/kWh)", fontsize=10)
    price_axis.set_xlabel("Hour of day", fontsize=10)
    # The evening spike leaves no room for a second block of text here, so the
    # shaded window is explained in the caption rather than in the panel.
    price_axis.legend(frameon=False, fontsize=9, loc="upper left", ncol=2,
                      handlelength=2.0, columnspacing=1.4, borderpad=0.2)

    for axis in axes:
        _style_axes(axis)
        axis.set_xlim(0, 24)
        # Finer than f2.1's six-hour ticks: with one day rather than two, when
        # each controller charges is the whole point of the figure.
        axis.set_xticks(range(0, 25, 3))
    axes[0].set_title(f"{chosen:%-d %B %Y}", fontsize=10, loc="right", pad=5,
                      color=INK_MUTED)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, -0.028),
               handletextpad=0.5, columnspacing=1.8)

    fig.tight_layout()
    return _save(fig, out_dir, name)


@figure("f2.1", chapter=2,
        description="Base-case daily dispatch on a winter and a summer day")
def build_f2_1(out_dir: Path) -> Path:
    _configure_style()
    schedules = pd.read_csv(CASE_SCHEDULES, parse_dates=["timestamp"],
                            index_col="timestamp")
    # Agile now carries a rules-based schedule per controller for f2.0, so this
    # panel has to name the one it wants rather than take the whole tariff.
    case = schedules[(schedules.tariff == "agile")
                     & (schedules.policy == "no-replacement")
                     & (schedules.controller == "milp")]
    daily_pv = case.pv_kw.resample("D").sum() * 0.5

    def closed(values) -> np.ndarray:
        """Repeat the 23:30 value at 24:00 so the last half-hour is drawn."""
        array = np.asarray(values, dtype=float)
        return np.append(array, array[-1])

    fig, axes = plt.subplots(3, 2, figsize=(7.6, 5.8), sharex="col", sharey="row",
                             gridspec_kw={"height_ratios": [2.0, 1.0, 1.0]})

    for column, (season, months) in enumerate(SEASON_MONTHS.items()):
        # The season's median-generation day, so neither panel is a brightest-
        # or dullest-day special case.
        seasonal = daily_pv[daily_pv.index.month.isin(months)]
        chosen = (seasonal - seasonal.median()).abs().idxmin()
        day = case[case.index.date == chosen.date()]
        hours = np.append(day.index.hour + day.index.minute / 60.0, 24.0)

        power, soc, price = axes[:, column]

        power.fill_between(hours, closed(day.pv_kw), step="post", color=COLOR_PV,
                           alpha=0.30, linewidth=0, label="PV generation")
        power.plot(hours, closed(day.pv_kw), drawstyle="steps-post",
                   color=COLOR_PV, linewidth=1.3)
        # Above the fills: the point of the panel is which flows meet demand.
        power.plot(hours, closed(day.demand_kw), drawstyle="steps-post",
                   color=INK_MUTED, linewidth=1.3, label="Demand", zorder=4)

        # One signed series: charging draws the battery below the axis, so the
        # two directions cannot be confused for two unrelated quantities.
        battery = closed(day.p_discharge_kw - day.p_charge_kw)
        power.fill_between(hours, battery.clip(min=0), step="post",
                           color=COLOR_DISCHARGE, linewidth=0, label="Battery discharge")
        power.fill_between(hours, battery.clip(max=0), step="post",
                           color=COLOR_CHARGE, linewidth=0, label="Battery charge")
        power.axhline(0.0, color=INK_MUTED, linewidth=0.8)

        soc.fill_between(hours, closed(day.soc * 100), step="post",
                         color=COLOR_SOC, alpha=0.22, linewidth=0)
        soc.plot(hours, closed(day.soc * 100), drawstyle="steps-post",
                 color=COLOR_SOC, linewidth=1.5)

        price.plot(hours, closed(day.import_price * 100), drawstyle="steps-post",
                   color=COLOR_PRICE, linewidth=1.5, label="Import")
        price.plot(hours, closed(day.export_price * 100), drawstyle="steps-post",
                   color=COLOR_PRICE, linewidth=1.3, linestyle=(0, (3, 2)),
                   alpha=0.75, label="Export")

        for ax in (power, soc, price):
            _style_axes(ax)
            ax.set_xlim(0, 24)
            ax.set_xticks(range(0, 25, 6))
        power.set_title(f"{season} — {chosen:%-d %B}", fontsize=11, loc="left", pad=6)
        price.set_xlabel("Hour of day", fontsize=10)

    axes[0, 0].set_ylabel("Power (kW)", fontsize=10)
    # Charging runs at the full 2.5 kW limit, which otherwise sits on the frame.
    axes[0, 0].margins(y=0.10)
    axes[1, 0].set_ylabel("State of\ncharge (%)", fontsize=10)
    axes[1, 0].set_ylim(0, 105)
    axes[2, 0].set_ylabel("Price\n(p/kWh)", fontsize=10)
    axes[2, 1].legend(frameon=False, fontsize=9, loc="upper right", handlelength=2.0,
                      labelspacing=0.3, borderpad=0.2)

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, -0.045),
               handletextpad=0.5, columnspacing=1.8)

    fig.tight_layout()
    return _save(fig, out_dir, "f2_1_daily_dispatch.png")


@figure("f2.4", chapter=2, description="Base-case capacity fade under both lifetime policies")
def build_f2_4(out_dir: Path) -> Path:
    _configure_style()
    fade = pd.read_csv(CASE_FADE)

    fig, axes = plt.subplots(1, 3, figsize=(7.6, 3.2), sharey=True)

    for ax, tariff in zip(axes, TARIFF_ORDER):
        panel = fade[fade.tariff == tariff]
        for policy, (label, colour, _, dashes) in POLICY_STYLE.items():
            track = panel[panel.policy == policy]
            ax.plot(track.year, track.soh * 100, color=colour, linewidth=1.9,
                    linestyle=dashes, label=label)

            # Only run-to-fade replaces, and the jump back to 100% is the whole
            # difference between the policies, so it is marked rather than left
            # to be inferred from the step.
            for year in track.loc[track.replaced, "year"]:
                ax.axvline(year, color=colour, linewidth=0.9,
                           linestyle=(0, (1, 2)), zorder=0)
                ax.annotate(f"replaced,\nyear {year:g}", xy=(year, 42),
                            xytext=(-4, 0), textcoords="offset points",
                            ha="right", va="bottom", fontsize=8.5, color=colour)

        ax.axhline(SOH_FLOOR_PCT, color=INK_MUTED, linewidth=1.0,
                   linestyle=(0, (4, 3)), label="Replacement floor")
        _style_axes(ax)
        ax.set_xlim(1, HORIZON_YEARS)
        ax.set_xticks([1, 5, 10, 15, 20])
        ax.set_xlabel("Year", fontsize=10)

        # The controller and penalty differ by tariff, and on Agile the two
        # policies do not even share a penalty, so each panel names its own.
        controllers = panel.drop_duplicates(["controller", "penalty_gbp_per_kwh"])
        penalties = "/".join(f"{p * 100:g}p" for p in
                             sorted(controllers.penalty_gbp_per_kwh.dropna()))
        controller = CONTROLLER_LABEL[controllers.controller.iloc[0]]
        ax.set_title(f"{TARIFF_LABEL[tariff]} — {controller}"
                     f"{' ' + penalties if penalties else ''}",
                     fontsize=11, loc="left", pad=6)

    axes[0].set_ylim(38, 104)
    axes[0].set_ylabel("State of health (%)", fontsize=10)
    axes[0].annotate(f"{SOH_FLOOR_PCT:g}%", xy=(1, SOH_FLOOR_PCT), xytext=(2, 3),
                     textcoords="offset points", fontsize=9, color=INK_MUTED)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=3,
               loc="lower center", bbox_to_anchor=(0.5, -0.07),
               handletextpad=0.5, columnspacing=2.0)

    fig.tight_layout()
    return _save(fig, out_dir, "f2_4_fade_trajectories.png")


@figure("f2.2", chapter=2,
        description="Break-even price against the observed band price, in £/kWh")
def build_f2_2(out_dir: Path) -> Path:
    _configure_style()
    # Maximised on break-even rather than dnpv, for the reason given in f1.3.
    optimum = per_cell_optimum(load_breakeven(), ["policy", "battery_kwh"],
                               by="breakeven_ratio")

    positions = np.arange(len(REPORTED_SIZES))
    observed = np.array([band_price(size) for size in REPORTED_SIZES])

    fig, axes = plt.subplots(1, 3, figsize=(7.6, 3.4), sharey=True)

    for ax, tariff in zip(axes, TARIFF_ORDER):
        # The observed price is constant within a DESNZ band, so it steps rather
        # than slopes. The 6 kWh band edge lies between the 5 and 10 kWh columns;
        # on an axis whose sizes are evenly spaced rather than to scale, the
        # midpoint is the only position that does not imply a specific kWh.
        ax.step(positions, observed, where="mid", color=INK_MUTED, linewidth=1.8,
                label="Observed price (DESNZ band median)")

        for policy, (label, colour, marker, dashes) in POLICY_STYLE.items():
            subset = optimum[(optimum.tariff == tariff) & (optimum.policy == policy)]
            grouped = subset.groupby("battery_kwh").breakeven_gbp_per_kwh
            means = np.array([grouped.mean()[s] for s in REPORTED_SIZES])
            highs = np.array([grouped.max()[s] for s in REPORTED_SIZES])
            lows = np.array([grouped.min()[s] for s in REPORTED_SIZES])

            ax.fill_between(positions, lows, highs, color=colour, alpha=0.13, linewidth=0)
            ax.plot(positions, means, color=colour, linewidth=2.0, linestyle=dashes,
                    marker=marker, markersize=5, label=label)

        _style_axes(ax)
        ax.set_xticks(positions)
        ax.set_xticklabels([f"{s:g}" for s in REPORTED_SIZES])
        ax.set_xlim(-0.35, len(REPORTED_SIZES) - 0.65)
        ax.set_xlabel("Battery size (kWh)", fontsize=10)
        ax.set_title(TARIFF_LABEL[tariff], fontsize=11, loc="left", pad=6)

    axes[0].set_ylim(0, 1480)
    axes[0].set_ylabel("Break-even battery price (£/kWh)", fontsize=10)
    axes[0].yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    # The step is the figure's second point, so both of its levels are labelled
    # once, on the panel with the most clear space beneath the reference line.
    for index, price in ((0, observed[0]), (len(REPORTED_SIZES) - 1, observed[-1])):
        axes[0].annotate(f"£{price:,.0f}", xy=(index, price), xytext=(0, 5),
                         textcoords="offset points", ha="center", fontsize=9.5,
                         color=INK_MUTED)

    # The one direct label on the data: the highest price any configuration in
    # the study can support, against a £1,300 asking price in the same column.
    peak = optimum.loc[optimum.breakeven_gbp_per_kwh.idxmax()]
    axes[TARIFF_ORDER.index(peak.tariff)].annotate(
        f"£{peak.breakeven_gbp_per_kwh:,.0f}",
        xy=(REPORTED_SIZES.index(peak.battery_kwh), peak.breakeven_gbp_per_kwh),
        xytext=(0, 7), textcoords="offset points", ha="center", fontsize=9.5,
        color=INK_MUTED)

    handles, labels = axes[0].get_legend_handles_labels()
    # Reference line last: it is the thing the two policy curves are read against.
    order = [1, 2, 0]
    fig.legend([handles[i] for i in order], [labels[i] for i in order],
               frameon=False, fontsize=9.5, ncol=3, loc="lower center",
               bbox_to_anchor=(0.5, -0.06), handletextpad=0.5, columnspacing=2.0)

    fig.tight_layout()
    return _save(fig, out_dir, "f2_2_breakeven_vs_price.png")


# --------------------------------------------------------------------------- #
# Chapter 3
# --------------------------------------------------------------------------- #

def _controller_pairs() -> pd.DataFrame:
    """One row per configuration: the MILP against the better of the heuristics.

    A configuration is a cell at one battery size under one lifetime policy, and
    every controller is read at its own best penalty, so the MILP is never
    handicapped by a bad one. This is the pairing tab:controller-choice counts,
    reused rather than restated so the figure cannot disagree with the table.

    The comparison is against the *better* heuristic, not against plain SC: it
    gives the heuristics their best shot, so a positive difference is an
    unambiguous MILP win and the count of positives is the table's MILP column.
    """
    config = ["location", "tariff", "pv_kwp", "battery_kwh", "policy"]
    assembly = load_assembly()
    best_penalty = assembly.loc[assembly.groupby(config + ["controller"]).dnpv.idxmax()]
    wide = best_penalty.pivot_table(index=config, columns="controller", values="dnpv")

    missing = wide.isna().any(axis=1).sum()
    if missing:
        raise ValueError(f"{missing} configurations are missing a controller")

    wide["rules"] = wide[["self_consumption", "self_consumption_tou"]].max(axis=1)
    wide["advantage"] = wide.milp - wide.rules
    return wide.reset_index()


@figure("f3.0", chapter=3,
        description="Paired MILP-vs-heuristic differences, every configuration")
def build_f3_0(out_dir: Path) -> Path:
    """What the optimiser is worth, configuration by configuration.

    Paired rather than distributional: the MILP and the heuristics are run on the
    same cell, the same array, the same pack and the same year, so the matched
    difference is the estimate and two overlaid distributions would throw away
    the pairing that makes it precise. Zero is not a reference level here, it is
    the decision boundary --- a point right of it is a configuration where
    optimising dispatch paid.

    The reading the chapter needs is that the answer is categorical in the
    tariff, not marginal: every Economy 7 and Agile configuration is a win and
    the flat ones are a wash, with no overlap between the groups. That is the
    evidence for treating the controller question as settled by the price signal
    rather than by the optimiser.

    One shared linear axis across all six rows, deliberately, even though it
    compresses flat against zero. The magnitudes are the second finding --- the
    MILP earns up to GBP3,422 on Agile and nothing on flat --- and rescaling
    flat to fill its row would hide exactly that. The sign on flat is carried by
    the margin counts instead of by pixels.

    Points are coloured by battery size because the advantage scales with it:
    the median Agile gain triples from 2.5 to 10 kWh. Left uncoded, that would
    read as unexplained width in every row.
    """
    _configure_style()
    pairs = _controller_pairs()

    # Header and counts share one centre, so the column reads as a column.
    COUNT_COLUMN_X = 1.09

    fig, axes = plt.subplots(1, 2, figsize=(7.8, 3.5), sharex=True, sharey=True)
    # Deterministic, so the figure is reproducible run to run.
    rng = np.random.default_rng(0)
    positions = {tariff: -index for index, tariff in enumerate(TARIFF_ORDER)}

    for ax, (policy, (label, _, _, _)) in zip(axes, POLICY_STYLE.items()):
        panel = pairs[pairs.policy == policy]
        for tariff, base in positions.items():
            block = panel[panel.tariff == tariff]
            if len(block) != 54:
                raise ValueError(f"expected 54 configurations for {policy}/{tariff}, "
                                 f"got {len(block)}")
            for size, (colour, marker) in SIZE_STYLE.items():
                values = block.loc[block.battery_kwh == size, "advantage"].to_numpy()
                first = tariff == TARIFF_ORDER[0] and policy == "no-replacement"
                ax.scatter(values, base + rng.uniform(-0.28, 0.28, len(values)),
                           s=17, color=colour, marker=marker, alpha=0.85, linewidths=0,
                           label=f"{size:g} kWh" if first else None)

            # The sign, stated rather than read off the pixels -- on flat the
            # points sit inside the zero line's own width at this scale.
            wins = int((block.advantage > 1e-6).sum())
            ax.annotate(f"{wins} of {len(block)}", xy=(COUNT_COLUMN_X, base),
                        xycoords=("axes fraction", "data"), ha="center", va="center",
                        fontsize=8.5, color=INK_MUTED, annotation_clip=False)

        # Between the tariff rows, not through them: the rows are three separate
        # groups and the eye needs the boundary at this point density.
        for base in list(positions.values())[:-1]:
            ax.axhline(base - 0.5, color="0.75", linewidth=0.7,
                       linestyle=(0, (1, 2)), zorder=0)

        ax.axvline(0.0, color=INK_MUTED, linewidth=1.1, zorder=1)
        _style_axes(ax)
        ax.grid(False, axis="y")
        ax.grid(True, axis="x", color="0.9", linewidth=0.6)
        ax.tick_params(axis="y", length=0)
        ax.set_title(label, fontsize=11, loc="left", pad=16)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
        # Headed on both panels: each carries its own count column, and one
        # header at the far right would appear to caption only the right one.
        ax.annotate("MILP wins", xy=(COUNT_COLUMN_X, max(positions.values()) + 0.70),
                    xycoords=("axes fraction", "data"), ha="center", va="bottom",
                    fontsize=8.5, color=INK_MUTED, annotation_clip=False)

    axes[0].set_yticks(list(positions.values()))
    axes[0].set_yticklabels([TARIFF_LABEL[t] for t in positions], fontsize=9.5)
    axes[0].set_ylim(min(positions.values()) - 0.62, max(positions.values()) + 0.62)

    # One shared label: both panels carry the same quantity on a shared axis, and
    # a definition this long reads as two different measures when repeated.
    fig.supxlabel("MILP advantage over the better RBC (£)", fontsize=10, y=0.02)

    # Swatches rather than markers: battery size is one ordered variable, and
    # three legend markers would read as three unrelated series.
    handles = [plt.Line2D([], [], marker=marker, linestyle="none", markersize=6,
                          markerfacecolor=colour, markeredgecolor="none",
                          color=colour, label=f"{size:g} kWh")
               for size, (colour, marker) in SIZE_STYLE.items()]
    fig.legend(handles=handles, title="Battery", frameon=False, fontsize=9,
               title_fontsize=9, ncol=len(handles), loc="lower center",
               bbox_to_anchor=(0.5, -0.14), handletextpad=0.2, columnspacing=1.4)

    fig.tight_layout()
    return _save(fig, out_dir, "f3_0_controller_paired_advantage.png")


@figure("f3.1", chapter=3, description="Battery contribution against the degradation penalty")
def build_f3_1(out_dir: Path) -> Path:
    _configure_style()
    # MILP only: the penalty is the MILP's decision variable and the heuristics
    # carry no value for it.
    milp = load_assembly()
    milp = milp[milp.controller == "milp"]

    # Flat is shown for contrast even though self-consumption is the selected
    # controller in every flat configuration under no replacement: its flat
    # penalty response is what makes the point that only Agile trades off.
    tariffs = ["flat", "e7", "agile"]

    cycle_life = milp.cycle_life_efc.unique()
    if len(cycle_life) != 1:
        raise ValueError(f"expected one cycle life in the assembly, got {cycle_life}")

    # c_thr = capex / (EFC x 2 x Q) = price / (2 x EFC): the nominal capacity
    # cancels, so the conventional derivation gives one value per price band
    # rather than one per size. See spec-02 section 4.1.
    derived = {price: 100 * price / (2 * cycle_life[0])
               for price in sorted({band_price(s) for s in REPORTED_SIZES})}
    sizes_of = {price: [s for s in REPORTED_SIZES if band_price(s) == price]
                for price in derived}

    fig, axes = plt.subplots(2, len(tariffs), figsize=(7.6, 5.0),
                             sharex=True, sharey=True)

    for row, (policy, (policy_label, *_)) in enumerate(POLICY_STYLE.items()):
        for column, tariff in enumerate(tariffs):
            ax = axes[row, column]
            subset = milp[(milp.policy == policy) & (milp.tariff == tariff)]
            for size, (colour, marker) in SIZE_STYLE.items():
                mean = (subset[subset.battery_kwh == size]
                        .groupby("deg_penalty_gbp_per_kwh").dnpv.mean())
                ax.plot(100 * mean.index.to_numpy(), mean.to_numpy(), color=colour,
                        linewidth=1.8, marker=marker, markersize=4.5,
                        label=f"{size:g} kWh" if row == 0 and column == 0 else None)

            for price, penalty in derived.items():
                ax.axvline(penalty, color=INK_MUTED, linewidth=1.0,
                           linestyle=(0, (4, 3)), zorder=0,
                           label=("Derived penalty" if row == 0 and column == 0
                                  and price == max(derived) else None))
            _style_axes(ax)
            if row == 0:
                ax.set_title(TARIFF_LABEL[tariff], fontsize=11, loc="left", pad=6)
        axes[row, 0].set_ylabel(policy_label, fontsize=10.5)

    swept = sorted(100 * milp.deg_penalty_gbp_per_kwh.unique())
    axes[0, 0].set_xlim(-0.5, max(derived.values()) + 1.6)
    axes[0, 0].set_xticks(swept)
    axes[0, 0].yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    # Labelled once: the same two lines repeat in every panel. Staggered in
    # height and set low in the Agile panel, the one corner no curve reaches.
    for offset, (price, penalty) in enumerate(sorted(derived.items(), reverse=True)):
        listed = ", ".join(f"{s:g}" for s in sizes_of[price])
        axes[0, -1].annotate(f"{penalty:.2f} ({listed} kWh)",
                             xy=(penalty, 0.14 - 0.11 * offset),
                             xycoords=("data", "axes fraction"), xytext=(-3, 0),
                             textcoords="offset points", ha="right", va="bottom",
                             fontsize=8.5, color=INK_MUTED,
                             bbox=dict(facecolor="white", edgecolor="none", pad=1.2))

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, -0.045),
               handletextpad=0.4, columnspacing=1.8)
    fig.supxlabel("Degradation penalty (p/kWh)", fontsize=10, y=0.035)
    fig.supylabel("Change in NPV from adding the battery (£)", fontsize=10, x=-0.005)

    fig.tight_layout()
    return _save(fig, out_dir, "f3_1_penalty_response.png")


@figure("f3.2", chapter=3,
        description="Marginal discounted benefit against the marginal battery price")
def build_f3_2(out_dir: Path) -> Path:
    """The battery-size axis: what the next kWh earns against what it costs.

    Sizing is a marginal decision, so the axis is argued on marginal quantities
    rather than on the totals every other figure reports. Benefit is read off
    the closest-approach configuration *at each size* -- an upper envelope over
    the study, not one system -- because the claim is a bound: if the next kWh
    fails to pay at the best available cell, it fails everywhere.

    One price line: the band median, which is what the break-even ratios
    elsewhere are measured against, so the shortfalls here and the ratios of
    tab:breakeven-ratio are on the same denominator. Each shortfall is read
    against the median of the band the step *ends* in, which is the price a
    buyer stopping at that size actually pays.
    """
    _configure_style()
    # Break-even price is the price at which the battery exactly repays itself,
    # so under no replacement -- the only policy where nothing but the initial
    # capex scales with price -- the discounted benefit is that price times the
    # capacity. Reusing it keeps this figure on the same definition as f1.3.
    breakeven = load_breakeven(sizes=None)
    breakeven = breakeven[(breakeven.policy == "no-replacement")
                          & breakeven.battery_kwh.map(lambda q: band_price(q) is not None)]
    best = best_in(breakeven, ["battery_kwh"], by="breakeven_ratio")
    best = best.sort_values("battery_kwh")

    sizes = [0.0] + best.battery_kwh.tolist()
    benefit = [0.0] + (best.breakeven_gbp_per_kwh * best.battery_kwh).tolist()

    steps = []
    for index in range(1, len(sizes)):
        width = sizes[index] - sizes[index - 1]
        steps.append((sizes[index - 1], sizes[index],
                      (benefit[index] - benefit[index - 1]) / width))

    fig, ax = plt.subplots(figsize=(7.2, 4.1))

    # Drawn as stairs rather than a line through points: a marginal quantity is
    # defined over an interval, and a point marker would invite reading it at a
    # size instead.
    def stair(values, colour, dashes, width, label, zorder):
        for index, ((low, high, *_), value) in enumerate(zip(steps, values)):
            ax.plot([low, high], [value] * 2, color=colour, linewidth=width,
                    linestyle=dashes, zorder=zorder,
                    label=label if index == 0 else None)
            if index:
                ax.plot([low] * 2, [values[index - 1], value], color=colour,
                        linewidth=width * 0.6, linestyle=dashes, alpha=0.55,
                        zorder=zorder)

    # The band median, which changes at 6 kWh rather than at a swept size, so it
    # is drawn on its own breakpoints and not on the step grid. Carried back to
    # the origin at the lowest band's price: the first step is itself bought in
    # that band, so the reference is not an extrapolation below the swept sizes.
    band_x = np.linspace(sizes[1], sizes[-1], 400)
    band_y = [band_price(q) for q in band_x]
    ax.plot([0.0, sizes[1]], [band_y[0]] * 2, color="0.62", linewidth=1.2, zorder=1)
    ax.plot(band_x, band_y, color="0.62", linewidth=1.2, zorder=1,
            label="Band median price")

    stair([mb for *_, mb in steps], COLOR_LEVEL, "-", 2.4,
          "Marginal discounted benefit", zorder=3)

    # The figure's reading: the shortfall on every step, including the one that
    # narrows because the price schedule steps down under it.
    for low, high, mb in steps:
        middle = (low + high) / 2
        price = band_price(high)
        ax.annotate("", xy=(middle, mb), xytext=(middle, price),
                    arrowprops=dict(arrowstyle="<->", color=INK_MUTED,
                                    linewidth=0.9, shrinkA=0, shrinkB=0))
        ax.annotate(f"−£{price - mb:,.0f}", xy=(middle, (mb + price) / 2), xytext=(5, 0),
                    textcoords="offset points", ha="left", va="center",
                    fontsize=9.5, color=INK_MUTED,
                    bbox=dict(facecolor="white", edgecolor="none", pad=1.0))

    _style_axes(ax)
    ax.set_xlim(0.0, sizes[-1] + 0.35)
    ax.set_ylim(0.0, 1480.0)
    ax.set_xticks(sizes)
    ax.set_xticklabels([f"{q:g}" for q in sizes])
    ax.set_xlabel("Battery size (kWh)", fontsize=10)
    ax.set_ylabel("£ per kWh of battery capacity", fontsize=10)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax.set_title("Closest approach to break-even at each battery size, no replacement",
                 fontsize=11, loc="left", pad=8)

    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(name) for name in
             ("Marginal discounted benefit", "Band median price")]
    ax.legend([handles[i] for i in order], [labels[i] for i in order], frameon=False,
              fontsize=9.5, loc="upper right", handlelength=2.4)

    fig.tight_layout()
    return _save(fig, out_dir, "f3_2_marginal_benefit.png")


# --------------------------------------------------------------------------- #
# Chapter 4
# --------------------------------------------------------------------------- #

@dataclass
class TornadoRow:
    """One sensitivity axis: a bar either side of the reported ratio.

    `extension` continues the favourable bar in a paler fill, for an axis whose
    plausible range has two ends rather than one.
    """
    name: str
    setting: str
    high: float | None = None
    low: float | None = None
    extension: float | None = None
    extension_note: str | None = None

    def span(self, base_ratio: float) -> float:
        """Total width of the bar, which is what orders the tornado."""
        upper = self.extension if self.extension is not None else self.high
        return ((upper if upper is not None else base_ratio)
                - (self.low if self.low is not None else base_ratio))


def _tornado_cell(cell: dict, src_dir: Path | None = None) -> pd.Series:
    """Break-even row for one held-fixed configuration in one re-valuation."""
    frame = load_breakeven(sizes=None, src_dir=src_dir)
    for column, value in cell.items():
        frame = frame[frame[column] == value]
    if len(frame) != 1:
        raise ValueError(f"expected one row for {cell} in "
                         f"{src_dir or 'the base assembly'}, got {len(frame)}")
    return frame.iloc[0]


def _variant_ratio(cell: dict, name: str) -> float:
    path = SENS_DIR / name
    if not path.exists():
        raise SystemExit(f"missing {path}; run scripts/make_sensitivity_assemblies.py")
    return float(_tornado_cell(cell, path).breakeven_ratio)


def _capex_rows(base: pd.Series) -> TornadoRow:
    """The capex bar, which needs no re-valuation.

    The break-even price solves `discounted benefit = price x (capex it must
    repay, per kWh)`, and every term on the right scales with the price, so the
    break-even in £/kWh is the same number under any capex specification. Only
    the observed price it is divided by changes.

    Both ends are fixed-plus-variable, so the bar compares two readings of what
    a pack costs rather than two functional forms. At this size the two are as
    close as they get: the retail list is 13% under the DESNZ band median at
    2.5 kWh against 36% at 5 kWh, so this is the narrowest the capex
    disagreement looks anywhere in the study.
    """
    def price(spec: tuple[float, float]) -> float:
        fixed, variable = spec
        return fixed / base.battery_kwh + variable

    def label(spec: tuple[float, float]) -> str:
        return f"£{spec[0]:,.0f}+£{spec[1]:.0f}/kWh"

    retail = retail_capex()
    return TornadoRow(
        name="Capex formulation",
        setting=f"{label(DESNZ_CAPEX)} ↔ {label(retail)}",
        low=float(base.breakeven_gbp_per_kwh) / price(DESNZ_CAPEX),
        high=float(base.breakeven_gbp_per_kwh) / price(retail),
    )


def _common_tornado_rows(cell: dict, base: pd.Series) -> list[TornadoRow]:
    """The axes both lifetime policies share."""
    base_ratio = float(base.breakeven_ratio)
    return [
        _capex_rows(base),
        TornadoRow("Price escalation, real", "0%/yr ↔ 4%/yr",
                   low=_variant_ratio(cell, "esc000"),
                   high=_variant_ratio(cell, "esc040")),
        TornadoRow("Discount rate, real", "7% ↔ 3.5% (Green Book)",
                   low=_variant_ratio(cell, "dr070"),
                   high=_variant_ratio(cell, "dr035")),
        TornadoRow("Analysis horizon", "15 yr ↔ 25 yr",
                   low=_variant_ratio(cell, "hor15"),
                   high=_variant_ratio(cell, "hor25")),
        TornadoRow("Myopia credit", f"→ +{100 * MYOPIA_AGILE_SHARE:.1f}% of saving",
                   high=base_ratio * (1 + MYOPIA_AGILE_SHARE)),
        TornadoRow("Calendar life", "8 yr ↔ 15 yr",
                   low=_variant_ratio(cell, "cal8"),
                   high=_variant_ratio(cell, "cal15")),
        TornadoRow("Cycle life", "4,000 ↔ 8,000 EFC",
                   low=_variant_ratio(cell, "efc4000"),
                   high=_variant_ratio(cell, "efc8000")),
        TornadoRow("Calendar fade form", r"→ $\sqrt{t}$",
                   high=_variant_ratio(cell, "calsqrt")),
    ]


def _draw_tornado(rows: list[TornadoRow], joint: TornadoRow, base_ratio: float,
                  title: str, out_dir: Path, filename: str,
                  xlim: tuple[float, float] = TORNADO_XLIM,
                  xlabel: str = "Battery benefit cost ratio") -> Path:
    """Render one tornado: the axes in order of span, then the joint corner."""
    rows = sorted(rows, key=lambda row: row.span(base_ratio), reverse=True)

    # Row pitch is fixed so the two tornados' bars are the same size on the page
    # despite the run-to-fade figure carrying two extra axes.
    fig, ax = plt.subplots(figsize=(7.2, 1.35 + 0.40 * (len(rows) + 1)))
    height = 0.56

    def bar(y: float, value: float, colour: str, alpha: float = 1.0,
            start: float | None = None) -> None:
        left = base_ratio if start is None else start
        ax.barh(y, value - left, left=left, height=height, color=colour,
                alpha=alpha, linewidth=0)

    def draw(row: TornadoRow, y: float) -> None:
        if row.low is not None:
            bar(y, row.low, COLOR_LOSS)
            ax.annotate(f"{row.low:.2f}", xy=(row.low, y), xytext=(-6, 0),
                        textcoords="offset points", ha="right", va="center",
                        fontsize=9, color=INK_MUTED)
        if row.high is None:
            return
        bar(y, row.high, COLOR_LEVEL)
        text = f"{row.high:.2f}"
        outer = row.high
        if row.extension is not None:
            bar(y, row.extension, COLOR_LEVEL, alpha=0.35, start=row.high)
            text = f"{row.high:.2f} → {row.extension:.2f}"
            outer = row.extension
        if row.extension_note:
            text += f"\n{row.extension_note}"
        ax.annotate(text, xy=(outer, y), xytext=(6, 0), textcoords="offset points",
                    ha="left", va="center", fontsize=9, color=INK_MUTED)

    ticks, tick_labels = [], []
    for index, row in enumerate(rows):
        draw(row, -index)
        ticks.append(-index)
        tick_labels.append(f"{row.name}\n{row.setting}")

    # The joint corner, set apart: every axis above at its battery-favourable end
    # at once, which is the one thing a one-at-a-time tornado cannot show.
    joint_y = -len(rows) - 0.9
    ax.axhline(joint_y + 0.75, color="0.75", linewidth=0.7, linestyle=(0, (3, 3)))
    draw(joint, joint_y)
    ticks.append(joint_y)
    tick_labels.append(joint.name)

    ax.axvline(base_ratio, color=INK_MUTED, linewidth=1.2)
    ax.axvline(1.0, color=INK_MUTED, linewidth=1.2, linestyle=(0, (4, 3)))
    _style_axes(ax)
    ax.grid(False, axis="y")
    ax.grid(True, axis="x", color="0.9", linewidth=0.6)
    ax.set_ylim(joint_y - 0.75, 0.95)
    ax.set_yticks(ticks)
    ax.set_yticklabels(tick_labels, fontsize=9)
    ax.set_xlim(*xlim)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.2f}")
    # Break-even price / observed price is identically the asset's own
    # benefit-cost ratio: both sides of the break-even scale with the price, so
    # the ratio is discounted benefit / present value of cost.
    ax.set_xlabel(xlabel, fontsize=10)

    # Inside the axes: both rules are vertical, so the only free space is the
    # strip above the widest bar.
    for value, text, align in ((base_ratio, f"as reported, {base_ratio:.2f}", "right"),
                               (1.0, "break-even", "left")):
        ax.annotate(text, xy=(value, 0.42), xytext=(3 if align == "left" else -3, 0),
                    textcoords="offset points", ha=align, va="bottom",
                    fontsize=9.5, color=INK_MUTED)

    # The cell itself is named in the document's caption; the title says only
    # which of the two lifetime policies the figure prices.
    ax.set_title(title, fontsize=11, loc="left", pad=8)

    fig.tight_layout()
    return _save(fig, out_dir, filename)


@figure("f4.1", chapter=4,
        description="What would move the break-even ratio, no replacement")
def build_f4_1(out_dir: Path) -> Path:
    _configure_style()
    base = _tornado_cell(TORNADO_CELL)
    base_ratio = float(base.breakeven_ratio)

    joint = TornadoRow(
        # Capex is excluded on purpose: it is the price the ratio is measured
        # against, not an input to it, so combining it would double-count.
        name="Every axis (excl. capex),\npositive ends", setting="",
        high=_variant_ratio(TORNADO_CELL, "best"),
        extension=_variant_ratio(TORNADO_CELL, "best") * (1 + MYOPIA_AGILE_SHARE),
        extension_note="(w/ myopia credit)",
    )
    return _draw_tornado(_common_tornado_rows(TORNADO_CELL, base), joint, base_ratio,
                         "Most favourable no-replacement case",
                         out_dir, "f4_1_tornado_breakeven.png")


@figure("f4.2", chapter=4,
        description="What would move the break-even ratio, run to SOH_min")
def build_f4_2(out_dir: Path) -> Path:
    _configure_style()
    cell = TORNADO_CELL_FADE
    base = _tornado_cell(cell)
    base_ratio = float(base.breakeven_ratio)

    # The axis that exists only under this policy. The base case holds battery
    # capex flat in real terms to 2046 while electricity escalates at 2%/yr
    # (results_summary.md caveat 4), so this is the one bar correcting an
    # asymmetry rather than exploring a range.
    rates = sorted(REPLACEMENT_DECLINE_RATES.items(), key=lambda item: item[1])
    (slow, slow_rate), (fast, fast_rate) = rates
    replacement = TornadoRow(
        name="Replacement price, real decline",
        setting=f"→ −{100 * slow_rate:g}%/yr ↔ −{100 * fast_rate:g}%/yr",
        high=_variant_ratio(cell, slow), extension=_variant_ratio(cell, fast),
    )

    joint = TornadoRow(
        name="Every axis (excl. capex),\npositive ends", setting="",
        high=_variant_ratio(cell, "best"),
        extension=_variant_ratio(cell, "best") * (1 + MYOPIA_AGILE_SHARE),
        extension_note="(w/ myopia credit)",
    )
    # Also policy-specific: under no replacement the sweep pins the floor to
    # zero, so it never binds and the axis does not exist there.
    floor = TornadoRow(
        name=r"SOH$_{\min}$", setting="70% ↔ 50%",
        low=_variant_ratio(cell, "floor070"), high=_variant_ratio(cell, "floor050"),
    )

    rows = _common_tornado_rows(cell, base) + [replacement, floor]
    return _draw_tornado(rows, joint, base_ratio,
                         r"Most favourable run-to-SOH$_{\min}$ case",
                         out_dir, "f4_2_tornado_breakeven_fade.png")


@lru_cache(maxsize=None)
def _pv_export_kwh(location: str, pv_kwp: float) -> float:
    """Annual PV export of the PV-only system, in kWh.

    A system with no battery has no dispatch: every surplus half-hour is
    exported, so the export volume follows from the profiles alone and the
    export rate can be re-priced without re-running anything. Cached because the
    dot map asks for all eighteen location/array combinations.
    """
    spec = get_location(location)
    data = load_all(resolve_pv_size_path(spec, DATA_DIR, pv_kwp),
                    DEMAND_PROFILE, resolve_paths(spec, DATA_DIR)[1], year=SWEEP_YEAR)
    return float((data.pv_kw - data.demand_kw).clip(lower=0).sum() * HOURS_PER_STEP)


def _pv_ratio(saving_yr1: float, capex: float, elasticity: float, *,
              rate: float = DISCOUNT_RATE, escalation: float = PRICE_ESCALATION,
              years: int = HORIZON_YEARS, om_frac: float = PV_OM_FRAC,
              degradation: float = 0.0) -> tuple[float, float]:
    """PV benefit-cost ratio and NPV, in closed form.

    PV has no dispatch, no replacement and no fade in the model, so the whole
    valuation is an escalating annuity: nothing here needs a re-run, and the NPV
    returned is checked against the assembly by the caller.

    `elasticity` converts a loss of *output* into a loss of *saving*. They are
    not the same: at this cell 73% of generation is exported at the SEG rate
    while the retained kWh displace import at roughly twice that, so the
    marginal kWh is worth less than the average one.
    """
    years_index = np.arange(1, years + 1)
    output = (1 - degradation) ** (years_index - 1)
    stream = saving_yr1 * (1 - elasticity * (1 - output))

    discount = (1 + rate) ** -years_index
    benefit = float((stream * (1 + escalation) ** (years_index - 1) * discount).sum())
    # O&M is a fraction of capex, so it scales with the price the break-even is
    # solving for and belongs on the cost side rather than netted off benefits.
    cost = capex * (1 + om_frac * float(discount.sum()))
    return benefit / cost, benefit - cost


@figure("f4.3", chapter=4, description="What would move the PV-only benefit cost ratio")
def build_f4_3(out_dir: Path) -> Path:
    _configure_style()
    pv_only = load_pv_only()
    for column, value in PV_TORNADO_CELL.items():
        pv_only = pv_only[pv_only[column] == value]
    if len(pv_only) != 1:
        raise ValueError(f"expected one PV-only row for {PV_TORNADO_CELL}")
    base = pv_only.iloc[0]
    saving, capex = float(base.annual_saving_yr1_gbp), float(base.capex_gbp)

    # Local slope of saving against array size, which is the same thing as its
    # slope against output: degradation scales the generation profile exactly as
    # a smaller array would.
    neighbour = load_pv_only()
    for column, value in (PV_TORNADO_CELL | {"pv_kwp": base.pv_kwp - 1.0}).items():
        neighbour = neighbour[neighbour[column] == value]
    marginal = saving - float(neighbour.iloc[0].annual_saving_yr1_gbp)
    elasticity = marginal * base.pv_kwp / saving

    def ratio(**overrides) -> float:
        value, _ = _pv_ratio(overrides.pop("saving", saving),
                             overrides.pop("capex", capex), elasticity, **overrides)
        return value

    def check(npv_expected: float, **overrides) -> None:
        """Fail loudly if the closed form has drifted from the assembly."""
        _, npv = _pv_ratio(saving, capex, elasticity, **overrides)
        if abs(npv - npv_expected) > 0.01:
            raise ValueError(f"closed-form PV NPV {npv:.2f} does not match the "
                             f"assembly's {npv_expected:.2f} at {overrides}")

    check(float(base.npv_gbp))
    for variant, years in PV_HORIZONS.items():
        path = SENS_DIR / variant
        if not path.exists():
            raise SystemExit(f"missing {path}; run scripts/make_sensitivity_assemblies.py")
        row = load_pv_only(path / CONSTANT_PRICE_ASSEMBLIES[1300.0].name)
        for column, value in PV_TORNADO_CELL.items():
            row = row[row[column] == value]
        check(float(row.iloc[0].npv_gbp), years=years)

    base_ratio = ratio()
    export_kwh = _pv_export_kwh(PV_TORNADO_CELL["location"],
                                PV_TORNADO_CELL["pv_kwp"])
    low_rate, high_rate = SEG_RANGE

    rows = [
        TornadoRow("Export rate",
                   f"{100 * low_rate:g}p ↔ {100 * high_rate:g}p/kWh",
                   low=ratio(saving=saving - (SEG_BASE - low_rate) * export_kwh),
                   high=ratio(saving=saving + (high_rate - SEG_BASE) * export_kwh)),
        TornadoRow("Installed price",
                   f"£{PV_COST_PER_KWP * (1 + PV_CAPEX_SPREAD):,.0f} ↔ "
                   f"£{PV_COST_PER_KWP * (1 - PV_CAPEX_SPREAD):,.0f}/kWp",
                   low=ratio(capex=capex * (1 + PV_CAPEX_SPREAD)),
                   high=ratio(capex=capex * (1 - PV_CAPEX_SPREAD))),
        TornadoRow("Analysis horizon", "15 yr ↔ 25 yr",
                   low=ratio(years=PV_HORIZONS["hor15"]),
                   high=ratio(years=PV_HORIZONS["hor25"])),
        TornadoRow("O&M, share of capex", "2%/yr ↔ 0.5%/yr",
                   low=ratio(om_frac=0.02), high=ratio(om_frac=0.005)),
        # One-sided by construction: the base case models no output decline at
        # all, so there is no favourable end to this axis.
        TornadoRow("Module degradation",
                   f"→ −{100 * PV_DEGRADATION:g}%/yr of output",
                   low=ratio(degradation=PV_DEGRADATION)),
    ]

    # The adverse corner, not the favourable one: the PV claim is positive, so
    # it is falsified from below. This is the mirror of the battery figures.
    joint = TornadoRow(
        name="Every axis, adverse ends", setting="",
        low=ratio(saving=saving - (SEG_BASE - low_rate) * export_kwh,
                  capex=capex * (1 + PV_CAPEX_SPREAD), years=PV_HORIZONS["hor15"],
                  om_frac=0.02, degradation=PV_DEGRADATION),
    )
    return _draw_tornado(rows, joint, base_ratio, "Most favourable PV-only case",
                         out_dir, "f4_3_tornado_pv_only.png",
                         xlim=PV_TORNADO_XLIM, xlabel="PV benefit cost ratio")


def _pv_cells() -> pd.DataFrame:
    """The 54 PV-only cells with everything the closed form needs."""
    cells = load_pv_only().sort_values(["location", "tariff", "pv_kwp"]).copy()

    # Local slope of saving against array size, which is also its slope against
    # output: degradation scales the generation profile exactly as a smaller
    # array would. The smallest array has no smaller neighbour and takes the
    # forward step instead.
    saving = cells.groupby(["location", "tariff"]).annual_saving_yr1_gbp
    marginal = saving.diff().fillna(saving.diff(-1).abs())
    cells["elasticity"] = marginal * cells.pv_kwp / cells.annual_saving_yr1_gbp

    cells["export_kwh"] = [_pv_export_kwh(row.location, row.pv_kwp)
                           for row in cells.itertuples()]
    # Flat and Economy 7 export at the flat SEG rate, Agile at the Agile price,
    # so the SEG axis cannot reach an Agile cell (Spec 03 matched export).
    cells["seg_exposed"] = cells.tariff.isin(["flat", "e7"])
    return cells


def _pv_scenario(cells: pd.DataFrame, *, seg_cut: float = 0.0,
                 capex_factor: float = 1.0, **overrides) -> np.ndarray:
    """PV benefit-cost ratio of every cell under one set of assumptions."""
    return np.array([
        _pv_ratio(row.annual_saving_yr1_gbp
                  - (seg_cut * row.export_kwh if row.seg_exposed else 0.0),
                  row.capex_gbp * capex_factor, row.elasticity, **overrides)[0]
        for row in cells.itertuples()])


@figure("f4.4", chapter=4,
        description="PV-only benefit cost ratio across all cells, by adverse assumption")
def build_f4_4(out_dir: Path) -> Path:
    _configure_style()
    cells = _pv_cells()
    low_rate, _ = SEG_RANGE
    seg_cut = SEG_BASE - low_rate

    adverse = {
        ("Export rate", f"→ {100 * low_rate:g}p/kWh, flat and E7 only"):
            {"seg_cut": seg_cut},
        ("Installed price", f"→ +{100 * PV_CAPEX_SPREAD:g}%"):
            {"capex_factor": 1 + PV_CAPEX_SPREAD},
        ("Analysis horizon", f"→ {PV_HORIZONS['hor15']} yr"):
            {"years": PV_HORIZONS["hor15"]},
        ("O&M", "→ 2%/yr of capex"): {"om_frac": 0.02},
        ("Module degradation", f"→ −{100 * PV_DEGRADATION:g}%/yr of output"):
            {"degradation": PV_DEGRADATION},
    }
    # Most damaging first, counted in cells lost rather than in mean ratio: the
    # export axis reaches only two thirds of the grid, so it moves the mean less
    # than it moves the count, and the count is what the figure is about.
    rows = [(label, _pv_scenario(cells, **kwargs)) for label, kwargs in adverse.items()]
    rows.sort(key=lambda row: (int((row[1] >= 1.0).sum()), row[1].mean()))
    rows.insert(0, (("As reported", ""), _pv_scenario(cells)))

    joint = {key: value for kwargs in adverse.values() for key, value in kwargs.items()}
    rows.append((("Every axis, adverse ends", ""), _pv_scenario(cells, **joint)))

    fig, ax = plt.subplots(figsize=(7.2, 0.62 * len(rows) + 1.5))
    ticks, tick_labels = [], []

    for index, ((name, setting), values) in enumerate(rows):
        # The joint corner is set apart, as in the tornados: it is a bound with
        # every axis at its adverse end at once, not a scenario.
        joint_row = index == len(rows) - 1
        y = -index - (0.9 if joint_row else 0.0)
        colour = COLOR_LEVEL if index == 0 else COLOR_LOSS

        ax.plot([values.min(), values.max()], [y, y], color=colour, linewidth=1.0,
                alpha=0.45, zorder=1)
        ax.scatter(values, np.full(len(values), y), s=15, color=colour, alpha=0.5,
                   linewidths=0, zorder=2)
        ax.scatter([values.mean()], [y], s=62, color=colour, marker="o",
                   edgecolors="white", linewidths=1.4, zorder=3)

        # The count is the point of the figure, so it is printed rather than
        # left to be read off the rule.
        ax.annotate(f"{int((values >= 1.0).sum())}/{len(values)}",
                    xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(7, 0),
                    textcoords="offset points", ha="left", va="center",
                    fontsize=9, color=INK_MUTED)
        ticks.append(y)
        tick_labels.append(f"{name}\n{setting}" if setting else name)
        if joint_row:
            ax.axhline(y + 0.75, color="0.75", linewidth=0.7, linestyle=(0, (3, 3)))

    ax.axvline(1.0, color=INK_MUTED, linewidth=1.2, linestyle=(0, (4, 3)))
    _style_axes(ax)
    ax.grid(False, axis="y")
    ax.grid(True, axis="x", color="0.9", linewidth=0.6)
    ax.set_ylim(ticks[-1] - 0.75, 0.95)
    ax.set_yticks(ticks)
    ax.set_yticklabels(tick_labels, fontsize=9)
    ax.set_xlabel("PV benefit cost ratio", fontsize=10)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.2f}")

    ax.annotate("break-even", xy=(1.0, 0.42), xytext=(4, 0),
                textcoords="offset points", ha="left", va="bottom",
                fontsize=9.5, color=INK_MUTED)
    ax.annotate("cells at\nor above 1.00", xy=(1.0, 0.42),
                xycoords=("axes fraction", "data"), xytext=(7, 0),
                textcoords="offset points", ha="left", va="bottom",
                fontsize=9, color=INK_MUTED)
    ax.set_title("All 54 PV-only cells", fontsize=11, loc="left", pad=8)

    fig.tight_layout()
    return _save(fig, out_dir, "f4_4_pv_dot_map.png")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Chapter 5 -- parameter axes
# --------------------------------------------------------------------------- #

# The five axes of the sweep proper. Controller and wear penalty are excluded:
# they are properties of how the system is run rather than of what is bought, and
# the controller chapter ranks them on its own terms. Battery size excludes the
# no-battery level, which would make the bar measure "owning a battery at all"
# rather than "which battery" and duplicate f1.c.
AXIS_SPEC = [
    ("Tariff", "tariff", lambda value: TARIFF_LABEL[value]),
    ("Location", "location", lambda value: value.capitalize()),
    ("PV size", "pv_kwp", lambda value: f"{value:g} kWp"),
    ("Battery size", "battery_kwh", lambda value: f"{value:g} kWh"),
    ("Lifetime policy", "policy", lambda value: POLICY_STYLE[value][0]),
]


def _axis_spans(frame: pd.DataFrame, value: str) -> list[dict]:
    """Each axis' reach on one response, longest first.

    For every level of an axis the response is maximised over everything else,
    which is the study's own convention -- "the highest anywhere" -- rather than
    a mean over cells. The span runs from the worst such level to the best.

    The level holding the global optimum returns the global maximum, so every
    axis shares the same upper end by construction and the bars are read from
    the right: how much of the study ceiling this axis gives up when set to its
    worst level, with everything else still free.
    """
    spans = []
    for name, column, label_of in AXIS_SPEC:
        by_level = frame.groupby(column)[value].max()
        if len(by_level) < 2:
            raise ValueError(f"{column} has {len(by_level)} level(s); nothing to span")
        spans.append({"name": name, "low": float(by_level.min()),
                      "high": float(by_level.max()), "levels": len(by_level),
                      "worst": label_of(by_level.idxmin()),
                      "best": label_of(by_level.idxmax())})

    ceilings = {round(span["high"], 6) for span in spans}
    if len(ceilings) != 1:
        raise ValueError(f"axes do not share a ceiling on {value}: {ceilings}; "
                         "the shared right edge the figure relies on has broken")
    return sorted(spans, key=lambda span: span["high"] - span["low"], reverse=True)


def _draw_axis_panel(ax, spans: list[dict], colour: str, fmt, xlabel: str) -> None:
    positions = np.arange(len(spans))[::-1]
    for position, span in zip(positions, spans):
        ax.barh(position, span["high"] - span["low"], left=span["low"], height=0.46,
                color=colour, linewidth=0)

    # The worst level is named in the row label rather than beside the bar it
    # explains: set on the plot, a long name on a long bar reaches the axis, and
    # a long name on a short bar overruns the panel. The best level is named
    # nowhere, because every bar ends at the same configuration.
    ax.set_yticks(positions)
    ax.set_yticklabels(
        [f"{index}.  {span['name']}\n{span['worst']}, {fmt(span['low'])}"
         for index, span in enumerate(spans, start=1)], fontsize=9.5)
    ax.tick_params(axis="y", length=0)
    _style_axes(ax)
    ax.grid(False, axis="y")
    ax.grid(True, axis="x", color="0.9", linewidth=0.6)
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylim(-0.7, len(spans) - 0.25)
    reach = spans[0]["high"] - spans[0]["low"]
    ax.set_xlim(left=spans[0]["low"] - 0.06 * reach)


@figure("f5.1", chapter=5,
        description="How far each parameter axis moves the battery's contribution")
def build_f5_1(out_dir: Path) -> Path:
    """Which axis matters, asked once, on the quantity the battery is judged by.

    The response is the change in NPV from adding the battery to the identical
    array in the same cell, maximised over controller and wear penalty within
    each cell first, so no axis is charged for a dispatch choice made elsewhere.
    The figure exists to rank the axes before they are taken one at a time, and
    the numbering down the panel is the order the subsections then follow.

    Every bar ends at the same place --- the best cell in the study, at
    -GBP926 --- because the level holding that cell returns it as its own
    maximum. A bar is therefore what its axis costs at its worst level with
    everything else still free, not a share of variance: the bars are
    conditional ranges and do not sum to anything.

    Read against the break-even line, the figure also states the negative
    result: the gap no axis closes is the one between the right-hand edge and
    zero.

    A caveat that belongs in the caption. This response is in pounds and is not
    normalised by what the battery cost, so battery size is flattered --- a
    10 kWh pack carries 3.4 times the capex of a 2.5 kWh one and can lose more
    for that reason alone. The break-even ratio of tab:breakeven-ratio divides
    that out, and on that measure tariff leads instead.
    """
    _configure_style()
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    spans = _axis_spans(optimum, "dnpv")

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    _draw_axis_panel(ax, spans, COLOR_LOSS,
                     lambda v: f"\u2212\u00a3{abs(v):,.0f}",
                     "Change in NPV from adding the battery (£)")
    ax.axvline(0.0, color=INK_MUTED, linewidth=1.0, linestyle=(0, (4, 3)), zorder=3)
    ax.annotate("break-even", xy=(0.0, -0.62), xytext=(-4, 0),
                textcoords="offset points", ha="right", va="bottom",
                fontsize=8.5, color=INK_MUTED)
    # Carried to zero even though nothing reaches it: the empty right-hand margin
    # is the shortfall the whole chapter is about.
    ax.set_xlim(right=abs(spans[0]["low"]) * 0.04)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    fig.tight_layout()
    return _save(fig, out_dir, "f5_1_axis_effect_size.png")


@figure("f5.2", chapter=5,
        description="The two weak axes: array size and location, within each tariff")
def build_f5_2(out_dir: Path) -> Path:
    """The axes f5.1 ranks fourth and fifth, taken together and on their own scale.

    Tariff is the panel rather than a series because f5.1 has already settled it;
    what is left to explain is the width inside a tariff, and array size and
    location are what is left to explain it with. Both are ordered, so both get a
    position or a ramp rather than categorical hues.

    Three readings, in the order the section takes them:

      1. Array size is monotone but strongly saturating. Almost all of it is the
         step from 1 to 2 kWp; beyond about 3 kWp the curve is flat. A larger
         array gives the battery more surplus to work with, and that runs out.
      2. On Economy 7 and Agile the optimum is *interior* -- ringed -- so past it
         a larger array makes the battery increment slightly worse. Flat is still
         improving at the largest array swept. This is the same interior-optimum
         reading as f1.0, on the increment rather than on the level.
      3. Location barely separates the lines at all, and where it does it does not
         follow irradiance: Manchester is the worst of the three on Economy 7 and
         Agile despite sitting between the other two in sun. The ramp runs light
         to dark with increasing irradiance, so a response that tracked sun would
         stack the lines in ramp order. None of the three panels does.

    Zero is off the scale deliberately. Every value here is a loss --- f1.1 and
    f1.c make that point --- and including the break-even line would compress all
    three panels into the top of the axis and hide the shape this figure exists
    to show.
    """
    _configure_style()
    optimum = per_cell_optimum(load_assembly(), ["policy", "battery_kwh"], by="dnpv")
    # One size and one policy: the least-damaging pack under the upper-bound
    # policy, which is the configuration the closest-approach cell uses. The
    # gradient steepens with battery size -- roughly threefold at 10 kWh -- so
    # this panel understates the array-size axis rather than flattering it.
    panel_data = optimum[(optimum.policy == TORNADO_CELL["policy"])
                         & (optimum.battery_kwh == TORNADO_CELL["battery_kwh"])]

    sizes = sorted(panel_data.pv_kwp.unique())
    fig, axes = plt.subplots(1, 3, figsize=(7.6, 3.2), sharey=True)

    for ax, tariff in zip(axes, TARIFF_ORDER):
        block = panel_data[panel_data.tariff == tariff]
        for location, (colour, marker) in LOCATION_STYLE.items():
            track = block[block.location == location].sort_values("pv_kwp")
            if len(track) != len(sizes):
                raise ValueError(f"expected {len(sizes)} array sizes for "
                                 f"{tariff}/{location}, got {len(track)}")
            ax.plot(track.pv_kwp, track.dnpv, color=colour, linewidth=1.9,
                    marker=marker, markersize=5,
                    label=location.capitalize() if tariff == TARIFF_ORDER[0] else None)

            # Ringed because it carries the second reading: on Economy 7 and
            # Agile the best array for the battery is not the largest one.
            peak = track.loc[track.dnpv.idxmax()]
            ax.scatter([peak.pv_kwp], [peak.dnpv], s=95, facecolors="none",
                       edgecolors=colour, linewidths=1.4, zorder=4)

        # The weak-axis claim as a number, so it does not rest on the reader
        # judging how close three lines are.
        spread = block.groupby("location").dnpv.mean()
        ax.annotate(f"location spread £{spread.max() - spread.min():,.0f}",
                    xy=(0.5, 0.04), xycoords="axes fraction", ha="center",
                    va="bottom", fontsize=8.5, color=INK_MUTED)

        _style_axes(ax)
        ax.set_xticks(sizes)
        ax.set_xlabel("PV array (kWp)", fontsize=10)
        ax.set_title(TARIFF_LABEL[tariff], fontsize=11, loc="left", pad=6)

    axes[0].set_ylabel("Change in NPV from\nadding the battery (£)", fontsize=10)
    axes[0].yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")

    # Neutral in the legend, where a coloured exemplar would read as a location.
    axes[0].scatter([], [], s=95, facecolors="none", edgecolors=INK_MUTED,
                    linewidths=1.4, label="Best array for the battery")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9.5, ncol=4,
               loc="lower center", bbox_to_anchor=(0.5, -0.08),
               handletextpad=0.5, columnspacing=1.8)

    fig.tight_layout()
    return _save(fig, out_dir, "f5_2_array_size_and_location.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapter", type=int, help="build every figure in this chapter")
    group.add_argument("--figure", help="build one figure by id, e.g. f1.2")
    group.add_argument("--all", action="store_true", help="build every registered figure")
    group.add_argument("--list", action="store_true", help="list registered figures")
    parser.add_argument("--out-dir", type=Path, default=Path("results/figures"))
    args = parser.parse_args()

    if args.list:
        for figure_id, entry in sorted(REGISTRY.items()):
            print(f"  ch{entry.chapter}  {figure_id:<6} {entry.description}")
        return

    if args.figure:
        if args.figure not in REGISTRY:
            raise SystemExit(f"unknown figure {args.figure!r}; try --list")
        wanted = [args.figure]
    elif args.chapter:
        wanted = [f for f, e in REGISTRY.items() if e.chapter == args.chapter]
        if not wanted:
            raise SystemExit(f"no figures registered for chapter {args.chapter}")
    else:
        wanted = list(REGISTRY)

    for figure_id in sorted(wanted):
        out_path = REGISTRY[figure_id].build(args.out_dir)
        print(f"  {figure_id:<6} -> {out_path}")


if __name__ == "__main__":
    main()
