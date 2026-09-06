"""Тест динамики КМ: контракт исполняется тем же кодом, что у ассесора.

Два свойства, которые обязаны выполняться на каждом запуске:
1. baseline, опубликованный адаптером, воспроизводится агрегацией по эталонной
   корзине (round-trip через общий laim_monitoring);
2. светофор считается только по сопоставимым числам: mismatch, not_computable
   и отказ ассесора дают серый, а не цвет.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from km_dynamics import km_dynamics_test, materialize_main_metric  # noqa: E402
from laim_monitoring import (  # noqa: E402
    MonitoringContractError,
    aggregate_main_metric,
    broadcast_scores,
    score_units,
    unitize,
    validate_monitoring_metric,
)
from main import main as node_main  # noqa: E402


def contract(
    *,
    method: str = "identity",
    sources: list[dict] | None = None,
    baseline: float = 0.8,
    recomputed: float | None = None,
    reconciliation: str = "match",
    reducer: str = "mean",
    missing_policy: str = "exclude_unit",
    majority_denominator: str | None = None,
    assessment_mode: str = "qa",
    status: str = "computed",
) -> dict:
    if sources is None:
        sources = [{
            "source_id": "source_1",
            "column_name": "итог_metric",
            "role": "final_score",
            "normalization": "numeric",
            "polarity": "direct",
        }]
    if status != "computed":
        return {
            "contract_version": "laim-monitoring-metric.v2",
            "umr_version": "laim-umr.v2",
            "status": status,
            "basket_id": "CI1",
            "assessment_mode": assessment_mode,
            "reason": "Пересчитанная КМ не воспроизводит значение validation report",
            "reason_code": "km_reconciliation_mismatch",
        }
    return {
        "contract_version": "laim-monitoring-metric.v2",
        "umr_version": "laim-umr.v2",
        "status": "computed",
        "basket_id": "CI1",
        "name": "Accuracy",
        "score_column": "main_metric",
        "assessment_mode": assessment_mode,
        "scoring": {
            "method": method,
            "sources": sources,
            "missing_policy": missing_policy,
            "majority_denominator": majority_denominator,
        },
        "aggregation": {
            "method": reducer,
            "weight_column": "input_query_count" if reducer == "frequency_weighted_mean" else None,
        },
        "baseline": {
            "value": baseline,
            "scale": "ratio",
            "value_source": "validation_report",
            "reported_value": baseline,
            "reported_scale": "ratio",
            "recomputed_value": baseline if recomputed is None else recomputed,
            "reconciliation": reconciliation,
        },
        "primary_validation": {
            "threshold": None,
            "comparator": None,
            "scale": "ratio",
            "verdict": None,
            "affects_monitoring": False,
        },
        "evidence": {},
    }


def scored(values: list[float | None], weights: list[int] | None = None) -> pd.DataFrame:
    size = len(values)
    return pd.DataFrame({
        "query_id": [f"q{i}" for i in range(size)],
        "input_query": [f"вопрос {i}" for i in range(size)],
        "output_answer": [f"ответ {i}" for i in range(size)],
        "input_query_count": weights or [1] * size,
        "main_metric": values,
    })


# ----------------------------------------------------------------------------
# round-trip: контракт → score_units → aggregate воспроизводит baseline
# ----------------------------------------------------------------------------

def test_reference_basket_reproduces_baseline_through_shared_core():
    reference = pd.DataFrame({
        "query_id": ["q1", "q2", "q3", "q4"],
        "input_query": ["a", "b", "c", "d"],
        "output_answer": ["x", "y", "z", "w"],
        "полнота_metric": [1, 1, 0, 1],
        "точность_metric": [1, 0, 0, 1],
    })
    payload = contract(
        method="mean_criteria",
        sources=[
            {"source_id": "source_1", "column_name": "полнота_metric",
             "role": "criterion", "normalization": "numeric", "polarity": "direct"},
            {"source_id": "source_2", "column_name": "точность_metric",
             "role": "criterion", "normalization": "numeric", "polarity": "direct"},
        ],
        baseline=0.625,
    )
    units = unitize(reference, payload)
    scores = score_units(units, payload)
    assert scores.tolist() == pytest.approx([1.0, 0.5, 0.0, 1.0])
    frame = broadcast_scores(reference, units, scores)
    result = aggregate_main_metric(frame, payload)
    assert result["value"] == pytest.approx(payload["baseline"]["recomputed_value"])


def test_all_assessors_contract_is_accepted_without_rewrite():
    """Раньше km-dynamic не знал all_assessors и подменял метод на identity."""
    payload = contract(
        method="all_assessors",
        sources=[
            {"source_id": f"source_{i}", "column_name": f"асессор_{i}_metric",
             "role": "assessor_vote", "normalization": "numeric", "polarity": "direct"}
            for i in (1, 2)
        ],
        baseline=0.5,
    )
    validate_monitoring_metric(payload)
    result = aggregate_main_metric(scored([1.0, 0.0, 1.0, None]), payload)
    assert result["value"] == pytest.approx(2 / 3)
    assert result["excluded_units"] == 1


def test_frequency_weighted_aggregation_uses_input_query_count():
    payload = contract(reducer="frequency_weighted_mean", baseline=0.75)
    result = aggregate_main_metric(scored([1.0, 0.0], weights=[3, 1]), payload)
    assert result["value"] == pytest.approx(0.75)
    assert result["weight_sum"] == 4.0


def test_missing_policy_fail_rejects_blank_scores():
    payload = contract(missing_policy="fail")
    with pytest.raises(MonitoringContractError):
        aggregate_main_metric(scored([1.0, None]), payload)


# ----------------------------------------------------------------------------
# светофор
# ----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "values, color",
    [
        ([1, 1, 1, 1, 1, 1, 1, 1, 0, 0], "green"),   # 0.8 → 0.8: снижение 0
        ([1, 1, 1, 1, 1, 1, 0, 0, 0, 0], "red"),     # 0.8 → 0.6: снижение 25% — граница красного
    ],
)
def test_traffic_light_boundaries(values, color):
    result = km_dynamics_test(
        acc_auto=0.9, monitoring_metric=contract(baseline=0.8), scored_df=scored(values),
    )
    assert result["status"] == "computed"
    assert result["trafic_light"] == color


def test_traffic_light_yellow_zone():
    # 0.8 → 0.65: относительное снижение 0.1875 — жёлтая зона
    values = [1] * 13 + [0] * 7
    result = km_dynamics_test(
        acc_auto=0.9, monitoring_metric=contract(baseline=0.8), scored_df=scored(values),
    )
    assert result["trafic_light"] == "yellow"
    assert result["kluch_metric"]["Дельта КМ"] == pytest.approx(0.1875)
    assert result["kluch_metric"]["baseline_reconciliation"] == "match"


def test_growth_is_green():
    result = km_dynamics_test(
        acc_auto=0.9, monitoring_metric=contract(baseline=0.5), scored_df=scored([1, 1, 1, 0]),
    )
    assert result["trafic_light"] == "green"


def test_reconciliation_mismatch_is_gray_not_colored():
    payload = contract(baseline=0.82, recomputed=0.25, reconciliation="mismatch")
    result = km_dynamics_test(
        acc_auto=0.9, monitoring_metric=payload, scored_df=scored([1, 1, 1, 1]),
    )
    assert result["status"] == "not_computable"
    assert result["trafic_light"] == "gray"
    assert "reconciliation='mismatch'" in result["reason"]
    assert result["kluch_metric"]["КМ на мониторинге"] is None
    assert "РАСХОДИТСЯ" in result["html_plot"]


def test_not_computable_contract_is_gray_with_adapter_reason():
    payload = contract(status="not_computable")
    result = km_dynamics_test(acc_auto=None, monitoring_metric=payload, scored_df=scored([1]))
    assert result["trafic_light"] == "gray"
    assert "validation report" in result["reason"]


def test_assessor_refusal_is_gray():
    result = km_dynamics_test(
        acc_auto=None,
        monitoring_metric=contract(),
        scored_df=scored([1, 1]),
        assessment_result={"status": "not_computable", "reason": "судья недоступен"},
    )
    assert result["trafic_light"] == "gray"
    assert result["reason"] == "судья недоступен"


def test_scored_df_without_main_metric_is_gray():
    frame = scored([1, 1]).drop(columns=["main_metric"])
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=contract(), scored_df=frame)
    assert result["trafic_light"] == "gray"
    assert "main_metric" in result["reason"]


def test_node_entrypoint_exposes_baseline_and_monitoring():
    payload = contract(baseline=0.8)
    result = node_main(
        acc_auto=0.9, monitoring_metric=payload, scored_df=scored([1, 1, 1, 0]),
    )
    all_results = result["all_results"]
    assert all_results["test_name"] == "km_test"
    assert all_results["km_baseline"] == pytest.approx(0.8)
    assert all_results["km_monitoring"] == pytest.approx(0.75)
    assert all_results["color"] == "green"
    assert all_results["coverage"]["scored_units"] == 4
    assert all_results["km_formula"] == "mean(main_metric)"
    assert "<h2" in result["test_description"]


# ----------------------------------------------------------------------------
# materialize_main_metric (ветка kriteria-selector)
# ----------------------------------------------------------------------------

def test_materialize_keeps_existing_main_metric():
    frame = scored(["1", "0", None])
    result, reason = materialize_main_metric(frame, {"main_metric": "x"})
    assert reason is None
    assert result["main_metric"].tolist()[:2] == [1.0, 0.0]
    assert math.isnan(result["main_metric"].tolist()[2])


def test_materialize_majority_from_selector_columns():
    frame = pd.DataFrame({"a": [1, 1, 0], "b": [1, 0, 0], "c": [0, 1, 1]})
    result, reason = materialize_main_metric(
        frame,
        {"main_metric": "a", "other_metrics": ["b", "c"], "scoring_method": "majority",
         "majority_denominator": "declared", "missing_policy": "exclude_unit"},
    )
    assert reason is None
    assert result["main_metric"].tolist() == [1.0, 1.0, 0.0]


def test_formula_contract_is_evaluated_on_judge_labels():
    """Контракт с формулой отчёта: КМ считается по колонкам разметки, не по main_metric."""
    payload = contract(
        method="formula",
        sources=[
            {"source_id": "source_1", "name": "prediction", "column_name": "класс_output_answer",
             "role": "prediction", "normalization": "label", "polarity": "direct"},
            {"source_id": "source_2", "name": "target", "column_name": "класс_reference_answer",
             "role": "target", "normalization": "label", "polarity": "direct"},
        ],
        baseline=0.5833,
    )
    payload["formula"] = 'f1(prediction, target, "macro")'
    frame = scored([None] * 5)
    frame["класс_output_answer"] = ["a", "a", "b", "b", "a"]
    frame["класс_reference_answer"] = ["a", "b", "b", "b", "b"]
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=payload, scored_df=frame)
    assert result["status"] == "computed"
    assert result["kluch_metric"]["КМ на мониторинге"] == pytest.approx(7 / 12)
    assert result["kluch_metric"]["formula"] == 'f1(prediction, target, "macro")'
    assert result["trafic_light"] == "green"
    assert 'f1(prediction, target, &quot;macro&quot;)' in result["html_plot"]
