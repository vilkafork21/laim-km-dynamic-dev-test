"""КМ-динамика на формах выходов ассессора и контракта адаптера."""

from __future__ import annotations

import pandas as pd
import pytest

from main import main


def _metric() -> dict[str, object]:
    return {
        "contract_version": "laim-monitoring-metric.v2",
        "umr_version": "laim-umr.v2",
        "status": "computed",
        "basket_id": "CI09997554",
        "name": "Accuracy",
        "score_column": "main_metric",
        "assessment_mode": "dialogue",
        "scoring": {
            "method": "all_assessors",
            "sources": [
                {
                    "source_id": f"source_{index}",
                    "column_name": f"mark_{index}_metric",
                    "role": "assessor_vote",
                    "normalization": "numeric",
                    "polarity": "direct",
                }
                for index in (1, 2, 3)
            ],
            "missing_policy": "fail",
            "majority_denominator": None,
        },
        "aggregation": {"method": "mean", "weight_column": None},
        "baseline": {
            "value": 0.92, "scale": "ratio", "value_source": "validation_report",
            "reported_value": 0.92, "reported_scale": "ratio",
            "recomputed_value": 0.9293, "reconciliation": "match",
        },
        "primary_validation": {
            "threshold": None, "comparator": None, "scale": "ratio",
            "verdict": None, "affects_monitoring": False,
        },
        "evidence": {},
    }


def _scored_df() -> pd.DataFrame:
    """Форма dialogue scored_data: один итоговый score на сессию."""
    return pd.DataFrame({
        "session_id": ["m1", "m1", "m2", "m3"],
        "query_id": ["t1", "t2", "t3", "t4"],
        "input_query": ["в1", "в2", "в3", "в4"],
        "output_answer": ["о1", "о2", "о3", "о4"],
        "input_query_count": [1, 1, 1, 1],
        "reference_group_id": ["m1", "m1", "m2", "m3"],
        "turn_index": [1, 2, 1, 1],
        "assessment_unit_id": ["m1", "m1", "m2", "m3"],
        "main_metric": [1.0, 1.0, 1.0, 0.0],
        "agent_assessment_score": [1.0, 1.0, 1.0, 0.0],
    })


def test_km_dynamics_accepts_assessor_output_and_contract():
    result = main(
        acc_auto=0.95,
        monitoring_metric=_metric(),
        scored_df=_scored_df(),
        assessment_result={
            "contract_version": "laim-assessment-result.v1",
            "status": "computed",
            "assessment_mode": "dialogue",
            "total_units": 3,
            "scored_units": 3,
        },
        metric_spec={"main_metric": "main_metric", "status": "resolved"},
    )

    verdict = result["all_results"]
    assert verdict["test_name"] == "km_test"
    assert verdict["color"] in {"green", "yellow", "red", "gray"}
    assert verdict["status"] in {"computed", "not_computable"}
    assert verdict["km_name"] == "Accuracy"
    assert verdict["km_baseline"] == 0.92
    assert verdict["km_monitoring"] == 2 / 3
    assert verdict["km_delta"] == pytest.approx((0.92 - 2 / 3) / 0.92)
    assert verdict["coverage"]["total_units"] == 3
    assert isinstance(result["test_description"], str)


def test_canonical_main_metric_wins_over_selector_columns():
    """Ассессор уже материализовал main_metric по контракту: km-тест обязан
    использовать его, а не пересобирать счёт из колонок корзины, которых в
    monitoring-разметке нет."""
    result = main(
        acc_auto=0.9,
        monitoring_metric=_metric(),
        scored_df=_scored_df(),
        assessment_result={
            "contract_version": "laim-assessment-result.v1",
            "status": "computed",
            "assessment_mode": "dialogue",
            "total_units": 3,
            "scored_units": 3,
        },
        metric_spec={
            "status": "resolved",
            "main_metric": "итоговая_оценка_metric",  # колонка эталонной корзины
            "scoring_method": "identity",
        },
    )

    verdict = result["all_results"]
    assert verdict["status"] == "computed", verdict["reason"]
