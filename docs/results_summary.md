# Results Summary — PV–BESS Parameter Sweep

Headline findings from the Spec 06 sweep. Run provenance is in
[`spec06_run_manifest.md`](spec06_run_manifest.md); verification in
[`.github/specs/spec-06-verification.md`](../.github/specs/spec-06-verification.md);
the canonical assumption table in [`.github/specs/README.md`](../.github/specs/README.md).

---

## Headline

1. **A domestic battery is not worth adding in any of the 54 cells studied**, under either
   lifetime policy, at any size from 1 to 10 kWh, at any discount rate from 3.5% to 7%.
2. **The best case in the entire study breaks even at 81% of the observed installed
   price** — Agile tariff, Inverness, 5 kWp PV, 1 kWh battery, never replaced. It would
   need a **19% discount (£253)** to reach zero. Every other cell is further away. That
   battery *does* pay back in cash inside the horizon (13.9 years); it fails on
   discounting and capacity fade (§2.1).
3. **PV alone is viable**: 52/54 cells positive, mean NPV £3,297, best £7,857. The best
   system in the study is PV-only, and adding any battery to it makes it worse.
4. **The verdict does not depend on how capex is modelled.** Three specifications spanning
   a twentyfold disagreement about *what size to buy* all agree that *buying is not worth
   it*. The one exception is fully explained (§4).
5. **Optimal battery size is not identified by this study** and is reported as a
   limitation, not a result (§5).

---

## 1. How to read these tables

A **cell** is one (location, tariff, PV size) combination: 3 locations × 6 PV sizes = 18
cells per tariff, 54 in total. Within a cell, battery size, controller and wear penalty
are optimised, so every comparison is like-for-like.

"**Worth adding**" always means *the best battery configuration in a cell beats the
PV-only configuration in the same cell*. It is an incremental test. It is not "NPV > 0",
which a large PV array can carry to positive on its own and which says nothing about the
battery.

Two **lifetime policies** are reported side by side and bracket the truth:

| Policy | Meaning | Role |
|---|---|---|
| no-replacement | one pack for the full 20 years, zero terminal residual | **upper bound** on battery value |
| run-to-fade | replace at the SOH floor, each swap paid in full, residual credited | lower bound |

**Battery sizes reported are 1, 2.5, 5 and 10 kWh.** The grid also contains 0.5 kWh; it is
excluded throughout because it lies below the lowest published DESNZ capacity band (1 kWh),
so no price schedule can cost it without extrapolating.

**Capex.** The battery is priced at the DESNZ published median £/kWh for the capacity band
its size falls in — £1,300/kWh for 1–5.99 kWh, £890/kWh for 6–10.99 kWh. Nothing is
fitted. PV is £1,109/kWp linear, whose fitted fixed term (£98, negative in 2023/24) is
indistinguishable from zero. Battery capex by size is therefore £1,300 / £3,250 / £6,500 /
£8,900.

---

## 2. The break-even price surface *(primary result)*

**Break-even price** is the installed cost, in £/kWh, at which the battery exactly pays
for **its own** capex over the 20-year horizon — the most a buyer could pay and still not
lose money.

It is *not* a requirement that the battery match what the PV array earns. The test is the
price at which *adding* the battery changes total NPV by zero, and both sides of that
comparison contain the identical PV array, so the PV cancels exactly:

```
NPV(PV + battery) − NPV(PV only)  =  battery discounted benefit − battery capex
```

Setting that to zero gives `battery discounted benefit = battery capex`, which is the
ordinary standalone test. PV never enters it.

*How derived:* battery capex has no fixed term in this specification, so NPV is exactly
linear in the price per kWh. The break-even is therefore solved in closed form from the
two assemblies already on disk (£1,300/kWh and £890/kWh) for every configuration, then
maximised over controller and wear penalty within each cell. No search, no interpolation
error. Under no-replacement it reduces to the discounted operating benefit per kWh,
G(Q)/Q; under run-to-fade it additionally absorbs the discounted replacement outflow and
the terminal residual credit, which is why it is lower.

**Break-even price, £/kWh — mean over the 18 cells of each tariff (best single cell):**

| Policy | Tariff | 1 kWh | 2.5 kWh | 5 kWh | 10 kWh |
|---|---|---:|---:|---:|---:|
| no-replacement | flat | 387 (444) | 310 (390) | 249 (346) | 182 (281) |
| | E7 | 865 (915) | 748 (795) | 654 (691) | 539 (555) |
| | **Agile** | **981 (1,047)** | 862 (929) | 723 (783) | 538 (586) |
| run-to-fade | flat | 332 (379) | 275 (345) | 224 (305) | 166 (250) |
| | E7 | 703 (746) | 621 (656) | 559 (590) | 469 (483) |
| | Agile | 805 (852) | 717 (779) | 604 (647) | 442 (488) |
| — | **observed price** | **1,300** | **1,300** | **1,300** | **890** |

Expressed as a fraction of the observed price — **the number that matters**, since 1.00
is break-even:

| Policy | Tariff | 1 kWh | 2.5 kWh | 5 kWh | 10 kWh |
|---|---|---:|---:|---:|---:|
| no-replacement | flat | 0.30 (0.34) | 0.24 (0.30) | 0.19 (0.27) | 0.20 (0.32) |
| | E7 | 0.67 (0.70) | 0.58 (0.61) | 0.50 (0.53) | 0.61 (0.62) |
| | **Agile** | **0.75 (0.81)** | 0.66 (0.71) | 0.56 (0.60) | 0.60 (0.66) |
| run-to-fade | flat | 0.26 (0.29) | 0.21 (0.27) | 0.17 (0.23) | 0.19 (0.28) |
| | E7 | 0.54 (0.57) | 0.48 (0.50) | 0.43 (0.45) | 0.53 (0.54) |
| | Agile | 0.62 (0.66) | 0.55 (0.60) | 0.46 (0.50) | 0.50 (0.55) |

Three things to read off it:

- **Nothing reaches 1.00.** The maximum anywhere in the study is 0.81.
- **Tariff dominates.** Flat recovers 17–34% of cost; Agile recovers 46–81%. A
  time-varying tariff is necessary but nowhere near sufficient.
- **The surface dips at 5 kWh and recovers at 10 kWh.** That is not a modelling artefact:
  10 kWh crosses into the cheaper £890/kWh band, so the price falls faster than the
  benefit does. It is the clearest evidence in the study that battery economics are driven
  by the *price schedule*, not only by the physics.

### 2.1 Worked example: why a £1,300 battery saving £94/yr still fails

The best cell in the study — Inverness, Agile, 5 kWp, 1 kWh, no-replacement, MILP at
1 p/kWh wear penalty. A 1 kWh pack costing £1,300 and never replaced sounds like it must
pay back over twenty years, and in **cash terms it does**. It fails the NPV test anyway:

| | |
|---|---:|
| PV-only NPV | £2,507 |
| PV + 1 kWh battery NPV | £2,254 |
| **NPV change from adding the battery** | **−£253** |
| Battery capex | £1,300 |
| ⇒ discounted 20-year benefit | £1,047 → break-even **£1,047/kWh**, 0.81 of £1,300 |
| Year-1 saving from the battery | £93.54/yr |
| Simple payback, undiscounted | **13.9 years** |
| Undiscounted 20-yr total (2%/yr escalation, no fade) | £2,273 |

Two things convert a £2,273 cash return into £1,047 of present value:

- **Discounting.** A 20-year stream escalating at 2% real, discounted at 5% real, is worth
  14.67× its first year — not 20×.
- **Capacity fade.** The delivered multiple is only **11.19×**, not 14.67×. A 1 kWh pack
  cycling 503 EFC/yr accumulates ~10,000 EFC over the horizon, well past the 6,000 EFC
  80%-capacity point, and ends at **SOH 0.37**. Roughly a quarter of the nominal benefit
  is lost to degradation.

£93.54 × 11.19 = £1,047. So the honest one-line statement is: **the battery pays back in
cash inside the horizon but not in discounted terms** — a useful result in its own right,
and a distinction worth making explicitly in the dissertation, since simple payback is the
metric the domestic market actually quotes.

### 2.2 Discount-rate sensitivity

*How derived:* the same cache reassembled at 3.5% and 7% real (discount rate does not
enter dispatch either), band pricing throughout.

| Discount rate | Best cell, no-repl. | Best cell, run-to-fade | Cells worth adding |
|---|---:|---:|---:|
| 3.5% real (HMT Green Book) | 0.91 | 0.73 | 0/54 |
| **5.0% real (base)** | **0.81** | **0.66** | **0/54** |
| 7.0% real | 0.69 | 0.58 | 0/54 |

The verdict is unchanged at every rate, but the *margin* is rate-sensitive: at the Green
Book rate the best cell reaches 0.91, so a 9% discount would close it. Any claim about how
emphatic the negative result is must be stated together with the discount rate.

---

## 3. What it would take: the required discount

The gap between the break-even and the observed price is the discount a buyer would need —
from a co-installation saving, a subsidy, or a price fall — for the battery to be worth
adding.

*How derived:* directly from §2, as `observed price − break-even price`, for the single
most favourable cell in the study (Agile, Inverness, 5 kWp, 1 kWh).

| Policy | Break-even | Observed | Discount needed | In cash |
|---|---:|---:|---:|---:|
| no-replacement | £1,047/kWh | £1,300/kWh | **19%** | £253 |
| run-to-fade | £852/kWh | £1,300/kWh | **34%** | £448 |

This replaces the earlier approach of *assuming* a £1,000–1,500 shared-install saving,
which was an unfitted free parameter that decided the answer. Stating the discount the
result *requires* leaves the reader to judge whether it is plausible.

It is a real question. A co-installed PV+battery system shares one scaffolding erection,
one site visit and one DNO notification with the PV work, and the DESNZ bands cannot
separate retrofit from co-install. But the shared component is small — the same table
gives PV a fixed install cost of ~£98 — so 19% is a demanding threshold, and it applies
only to the single best cell. Averaged over the 18 Agile cells at 1 kWh the requirement is
25%; across flat-tariff cells it is 66–81%.

---

## 4. Capex sensitivity: does the specification change the answer?

*How derived:* the same 2,160-curve dispatch cache reassembled at five capex
specifications (~25 s each, zero solves — capex never enters dispatch). Counts are cells
where the battery is worth adding, sizes 1–10 kWh.

| Specification | Basis | no-repl. | run-to-fade | Best cell (no-repl.) |
|---|---|---:|---:|---:|
| **Band-observed** (adopted) | published median per band | **0/54** | **0/54** | −£253 |
| Linear £890/kWh | pooled median, original spec | 24/54 | 0/54 | **+£157** |
| £2,959 + £475/kWh | OLS, low-F corner | 0/54 | 0/54 | −£1,419 |
| £4,584 + £373/kWh | OLS, central | 0/54 | 0/54 | −£2,453 |
| £4,897 + £312/kWh | OLS, high-F corner | 0/54 | 0/54 | −£2,156 |

**Four of the five agree completely: 0/54 under both policies.** The single disagreement
is the original linear specification under the optimistic policy, and it is fully
explained rather than merely noted:

> £890/kWh is the size-pooled median of an installation population dominated by 6–11 kWh
> systems. Applied to a 1 kWh battery it charges £890 for something the same table prices
> at £1,300 — a **32% underprice**. All 24 of those cells sit at 1 kWh, and all disappear
> when the size is charged its own band's price.

So the conclusion is robust to the capex specification, and where a specification
disagrees, the reason is a known pricing error rather than genuine model uncertainty. This
matters because it makes the headline independent of the weakly identified parameter: the
fixed term F moves by 65% across three defensible readings of the same table, and the
band-observed specification does not use it at all.

---

## 5. What is *not* identified: optimal battery size

*How derived:* best size in each cell conditional on buying a battery, no-replacement
policy, sizes 1–10 kWh.

| Specification | flat | E7 | Agile |
|---|---|---|---|
| Band-observed | 1 kWh ×18 | 1 kWh ×18 | 1 kWh ×18 |
| Linear £890/kWh | 1 kWh ×18 | 1 kWh ×18 | 1 kWh ×18 |
| £2,959 + £475/kWh | 1 kWh ×18 | 5 kWh ×17, 10 ×1 | 5 kWh ×18 |
| £4,584 + £373/kWh | 1 kWh ×18 | 10 kWh ×18 | 5 kWh ×15, 10 ×3 |
| £4,897 + £312/kWh | 1 ×11, 2.5 ×7 | 10 kWh ×18 | 10 kWh ×18 |

The recommended size moves by a factor of **5–10** across specifications that all fit the
same DESNZ table. **This study does not identify the optimal size, and should not report
one.**

The reason is structural, not a data limitation. Optimal size is set by the *marginal*
price d(capex)/dQ, whereas the DESNZ bands publish only *average* prices. Under any
constant £/kWh, average equals marginal, marginal benefit is below it everywhere, and the
answer is always the smallest available size — an artefact of the assumption, not a
finding. Only a specification with a fixed term produces an interior optimum, and the size
of that optimum is set by the fixed term, which is the least well-measured quantity in the
analysis.

Unconstrained (allowing zero), the optimum is **zero battery in all 54 cells** under the
band-observed specification — which is §1's headline restated.

---

## 6. PV alone

*How derived:* the zero-battery reference row of each cell, `sweep_scenarios_v2_band.csv`,
PV at £1,109/kWp.

| | NPV > 0 | mean NPV | best NPV |
|---|---:|---:|---:|
| PV-only | 52/54 | £3,297 | £7,857 |

Best system in the study: **Plymouth, flat tariff, 6 kWp, PV-only, £7,857**. It is
PV-only under every capex specification tested.

Note that the best PV system is on a *flat* tariff while the best battery cell is on
Agile — the two technologies are favoured by opposite tariff structures, which is worth a
figure in Spec 07.

---

## 7. Day-ahead myopia effect

`solve_year` optimises one day at a time. SOC carries across midnight but no terminal-SOC
value is applied, so the solver never holds energy overnight for the next morning. This
quantifies what that costs.

*How derived:* [`scripts/test_myopia.py`](../scripts/test_myopia.py) re-solves one year
three ways — the production daily MILP, a **single full-year MILP** over all 17,520
half-hours (perfect foresight across every midnight), and the self-consumption heuristic.
The full-year solve is run with its terminal SOC left free, matching the daily method's
unconstrained 31 December; constraining it makes the "relaxation" score *worse* than the
daily solve and is not a like-for-like control. ~4–9 min per full-year solve.

**Cell: Inverness, Agile, 5 kWp, 10 kWh at 0.5C, 5 p/kWh wear penalty** — a real grid cell
at a size representative of what households actually buy. Savings are against the PV-only
counterfactual in the same cell.

| Tariff | Daily MILP | Full-year MILP | Self-consumption | **Myopia loss** |
|---|---:|---:|---:|---:|
| flat | £145.46 | £176.30 | £176.30 | **£30.84/yr** |
| E7 | £436.37 | £437.62 | £234.76 | **£1.25/yr** |
| Agile | £360.86 | £382.61 | £237.17 | **£21.76/yr** |

The daily-MILP Agile figure, £360.86/yr, reproduces this cell's year-1 marginal battery
saving in `_band_c890.csv` exactly, which ties the measurement to the sweep it is auditing.
Glasgow / 4 kWp / 10 kWh replicates the pattern at a second location (£27.33 flat, £0.46
E7, £11.34 Agile), so the Agile figure is cell-specific to roughly a factor of two.

**Implications for the results above: none that change a verdict.**

- **On flat, the daily MILP is not what the sweep reports.** The full-year optimum turns
  out to be *exactly* the self-consumption schedule — identical import, export and
  throughput to 0.1 kWh, at both locations tested. On a tariff with no arbitrage the
  optimal annual policy simply *is* greedy self-consumption, which runs as one continuous
  half-hourly pass with no daily decomposition to be myopic about. Since §1 optimises the
  controller within each cell, and self-consumption wins **90/90** flat configurations
  under no-replacement, the flat figures in §2 do not carry the £31 shortfall. This is a
  coincidence of the flat tariff, **not** a myopia-free policy — none is implemented.
  Under run-to-fade the MILP does win **8/90** flat configurations, all at 5–10 kWh with a
  non-zero wear penalty where it defers replacement by cycling less (margins £14–271);
  those eight rows do understate the battery.
- **On E7 it is nil** (£1.25/yr) — the arbitrage that matters is intra-day, so a daily
  horizon already sees it.
- **On Agile it is real but small against the shortfall.** Crediting the full £21.76/yr to
  this cell at its own delivered multiple:

  | Policy | Break-even now | With myopia credited | Observed |
  |---|---:|---:|---:|
  | no-replacement | £569/kWh (0.64) | £597/kWh (**0.67**) | £890/kWh |
  | run-to-fade | £352/kWh (0.40) | £373/kWh (**0.42**) | £890/kWh |

  A £21/yr uplift buys ~£21–28/kWh of break-even price against a shortfall of £320–540/kWh.
  The same credit applied to the study's closest approach (0.81, §2.1) leaves it below 1.00.

So the effect is real, largest exactly where the tariff gives the MILP least to do, and an
order of magnitude too small to overturn the 0/54 headline. Report it as an understatement
of MILP dispatch quality on Agile, not as a threat to the conclusions. Removing it needs a
longer or overlapping horizon at materially higher compute cost.

---

## 8. Caveats

1. **Applying a band median at 1 kWh still flatters the battery.** The 1–5.99 kWh band has
   a sample-mean size of ~3.5 kWh and £/kWh falls steeply with size *within* each band, so
   a real 1 kWh system almost certainly costs more than £1,300/kWh. The closest-approach
   figure of 0.81 is therefore optimistic.
2. **Bands may not separate on size alone.** Retrofit and co-installed systems are pooled,
   and retrofit is dearer. This is the main confound behind the required-discount figure
   in §3 and cannot be resolved from published data.
3. **Battery prices are nominal; PV prices are inflation-adjusted.** The DESNZ battery
   table publishes no real-terms column, so 2024/25 and 2025/26 are pooled undeflated.
   Deflating would lower the band prices by a few percent, marginally favouring the
   battery.
4. **No real battery price decline is modelled.** Electricity prices escalate at 2%/yr
   real while battery capex is held flat in real terms to 2046. Both are forecasts and
   only the one unfavourable to the battery is applied. A replacement-price-decline
   sensitivity is specified but not yet run; it affects run-to-fade only.
5. **Day-ahead myopia.** SOC carries across midnight but no terminal-SOC value is applied,
   so the MILP does not position the battery for the following day. **Now quantified in
   §7**: at Inverness / 5 kWp / 10 kWh, £31/yr on flat (which the reported figures largely
   escape, since self-consumption wins there), £1.25 on E7, £22/yr on Agile — worth
   ~£21–28/kWh of break-even price against a £320–540/kWh shortfall. It is a property of
   the experiment, not of the pricing, and applies equally to every specification above.
6. **Peak-demand benefits are not monetised.** Nothing in these tables credits the battery
   for peak reduction, because no domestic UK tariff currently pays for it.
   `sweep_peak_events_v2.csv` carries those metrics for Spec 07 and they are unaffected by
   capex.

---

## 9. Provenance

All tables above come from the same 2,160-curve dispatch cache; capex never enters
dispatch, so each specification is a ~25 s reassembly with zero solves.

| File | Capex | Role |
|---|---|---|
| `results/sweep_scenarios_v2_band.csv` | band-observed | **primary** |
| `results/sweep_scenarios_v2.csv` | linear £890/kWh | conventional sensitivity, immutable |
| `results/sweep_scenarios_v2_{lowF,central,highF}.csv` | fixed + variable | decomposed sensitivity |
| `results/sweep_peak_events_v2.csv` | — | serves all; identical across specifications |

The primary table is built by [`scripts/assemble_band_capex.py`](../scripts/assemble_band_capex.py),
which merges two constant-price assemblies and refuses to write unless the merge is
verified: identical grids, no dispatch column perturbed by capex, each size drawn from
exactly one source with no overlap or omission, and every row's capex reconciling to
`band price × size`. The £890 assembly reproduces the immutable `sweep_scenarios_v2.csv`
sha256-identical. Commands and hashes are in
[`spec06_run_manifest.md`](spec06_run_manifest.md) §5a.

### Superseded

- **The `F*` table** (maximum supportable fixed install cost) was withdrawn. It reported
  break-even in £ *conditional on an assumed marginal price*, which is not comparable to
  any observed quantity. §2 reports break-even in £/kWh instead, which is directly
  comparable to the published price.
- **A PV fit of F = £766, c = £1,582** came from a superseded table mixing nominal prices
  with a 10–50 kW band. Withdrawn; PV is £1,109/kWp linear.
- **The claim that the linear and fixed-plus-variable models "agree on the verdict"** was
  based on run-to-fade rows only and did not hold under no-replacement. Replaced by §4,
  which reports the disagreement and its cause.
