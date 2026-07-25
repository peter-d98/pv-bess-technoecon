"""Unit tests for the grid-import peak metrics (Spec 06 §4.6).

Oracles are hand-derived from a small synthetic schedule.
"""

import numpy as np
import pandas as pd
import pytest

from src.peaks import PEAK_THRESHOLDS_KW, peak_metrics, peak_reduction_kw


def _schedule(imports, **columns):
    n = len(imports)
    index = pd.date_range("2023-01-01 00:00", periods=n, freq="30min")
    data = {"p_import_kw": np.asarray(imports, dtype=float)}
    for name, values in columns.items():
        data[name] = np.asarray(values, dtype=float)
    return pd.DataFrame(data, index=index)


def test_p1_summary_matches_hand_derived_schedule():
    """Max/timestamp, percentiles, and exceedance counts on a 10-slot schedule."""
    imports = [0, 0, 0, 0, 1, 2, 3, 6, 8, 10]
    metrics = peak_metrics(_schedule(imports))

    assert metrics["peak_import_kw"] == pytest.approx(10.0)
    assert metrics["peak_import_time"] == "2023-01-01T04:30:00"
    # np.percentile linear interpolation on the sorted series.
    assert metrics["p95_import_kw"] == pytest.approx(9.1)
    assert metrics["p99_import_kw"] == pytest.approx(9.82)
    assert metrics["count_above_2kw"] == 4      # 3, 6, 8, 10
    assert metrics["count_above_3kw"] == 3      # 6, 8, 10
    assert metrics["count_above_5kw"] == 3      # 6, 8, 10
    assert metrics["count_above_7kw"] == 2      # 8, 10
    assert PEAK_THRESHOLDS_KW == (2.0, 3.0, 5.0, 7.0)


def test_p2_top_events_ranked_with_context_and_stable_ties():
    imports = [5, 5, 1]
    metrics = peak_metrics(
        _schedule(
            imports,
            demand_kw=[6, 5, 1],
            pv_kw=[0, 0, 2],
            p_charge_kw=[1, 0, 0],
            p_discharge_kw=[0, 0, 0.5],
        ),
        top_n=2,
    )
    events = metrics["top_events"]

    assert [e["rank"] for e in events] == [1, 2]
    # Ties broken chronologically, so the earlier 5 kW half-hour ranks first.
    assert [e["time"] for e in events] == [
        "2023-01-01T00:00:00",
        "2023-01-01T00:30:00",
    ]
    assert events[0]["grid_import_kw"] == pytest.approx(5.0)
    assert events[0]["demand_kw"] == pytest.approx(6.0)
    assert events[0]["p_charge_kw"] == pytest.approx(1.0)
    assert len(events) == 2


def test_p3_uses_actual_import_not_gross_deficit():
    """Grid charging makes actual import exceed max(0, demand - pv - discharge)."""
    schedule = _schedule(
        [4.0, 0.5],
        demand_kw=[1.0, 1.0],
        pv_kw=[0.0, 0.5],
        p_charge_kw=[3.0, 0.0],
        p_discharge_kw=[0.0, 0.0],
    )
    gross_deficit = np.maximum(
        0.0,
        schedule["demand_kw"] - schedule["pv_kw"] - schedule["p_discharge_kw"],
    ).max()

    metrics = peak_metrics(schedule)

    assert gross_deficit == pytest.approx(1.0)
    assert metrics["peak_import_kw"] == pytest.approx(4.0)


def test_p4_peak_reduction_is_signed_and_unclipped():
    no_battery = {"peak_import_kw": 5.0}
    lower = {"peak_import_kw": 3.0}
    higher = {"peak_import_kw": 8.0}

    assert peak_reduction_kw(no_battery, lower) == pytest.approx(2.0)
    assert peak_reduction_kw(no_battery, higher) == pytest.approx(-3.0)
    assert np.isnan(peak_reduction_kw(no_battery, None))


def test_p5_requires_import_column_and_rows():
    with pytest.raises(ValueError, match="p_import_kw"):
        peak_metrics(pd.DataFrame({"demand_kw": [1.0]}))
    with pytest.raises(ValueError, match="non-empty"):
        peak_metrics(pd.DataFrame({"p_import_kw": []}))
