"""Неизвестные оценки не меняют знаменатель КМ принятого набора."""
from __future__ import annotations

import itertools

import pandas as pd
import pytest

from km_dynamics import summarize_units
from main import main
from measurement_fixture import reviewed_metric
from test_scored_output_contract import _assessment, _flat_frame, _metric


def _case(ones=720, zeros=80, missing=200, *, scale=(0, 1), weights=None):
    metric = _metric()
    metric["baseline"].update(value=max(scale), recomputed_value=max(scale), reported_value=max(scale),
                              scale="ratio" if scale == (0, 1) else "raw")
    if weights is not None:
        metric["aggregation"] = {"method": "frequency_weighted_mean", "weight_column": "input_query_count"}
    metric = reviewed_metric(metric, score_values=list(scale), defect_threshold=max(scale))
    frame = _flat_frame(ones, zeros + missing).assign(definition_id=metric["definition_id"])
    frame["main_metric"] = [max(scale)] * ones + [min(scale)] * zeros + [float("nan")] * missing
    if weights is not None:
        frame["input_query_count"] = weights
    assessment = _assessment(len(frame), ones + zeros)
    assessment["definition_id"] = metric["definition_id"]
    return metric, frame, assessment


def test_nonrandom_refusals_cannot_turn_red_population_green():
    metric, frame, assessment = _case()
    result = main(metric, 1.0, frame, assessment)
    actual = result["all_results"]
    assert actual["status"] == "not_computable" and actual["color"] == "gray"
    assert actual["km_monitoring"] is None and actual["km_delta"] is None
    assert actual["interval"] is None
    assert actual["provenance"]["observed_mean"] == pytest.approx(0.9)
    assert actual["provenance"]["completion_bounds"] == {
        "lower": pytest.approx(0.72), "upper": pytest.approx(0.92), "scope": "received_units",
    }
    assert "не доверительный интервал" in result["test_description"]
    assert "0.72" in result["test_description"] and "0.92" in result["test_description"]
    frame["main_metric"] = frame["main_metric"].fillna(0)
    assessment["scored_units"] = 1000
    oracle = main(metric, 1.0, frame, assessment)["all_results"]
    assert oracle["color"] == "red" and oracle["km_monitoring"] == pytest.approx(0.72)


def test_missing_weight_is_not_replaced_by_missing_unit_share():
    metric, frame, assessment = _case(99, 0, 1, weights=[1] * 99 + [9900])
    actual = main(metric, 1.0, frame, assessment)["all_results"]
    evidence = actual["provenance"]
    assert actual["status"] == "not_computable"
    assert evidence["refused_share"] == pytest.approx(0.01)
    assert evidence["refused_weight_share"] == pytest.approx(9900 / 9999)
    assert evidence["completion_bounds"]["lower"] == pytest.approx(99 / 9999)
    assert evidence["completion_bounds"]["upper"] == 1.0


@pytest.mark.parametrize("scale", [(0, 1), (0, 1, 2), (-2, 0, 3)])
def test_bounds_equal_extreme_completions_on_declared_scale(scale):
    weights = [1, 2, 3, 4]
    metric, frame, _ = _case(1, 1, 2, scale=scale, weights=weights)
    bounds = summarize_units(frame, metric)["provenance"]["completion_bounds"]
    possible = [(max(scale) + 2 * min(scale) + 3 * a + 4 * b) / 10
                for a, b in itertools.product(scale, repeat=2)]
    assert bounds["lower"] == pytest.approx(min(possible))
    assert bounds["upper"] == pytest.approx(max(possible))
    duplicated = pd.concat([frame, frame], ignore_index=True)
    # Повтор turns внутри dialogue не создаёт новые единицы и не меняет их вес.
    assert summarize_units(duplicated, metric)["provenance"]["completion_bounds"] == bounds
    frame["input_query_count"] *= 13
    assert summarize_units(frame, metric)["provenance"]["completion_bounds"] == bounds


def test_all_missing_still_exposes_full_domain_without_mean():
    metric, frame, assessment = _case(0, 0, 10, scale=(0, 1, 2))
    summary = summarize_units(frame, metric)["provenance"]
    assert summary["observed_mean"] is None
    assert summary["completion_bounds"] == {"lower": 0, "upper": 2, "scope": "received_units"}
    assessment.update(status="not_computable", reason="Ни одна единица не оценена")
    assert main(metric, 1.0, frame, assessment)["all_results"]["status"] == "not_computable"


def test_refused_weight_must_be_finite():
    metric, frame, _ = _case(1, 0, 1, weights=[1, float("inf")])
    with pytest.raises(ValueError, match="конечным"):
        summarize_units(frame, metric)
