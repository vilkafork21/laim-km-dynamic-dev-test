"""Расчет динамики КМ: контрактная агрегация + dev-отчёт с графиком."""

from __future__ import annotations

import logging
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from km_report import report_html
from laim_monitoring import MonitoringContractError, unitize, validate_monitoring_metric
from verdict import UNITS, Interval, drop, effective_n, interval
from verdict import verdict as decide_color

logger = logging.getLogger(__name__)


def summarize_units(scored_df: pd.DataFrame, contract: dict) -> dict[str, object]:
    """Единицы оценки по контракту: отказы судьи (NaN main_metric) исключаются из
    числителя и знаменателя независимо от missing_policy и считаются отдельно."""
    units = unitize(scored_df, contract, include_sources=False)
    scores = pd.to_numeric(units["main_metric"], errors="coerce")
    weighted = contract["aggregation"]["method"] == "frequency_weighted_mean"
    weights = (
        pd.to_numeric(units["input_query_count"], errors="coerce").astype(float)
        if weighted
        else pd.Series(1.0, index=units.index)
    )
    if weighted and (weights.isna().any() or (weights <= 0).any()):
        raise MonitoringContractError("input_query_count должен быть положительным числом")
    scored = scores.notna()
    values = scores[scored].astype(float).tolist()
    if not scores[scored].isin(contract["evaluation"]["score_values"]).all():
        raise MonitoringContractError("main_metric выходит за утверждённый score_values")
    used = weights[scored].tolist()
    total = int(len(units))
    refused = int((~scored).sum())
    return {
        "scores": values,
        "weights": used,
        "provenance": {
            "unit": contract["assessment_mode"],
            "total_units": total,
            "scored_units": total - refused,
            "refused_units": refused,
            "refused_share": (refused / total) if total else 1.0,
            "weight_sum": float(sum(used)),
            "n_effective": effective_n(used) if used else 0.0,
        },
    }


def _coverage(provenance: dict[str, object] | None) -> dict[str, object]:
    details = provenance or {}
    return {
        "total_units": details.get("total_units"),
        "scored_units": details.get("scored_units"),
        "excluded_units": details.get("refused_units"),
        "weight_sum": details.get("weight_sum"),
    }


def _not_computable_result(
    contract: dict,
    *,
    reason: str,
    reason_code: str,
    acc_auto: float | None,
    thresholds: dict[str, object],
    provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    baseline_payload = contract.get("baseline")
    baseline_value = (
        baseline_payload.get("value")
        if isinstance(baseline_payload, dict)
        else None
    )
    baseline = None if baseline_value is None else float(baseline_value)
    name = contract.get("name")
    metric_details = {
        "name": name,
        "КМ на мониторинге": None,
        "КМ на первичной валидации": baseline,
        "Дельта КМ": None,
        "Порог минимальной дельты КМ": thresholds["red"],
        "coverage": _coverage(provenance),
    }
    return {
        "status": "not_computable",
        "trafic_light": "gray",
        "reason": reason,
        "reason_code": reason_code,
        "kluch_metric": metric_details,
        "interval": None,
        "provenance": provenance,
        "warnings": [],
        "delta_unit": thresholds["unit"],
        "judge_bias": None,
        "html_plot": report_html(
            name,
            baseline,
            None,
            None,
            acc_auto,
            "gray",
            reason=reason,
            assessment_mode=contract.get("assessment_mode"),
            thresholds=thresholds,
            provenance=provenance,
        ),
    }


def km_dynamics_test(
    acc_auto: float | None,
    monitoring_metric: dict,
    scored_df: pd.DataFrame,
    assessment_result: dict | None = None,
    *,
    green_threshold: float = 0.15,
    red_threshold: float = 0.25,
    delta_unit: str = "absolute",
    c_min: float | None = None,
    min_valid_units: int = 50,
    max_invalid_share: float = 0.2,
) -> dict[str, object]:
    if delta_unit not in UNITS:
        raise ValueError(f"delta_unit должен быть одним из {UNITS}, получено {delta_unit!r}")
    thresholds = {
        "green": green_threshold, "red": red_threshold, "unit": delta_unit, "c_min": c_min,
    }

    def refused(reason: str, reason_code: str, provenance: dict | None = None):
        return _not_computable_result(
            contract, reason=reason, reason_code=reason_code, acc_auto=acc_auto,
            thresholds=thresholds, provenance=provenance,
        )

    contract = validate_monitoring_metric(
        monitoring_metric,
        require_computed=False,
    )
    if contract["status"] != "computed":
        return refused(
            contract.get("reason", "monitoring_metric невычислим"), "upstream_not_computable"
        )

    if not contract["evaluation"]["higher_is_better"]:
        return refused("Тест снижения пока не поддерживает метрику с направлением меньше — лучше", "unsupported_direction")
    if assessment_result is not None and not isinstance(assessment_result, dict):
        raise TypeError("assessment_result должен быть объектом")
    if assessment_result is not None and assessment_result.get("status") != "computed":
        reason = assessment_result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = f"assessment_status={assessment_result.get('status')!r}"
        return refused(reason, "assessment_not_computable")
    calibration = (assessment_result or {}).get("calibration_metrics")
    if not isinstance(calibration, dict):
        return refused("Нет результата калибровки автоассессора (6.3.3)", "judge_not_admitted")
    admission = calibration.get("admission_status")
    warnings: list[str] = []
    if admission not in ("green", "amber"):
        return refused(
            f"автоассессор не допущен (6.3.3), admission_status={admission!r}: "
            f"{calibration.get('admission_reason') or 'допуск не подтверждён'}",
            "judge_not_admitted",
        )
    if admission == "amber":
        warnings.append(
            f"допуск автоассессора жёлтый: {calibration.get('admission_reason')}"
        )

    if not isinstance(scored_df, pd.DataFrame) or "main_metric" not in scored_df:
        return refused("scored_df не содержит итоговый main_metric ассесора", "missing_final_score")
    if assessment_result.get("purpose") != "monitoring":
        return refused("Калибровочные оценки нельзя использовать как мониторинговые", "wrong_assessment_purpose")
    roles = scored_df.get("dataset_role")
    if roles is None or not roles.eq("monitoring").all():
        return refused("scored_df содержит данные другого назначения", "wrong_dataset_role")
    run_id = assessment_result.get("run_id")
    run_ids = scored_df.get("assessment_run_id")
    if not isinstance(run_id, str) or not run_id or run_ids is None or not run_ids.eq(run_id).all():
        return refused("scored_df и допуск судьи относятся к разным прогонам", "assessment_run_mismatch")
    identifiers = scored_df.get("definition_id")
    if identifiers is None or not identifiers.eq(contract["definition_id"]).all():
        return refused("scored_df относится к другому определению КМ", "definition_mismatch")
    if (assessment_result.get("contract_version") != "laim-assessment-result.v2"
            or assessment_result.get("definition_id") != contract["definition_id"]):
        return refused("Допуск судьи относится к другому определению КМ", "definition_mismatch")
    baseline = float(contract["baseline"]["value"])
    summary = summarize_units(scored_df, contract)
    provenance = summary["provenance"]
    if baseline <= 0:
        return refused(
            "Базовое значение КМ отсутствует или неположительно.",
            "baseline_not_positive", provenance,
        )
    if provenance["refused_share"] > max_invalid_share:
        return refused(
            f"Доля отказов судьи {provenance['refused_share']:.2f} выше допустимой "
            f"{max_invalid_share:.2f}.",
            "judge_refusals", provenance,
        )
    if provenance["scored_units"] < min_valid_units:
        return refused(
            f"Оценённых единиц {provenance['scored_units']} меньше минимума {min_valid_units}.",
            "insufficient_units", provenance,
        )

    ci = interval(summary["scores"], summary["weights"])
    current = sum(s * w for s, w in zip(summary["scores"], summary["weights"])) / sum(
        summary["weights"]
    )
    judge_bias = None
    if calibration.get("bias_mean") is not None:
        # Поправка на смещение судьи (карточка 6.3.4, шаги 8/10/12): КМ_тек − b,
        # интервал расширяется на неопределённость самого смещения.
        bias_mean = float(calibration["bias_mean"])
        half = (
            float(calibration["bias_ci_upper"]) - float(calibration["bias_ci_lower"])
        ) / 2
        tolerance = green_threshold if delta_unit == "absolute" else green_threshold * baseline
        if half > tolerance:
            return refused(
                f"интервал смещения судьи ±{half:.3f} шире допустимого снижения КМ "
                f"{tolerance:.3f}: ошибка измерителя способна изменить цвет",
                "judge_bias_uncertain", provenance,
            )
        current -= bias_mean
        ci = Interval(
            ci.lower - bias_mean - half, ci.upper - bias_mean + half, ci.level,
            f"{ci.method}+bias",
        )
        if contract["baseline"]["scale"] == "ratio":
            # Долевая метрика: сдвиг на смещение не выводит оценку за пределы шкалы.
            current = min(1.0, max(0.0, current))
            ci = Interval(max(0.0, ci.lower), min(1.0, ci.upper), ci.level, ci.method)
        judge_bias = {
            "mean": bias_mean,
            "ci_lower": float(calibration["bias_ci_lower"]),
            "ci_upper": float(calibration["bias_ci_upper"]),
            "applied": True,
        }
    decision = decide_color(
        baseline, ci, green_threshold=green_threshold, red_threshold=red_threshold,
        unit=delta_unit, c_min=c_min,
    )
    delta = drop(baseline, current, delta_unit)

    logger.info(
        "[km] baseline=%s current=%s interval=[%s; %s] drop=%s unit=%s color=%s units=%s",
        baseline, current, ci.lower, ci.upper, delta, delta_unit, decision.color, provenance,
    )
    metric_details = {
        "name": contract["name"],
        "КМ на мониторинге": current,
        "КМ на первичной валидации": baseline,
        "Дельта КМ": delta,
        "Порог минимальной дельты КМ": red_threshold,
        "coverage": _coverage(provenance),
    }
    return {
        "status": "computed",
        "trafic_light": decision.color,
        "reason": decision.reason,
        "reason_code": decision.reason_code,
        "kluch_metric": metric_details,
        "interval": {
            "lower": ci.lower, "upper": ci.upper, "level": ci.level, "method": ci.method,
        },
        "provenance": provenance,
        "warnings": warnings,
        "delta_unit": delta_unit,
        "judge_bias": judge_bias,
        "html_plot": report_html(
            contract["name"],
            baseline,
            current,
            delta,
            acc_auto,
            decision.color,
            reason=decision.reason,
            assessment_mode=contract["assessment_mode"],
            thresholds=thresholds,
            interval_=ci,
            provenance=provenance,
        ),
    }
