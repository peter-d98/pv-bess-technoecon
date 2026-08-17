# Spec 06 — Full Experiment Run Manifest

Required by [spec-06-sweep-economic-extensions.md](specs/spec-06-sweep-economic-extensions.md) §5.
Reconstructed 2026-07-28 from the artefacts, launcher scripts, and run records. `results/`
is gitignored, so this manifest (in `docs/`, which is tracked) is the preserved audit
record. Verification results are in
[spec-06-verification.md](specs/spec-06-verification.md).

> **Reconstructed, not contemporaneous.** This was written after the experiment finished
> rather than emitted by the runner. Everything in §1–§5 is evidenced from artefacts on
> disk; §6 lists what could not be recovered and needs confirmation. Future runs should
> emit the manifest at launch.

## 1. Code identity

| Item | Value |
|---|---|
| Commit at time of writing | `5ba0e5984214861438a06f8f2cdc8ce20dc0b502` (branch `main`) |
| Last commit touching `src/` or `scripts/` | `9f99e32` — 2026-07-25 13:11 ("expanded sweep") |
| Modelling code frozen before first solve? | **Yes** — first curve written 2026-07-26 01:30, ~12 h after the last science-code commit |
| Working tree during the run | clean for `src/` and `scripts/` |

The launcher scripts changed *during* the campaign (see §5); the modelling code did not.

## 2. Environment

Verified on the machine that ran partitions 0–3 and performed final assembly:

| Component | Version |
|---|---|
| Python | 3.10.12 |
| numpy | 2.2.6 |
| scipy | 1.15.3 (supplies HiGHS via `SCIPY`) |
| pandas | 2.3.3 |
| cvxpy | 1.7.5 |
| CVXPY installed solvers | `CLARABEL`, `OSQP`, `SCIPY`, `SCS` |
| MILP solver used | **`SCIPY` (HiGHS)** — pinned explicitly via `--solver SCIPY` on every job |

`requirements-lock.txt` is committed. Solver pinning closes the §9 open item: with
`--solver SCIPY` passed by both launchers, a machine that happened to have SCIP or CBC
installed could not silently select a different solver.

**Not verified:** library versions on the machines that ran partitions 4–11 (see §6).

## 3. Partitioning

378 jobs — 324 MILP (one per location × PV × tariff × penalty) and 54 rules (one per
location × PV × tariff) — split by `job_index mod 12`, evaluated in a fixed nested order
(location → PV size → tariff → penalty). The split is disjoint and complete; this is
asserted in `tests/test_spec06_artefacts.py::test_v14_partition_is_disjoint_and_complete`.

| Index | Jobs | MILP | Rules | Expected curves | Host class |
|---:|---:|---:|---:|---:|---|
| 0 | 32 | 28 | 4 | 180 | 6-core laptop (WSL2/Linux) |
| 1 | 32 | 27 | 5 | 185 | 6-core laptop (WSL2/Linux) |
| 2 | 32 | 28 | 4 | 180 | 6-core laptop (WSL2/Linux) |
| 3 | 32 | 27 | 5 | 185 | 6-core laptop (WSL2/Linux) |
| 4 | 32 | 28 | 4 | 180 | 14-core Windows desktop |
| 5 | 32 | 27 | 5 | 185 | 14-core Windows desktop |
| 6 | 31 | 26 | 5 | 180 | 14-core Windows desktop |
| 7 | 31 | 27 | 4 | 175 | 14-core Windows desktop |
| 8 | 31 | 26 | 5 | 180 | 14-core Windows desktop |
| 9 | 31 | 27 | 4 | 175 | 14-core Windows desktop |
| 10 | 31 | 26 | 5 | 180 | 14-core Windows desktop |
| 11 | 31 | 27 | 4 | 175 | 14-core Windows desktop |
| **Total** | **378** | **324** | **54** | **2160** | |

Curves per job: 5 per MILP job (one per positive battery size); 10 per rules job
(5 sizes × 2 controllers).

## 4. Commands

Linux/WSL partitions (0–3), via [`launch.sh`](../launch.sh):

```bash
./launch.sh -m <INDEX> -p 6        # 6 concurrent jobs = one per physical core
```

Windows partitions (4–11), via [`launch.ps1`](../launch.ps1):

```powershell
powershell -ExecutionPolicy Bypass -File .\launch.ps1 -MachineIndex <INDEX> -MaxProc 12
```

Both launchers emit, per job:

```
python scripts/run_sweep.py --locations <LOC> --pv-sizes <PV> --tariffs <TARIFF> \
    --solver SCIPY --cache-dir results/cache/sweep_v2 \
    --out results/parts/part_<TAG>.csv --peak-out results/parts/peaks_<TAG>.csv \
    { --controllers milp --deg-scenarios <PENALTY>:6000
    | --controllers self_consumption self_consumption_tou }
```

Final assembly (zero solves, reads every curve from the merged cache):

```bash
python scripts/run_sweep.py          # all axes default to the full grid
```

## 5. Execution record and completion

| Event | Timestamp |
|---|---|
| First curve written | 2026-07-26 01:30 |
| Last curve written | 2026-07-27 14:05 |
| Assembled outputs written | 2026-07-27 15:13 |

**Completion status: COMPLETE.** The strongest evidence is not a per-machine log but the
cache itself: the 2160 files on disk are an **exact set match** against the 2160 filenames
the confirmed axes imply — 0 missing, 0 unexpected. Since curve filenames are a
deterministic function of each job's parameters, an incomplete partition is not
representable in a complete set.

| Artefact | Count / hash |
|---|---|
| `results/cache/sweep_v2/*.pkl` | 2160 |
| `results/sweep_scenarios_v2.csv` | 4374 rows — sha256 `5340b655aa8ca5df5b8127c6…` |
| `results/sweep_peak_events_v2.csv` | 110700 rows — sha256 `2dff67274eb730aa92584147…` |
| Per-job outputs in `results/parts/` | 128 jobs (indices 0–3 only) |

Both assembled tables were independently shown **byte-reproducible**: re-running assembly
against the merged cache regenerated them sha256-identical, with no new cache writes.

### Incidents

1. **2026-07-25 — first launch attempt failed (superseded).** Two Windows machines were
   started at `-MaxProc 12` with no launch stagger. One aborted with `DLL load failed while
   importing _flapack: The paging file is too small for this operation to complete` after
   16 of 32 jobs; the other stopped silently around job 14. Approximately 52 partition-0
   curves were written before the failures. These were later superseded by identical-key
   curves from the successful campaign and are indistinguishable in the merged cache
   (same deterministic filenames, same frozen code, same solver).
2. **Launcher hardening applied before the successful campaign.** `launch.ps1` gained a
   lower default `-MaxProc`, a `-LaunchDelaySec` stagger, retry-with-backoff around
   `Start-Process`, and `@()` array wrapping to fix a `Set-StrictMode` failure on
   `.Count` when exactly one error log was non-empty.
3. **Partition 8 — launcher console stall.** The launcher blocked on a `Write-Host` after
   launching 28 of 31 jobs because the Windows console had entered QuickEdit selection
   mode. All 28 launched jobs completed normally; the remaining 3 launched once the
   selection was cleared. No computation was affected.
4. **Partitions 4 and 5 — inflated interim curve counts.** Leftover curves from incident 1
   were present on those machines, so in-progress counts exceeded each partition's
   theoretical maximum. Resolved by deduplication on merge (identical filenames).
5. **Solver-noise deviations** are quantified in the verification document, not repeated
   here.

## 5a. Capex re-assemblies (2026-07-29)

No solves and no cache writes: each is a re-read of the same 2160 curves at a different
price, ~25 s. Every capex specification in `docs/results_summary.md` traces to one of
these commands.

```bash
# fixed-plus-variable sensitivity, (F, c) in {(2959,475), (4584,373), (4897,312)}
python scripts/run_sweep.py --pv-cost-per-kwp 1109 --pv-capex-fixed 0 \
    --battery-cost-per-kwh <c> --battery-capex-fixed <F> \
    --out results/sweep_scenarios_v2_<name>.csv --peak-out <tmp> --overwrite

# the two band prices, then the band assembly
python scripts/run_sweep.py --pv-cost-per-kwp 1109 --pv-capex-fixed 0 \
    --battery-cost-per-kwh 1300 --battery-capex-fixed 0 \
    --out results/_band_c1300.csv --peak-out <tmp> --overwrite
python scripts/run_sweep.py --pv-cost-per-kwp 1109 --pv-capex-fixed 0 \
    --battery-cost-per-kwh 890 --battery-capex-fixed 0 \
    --out results/_band_c890.csv --peak-out <tmp> --overwrite
python scripts/assemble_band_capex.py

# discount-rate sensitivity (results_summary.md §2.2): r in {0.035, 0.07} x c in {1300, 890}.
# Not persisted under results/ — regenerate on demand, ~25 s each, zero solves.
python scripts/run_sweep.py --pv-cost-per-kwp 1109 --pv-capex-fixed 0 \
    --battery-cost-per-kwh <c> --battery-capex-fixed 0 --discount-rate <r> \
    --out <tmp>.csv --peak-out <tmp>.csv --overwrite
```

| Artefact | Count / hash |
|---|---|
| `results/_band_c1300.csv` | 4374 rows — sha256 `78f9c306154cf111b7a1d8da…` |
| `results/_band_c890.csv` | 4374 rows — sha256 `5340b655aa8ca5df5b8127c6…` |
| `results/sweep_scenarios_v2_band.csv` | 3510 rows — sha256 `45b3be0a6506b547f8cb8ce7…` |

Two checks stand behind the primary table:

1. **Regression.** `_band_c890.csv` reproduces the immutable `sweep_scenarios_v2.csv`
   **sha256-identical** (`5340b655…`, §5) — the re-assembly path is behaviour-preserving
   at the original parameters.
2. **Merge integrity.** `assemble_band_capex.py` asserts, and writes nothing otherwise:
   the two runs share an identical key set; every dispatch-derived column is bit-identical
   across them (max abs difference 0.0, confirming capex never enters dispatch); each
   battery size is drawn from exactly one source (2592 rows at £1,300/kWh, 918 at
   £890/kWh, zero key overlap, union equal to the expected 3510); and every row's implied
   battery capex reconciles to `band price × size` to within 1e-6. 0.5 kWh is dropped as
   outside the published bands. The assertions were confirmed to bite by perturbing one
   dispatch value in a source and observing the script refuse to write.

## 6. Gaps requiring confirmation

Recoverable only from the operator or the university machines:

1. **Host identity per partition** — which physical desktop ran indices 4–11. §3 records
   the host *class* only.
2. **Library versions on the Windows machines.** §2 is verified for the Linux machine that
   ran indices 0–3 and did final assembly. The Windows desktops used a separately created
   `.venv`; if `requirements-lock.txt` was installed there, versions match by construction,
   but this was not captured at run time. The solver *choice* was pinned; the HiGHS build
   shipped with each machine's `scipy` was not independently recorded.
3. **Per-job logs and outputs for indices 4–11** were not merged onto the assembly machine
   — only the caches were. `results/parts/` and `results/logs/` therefore hold indices 0–3
   only. If the Windows machines are still accessible, copying their `parts/` and `logs/`
   directories would complete the audit trail.
4. **Whether any partition was re-launched** after a mid-run interruption. Re-launches are
   harmless (completed jobs fast-fail on the existing-output guard, incomplete jobs resume
   from cache) but are not recorded.
