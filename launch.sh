#!/usr/bin/env bash
# Bash port of launch.ps1 for running one machine's partition on Linux/WSL.
set -euo pipefail

usage() {
    echo "Usage: $0 -m MACHINE_INDEX [-p MAX_PROC] [-d LAUNCH_DELAY_SEC]"
    echo "  -m  machine index, 0-11 (required)"
    echo "  -p  max concurrent jobs (default 4)"
    echo "  -d  seconds to wait between launching jobs (default 5)"
    exit 1
}

MAX_PROC=4
DELAY=5
MACHINE_INDEX=""

while getopts "m:p:d:" opt; do
    case $opt in
        m) MACHINE_INDEX=$OPTARG ;;
        p) MAX_PROC=$OPTARG ;;
        d) DELAY=$OPTARG ;;
        *) usage ;;
    esac
done

[[ -z "$MACHINE_INDEX" ]] && usage
if (( MACHINE_INDEX < 0 || MACHINE_INDEX > 11 )); then
    echo "MACHINE_INDEX must be 0-11" >&2
    exit 1
fi

echo "START $(date -Iseconds)"
echo "MachineIndex=$MACHINE_INDEX MaxProc=$MAX_PROC"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "Python not found at $PY. Create the venv first: python3 -m venv .venv" >&2
    exit 1
fi

MACHINE=$(printf "machine%02d" $((MACHINE_INDEX + 1)))

CACHE_DIR="$ROOT/results/cache/sweep_v2"
LOGS_DIR="$ROOT/results/logs"
PARTS_DIR="$ROOT/results/parts"
mkdir -p "$CACHE_DIR" "$LOGS_DIR" "$PARTS_DIR"

locs=(inverness manchester plymouth)
pvs=(1 2 3 4 5 6)
tars=(flat e7 agile)
pens=(0 0.01 0.03 0.05 0.07 0.09)

# 324 MILP jobs + 54 rules jobs, deterministic ordering (matches launch.ps1).
all=()
for l in "${locs[@]}"; do
    for pv in "${pvs[@]}"; do
        for t in "${tars[@]}"; do
            for p in "${pens[@]}"; do
                all+=("milp:${l}:${pv}:${t}:${p}")
            done
            all+=("rules:${l}:${pv}:${t}:na")
        done
    done
done

mine=()
for i in "${!all[@]}"; do
    if (( i % 12 == MACHINE_INDEX )); then
        mine+=("${all[$i]}")
    fi
done

echo "total jobs ${#all[@]}; this machine ${#mine[@]}"

launched=0

for job in "${mine[@]}"; do
    while (( $(jobs -rp | wc -l) >= MAX_PROC )); do
        sleep 5
    done

    IFS=: read -r kind loc pv tar pen <<< "$job"
    tag="${kind}_${loc}_pv${pv}_${tar}_${pen}"

    if [[ "$kind" == "milp" ]]; then
        ctrl=(--controllers milp --deg-scenarios "${pen}:6000")
    else
        ctrl=(--controllers self_consumption self_consumption_tou)
    fi

    "$PY" scripts/run_sweep.py \
        --locations "$loc" \
        --pv-sizes "$pv" \
        --tariffs "$tar" \
        --solver SCIPY \
        --cache-dir "$CACHE_DIR" \
        --out "$PARTS_DIR/part_${tag}.csv" \
        --peak-out "$PARTS_DIR/peaks_${tag}.csv" \
        "${ctrl[@]}" \
        > "$LOGS_DIR/${tag}.out.txt" 2> "$LOGS_DIR/${tag}.err.txt" &

    launched=$((launched + 1))
    echo "launched ${launched}/${#mine[@]}: ${tag} (pid $!)"
    sleep "$DELAY"
done

echo "All jobs launched. Waiting for completion..."
wait

errs=$(find "$LOGS_DIR" -name "*.err.txt" -size +0c)
curve_count=$(find "$CACHE_DIR" -name "*.pkl" | wc -l)

echo "DONE $MACHINE - curves: $curve_count"
if [[ -n "$errs" ]]; then
    echo "Non-empty error logs detected:"
    echo "$errs"
    exit 1
fi

echo "No non-empty error logs found."
