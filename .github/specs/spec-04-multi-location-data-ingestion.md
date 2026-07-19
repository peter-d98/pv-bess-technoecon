# Spec 04 — Multi-Location Data Ingestion

**Status:** Implemented · verified & accepted (2026-07-19) · **Owner:** — · **Depends on:** — (uses the existing loader + Spec 03 tariff layer) · **Blocks:** Specs 5, 6

## 1. Purpose

Make **UK location** a controlled axis of the parameter study. Provide a small,
tested **location registry** that binds each study location to its three
location-specific inputs — a PVGIS PV-generation file, an Octopus Agile price CSV,
and a region-specific `TariffRates` instance (flat / E7 / SEG rates + standing-charge
metadata) — so that `run_stage2` can be pointed at any location by name and produce a
like-for-like techno-economic result.

The MILP, the rolling-horizon runner, the tariff layer, the fade loop, and the NPV
model are all unchanged. Spec 03 already made `TariffRates` region-parameterised and the
loaders already take explicit file paths; this spec only supplies the **per-location
data bindings** and a thin resolution layer that selects them.

Rates and Agile data are real **2025** values for each location's DNO region
(VAT-inclusive); the PVGIS PV profile is **2023** (the latest year available) aligned
**positionally** (by half-hour-of-year); the CREST demand profile is **shared across all
locations** (a stated limitation, §9). Mixing a 2023 PV year with 2025 prices/demand is
the same acknowledged limitation carried from Spec 03.

## 2. Scope

### In scope
- A pure-Python module `src/locations.py`:
  - a `LocationSpec` dataclass bundling one location's key, human label, region,
    coordinates, PVGIS filename, Agile filename, and its `TariffRates` instance;
  - a `LOCATIONS` registry populated with the **four** study locations
    (Inverness, Glasgow, Manchester, Plymouth) from
    [data/location_data_summary.md](../../data/location_data_summary.md);
  - a `get_location(name)` accessor and a `resolve_paths(spec, data_dir)` helper that
    returns the absolute PV and Agile paths.
- Integration into `scripts/run_stage2.py` via a `--location` option that resolves the
  PV file, Agile file, and `TariffRates` for the chosen location (Glasgow remains the
  default, so current behaviour is preserved). Explicit `--pv-file` / `--agile-file`
  overrides still win.
- Unit tests with hand-checkable numeric oracles (rate values, file bindings, registry
  membership).

### Out of scope (handled elsewhere or deliberately excluded)
- **Per-location demand profiles.** A single CREST demand profile is reused for every
  location — a deliberate, stated limitation (§9). Regional demand differences are not
  modelled.
- **Per-location input validation.** Whether to replicate the Glasgow
  `docs/verification_report.md` PV/demand validation for each new location, or validate
  one and cite Glasgow for the rest, is **deferred** (§9, open item carried from the
  backlog README).
- **Live API retrieval.** Data is still ingested from the manually-downloaded PVGIS and
  Agile CSVs already in `data/`; the planned Octopus/PVGIS API layer is future work.
- **New tariff *construction* logic.** The flat / E7 / Agile / SEG constructors and
  `build_tariff` are unchanged (Spec 03). This spec only supplies per-region *values*.
- **The parameter-sweep harness** → Spec 05. **Figures** → Spec 06.
- **Changes to `src/model.py`, `src/battery.py`, `src/economics.py`,
  `src/degradation.py`, or `src/tariffs.py` logic** (project invariant). `src/tariffs.py`
  is imported (its `TariffRates` type is reused) but its code is not modified.

## 3. Inputs — the four study locations (real 2025 rates, VAT-inclusive)

All flat / E7 / SEG rates and standing charges are taken verbatim from
[data/location_data_summary.md](../../data/location_data_summary.md). SEG export is the
13.0 p/kWh national-average headline for all four locations (4.5 p/kWh untied floor and
0 p none remain the Spec 03 sensitivities). All values VAT-inclusive.

| Location | DNO region key | Flat (p/kWh) | E7 day (p/kWh) | E7 night (p/kWh) | Flat standing (p/day) | E7 standing (p/day) |
|---|---|---|---|---|---|---|
| Inverness | `north_scotland` | 26.42 | 32.60 | 13.75 | 57.55 | 58.20 |
| Glasgow | `southern_scotland` | 26.42 | 31.55 | 13.30 | 64.20 | 64.70 |
| Manchester | `nw_england` | 26.13 | 31.55 | 13.30 | 47.61 | 46.90 |
| Plymouth | `sw_england` | 26.39 | 31.90 | 13.40 | 57.89 | 58.30 |

Standing charges are **metadata only** — they cancel on a fixed tariff and never enter
the NPV (Spec 03 §2 decision). They are carried on `TariffRates` for a possible future
absolute-bill comparison. The E7 off-peak window is assumed **00:00–07:00** (7 h) for all
four locations (the standard Economy 7 window; see §9 open item — the summary does not
specify per-location windows).

### File bindings

| Location | PVGIS PV file (`data/`) | Agile CSV (`data/`) |
|---|---|---|
| Inverness | `Timeseries_57.479_-4.222_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv` | `agile-half-hour-actual-rates-01-01-2025_31-12-2025_NScot.csv` |
| Glasgow | `Timeseries_55.829_-4.276_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv` | `agile-half-hour-actual-rates-01-01-2025_31-12-2025_SScot.csv` |
| Manchester | `Timeseries_53.483_-2.242_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv` | `agile-half-hour-actual-rates-01-01-2025_31-12-2025_NWEng.csv` |
| Plymouth | `Timeseries_50.373_-4.143_SA3_4kWp_crystSi_14_35deg_0deg_2023_2023.csv` | `agile-half-hour-actual-rates-01-01-2025_31-12-2025_SWEng.csv` |

> **Note (data discrepancy resolved):** the summary originally listed Manchester's PV
> file as `Timeseries_53.479_-2.244_…`, but the file actually present in `data/` is
> `Timeseries_53.483_-2.242_…` (a ~0.4 km coordinate difference — the same Manchester
> grid cell). This spec binds the **on-disk** filename and
> `data/location_data_summary.md` has been corrected to match. The demand profile is the
> single shared CREST file (`data/demand_halfhourly_2025.csv`) for every location.

## 4. Method

### 4.1 Location registry
Each location is a frozen `LocationSpec` bundling its identity, coordinates, the two
file basenames, and a fully-populated `TariffRates` instance carrying that region's real
2025 rates. The four specs are held in an ordered `LOCATIONS` dict keyed by lowercase
name (`"inverness"`, `"glasgow"`, `"manchester"`, `"plymouth"`).

`TariffRates` (Spec 03) is reused unchanged: each location instance sets `region`,
`year=2025`, `flat_rate`, `e7_day`, `e7_night`, `e7_offpeak_start_h=0.0`,
`e7_offpeak_end_h=7.0`, `seg_national=0.13`, `seg_untied=0.045`, and the two
standing-charge metadata fields. Glasgow's instance is numerically identical to the
current `TariffRates()` default, so selecting Glasgow reproduces today's baseline exactly.

### 4.2 Path resolution
`resolve_paths(spec, data_dir)` joins the two stored basenames onto `data_dir` and
returns `(pv_path, agile_path)` as `Path` objects. The demand path is **not** part of a
`LocationSpec` (shared across locations) and is supplied by the runner independently.

### 4.3 Accessor
`get_location(name)` looks up a location case-insensitively and raises a `KeyError` (or
`ValueError`) with the list of valid names on an unknown key, so the CLI fails fast with
a helpful message.

### 4.4 What flows into the pipeline
For a chosen location the runner obtains: (1) the PV file → `load_pv`; (2) the Agile file
→ `load_agile_prices` / the Agile tariff constructor; (3) the `TariffRates` instance →
`build_tariff` and the flat-tariff counterfactual rate. The headline whole-system
counterfactual is grid-only on **that location's** flat rate (Spec 03 §4.7), so the
counterfactual correctly tracks the location. Everything downstream (dispatch, costs,
fade, NPV) is untouched.

## 5. Interface (`src/locations.py`)

```python
from dataclasses import dataclass
from pathlib import Path
from src.tariffs import TariffRates


@dataclass(frozen=True)
class LocationSpec:
    """One UK study location: identity, coordinates, data files, and tariff rates."""
    name: str            # lowercase key, e.g. "glasgow"
    label: str           # human label, e.g. "Glasgow"
    region: str          # DNO region key, matches TariffRates.region
    latitude: float
    longitude: float
    pv_file: str         # basename in data/
    agile_file: str      # basename in data/
    rates: TariffRates   # region-specific 2025 rates (Spec 03 type, unchanged)


LOCATIONS: dict[str, LocationSpec]     # keyed by lowercase name; the four §3 rows

def get_location(name: str) -> LocationSpec: ...
def resolve_paths(spec: LocationSpec, data_dir: str | Path) -> tuple[Path, Path]: ...
```

## 6. Integration

- `scripts/run_stage2.py`: add
  `--location {inverness,glasgow,manchester,plymouth}` (default `glasgow`). When set, the
  runner:
  1. looks up the `LocationSpec` via `get_location`;
  2. sets the PV and Agile paths from `resolve_paths(...)` **unless** the user passed an
     explicit `--pv-file` / `--agile-file` (explicit flags win);
  3. uses the location's `rates` instance in place of the hard-coded `TariffRates()` when
     calling `build_tariff(...)` and when taking the flat-tariff counterfactual rate.
- The demand file default is unchanged (shared CREST profile).
- Glasgow's registry values equal the current `TariffRates()` and `DEFAULT_*` files, so
  the **default invocation (`--location glasgow`, or no flag) reproduces the current
  baseline bit-for-bit** — a regression guard.
- **No changes** to `src/model.py`, `src/battery.py`, `src/economics.py`,
  `src/degradation.py`, or the *logic* of `src/tariffs.py`.

## 7. Tests (`tests/test_locations.py`)

Hand-checkable oracles (compute independently; do not read from the module):

| # | Case | Expected |
|---|------|----------|
| L1 | `set(LOCATIONS)` | exactly `{inverness, glasgow, manchester, plymouth}` (4 entries) |
| L2 | `get_location("glasgow").rates` | `flat_rate 0.2642`, `e7_day 0.3155`, `e7_night 0.1330`, `region "southern_scotland"` |
| L3 | `get_location("inverness").rates` | `flat_rate 0.2642`, `e7_day 0.3260`, `e7_night 0.1375` |
| L4 | `get_location("manchester").rates` | `flat_rate 0.2613`, `e7_day 0.3155`, `e7_night 0.1330` |
| L5 | `get_location("plymouth").rates` | `flat_rate 0.2639`, `e7_day 0.3190`, `e7_night 0.1340` |
| L6 | every location's `rates` | `year == 2025`, `seg_national == 0.13`, `e7_offpeak_start_h == 0.0`, `e7_offpeak_end_h == 7.0` |
| L7 | standing-charge metadata (Glasgow / Manchester) | flat `0.642` / `0.4761`; E7 `0.647` / `0.4690` |
| L8 | `resolve_paths(get_location("manchester"), DATA_DIR)` | PV path ends `…53.483_-2.242…2023_2023.csv`; Agile path ends `…NWEng.csv`; **both exist on disk** |
| L9 | every location's resolved PV and Agile paths | all four × 2 files exist under `data/` |
| L10 | `get_location("GLASGOW")` (case-insensitive) | returns the Glasgow spec |
| L11 | `get_location("cardiff")` (unknown) | raises `KeyError`/`ValueError` naming the valid keys |
| L12 | `get_location("glasgow").rates` vs `TariffRates()` default | field-for-field equal (baseline-preservation guard) |

Plus: the full existing suite still passes; `model.py`, `battery.py`, `economics.py`,
`degradation.py`, and `tariffs.py` are unchanged (diff).

## 8. Acceptance criteria (MUST unless marked SHOULD)

- `src/locations.py` implemented with the §5 interface; `LOCATIONS` carries the four §3
  rows with rates transcribed exactly from `data/location_data_summary.md`.
- All L1–L12 oracles pass; full suite green; `model.py` / `battery.py` /
  `economics.py` / `degradation.py` / `tariffs.py` logic unchanged (diff).
- `run_stage2.py` runs end-to-end for each of the four locations with a chosen tariff and
  reports the location-specific operating cost, annual saving, NPV, and BCR, reusing the
  unchanged dispatch / NPV path.
- `--location glasgow` (and the no-flag default) reproduces the current Spec-03 baseline
  exactly (regression guard, L12).
- Each location's flat-tariff counterfactual uses **that location's** flat rate (§4.4).
- Rates cross-checked against `data/location_data_summary.md` in
  `spec-04-verification.md`.
- Independent verification per `spec-04-verification.md` completed.

*(Recorded baseline numbers per location live in §11 (Results), not in the acceptance
criteria.)*

## 9. Open decisions

- **Location set vs. the backlog wording.** CONFIRMED (2026-07-19) — the study uses the
  **four** supplied locations: Inverness (N Scotland), Glasgow (S Scotland), Manchester
  (NW England), Plymouth (SW England). No separate Midlands location is modelled. The
  backlog README row is reworded to match.
- **Per-location input validation.** DEFERRED (2026-07-19) — the Glasgow
  `docs/verification_report.md` PV/demand validation is **not** replicated per location
  for now; revisit before the results chapter.
- **Shared demand profile.** CONFIRMED (2026-07-19) — a single CREST profile
  (`data/demand_halfhourly_2025.csv`) is reused for every location; regional demand
  differences are unmodelled. Stated limitation.
- **E7 off-peak window per location.** CONFIRMED (2026-07-19) — the standard 00:00–07:00
  (7 h) window is used for all four locations.
- **Manchester PV filename discrepancy** (§3 note): RESOLVED (2026-07-19) — the spec binds
  the on-disk file (`Timeseries_53.483_-2.242_…`) and `data/location_data_summary.md` has
  been corrected to match.
- **Data-year mix** (carried from Spec 03): 2023 PV with 2025 Agile/standing rates,
  aligned positionally; shared 2025 demand. Left unchanged — stated limitation.

## 10. References (to confirm during implementation/write-up)

| Quantity | Source |
|---|---|
| Per-location flat / E7 / standing rates (2025) | `data/location_data_summary.md` (to be pinned to Ofgem cap + supplier E7 tariffs per DNO region) |
| SEG 13.0 p/kWh (headline), 4.5 p/kWh (untied floor) | Smart Export Guarantee national-average / untied rates (Spec 03) |
| Agile import & export per region (2025) | Octopus Agile actual half-hourly rates, per DNO region CSV |
| PV generation per location (2023) | PVGIS SARAH-3 hourly Timeseries, per coordinate |

All rates VAT-inclusive. Exact source URLs and the 2025 averaging basis to be pinned in
the dissertation.

## 11. Results (baseline — per-location, to be filled)

Recorded here (not in the acceptance criteria) so they can be revised as the model
evolves. To be populated once the four locations are run end-to-end (whole-system,
£15,360 capex, derived degradation penalty, headline counterfactual = grid-only on each
location's flat rate, run-to-fade replacement policy).

| Location | Tariff | PV yield (kWh/yr) | Operating cost (£/yr) | Annual saving (£/yr) | Run-to-fade NPV (£) | BCR |
|---|---|---|---|---|---|---|
| Inverness | — | — | — | — | — | — |
| Glasgow | — | — | — | — | — | — |
| Manchester | — | — | — | — | — | — |
| Plymouth | — | — | — | — | — | — |

*(Glasgow / Agile is expected to match the Spec 03 §11 baseline of NPV −£4,040,
BCR 0.784 as a cross-check.)*
