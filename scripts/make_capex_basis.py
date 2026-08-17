"""Decompose every sweep row into benefit, fixed-cost and variable-cost terms.

The battery's contribution to NPV is exactly linear in the two capex terms::

    dnpv(F, c) = G - k_F * F - k_c * c * Q

with no cross term, because the replacement years and the realised life come
from the fade simulation, which never sees a price. `G` is the discounted
benefit of the battery, `k_F` and `k_c` are the multiples at which the fixed and
variable terms are actually charged over the horizon -- 1.0 under no
replacement, and above 1.0 under run-to-fade, where a swap re-incurs both terms
and the terminal residual credits back the unused life of the last pack.

Two probe runs pin all three coefficients, so *any* capex specification can then
be valued in closed form for both lifetime policies, with no further solves:

    F=0, c=0     -> dnpv = G
    F=1000, c=0  -> k_F
    F=0, c=1300  -> k_c   (results/_band_c1300.csv, already on disk)

    python scripts/make_capex_basis.py            # missing probes only
    python scripts/make_capex_basis.py --overwrite

The result is checked against two assemblies built the ordinary way -- the band
table and the fixed-plus-variable `lowF` table -- and nothing is written unless
both reproduce to within a penny.
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

from scripts.assemble_band_capex import band_price
from src.results_data import SCENARIO_KEY, _attach_reference

BASIS_DIR = REPO_ROOT / "results" / "capex_basis"

# probe -> (fixed term, per-kWh term). The variable probe is the assembly the
# break-even calculation already uses, so it is read rather than re-run.
FIXED_PROBE = 1000.0
VARIABLE_PROBE = 1300.0
PROBES = {"benefit.csv": (0.0, 0.0), "fixed1000.csv": (FIXED_PROBE, 0.0)}
VARIABLE_ASSEMBLY = REPO_ROOT / "results" / "_band_c1300.csv"

# A replacement pays the install overhead in full. The runner's 0.3 default
# exists to test a *proposed* discount on the fixed cost of a swap; adopting it
# in the base case would price a policy that is not being assumed elsewhere.
REPLACEMENT_FIXED_FRAC = 1.0

# (fixed, per-kWh, policies, source) of the assemblies the basis is checked
# against. The band table prices each size from its own capacity band, so its
# per-kWh term is a function rather than a scalar; it also carries no fixed term,
# so the replacement convention above cannot affect it and both policies check.
# `lowF` was built at the 0.3 default, so only its no-replacement rows -- where
# nothing is ever replaced and the convention is inert -- are comparable.
CHECKS = [(0.0, None, None, REPO_ROOT / "results" / "sweep_scenarios_v2_band.csv"),
          (2959.0, 475.0, ["no-replacement"],
           REPO_ROOT / "results" / "sweep_scenarios_v2_lowF.csv")]
TOLERANCE = 0.01


def run_probe(name: str, fixed: float, per_kwh: float, scratch: Path,
              overwrite: bool) -> None:
    out = BASIS_DIR / name
    if out.exists() and out.stat().st_size > 0 and not overwrite:
        print(f"  {name}: present, skipped")
        return
    print(f"  {name}: F={fixed:g}, c={per_kwh:g}", flush=True)
    subprocess.run(
        [sys.executable, "scripts/run_sweep.py",
         "--pv-cost-per-kwp", "1109", "--pv-capex-fixed", "0",
         "--battery-cost-per-kwh", f"{per_kwh:g}", "--battery-capex-fixed", f"{fixed:g}",
         "--replacement-fixed-frac", f"{REPLACEMENT_FIXED_FRAC:g}",
         "--out", str(out),
         # Peak events are dispatch-derived and identical in every run; the
         # runner insists on writing them, so they go to scratch.
         "--peak-out", str(scratch / "peaks.csv"), "--overwrite"],
        cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)


def build_basis() -> pd.DataFrame:
    """One row per scenario carrying `G`, `k_F` and `k_c`."""
    benefit = _attach_reference(pd.read_csv(BASIS_DIR / "benefit.csv"))
    fixed = _attach_reference(pd.read_csv(BASIS_DIR / "fixed1000.csv"))
    variable = _attach_reference(pd.read_csv(VARIABLE_ASSEMBLY))

    basis = benefit.merge(fixed[SCENARIO_KEY + ["dnpv"]].rename(columns={"dnpv": "dnpv_F"}),
                          on=SCENARIO_KEY, validate="one_to_one")
    basis = basis.merge(variable[SCENARIO_KEY + ["dnpv"]].rename(columns={"dnpv": "dnpv_c"}),
                        on=SCENARIO_KEY, validate="one_to_one")
    if len(basis) != len(benefit):
        raise SystemExit("the probes do not describe the same grid")

    basis["G"] = basis.dnpv
    basis["k_F"] = (basis.dnpv - basis.dnpv_F) / FIXED_PROBE
    basis["k_c"] = (basis.dnpv - basis.dnpv_c) / (VARIABLE_PROBE * basis.battery_kwh)
    return basis.drop(columns=["dnpv_F", "dnpv_c"])


def contribution(basis: pd.DataFrame, fixed: float, per_kwh: float | None) -> pd.Series:
    """Battery contribution to NPV under one capex specification."""
    price = basis.battery_kwh.map(band_price) if per_kwh is None else per_kwh
    return basis.G - basis.k_F * fixed - basis.k_c * price * basis.battery_kwh


def check(basis: pd.DataFrame, fixed: float, per_kwh: float | None,
          policies: list[str] | None, path: Path) -> None:
    """Reproduce an assembly built the ordinary way, or refuse to write."""
    actual = _attach_reference(pd.read_csv(path))[SCENARIO_KEY + ["dnpv"]]
    merged = basis.merge(actual, on=SCENARIO_KEY, suffixes=("", "_actual"),
                         validate="one_to_one")
    if len(merged) != len(actual):
        raise SystemExit(f"{path.name}: grid differs from the probes")
    if policies is not None:
        merged = merged[merged.policy.isin(policies)]
    residual = float((contribution(merged, fixed, per_kwh) - merged.dnpv_actual).abs().max())
    if residual > TOLERANCE:
        raise SystemExit(f"{path.name}: basis misprices by up to £{residual:.4f}")
    scope = "" if policies is None else f" ({', '.join(policies)} only)"
    print(f"  {path.name}{scope}: reproduced, max error £{residual:.2e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--out", type=Path, default=BASIS_DIR / "basis.csv")
    args = parser.parse_args()

    BASIS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        for name, (fixed, per_kwh) in PROBES.items():
            run_probe(name, fixed, per_kwh, Path(scratch), args.overwrite)

    basis = build_basis()
    for fixed, per_kwh, policies, path in CHECKS:
        check(basis, fixed, per_kwh, policies, path)

    basis.to_csv(args.out, index=False)
    print(f"  wrote {len(basis)} rows -> {args.out}")


if __name__ == "__main__":
    main()
