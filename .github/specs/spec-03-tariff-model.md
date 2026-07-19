# Spec 03 — Tariff Model Layer

**Status:** Implemented · verified & accepted (2026-07-15) · **Owner:** — · **Depends on:** — (uses the existing price interface) · **Blocks:** Specs 5, 6

## 1. Purpose

Provide a small, tested **tariff layer** that constructs the two half-hourly price
series the dispatch MILP already consumes — `import_price` and `export_price`
(GBP/kWh) — under a named set of real-world tariffs, so that **tariff structure**
becomes a controlled axis of the parameter study.

The MILP, `run_stage2`, the fade loop, and the NPV model are unchanged: they still
see only the two price arrays. This spec only changes *how those arrays are built*.

Rates are grounded in real **Southern Scotland (SP Distribution), 2025,
VAT-inclusive** values (Glasgow's DNO region). The Agile and fixed-tariff data are
2025; the PV profile is PVGIS 2023 (the latest available) and is aligned
**positionally** (by half-hour-of-year), and the CREST demand profile is the 2025
calendar file so its weekday/weekend structure matches the 2025 Agile prices. Mixing a
2023 PV year with 2025 prices/demand is a stated limitation (§9).

## 2. Scope

### In scope
- A pure-Python module `src/tariffs.py`:
  - `flat` single-rate import constructor;
  - `economy7` two-rate import constructor with a configurable off-peak window;
  - `agile` import + export constructor (wraps the existing `load_agile_prices`);
  - export constructors: constant `flat_seg`, dynamic `agile` export, and `none` (0p);
  - a `TariffRates` dataclass holding the real Southern Scotland 2023 constants
    (region-parameterised so Spec 04 can add regions without touching logic);
  - a `build_tariff(...)` factory returning the `(import_price, export_price)` pair
    for a named tariff + export choice, aligned to the canonical half-hourly index.
- Integration into `scripts/run_stage2.py` via `--tariff` and `--export` CLI options
  (Agile import + Agile export remains the default, so current behaviour is preserved).
- Unit tests with hand-checkable numeric oracles.

### Out of scope (handled elsewhere or deliberately excluded)
- **Multi-region rate tables** → Spec 04. The module is region-parameterised, but only
  Southern Scotland rates are populated now.
- **Standing charges in the NPV.** Excluded by decision: on a given tariff the daily
  standing charge is identical with and without the PV-BESS, so it cancels in the saving
  and does not affect the investment NPV. Stored as `TariffRates` metadata only, for a
  possible future absolute-bill comparison.
- **Absolute cross-tariff bill comparison** (would need standing charges) — future work.
- **Unmatched import/export cross-products.** Only *matched* (import, export) pairs are
  reported (§4.5). Legally import and export contracts are independent, but realistic
  "untied" export is ~4.5 p/kWh; higher SEG (~13 p/kWh) is a tied-tariff offer.
- **The parameter-sweep harness** → Spec 05. **Figures** → Spec 06.
- **Changes to `src/model.py` or `src/battery.py`** (project invariant).

## 3. Inputs — real Southern Scotland 2025 rates (VAT-inclusive)

| Quantity | Value | Source (to cite) |
|---|---|---|
| Flat single-rate unit price | **26.42 p/kWh** | Ofgem price-cap unit rate, Southern Scotland, 2025 (representative annual) |
| Flat standing charge | 64.20 p/day (metadata; excluded from NPV) | as above |
| Economy 7 day rate | **31.55 p/kWh** | E7 tariff, Southern Scotland, 2025 |
| Economy 7 night rate | **13.30 p/kWh** | as above |
| Economy 7 off-peak window | **00:00–07:00** (7 h) | as above |
| Economy 7 standing charge | 64.70 p/day (metadata; excluded from NPV) | as above |
| Flat SEG export (headline) | **13.0 p/kWh** | national-average SEG / tied outgoing tariff |
| Flat SEG export (untied floor, sensitivity) | **4.5 p/kWh** | typical untied SEG minimum |
| No export (self-consumption only, sensitivity) | 0 p/kWh | — |
| Agile import & export | 2025 Southern Scotland CSV (`..._SScot.csv`) | Octopus Agile actual rates 2025 |

All values are **VAT-inclusive** (the Agile CSV is confirmed VAT-inclusive), so the
series are directly comparable. Single **annual representative** values are used for the
flat and E7 rates (not quarter-by-quarter).

```python
@dataclass(frozen=True)
class TariffRates:
    """Real tariff rates for one DNO region/year (Southern Scotland 2025 defaults).

    All prices GBP/kWh, VAT-inclusive, unless stated. Region-parameterised: Spec 04
    supplies a different instance per region.
    """
    region: str = "southern_scotland"
    year: int = 2025

    flat_rate: float = 0.2642
    e7_day: float = 0.3155
    e7_night: float = 0.1330
    e7_offpeak_start_h: float = 0.0     # inclusive hour-of-day
    e7_offpeak_end_h: float = 7.0       # exclusive hour-of-day

    seg_national: float = 0.13          # headline flat SEG
    seg_untied: float = 0.045           # untied-floor sensitivity

    # Metadata only — NOT used in the NPV (standing charges cancel on a fixed tariff).
    flat_standing_charge_per_day: float = 0.642
    e7_standing_charge_per_day: float = 0.647
```

## 4. Method

### 4.1 Flat import
Constant series: `import_price[t] = flat_rate` for every half-hour of the canonical index.

### 4.2 Economy 7 import
Two-rate series keyed on **local time-of-day**:
```
tod_h = index.hour + index.minute / 60
night = (tod_h >= offpeak_start_h) & (tod_h < offpeak_end_h)     # non-wrapping
        # if start > end (wrapping window, e.g. 23:30–06:30):
        # night = (tod_h >= offpeak_start_h) | (tod_h < offpeak_end_h)
import_price[t] = e7_night if night else e7_day
```
For the default 00:00–07:00 window this is the first **14 half-hours** of each day
(indices 0–13: 00:00 … 06:30) at the night rate, the remaining **34** at the day rate.

### 4.3 Export series
- `flat_seg`: constant `export_price[t] = seg_rate` (13.0p headline or 4.5p untied).
- `agile`: the Agile export column from the CSV (already GBP/kWh).
- `none`: `export_price[t] = 0.0` for all t.

### 4.4 Agile import/export
Wraps `load_agile_prices` (unchanged): returns the CSV's `import_price` and
`export_price` in GBP/kWh, aligned to the canonical index (DST handling as documented).

### 4.5 Matched (import, export) pairs — the reported tariff axis
Only realistic matched pairs are reported. `build_tariff(name, ...)` resolves the export
when `export="matched"`:

| Import tariff | Matched export (headline) | Sensitivities |
|---|---|---|
| `flat` | `flat_seg` @ 13.0p | 4.5p untied; none |
| `e7` | `flat_seg` @ 13.0p | 4.5p untied; none |
| `agile` | `agile` (dynamic) | 4.5p untied; none |

The export choice can be overridden explicitly (`--export`) for the sensitivities.

### 4.6 Factory
```
build_tariff(name, index, rates, agile_path=None, export="matched", seg_rate=None)
    -> (import_price: pd.Series, export_price: pd.Series)
```
- `name ∈ {"flat", "e7", "agile"}` selects the import constructor.
- `export ∈ {"matched", "seg", "agile", "none"}`; `seg_rate` overrides the SEG value
  (defaults to `rates.seg_national` for headline, `rates.seg_untied` for the untied case).
- `agile_path` is required when Agile import or Agile export is involved.
- Both returned series are indexed by `index` and expressed in GBP/kWh.

### 4.7 Reported metrics, counterfactual, and replacement policy
The runner reports each tariff's economics as **separate** quantities so the tariff
ranking is not collapsed into one ambiguous "saving":
- **Operating cost** — the PV-BESS household's annual net energy cost on its tariff
  (import − export).
- **Annual saving (headline)** — versus the **grid-only flat** counterfactual: a
  do-nothing household with no PV/battery importing all demand on the standard flat
  tariff (26.42 p/kWh), *independent of the system's tariff*. Tariff choice is thus part
  of the investment decision (install PV-BESS + possibly switch tariff vs. do nothing on
  flat).
- **Decomposition (sensitivity)** — the **same-tariff** grid-only counterfactual isolates
  the PV-BESS hardware value from the tariff-switch value:
  `PV-BESS saving = grid-only@tariff − operating cost`;
  `tariff-switch effect = grid-only@flat − grid-only@tariff`.
- **NPV & BCR** — the **baseline replacement policy is run-to-fade** (Spec 02
  `--fade-npv`: 0.60 SOH floor, warranty-residual credit). **Forced 10-year replacement**
  is retained as a conservative sensitivity (the always-computed flat-NPV block). Standing
  charges are excluded (they cancel on a fixed tariff).

## 5. Interface (`src/tariffs.py`)

```python
@dataclass(frozen=True)
class TariffRates: ...        # §3

def flat_import(index: pd.DatetimeIndex, rate: float) -> pd.Series: ...
def economy7_import(index, day: float, night: float,
                    offpeak_start_h: float, offpeak_end_h: float) -> pd.Series: ...
def flat_export(index, seg_rate: float) -> pd.Series: ...
def no_export(index) -> pd.Series: ...
def agile_import_export(agile_path, index) -> tuple[pd.Series, pd.Series]: ...

def build_tariff(name: str, index: pd.DatetimeIndex, rates: TariffRates,
                 agile_path=None, export: str = "matched",
                 seg_rate: float | None = None) -> tuple[pd.Series, pd.Series]: ...
```

## 6. Integration

- `scripts/run_stage2.py`: add `--tariff {flat,e7,agile}` (default `agile`) and
  `--export {matched,seg,agile,none}` (default `matched`), plus `--seg-rate` override.
  After `load_all` builds `pv_kw`/`demand_kw` (and the default Agile prices), overwrite
  `data["import_price"]` / `data["export_price"]` with `build_tariff(...)` output unless
  the tariff is Agile with Agile export (the default, already loaded). The rest of the
  runner (dispatch, costs, fade, NPV) is untouched.
- `TariffRates()` defaults to Southern Scotland 2025.
- **No changes** to `src/model.py`, `src/battery.py`, or the NPV/degradation modules.

## 7. Tests (`tests/test_tariffs.py`)

Hand-checkable oracles (compute independently; do not read from the module):

| # | Case | Expected |
|---|------|----------|
| T1 | `flat_import(index2023, 0.30)` | every value `0.30`; length 17 520 |
| T2 | E7 default window: value at 00:00, 06:30, 07:00, 12:00 | `0.1435, 0.1435, 0.3439, 0.3439` |
| T3 | E7 count of night half-hours in 2023 | `14 × 365 = 5110` |
| T4 | E7 mean import over one day | `(14·0.1435 + 34·0.3439)/48 = 0.285450 GBP/kWh` |
| T5 | E7 wrapping window 23:30–06:30 → night mask at 23:30 and 06:00 | both night; 06:30 day |
| T6 | `flat_export(index, 0.045)` / `0.13` | constant `0.045` / `0.13` |
| T7 | `no_export(index)` | all `0.0` |
| T8 | `agile_import_export` reproduces `load_agile_prices` import/export | element-wise equal |
| T9 | `build_tariff("flat", …, export="matched")` | import `0.2642`; export `0.13` (seg_national) |
| T10 | `build_tariff("agile", …, export="matched")` | import & export equal the Agile CSV series |
| T11 | `build_tariff("e7", …, export="none")` export | all `0.0`; import ∈ {`0.1330`, `0.3155`} |

Plus: the full existing suite (68 tests) still passes; `model.py`/`battery.py` unchanged.

## 8. Acceptance criteria (MUST unless marked SHOULD)

- `src/tariffs.py` implemented with the §5 interface; `TariffRates` carries the real
  Southern Scotland 2025 values from §3.
- All T1–T11 oracles pass; full suite green; `model.py`/`battery.py` unchanged (diff).
- `run_stage2.py` runs end-to-end for each of `flat`, `e7`, `agile` with matched export
  and reports **operating cost, annual saving, NPV, and BCR separately** (§4.7), reusing
  the unchanged dispatch/NPV path.
- The headline whole-system counterfactual is grid-only on the flat tariff; the
  same-tariff decomposition is reported as a sensitivity; the baseline replacement policy
  is run-to-fade, with forced 10-year replacement as a conservative sensitivity.
- Rates cross-checked against the cited sources (§3) in `spec-03-verification.md`.
- Independent verification per `spec-03-verification.md` completed.

*(Recorded baseline numbers live in §11 (Results), not in the acceptance criteria.)*

## 9. Open decisions

- **Matched-pair definition (§4.5):** CONFIRMED — headline = `flat`/`e7` → 13p SEG,
  `agile` → Agile export; 4.5p untied and no-export as sensitivities.
- **Base data year:** fixed tariffs, Agile, and demand are **2025** (Southern Scotland);
  the PV profile is **2023** (latest PVGIS available), aligned positionally. Mixing a 2023
  PV year with 2025 prices/demand is a stated limitation. 2025 is a calmer, post-crisis
  year — more representative of a forward-looking investment than the elevated 2023.
- **E7 standing charge** supplied (64.70 p/day); stored as metadata, excluded from the NPV.

## 10. References (to confirm during implementation/write-up)

| Rate | Source |
|---|---|
| Flat 26.42 p/kWh + 64.20 p/day standing charge | Ofgem energy price cap, Southern Scotland region, 2025 |
| E7 day 31.55 / night 13.30 p/kWh, 00:00–07:00, 64.70 p/day | Economy 7 tariff, Southern Scotland, 2025 |
| SEG 13.0 p/kWh (headline), 4.5 p/kWh (untied floor) | Smart Export Guarantee national-average / untied rates |
| Agile import & export (2025) | Octopus Agile actual half-hourly rates, Southern Scotland |

All rates VAT-inclusive. Exact source URLs/DOIs and the precise 2025 averaging basis to
be pinned in the dissertation.

## 11. Results (baseline — Southern Scotland 2025, Glasgow, whole-system)

Recorded here (not in the acceptance criteria) so they can be revised as the model
evolves. Framing: whole-system, £15,360 capex, derived 5 p/kWh degradation penalty,
headline counterfactual = grid-only on the flat tariff (26.42 p/kWh), **run-to-fade**
replacement policy. Metrics reported separately.

| Tariff (matched export) | Operating cost (£/yr) | Annual saving (£/yr) | Run-to-fade NPV (£) | BCR |
|---|---|---|---|---|
| Flat (→ 13p SEG) | 550.50 | 749.42 | −6,700 | 0.641 |
| Economy 7 (→ 13p SEG) | 359.77 | 940.15 | −5,292 | 0.722 |
| Agile (→ Agile export) | 359.97 | 939.94 | −4,040 | 0.784 |

**Ranking (run-to-fade NPV): Agile > E7 > Flat.** None is viable, but Agile is closest.

**Decomposition** (headline saving vs grid-only-flat = tariff-switch effect + PV-BESS
saving on that tariff):

| Tariff | grid-only @ tariff (£/yr) | PV-BESS saving, same tariff (£/yr) | tariff-switch effect (£/yr) |
|---|---|---|---|
| Flat | 1,299.92 | 749.42 | 0.00 |
| Economy 7 | 1,455.40 | 1,095.64 | −155.49 |
| Agile | 1,064.55 | 704.58 | +235.36 |

Two findings: (1) **E7 and Agile deliver near-identical headline savings (~£940), but Agile
has the better NPV** because E7's deep, predictable night/day cycling wears the battery
faster (replacement at year 16 vs 18; residual £3,800 vs £5,320) — a degradation effect
that a naïve annual-saving comparison would miss. (2) The decomposition separates the two
value sources: on **E7** almost all value is the battery (£1,096 PV-BESS saving, but −£155
to switch a no-battery household onto E7's high day rate); on **Agile** the value is split
(+£235 from the cheaper tariff itself and £705 from the battery).

Forced-10-year-replacement NPVs (conservative sensitivity): flat −£10,002 (BCR 0.501),
E7 −£7,205 (0.640), Agile −£7,208 (0.640). Numbers from `results/tariff_fade_baselines.log`.
