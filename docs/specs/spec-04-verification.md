# Spec 04 — Verification Plan

Companion to [spec-04-multi-location-data-ingestion.md](spec-04-multi-location-data-ingestion.md).
Defines what "correct" means, the independent-review gate, and the external
(source-grounding) checks. Mirrors the Spec 01/02/03 workflow.

## 1. Numeric / binding oracles (recompute independently — do NOT read module output)

A second reviewer (subagent, separate tool, or by hand) must reproduce these *without*
seeing the implementation's answers. All rate values are transcribed from
[data/location_data_summary.md](../../data/location_data_summary.md) (VAT-inclusive).

| # | Input | Quantity | Expected |
|---|-------|----------|----------|
| L1 | `set(LOCATIONS)` | registry keys | exactly `{inverness, glasgow, manchester, plymouth}` (4) |
| L2 | `get_location("glasgow").rates` | flat / e7_day / e7_night / region | 0.2642 / 0.3155 / 0.1330 / `southern_scotland` |
| L3 | `get_location("inverness").rates` | flat / e7_day / e7_night | 0.2642 / 0.3260 / 0.1375 |
| L4 | `get_location("manchester").rates` | flat / e7_day / e7_night | 0.2613 / 0.3155 / 0.1330 |
| L5 | `get_location("plymouth").rates` | flat / e7_day / e7_night | 0.2639 / 0.3190 / 0.1340 |
| L6 | every location's `rates` | year / seg_national / e7 window | 2025 / 0.13 / [0.0, 7.0) |
| L7 | Glasgow / Manchester standing charges | flat_standing / e7_standing (GBP/day) | 0.642 & 0.647 / 0.4761 & 0.4690 |
| L8 | `resolve_paths(get_location("manchester"), DATA_DIR)` | PV / Agile basenames | `…53.483_-2.242…2023_2023.csv` / `…NWEng.csv`; both exist |
| L9 | all four locations' resolved paths | 8 files (4 PV + 4 Agile) | every file exists under `data/` |
| L10 | `get_location("GLASGOW")` | case-insensitive lookup | returns the Glasgow spec |
| L11 | `get_location("cardiff")` | unknown key | raises `KeyError`/`ValueError` naming valid keys |
| L12 | `get_location("glasgow").rates` vs `TariffRates()` | field-for-field | equal (baseline-preservation guard) |

Hand-derivations / notes for the non-obvious ones:
- **L1:** four confirmed locations; no Midlands entry (confirmed 2026-07-19).
- **L7:** standing charges are p/day in the summary → GBP/day = value / 100; they are
  **metadata only** and must not enter any NPV.
- **L8/L9:** the Manchester PV file is the **on-disk** `53.483_-2.242` name, not the
  summary's original `53.479_-2.244` (now corrected). The reviewer should confirm the
  files physically exist, not merely that the strings are well-formed.
- **L12:** Glasgow's registry `TariffRates` must equal the Spec 03 default instance so
  that `--location glasgow` (and the no-flag default) reproduces the current baseline
  bit-for-bit.

## 2. Independent code review (second-AI gate)

### 2.1 Pre-implementation (this document)
- Confirm `src/locations.py` only *binds data and rates* and does not touch the MILP,
  battery, NPV, degradation, or the *logic* of the tariff layer (it may import
  `TariffRates`).
- Confirm the four `TariffRates` instances set `region`, `year=2025`, the flat/E7/SEG
  values, the 00:00–07:00 window, and the standing-charge metadata — with values matching
  `data/location_data_summary.md` exactly.
- Confirm standing charges remain **metadata only** and never enter the NPV.
- Confirm the shared demand profile is **not** part of `LocationSpec` (supplied by the
  runner) — one CREST profile for all locations.

### 2.2 Post-implementation (run on final code before acceptance)
- Recompute L1–L12 blind; all must match.
- Verify `src/model.py`, `src/battery.py`, `src/economics.py`, `src/degradation.py`, and
  the *logic* of `src/tariffs.py` are unchanged vs pre-Spec-04 (diff).
- Confirm `--location glasgow` (and the default) reproduces the Spec-03 baseline
  bit-for-bit — same PV/Agile files, same `TariffRates`, so the reported operating cost,
  annual saving, NPV and BCR are identical to the recorded Glasgow baseline.
- Confirm each non-Glasgow location resolves to a *different* PV file, Agile file, and
  rate set, and that the flat-tariff counterfactual uses **that location's** flat rate.
- Confirm explicit `--pv-file` / `--agile-file` still override the location defaults.
- Confirm `get_location` fails fast and helpfully on an unknown key (L11).

## 3. External context (source grounding, beyond the code)

- **Cross-check each per-location rate against `data/location_data_summary.md`** (the
  single source of truth for this spec) and, where possible, against the underlying Ofgem
  regional price cap (2025) and the supplier E7 tariffs per DNO region.
- **Sanity anchors (per location):** E7 day rate > flat rate > E7 night rate (ToU shape)
  for all four; flat rates cluster tightly (26.13–26.42 p/kWh) as expected for a single
  national cap year with small regional variation; standing charges vary more by region
  (47.6–64.7 p/day) — plausible.
- **PV-yield ordering sanity:** annual PV yield should fall broadly with latitude —
  Plymouth (50.4°N) ≥ Manchester (53.5°N) ≥ Glasgow (55.8°N) ≥ Inverness (57.5°N). A gross
  inversion would indicate a mis-bound PV file.
- **VAT basis** confirmed consistent (all inclusive) across locations and sources.
- **Data-year mix:** 2025 tariffs/Agile/demand with 2023 PV, aligned positionally; shared
  demand profile across locations — both stated limitations.
- **Supervisor sign-off** on the four-location set, the shared demand profile, and the
  deferred per-location input validation.

## 4. Definition of done

- All §1 oracles reproduced blind; §2.2 checks pass.
- All acceptance criteria in spec §8 met; full test suite green.
- `--location glasgow` reproduces the Spec-03 Glasgow baseline exactly (regression guard).
- Per-location baseline results (§11 of the spec) recorded once the four locations are run
  end-to-end.
- Stated limitations (shared demand profile; 2025 tariffs with 2023 PV; deferred
  per-location input validation; standing charges excluded from NPV) written up for the
  dissertation.

### Acceptance record
- **2026-07-19 — Independent AI reviewer — PASS.** All 8 checks passed with no
  discrepancies. (1) Rates re-derived blind from `data/location_data_summary.md` (÷100 to
  GBP) and matched exactly for all four locations (flat/E7-day/E7-night/standing/SEG); SEG
  0.13, E7 window [0.0, 7.0), year 2025 confirmed. (2) All 8 PV/Agile files exist on disk;
  Manchester binds the on-disk `53.483_-2.242` name and the summary was corrected to match.
  (3) `get_location("glasgow").rates == TariffRates()` → True (baseline preserved
  field-for-field). (4) `model.py`/`battery.py`/`economics.py`/`degradation.py`/`tariffs.py`
  empty diff vs HEAD (tariff logic imported, not modified). (5) 80/80 tests pass.
  (6) case-insensitive lookup + `KeyError` naming valid keys on unknown location confirmed.
  (7) PV yield falls with latitude (Plymouth 4342 > Glasgow 3387 kWh/yr) — no mis-binding.
  (8) `--location` default glasgow with 4 choices; explicit `--pv-file`/`--agile-file`
  override; `location.rates` (not a hardcoded `TariffRates()`) passed to `build_tariff`;
  demand shared (not a `LocationSpec` field). **Spec 04 = VERIFIED / ACCEPTED.**
