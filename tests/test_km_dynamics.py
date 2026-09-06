"""Тест динамики КМ: формулу считает общий пакет, здесь — сравнение и светофор."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from km_dynamics import km_dynamics_test as _km_dynamics_test  # noqa: E402
from laim_monitoring import MonitoringContractError  # noqa: E402
from main import main as node_main  # noqa: E402


def km_dynamics_test(**kwargs):
    kwargs.setdefault("min_units", 1)  # корзины в тестах маленькие
    return _km_dynamics_test(**kwargs)


def contract(*, formula="mean(итог)", inputs=None, baseline=0.8, status="computed") -> dict:
    if status != "computed":
        return {"contract_version": "laim-monitoring-metric.v3", "status": status,
                "basket_id": "CI1", "reason": "Пересчитанная КМ не воспроизводит значение validation report"}
    return {
        "contract_version": "laim-monitoring-metric.v3",
        "status": "computed",
        "basket_id": "CI1",
        "metric_name": "Accuracy",
        "assessment_mode": "qa",
        "formula": formula,
        "inputs": inputs or [{"name": "итог", "column": "итог_metric", "judged": True}],
        "baseline": {"value": baseline, "recomputed_value": baseline, "reconciliation": "match"},
    }


def scored(values: list, weights: list | None = None, column: str = "итог_metric") -> pd.DataFrame:
    """scored_data ассесора: разметка судьи в колонке контракта, построчный score в main_metric."""
    size = len(values)
    return pd.DataFrame({
        "query_id": [f"q{i}" for i in range(size)],
        "input_query": [f"вопрос {i}" for i in range(size)],
        "output_answer": [f"ответ {i}" for i in range(size)],
        "input_query_count": weights or [1] * size,
        column: values,
        "main_metric": values,
    })


@pytest.mark.parametrize("values, color", [
    ([1] * 8 + [0] * 2, "green"),    # 0.8 → 0.8
    ([1] * 13 + [0] * 7, "yellow"),  # 0.8 → 0.65: снижение 18.75%
    ([1] * 6 + [0] * 4, "red"),      # 0.8 → 0.6: снижение 25% — граница красного
])
def test_traffic_light(values, color):
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=contract(), scored_df=scored(values))
    assert result["status"] == "computed" and result["color"] == color
    assert result["details"]["formula"] == "mean(итог)"


def test_growth_is_green():
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=contract(baseline=0.5), scored_df=scored([1, 1, 1, 0]))
    assert result["color"] == "green" and result["details"]["delta"] == pytest.approx(-0.5)


def test_weighted_formula_uses_input_query_count():
    result = km_dynamics_test(
        acc_auto=0.9, monitoring_metric=contract(formula="wmean(итог, weight)", baseline=0.75),
        scored_df=scored([1, 0], weights=[3, 1]),
    )
    assert result["details"]["monitoring"] == pytest.approx(0.75) and result["color"] == "green"


def test_formula_over_judge_labels_and_agent_answer():
    payload = contract(
        formula='f1(prediction, target, "macro")',
        inputs=[{"name": "prediction", "column": "класс_output_answer", "judged": False},
                {"name": "target", "column": "класс_metric", "judged": True}],
        baseline=0.5833,
    )
    frame = scored([None] * 5).drop(columns=["итог_metric", "main_metric"])
    frame["класс_output_answer"] = ["a", "a", "b", "b", "a"]
    frame["класс_metric"] = ["a", "b", "b", "b", "b"]
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=payload, scored_df=frame)
    assert result["details"]["monitoring"] == pytest.approx(7 / 12) and result["color"] == "green"
    assert 'f1(prediction, target, &quot;macro&quot;)' in result["html_plot"]


def test_judge_final_score_semantics_aggregates_main_metric():
    payload = contract(
        formula="mean(prediction == target)",
        inputs=[{"name": "prediction", "column": "класс_output_answer", "judged": False},
                {"name": "target", "column": "класс_metric", "judged": True}],
        baseline=0.75,
    )
    result = km_dynamics_test(
        acc_auto=0.9, monitoring_metric=payload, scored_df=scored([1, 1, 1, 0]).drop(columns=["итог_metric"]),
        assessment_result={"status": "computed", "scoring_semantics": "judge_final_score"},
    )
    assert result["status"] == "computed"
    assert result["details"]["formula"] == "mean(assessment_score)"
    assert result["details"]["monitoring"] == pytest.approx(0.75)


# --------------------------------------------------------------------- серые случаи

def test_unreconciled_baseline_is_rejected_by_contract():
    payload = contract()
    payload["baseline"]["reconciliation"] = "mismatch"
    with pytest.raises(MonitoringContractError, match="не воспроизведён"):
        km_dynamics_test(acc_auto=0.9, monitoring_metric=payload, scored_df=scored([1]))


def test_not_computable_contract_is_gray_with_adapter_reason():
    result = km_dynamics_test(acc_auto=None, monitoring_metric=contract(status="not_computable"), scored_df=scored([1]))
    assert result["color"] == "gray" and "validation report" in result["reason"]


def test_assessor_refusal_is_gray():
    result = km_dynamics_test(
        acc_auto=None, monitoring_metric=contract(), scored_df=scored([1, 1]),
        assessment_result={"status": "not_computable", "reason": "судья недоступен"},
    )
    assert result["color"] == "gray" and result["reason"] == "судья недоступен"


def test_scored_df_without_formula_inputs_is_gray():
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=contract(), scored_df=scored([1, 1]).drop(columns=["итог_metric"]))
    assert result["color"] == "gray" and "входов формулы" in result["reason"]


def test_too_few_units_is_gray():
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=contract(), scored_df=scored([1, 1, 1]), min_units=30)
    assert result["color"] == "gray" and "3 < min_units=30" in result["reason"]
    assert result["details"]["coverage"]["scored_units"] == 3


def test_zero_baseline_is_gray():
    result = km_dynamics_test(acc_auto=0.9, monitoring_metric=contract(baseline=0.0), scored_df=scored([1, 0]))
    assert result["color"] == "gray"


# --------------------------------------------------------------------- entrypoint

def test_node_entrypoint_exposes_baseline_monitoring_and_formula():
    result = node_main(acc_auto=0.9, monitoring_metric=contract(), scored_df=scored([1, 1, 1, 0]), min_units=1)
    all_results = result["all_results"]
    assert all_results["test_name"] == "km_test" and all_results["color"] == "green"
    assert all_results["km_baseline"] == pytest.approx(0.8)
    assert all_results["km_monitoring"] == pytest.approx(0.75)
    assert all_results["km_formula"] == "mean(итог)"
    assert all_results["coverage"]["scored_units"] == 4
    assert all_results["laim_monitoring_version"]
    assert "<h2" in result["test_description"]


def test_yellow_is_amber_for_platform():
    result = node_main(acc_auto=0.9, monitoring_metric=contract(), scored_df=scored([1] * 13 + [0] * 7), min_units=1)
    assert result["all_results"]["color"] == "amber"
