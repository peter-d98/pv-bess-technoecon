"""Grid-import peak metrics for solved dispatch schedules (Spec 06 §4.6).

Pure post-processing: these helpers read an already-solved schedule and never
influence the MILP objective, its constraints, or the number of solves. The
quantity of interest is the demand actually served by the grid, i.e. the
schedule's ``p_import_kw`` column — *not* ``max(0, demand - pv - discharge)``
(which ignores battery charging) and not unserved load (the model always has
grid access).

Peak *analysis* is deferred; this module exists so the expensive v2 dispatch
runs retain enough compact evidence that later peak questions do not require
re-solving the sweep.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Study-defined exceedance thresholds (kW). These are analytical reference
# levels for the dissertation, not DNO or standards-derived limits.
PEAK_THRESHOLDS_KW: tuple[float, ...] = (2.0, 3.0, 5.0, 7.0)

TOP_EVENTS = 50

_EVENT_COLUMNS = (
    "demand_kw",
    "pv_kw",
    "p_charge_kw",
    "p_discharge_kw",
)


def threshold_key(threshold_kw: float) -> str:
    """Column name holding the exceedance count for *threshold_kw*."""
    return f"count_above_{threshold_kw:g}kw"


def _stamp(index: pd.Index, position: int) -> str:
    label = index[position]
    return label.isoformat() if isinstance(label, pd.Timestamp) else str(label)


def peak_metrics(
    schedule: pd.DataFrame,
    thresholds_kw: tuple[float, ...] = PEAK_THRESHOLDS_KW,
    top_n: int = TOP_EVENTS,
) -> dict:
    """Summarise grid-import peaks for one solved annual schedule.

    Returns the maximum import and its first timestamp, the 95th/99th
    percentiles, the number of half-hours above each threshold, and the
    ``top_n`` highest-import half-hours (with the demand, PV, charge, and
    discharge at each, so time-of-day and driver analysis remain possible).
    """
    if "p_import_kw" not in schedule:
        raise ValueError("schedule must contain a 'p_import_kw' column.")

    imports = schedule["p_import_kw"].to_numpy(dtype=float)
    if imports.size == 0:
        raise ValueError("schedule must be non-empty.")

    peak_position = int(np.argmax(imports))
    metrics: dict = {
        "peak_import_kw": float(imports[peak_position]),
        "peak_import_time": _stamp(schedule.index, peak_position),
        "p95_import_kw": float(np.percentile(imports, 95)),
        "p99_import_kw": float(np.percentile(imports, 99)),
    }
    for threshold in thresholds_kw:
        metrics[threshold_key(threshold)] = int((imports > threshold).sum())

    # Highest-import half-hours, ties broken by chronological order.
    order = np.argsort(-imports, kind="stable")[: min(top_n, imports.size)]
    events = []
    for rank, position in enumerate(order, start=1):
        event = {
            "rank": rank,
            "time": _stamp(schedule.index, int(position)),
            "grid_import_kw": float(imports[position]),
        }
        for column in _EVENT_COLUMNS:
            if column in schedule:
                event[column] = float(schedule[column].to_numpy()[position])
        events.append(event)
    metrics["top_events"] = events
    return metrics


def peak_reduction_kw(no_battery: dict | None, with_battery: dict | None) -> float:
    """Peak-import reduction versus the matching no-battery case.

    A negative value is a genuine result (the battery raised the peak, e.g. by
    grid-charging) and is never clipped.
    """
    if not no_battery or not with_battery:
        return float("nan")
    return float(no_battery["peak_import_kw"] - with_battery["peak_import_kw"])
