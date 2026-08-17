"""Re-assemble the sweep at each sensitivity axis of the Chapter 4 tornado.

Every axis below is an *economics or ageing* assumption, none of which enters
dispatch, so each variant is a re-read of the same 2,160 cached saving curves at
different parameters -- ~30 s per run, zero solves. Each variant needs the same
pair of constant-price assemblies the break-even calculation always uses
(:mod:`src.results_data`), so it is two runs, written to
``results/sens/<variant>/_band_c{1300,890}.csv``.

    python scripts/make_sensitivity_assemblies.py            # missing variants
    python scripts/make_sensitivity_assemblies.py --overwrite

The capex-specification and myopia-credit axes of the tornado need no run: the
first changes only the observed price the break-even is divided by, and the
second scales the break-even ratio in closed form (results_summary.md §7).

Zero solves is asserted rather than assumed: every variant must reproduce the
base assembly's year-1 saving in every row, since only the valuation changed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.results_data import CONSTANT_PRICE_ASSEMBLIES

SENS_DIR = REPO_ROOT / "results" / "sens"

# The six MILP wear penalties of the v2 grid, in the runner's `penalty:EFC` form.
# The cycle life is carried on the scenario as well as on `--cycle-life-efc`,
# because the first drives the fade and the second the derived penalty.
PENALTIES = (0.0, 0.01, 0.03, 0.05, 0.07, 0.09)


def _efc_scenarios(cycle_life_efc: float) -> list[str]:
    return [f"{p:g}:{cycle_life_efc:g}" for p in PENALTIES]


# Real battery price decline (results_summary.md caveat 4), which the base case
# does not model. `run_sweep.py` takes a flat factor on the replacement pack
# rather than a rate, so each rate is compounded over the years to the swap in
# the cell the Chapter 4 tornado reports. Run-to-fade only: with no replacement
# there is nothing to re-price.
REPLACEMENT_DECLINE_YEARS = 15
REPLACEMENT_DECLINE_RATES = {"repl5pc": 0.05, "repl10pc": 0.10}


def decline_factor(rate: float) -> float:
    return (1.0 - rate) ** REPLACEMENT_DECLINE_YEARS


# variant -> extra run_sweep.py arguments. Base is 5% real discount, 2% real
# escalation, 6,000 EFC, 10-year linear calendar life.
VARIANTS: dict[str, list[str]] = {
    "dr035": ["--discount-rate", "0.035"],
    "dr070": ["--discount-rate", "0.07"],
    "esc000": ["--price-escalation", "0.0"],
    "esc040": ["--price-escalation", "0.04"],
    # The four off-diagonal cells of the discount rate x escalation grid of
    # t4.1. The five on the axes are the four variants above plus the base.
    "dr035_esc000": ["--discount-rate", "0.035", "--price-escalation", "0.0"],
    "dr035_esc040": ["--discount-rate", "0.035", "--price-escalation", "0.04"],
    "dr070_esc000": ["--discount-rate", "0.07", "--price-escalation", "0.0"],
    "dr070_esc040": ["--discount-rate", "0.07", "--price-escalation", "0.04"],
    # Both ageing axes are two-sided: the adverse ends are a cheap LFP pack, the
    # favourable ends a good one. 6,000 EFC / 10 yr is the base.
    "efc4000": ["--cycle-life-efc", "4000", "--deg-scenarios", *_efc_scenarios(4000)],
    "efc8000": ["--cycle-life-efc", "8000", "--deg-scenarios", *_efc_scenarios(8000)],
    "cal8": ["--calendar-life-years", "8"],
    "cal12": ["--calendar-life-years", "12"],
    "cal15": ["--calendar-life-years", "15"],
    "calsqrt": ["--calendar-form", "sqrt"],
    "hor15": ["--horizon-years", "15"],
    "hor25": ["--horizon-years", "25"],
    # Run-to-fade only: the no-replacement policy pins the floor to zero, so it
    # never triggers there. 0.70 replaces sooner, 0.50 runs the pack longer.
    "floor050": ["--soh-floor", "0.50"],
    "floor070": ["--soh-floor", "0.70"],
    # Not a defensible assumption -- a bound. Isolates how much of the fade
    # penalty is calendar ageing rather than cycling.
    "nocal": ["--calendar-life-years", "1e6"],
    **{name: ["--replacement-price-factor", f"{decline_factor(rate):.4f}"]
       for name, rate in REPLACEMENT_DECLINE_RATES.items()},
    # Every axis above at its battery-favourable end at once, which a
    # one-at-a-time tornado cannot show. The myopia credit is applied to this
    # on top, in closed form.
    # The horizon and the floor take the same favourable end under both lifetime
    # policies, so one corner still serves both tornados; the floor is simply
    # inert on the no-replacement leg.
    "best": ["--discount-rate", "0.035", "--price-escalation", "0.04",
             "--calendar-life-years", "15", "--calendar-form", "sqrt",
             "--cycle-life-efc", "8000", "--deg-scenarios", *_efc_scenarios(8000),
             "--horizon-years", "25", "--soh-floor", "0.50"],
}


def run_variant(name: str, extra: list[str], scratch: Path, overwrite: bool) -> None:
    out_dir = SENS_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    for price, base_path in CONSTANT_PRICE_ASSEMBLIES.items():
        out = out_dir / base_path.name
        # Size, not existence: a killed run leaves a 0-byte file behind, and
        # skipping that would hide the failure until the assembly is read.
        if out.exists() and out.stat().st_size > 0 and not overwrite:
            print(f"  {name}/{out.name}: present, skipped")
            continue
        command = [
            sys.executable, "scripts/run_sweep.py",
            "--pv-cost-per-kwp", "1109", "--pv-capex-fixed", "0",
            "--battery-cost-per-kwh", f"{price:.0f}", "--battery-capex-fixed", "0",
            *extra,
            "--out", str(out),
            # Peak events are dispatch-derived and identical in every variant;
            # the runner insists on writing them, so they go to scratch.
            "--peak-out", str(scratch / "peaks.csv"), "--overwrite",
        ]
        print(f"  {name}/{out.name}: {' '.join(extra)}", flush=True)
        subprocess.run(command, cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)


def check_dispatch_unchanged(name: str) -> None:
    """Assert the variant re-valued the same dispatch rather than re-solving."""
    for price, base_path in CONSTANT_PRICE_ASSEMBLIES.items():
        base = pd.read_csv(REPO_ROOT / base_path)
        variant = pd.read_csv(SENS_DIR / name / base_path.name)
        if len(base) != len(variant):
            raise SystemExit(f"{name}: row count {len(variant)} != base {len(base)}")
        delta = (base.annual_saving_yr1_gbp - variant.annual_saving_yr1_gbp).abs().max()
        if delta > 1e-9:
            raise SystemExit(f"{name}: year-1 saving moved by {delta} at c={price:.0f}; "
                             "the variant re-solved dispatch instead of re-valuing it")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--variant", choices=sorted(VARIANTS), action="append")
    args = parser.parse_args()

    wanted = args.variant or list(VARIANTS)
    with tempfile.TemporaryDirectory() as scratch:
        for name in wanted:
            run_variant(name, VARIANTS[name], Path(scratch), args.overwrite)
            check_dispatch_unchanged(name)
            print(f"  {name}: dispatch identical to base, valuation only")

    print(f"\n  {len(wanted)} variants under {SENS_DIR}")


if __name__ == "__main__":
    main()
