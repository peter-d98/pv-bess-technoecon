"""Generate the interim-presentation Gantt chart for the PV-BESS project.

Timeline: 13/05/2026 -> 18/08/2026, batched into 14 weekly slots (W1..W14).
Tasks are grouped by phase and coloured by status (completed / ongoing /
future). Milestones are drawn as diamonds and a vertical line marks "today".

Run from the repository root:

    python docs/gantt_chart.py
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

OUT_PATH = Path(__file__).resolve().parent / "gantt_chart.png"

# --- Timeline -------------------------------------------------------------
BASE = date(2026, 5, 13)      # start of week 1
N_WEEKS = 14
TODAY = date(2026, 7, 13)

# Status styling
COLOURS = {
    "done": "#2E7D32",       # green  - completed
    "ongoing": "#F9A825",    # amber  - in progress
    "future": "#90A4AE",     # grey   - not started
}
MILESTONE_COLOUR = "#6A1B9A"


def wk_start(n: int) -> date:
    """First day of week n (1-indexed)."""
    return BASE + timedelta(days=7 * (n - 1))


def wk_end(n: int) -> date:
    """Day after the last day of week n (exclusive right edge)."""
    return BASE + timedelta(days=7 * n)


# --- Task definition ------------------------------------------------------
# Each task: (label, start_week, end_week, status). Spans are inclusive of
# both week numbers, e.g. (1, 4) => start of W1 to end of W4.
PHASES = [
    ("1 \u00b7 Initiation & Literature Review", [
        ("Project scoping & research question", 1, 2, "done"),
        ("Literature review", 1, 4, "done"),
        ("Methodology & tooling design", 2, 3, "done"),
    ]),
    ("2 \u00b7 Data & Model Development", [
        ("Data sourcing (PVGIS, Octopus Agile)", 4, 5, "done"),
        ("CREST demand-profile generation", 4, 5, "done"),
        ("v1 \u00b7 single-day MILP (synthetic)", 3, 5, "done"),
        ("v2 \u00b7 real data + annual rolling horizon", 5, 7, "done"),
        ("Input validation (PV & demand)", 7, 8, "done"),
        ("Rules-based controller benchmark", 7, 8, "done"),
        ("NPV economic model (Spec 01)", 8, 9, "done"),
        ("Degradation + capacity fade (Spec 02)", 8, 9, "done"),
    ]),
    ("3 \u00b7 Parameter Study & Analysis", [
        ("Tariff model layer (Spec 03)", 9, 10, "ongoing"),
        ("Multi-location ingestion (Spec 04)", 10, 11, "future"),
        ("Parameter-sweep harness (Spec 05)", 11, 12, "future"),
        ("Analysis figures (Spec 06)", 12, 13, "future"),
    ]),
    ("4 \u00b7 Dissertation Write-up", [
        ("Dissertation drafting", 10, 13, "future"),
        ("Final write-up & revision", 13, 14, "future"),
    ]),
]

MILESTONES = [
    ("Interim presentation", date(2026, 7, 13)),
    ("Final submission", date(2026, 8, 18)),
]


def build_rows():
    """Flatten phases/milestones into a top-to-bottom ordered row list."""
    rows = []
    for phase_name, tasks in PHASES:
        rows.append(("header", phase_name))
        for label, a, b, status in tasks:
            rows.append(("task", label, a, b, status))
    rows.append(("header", "Milestones"))
    for label, when in MILESTONES:
        rows.append(("milestone", label, when))
    return rows


def main() -> None:
    rows = build_rows()
    n = len(rows)

    fig, ax = plt.subplots(figsize=(13, 8))

    yticks, yticklabels, bold_flags = [], [], []
    bar_h = 0.72

    for i, row in enumerate(rows):
        y = n - i  # top row gets the highest y
        yticks.append(y)

        if row[0] == "header":
            _, text = row
            # Faint full-width band to separate phase groups.
            ax.axhspan(y - 0.5, y + 0.5, color="#ECEFF1", zorder=0)
            yticklabels.append(text)
            bold_flags.append(True)
            continue

        bold_flags.append(False)

        if row[0] == "task":
            _, label, a, b, status = row
            x0 = mdates.date2num(wk_start(a))
            x1 = mdates.date2num(wk_end(b))
            ax.broken_barh(
                [(x0, x1 - x0)], (y - bar_h / 2, bar_h),
                facecolors=COLOURS[status], edgecolor="white", linewidth=0.8,
                zorder=3,
            )
            yticklabels.append("   " + label)

        elif row[0] == "milestone":
            _, label, when = row
            x = mdates.date2num(when)
            ax.plot(x, y, marker="D", markersize=12, color=MILESTONE_COLOUR,
                    zorder=4)
            ax.annotate(f"{when:%d %b}", (x, y), xytext=(12, 0),
                        textcoords="offset points", va="center", ha="left",
                        fontsize=8, color=MILESTONE_COLOUR, fontweight="bold")
            yticklabels.append("   " + label)

    # --- Today marker -----------------------------------------------------
    today_x = mdates.date2num(TODAY)
    ax.axvline(today_x, color="#C62828", linestyle="--", linewidth=1.5, zorder=2)
    ax.annotate("Today", (today_x, n + 0.9), color="#C62828", fontsize=9,
                ha="center", fontweight="bold")

    # --- Week grid + labels ----------------------------------------------
    for wk in range(1, N_WEEKS + 2):
        ax.axvline(mdates.date2num(wk_start(wk)), color="#CFD8DC",
                   linewidth=0.7, zorder=1)
    for wk in range(1, N_WEEKS + 1):
        mid = mdates.date2num(wk_start(wk)) + 3.5
        ax.annotate(f"W{wk}", (mid, n + 0.2), ha="center", fontsize=8,
                    color="#455A64")

    # --- Axes cosmetics ---------------------------------------------------
    ax.set_yticks(yticks)
    ax.set_yticklabels(yticklabels, fontsize=9)
    for lbl, bold in zip(ax.get_yticklabels(), bold_flags):
        if bold:
            lbl.set_fontweight("bold")
    ax.tick_params(axis="y", length=0)

    ax.set_xlim(mdates.date2num(wk_start(1)), mdates.date2num(wk_end(N_WEEKS)))
    ax.set_ylim(0.3, n + 1.4)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.WE))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.tick_params(axis="x", labelsize=8)
    plt.setp(ax.get_xticklabels(), rotation=0)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)

    ax.set_title(
        "PV\u2013BESS Techno-Economic Project \u2014 Interim Gantt Chart "
        "(13 May \u2013 18 Aug 2026)",
        fontsize=13, fontweight="bold", pad=18,
    )

    # --- Legend -----------------------------------------------------------
    handles = [
        mpatches.Patch(color=COLOURS["done"], label="Completed"),
        mpatches.Patch(color=COLOURS["ongoing"], label="Ongoing"),
        mpatches.Patch(color=COLOURS["future"], label="Future"),
        plt.Line2D([0], [0], marker="D", color="w",
                   markerfacecolor=MILESTONE_COLOUR, markersize=11,
                   label="Milestone"),
        plt.Line2D([0], [0], color="#C62828", linestyle="--", label="Today"),
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.07),
              ncol=5, fontsize=9, frameon=True, framealpha=0.9)

    fig.tight_layout()
    fig.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
    print(f"Saved Gantt chart to {OUT_PATH}")


if __name__ == "__main__":
    main()
