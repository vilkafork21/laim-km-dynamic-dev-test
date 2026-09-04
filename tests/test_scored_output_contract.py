"""КМ-динамика на формах выходов ассессора и контракта адаптера."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from main import main

NODE_ROOT = Path(__file__).resolve().parents[1]


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


def _assessment(total=3, scored=3):
    return {
        "contract_version": "laim-assessment-result.v1",
        "status": "computed",
        "assessment_mode": "dialogue",
        "total_units": total,
        "scored_units": scored,
    }


def _run(frame=None, **overrides):
    kwargs = dict(
        acc_auto=0.95,
        monitoring_metric=_metric(),
        scored_df=frame if frame is not None else _scored_df(),
        assessment_result=_assessment(),
        metric_spec={"main_metric": "main_metric", "status": "resolved"},
    )
    kwargs.update(overrides)
    return main(**kwargs)["all_results"]


def _flat_frame(ones: int, zeros: int) -> pd.DataFrame:
    rows = ones + zeros
    scores = [1.0] * ones + [0.0] * zeros
    return pd.DataFrame({
        "session_id": [f"s{i}" for i in range(rows)],
        "query_id": [f"q{i}" for i in range(rows)],
        "input_query": ["в"] * rows,
        "output_answer": ["о"] * rows,
        "input_query_count": [1] * rows,
        "reference_group_id": [f"s{i}" for i in range(rows)],
        "turn_index": [1] * rows,
        "assessment_unit_id": [f"s{i}" for i in range(rows)],
        "main_metric": scores,
        "agent_assessment_score": scores,
    })


def test_km_dynamics_accepts_assessor_output_and_contract():
    result = main(
        acc_auto=0.95,
        monitoring_metric=_metric(),
        scored_df=_scored_df(),
        assessment_result=_assessment(),
        metric_spec={"main_metric": "main_metric", "status": "resolved"},
        min_valid_units=3,
    )

    verdict = result["all_results"]
    assert verdict["test_name"] == "km_test"
    assert verdict["color"] in {"green", "amber", "red", "gray"}
    assert verdict["status"] == "computed"
    assert verdict["km_name"] == "Accuracy"
    assert verdict["km_baseline"] == 0.92
    assert verdict["km_monitoring"] == pytest.approx(2 / 3)
    assert verdict["km_delta"] == pytest.approx(0.92 - 2 / 3)
    assert verdict["km_delta_unit"] == "absolute"
    assert verdict["interval"]["method"] == "wilson"
    assert verdict["interval"]["lower"] < 2 / 3 < verdict["interval"]["upper"]
    assert verdict["provenance"] == {
        "unit": "dialogue", "total_units": 3, "scored_units": 3, "refused_units": 0,
        "refused_share": 0.0, "weight_sum": 3.0, "n_effective": 3.0,
    }
    assert verdict["coverage"]["total_units"] == 3
    assert verdict["warnings"] == []
    assert verdict["reason_code"]
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
        min_valid_units=3,
    )

    verdict = result["all_results"]
    assert verdict["status"] == "computed", verdict["reason"]


def test_zero_baseline_is_not_computable_not_green():
    # Baseline 0 означает, что относительная динамика не определена: любой
    # результат мониторинга (включая 0) — серый и not_computable, иначе
    # агент с нулевой КМ получал бы зелёный, а агрегатор — противоречивую
    # пару gray/computed.
    metric = _metric()
    metric["baseline"]["value"] = 0.0
    metric["baseline"]["reported_value"] = 0.0
    for scores in ([1.0, 1.0, 1.0, 0.0], [0.0, 0.0, 0.0, 0.0]):
        frame = _scored_df()
        frame["main_metric"] = scores
        frame["agent_assessment_score"] = scores
        verdict = _run(frame, monitoring_metric=metric, min_valid_units=3)
        assert verdict["color"] == "gray" and verdict["status"] == "not_computable"
        assert verdict["reason_code"] == "baseline_not_positive"


def test_optional_ports_can_be_absent():
    # descriptor объявляет scored_df и acc_auto необязательными: вызов без
    # них — штатная деградация, а не TypeError.
    verdict = main(monitoring_metric=_metric())["all_results"]
    assert verdict["color"] == "gray" and verdict["status"] == "not_computable"
    assert verdict["reason"]


def test_thresholds_are_node_settings():
    # Пороги, единицы и C_MIN — настройки ноды и публикуются в выходе. На трёх
    # единицах интервал широк: снижение возможно, но не подтверждено — жёлтый.
    default = _run(min_valid_units=3)
    strict = _run(min_valid_units=3, green_threshold=0.05, red_threshold=0.2, delta_unit="relative")
    assert default["color"] == "amber"
    assert default["thresholds"] == {"green": 0.15, "red": 0.25, "unit": "absolute", "c_min": None}
    assert strict["color"] == "amber" and strict["km_delta_unit"] == "relative"
    assert strict["thresholds"] == {"green": 0.05, "red": 0.2, "unit": "relative", "c_min": None}


def test_default_minimum_units_blocks_small_sample():
    verdict = _run()
    assert verdict["color"] == "gray" and verdict["status"] == "not_computable"
    assert verdict["reason_code"] == "insufficient_units"
    assert verdict["provenance"]["scored_units"] == 3 and "50" in verdict["reason"]


def test_judge_refusals_are_excluded_and_capped():
    frame = _scored_df()
    frame.loc[frame["session_id"] == "m3", ["main_metric", "agent_assessment_score"]] = float("nan")
    tolerant = _run(frame, min_valid_units=2, max_invalid_share=0.5)
    assert tolerant["status"] == "computed"
    assert tolerant["provenance"]["refused_units"] == 1
    assert tolerant["provenance"]["scored_units"] == 2
    assert tolerant["km_monitoring"] == 1.0
    capped = _run(frame, min_valid_units=2, max_invalid_share=0.2)
    assert capped["status"] == "not_computable" and capped["reason_code"] == "judge_refusals"


def test_missing_policy_fail_does_not_crash_on_refusal():
    # Контракт с missing_policy=fail относится к пропускам разметки, а не к
    # отказам судьи: отказ исключается и считается, нода не падает.
    frame = _scored_df()
    frame.loc[0:1, ["main_metric", "agent_assessment_score"]] = float("nan")
    verdict = _run(frame, min_valid_units=2, max_invalid_share=0.5)
    assert verdict["status"] == "computed" and verdict["provenance"]["refused_units"] == 1


def test_large_sample_colours_by_interval():
    green = _run(_flat_frame(108, 12), assessment_result=_assessment(120, 120))
    assert green["status"] == "computed" and green["km_monitoring"] == pytest.approx(0.9)
    assert green["interval"]["lower"] < 0.9 < green["interval"]["upper"]
    # 0.92 -> 0.9: пессимистичное снижение около 0.09 <= 0.15
    assert green["color"] == "green" and green["reason_code"] == "within_tolerance"
    amber = _run(_flat_frame(72, 48), assessment_result=_assessment(120, 120))
    # 0.92 -> 0.6: оптимистичная граница около 0.687 даёт снижение 0.23 < 0.25
    assert amber["color"] == "amber" and amber["reason_code"] == "drop_possible"
    red = _run(_flat_frame(60, 60), assessment_result=_assessment(120, 120))
    # 0.92 -> 0.5: оптимистичная граница около 0.589 даёт снижение 0.33 >= 0.25
    assert red["color"] == "red" and red["reason_code"] == "drop_confirmed"


def test_c_min_setting_and_relative_unit():
    frame = _flat_frame(88, 12)
    kwargs = dict(assessment_result=_assessment(100, 100))
    # Уилсон для 88/100: интервал около [0.80; 0.93]; пессимистичное снижение 0.12 <= 0.15
    assert _run(frame, **kwargs)["color"] == "green"
    # тот же интервал пересекает C_MIN = 0.9
    crossing = _run(frame, c_min=0.9, **kwargs)
    assert crossing["color"] == "amber" and crossing["reason_code"] == "drop_possible"
    below = _run(frame, c_min=0.95, **kwargs)
    assert below["color"] == "red" and below["reason_code"] == "below_c_min"
    assert _run(frame, delta_unit="relative", **kwargs)["km_delta_unit"] == "relative"


def _calibrated(status="green", reason="ok", **bias):
    assessment = _assessment(120, 120)
    assessment["calibration_metrics"] = {
        "admission_status": status, "admission_reason": reason, **bias,
    }
    return assessment


def test_judge_admission_gates_km():
    frame = _flat_frame(108, 12)
    refused = _run(frame, assessment_result=_calibrated("red", "судья не лучше моды"))
    assert refused["status"] == "not_computable"
    assert refused["reason_code"] == "judge_not_admitted" and "моды" in refused["reason"]
    pending = _run(frame, assessment_result=_calibrated("not_assessed", "holdout мал"))
    assert pending["reason_code"] == "judge_not_admitted"
    limited = _run(frame, assessment_result=_calibrated("amber", "каппа ниже порога"))
    assert limited["status"] == "computed" and limited["color"] == "green"
    assert any("жёлтый" in warning for warning in limited["warnings"])


def test_judge_bias_shifts_estimate_and_widens_interval():
    frame = _flat_frame(98, 22)   # 0.8167: без поправки пессимистичное снижение > 0.15
    plain = _run(frame, assessment_result=_assessment(120, 120))
    assert plain["color"] == "amber" and plain["judge_bias"] is None
    corrected = _run(frame, assessment_result=_calibrated(
        bias_mean=-0.1, bias_ci_lower=-0.12, bias_ci_upper=-0.08, bias_units=40,
    ))
    assert corrected["color"] == "green" and corrected["reason_code"] == "within_tolerance"
    assert corrected["km_monitoring"] == pytest.approx(0.9167, abs=1e-3)
    assert corrected["interval"]["method"] == "wilson+bias"
    assert corrected["interval"]["lower"] == pytest.approx(plain["interval"]["lower"] + 0.1 - 0.02)
    assert corrected["judge_bias"] == {
        "mean": -0.1, "ci_lower": -0.12, "ci_upper": -0.08, "applied": True,
    }


def test_bias_corrected_interval_stays_in_metric_domain():
    verdict = _run(_flat_frame(114, 6), assessment_result=_calibrated(
        bias_mean=-0.1, bias_ci_lower=-0.12, bias_ci_upper=-0.08, bias_units=40,
    ))
    assert verdict["status"] == "computed"
    assert verdict["km_monitoring"] <= 1.0 and verdict["interval"]["upper"] == 1.0


def test_uncertain_judge_bias_blocks_verdict():
    verdict = _run(_flat_frame(108, 12), assessment_result=_calibrated(
        bias_mean=-0.05, bias_ci_lower=-0.4, bias_ci_upper=0.3, bias_units=8,
    ))
    assert verdict["status"] == "not_computable"
    assert verdict["reason_code"] == "judge_bias_uncertain"


def test_reconciliation_mismatch_is_warning_not_gate():
    metric = _metric()
    metric["baseline"]["reconciliation"] = "mismatch"
    verdict = _run(monitoring_metric=metric, min_valid_units=3)
    assert verdict["status"] == "computed"
    assert any("reconciliation" in warning for warning in verdict["warnings"])


def test_descriptor_declares_settings_and_sources():
    descriptor = json.loads((NODE_ROOT / "descriptor.json").read_text("utf-8"))
    settings = descriptor["ui"]["settings"][0]["components"][0]["config"]["components"]
    by_name = {item["parameter"]: item["defaultValue"] for item in settings}
    assert by_name == {
        "green_threshold": 0.15, "red_threshold": 0.25, "delta_unit": "absolute",
        "c_min": 0.0, "min_valid_units": 50, "max_invalid_share": 0.2,
    }
    assert "verdict.py" in descriptor["script"]["runConfiguration"]["sourceFiles"]
