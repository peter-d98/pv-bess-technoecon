# Spec 03 — Verification Plan

Companion to [spec-03-tariff-model.md](spec-03-tariff-model.md). Defines what "correct"
means, the independent-review gate, and the external (source-grounding) checks. Mirrors
the Spec 01/02 workflow.

## 1. Numeric oracles (recompute independently — do NOT read module output)

A second reviewer (subagent, separate tool, or by hand) must reproduce these *without*
seeing the implementation's answers.

| # | Input | Quantity | Expected |
|---|-------|----------|----------|
| T1 | flat 0.30, 2023 index | all values; length | 0.30; 17 520 |
| T2 | E7 default 00:00–07:00 at 00:00 / 06:30 / 07:00 / 12:00 | import price | 0.1435 / 0.1435 / 0.3439 / 0.3439 |
| T3 | E7 default, full 2023 | count of night half-hours | 14 × 365 = 5110 |
| T4 | E7 default, one day | mean import price | 0.285450 GBP/kWh |
| T5 | E7 window 23:30–06:30 at 23:30 / 06:00 / 06:30 | night mask | night / night / day |
| T6 | flat SEG 0.045 and 0.13 | all values | 0.045 / 0.13 |
| T7 | no_export | all values | 0.0 |
| T8 | agile constructor vs `load_agile_prices` | import & export arrays | element-wise equal |
| T9 | build_tariff("flat", export="matched") | import & export | 0.2642 / 0.13 |
| T10 | build_tariff("agile", export="matched") | import & export | equal Agile CSV series |
| T11 | build_tariff("e7", export="none") | export / import | all 0.0 / {0.1330, 0.3155} |

Hand-derivations for the non-obvious ones:
- **T3:** 7 h off-peak = 14 half-hours/day; non-leap 2023 has 365 days → 14 × 365 = 5110.
- **T4:** day has 14 night + 34 day half-hours → mean = (14·0.1435 + 34·0.3439)/48
  = (2.009 + 11.6926)/48 = 13.7016/48 = **0.285450** GBP/kWh.
- **T5:** wrapping window (start 23.5 > end 6.5) → night = `tod ≥ 23.5 or tod < 6.5`;
  23:30 (23.5) ✓, 06:00 (6.0) ✓, 06:30 (6.5) ✗ (end is exclusive).

## 2. Independent code review (second-AI gate)

### 2.1 Pre-implementation (this document)
- Confirm the tariff layer only *builds the two price arrays* and does not touch the MILP,
  battery, NPV, or degradation modules.
- Confirm the E7 window logic uses a half-open interval `[start, end)` on time-of-day and
  correctly handles the wrapping case (`start > end`).
- Confirm standing charges are **metadata only** and never enter the NPV.

### 2.2 Post-implementation (run on final code before acceptance)
- Recompute T1–T11 blind; all must match.
- Verify `src/model.py` and `src/battery.py` are byte-identical to pre-Spec-03 (diff).
- Confirm the Agile constructor is a thin wrapper that reproduces `load_agile_prices`
  exactly (no re-scaling, no double VAT).
- Confirm VAT consistency: flat/E7/SEG values are used as-is (already VAT-inclusive), not
  re-grossed; the Agile CSV is not re-adjusted.
- Confirm `build_tariff` matched pairs resolve to the §4.5 table, and that `--export`
  overrides work for the sensitivities.
- Confirm the Agile constructor reproduces `load_agile_prices` for the **same file/index**
  (a structural no-op in the tariff layer). The default baseline is now the **2025**
  Southern Scotland data; the 2023→2025 switch is a deliberate baseline change, not a
  regression.

## 3. External context (source grounding, beyond the code)

- **Cross-check each rate against its cited source** (§3/§10 of the spec): the Ofgem
  Southern Scotland 2025 cap unit rate (~26.42 p/kWh) and standing charge (~64.20 p/day);
  the E7 day/night split (31.55 / 13.30 p/kWh) and 7-hour window; the SEG national-average
  (~13 p/kWh) and untied floor (~4.5 p/kWh). Flag any value outside a defensible range.
- **Sanity anchors:** E7 day rate > flat rate > E7 night rate (ToU shape); E7 daily mean
  (~26.2 p/kWh) sits just below the flat rate, so E7 is *not* automatically cheaper unless
  the load is night-weighted — a real finding.
- **VAT basis** confirmed consistent (all inclusive) across the four sources.
- **Base/PV-year caveat:** tariffs, Agile and demand are 2025; PV is 2023 (aligned
  positionally). 2025 is a calmer post-crisis year (more representative than the elevated
  2023); the cross-year PV mix is the stated limitation.
- **Supervisor sign-off** on the matched-pair convention and the 2025/2023 data-year mix.

## 4. Definition of done

- All §1 oracles reproduced blind; §2.2 checks pass.
- All acceptance criteria in spec §8 met; full test suite green.
- Baseline NPVs for the three matched headline tariffs recorded (Glasgow, whole-system,
  run-to-fade).
- Stated limitations (2025 tariffs with 2023 PV; standing charges excluded from NPV;
  matched pairs only; single-region rates) written up for the dissertation.

### Acceptance record
- **2026-07-15 — Independent AI reviewer — PASS.** Oracles T1–T7 re-derived blind
  (scratch script not importing `src.tariffs`) and matched exactly: T1 17 520 slots all
  0.30; T2 0.1435/0.1435/0.3439/0.3439; T3 5110 night half-hours; T4 mean 0.285450; T5
  wrapping window night/night/day; T6/T7 constants. VAT single-conversion confirmed (no
  double gross-up; Agile `/100` happens once in `load_agile_prices`). E7 window half-open
  `[start,end)` with correct wrapping and exclusive 07:00 boundary. `build_tariff` matched
  pairs (flat/e7→0.13 SEG, agile→Agile export) and ValueError guards confirmed.
  `model.py`/`battery.py` byte-identical (empty diff). 68/68 tests pass. Rates plausible
  for 2023 with correct ToU ordering (E7_day > flat > E7_night). Agile default reproduces
  the pre-Spec-03 price path bit-for-bit. Residual issues: none blocking (the flagged
  Agile-export-missing-path coverage gap has since been closed with a dedicated test).
