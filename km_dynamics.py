"""Расчет динамики КМ: контрактная агрегация + dev-отчёт с графиком."""

from __future__ import annotations

from copy import deepcopy
import json
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


def _km_compatible_metric(payload: object) -> object:
    """Агрегирует готовый all_assessors score без повторной оценки голосов."""
    if not isinstance(payload, dict):
        return payload
    scoring = payload.get("scoring")
    if not isinstance(scoring, dict) or scoring.get("method") != "all_assessors":
        return payload

    mapped = deepcopy(payload)
    mapped["scoring"] = {
        "method": "identity",
        "sources": [
            {
                "source_id": "source_1",
                "column_name": mapped.get("score_column"),
                "role": "final_score",
                "normalization": "numeric",
                "polarity": "direct",
            }
        ],
        "missing_policy": scoring.get("missing_policy"),
        "majority_denominator": None,
    }
    return mapped


def summarize_units(scored_df: pd.DataFrame, contract: dict) -> dict[str, object]:
    """Единицы оценки по контракту: отказы судьи (NaN main_metric) исключаются из
    числителя и знаменателя независимо от missing_policy и считаются отдельно."""
    units = unitize(scored_df, contract)
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


def materialize_main_metric(
    scored_df: pd.DataFrame,
    metric_spec: dict,
) -> tuple[pd.DataFrame, str | None]:
    """Привести выбранные assessor-колонки к каноническому main_metric."""
    if not isinstance(scored_df, pd.DataFrame):
        raise TypeError("scored_df должен быть pandas.DataFrame")
    if not isinstance(metric_spec, dict):
        raise TypeError("metric_spec должен быть объектом")
    if metric_spec.get("status") == "not_computable":
        return scored_df, str(
            metric_spec.get("reason")
            or "kriteria-selector не разрешил ключевую метрику"
        )

    if "main_metric" in scored_df.columns:
        materialized = pd.to_numeric(scored_df["main_metric"], errors="coerce")
        if materialized.notna().any():
            # Ассессор уже посчитал канонический score по контракту (score_units:
            # нормализация, полярность, метод) — пересборка из колонок корзины
            # не нужна, а в monitoring-разметке их и нет. Строковый транспорт
            # приводится к числу здесь же; NaN — отказ судьи, его политику
            # применяет контрактная агрегация.
            return scored_df.assign(main_metric=materialized), None

    criteria = [metric_spec.get("main_metric")]
    criteria.extend(metric_spec.get("other_metrics") or [])
    criteria = list(dict.fromkeys(
        str(column).strip() for column in criteria if str(column).strip()
    ))
    method = str(metric_spec.get("scoring_method") or "identity").strip()
    supported_methods = {
        "identity", "mean_criteria", "all_criteria", "all_assessors", "majority",
    }
    if method not in supported_methods:
        return scored_df, f"неподдержанный scoring_method={method!r}"

    formula_columns = criteria if method != "identity" else criteria[:1]
    missing = [column for column in formula_columns if column not in scored_df.columns]
    if not formula_columns or missing:
        return scored_df, (
            "scored_df не содержит выбранные selector-колонки: "
            f"{missing or formula_columns}; доступны {list(scored_df.columns)}"
        )

    selected = scored_df[formula_columns]
    numeric = selected.apply(pd.to_numeric, errors="coerce")
    nonblank = selected.notna() & selected.astype(str).apply(
        lambda column: column.str.strip().ne("")
    )
    invalid = nonblank & numeric.isna()
    if invalid.to_numpy().any():
        return scored_df, (
            f"выбранные selector-колонки {formula_columns!r} не являются числовыми: "
            f"невалидных значений {int(invalid.to_numpy().sum())}"
        )

    policy = str(metric_spec.get("missing_policy") or "exclude_value").strip()
    if policy not in {"fail", "exclude_unit", "exclude_value", "zero"}:
        return scored_df, f"неподдержанный missing_policy={policy!r}"
    missing_rows = numeric.isna().any(axis=1)
    if method in {"identity", "mean_criteria", "all_criteria", "all_assessors"}:
        if policy == "fail" and missing_rows.any():
            return scored_df, (
                f"выбранные selector-колонки {formula_columns!r} содержат пропуски"
            )
    if method in {"all_criteria", "all_assessors", "majority"}:
        present_values = numeric.stack().dropna()
        if not present_values.isin([0, 1]).all():
            return scored_df, (
                f"scoring_method={method!r} требует бинарные значения 0/1"
            )

    values = numeric.fillna(0) if policy == "zero" else numeric
    if method == "identity":
        scores = values.iloc[:, 0]
    elif method == "mean_criteria":
        scores = values.mean(axis=1)
        if policy == "exclude_unit":
            scores = scores.mask(missing_rows)
    elif method in {"all_criteria", "all_assessors"}:
        scores = values.min(axis=1)
        if policy == "exclude_unit":
            scores = scores.mask(missing_rows)
    elif method == "majority":
        present = values.notna().sum(axis=1)
        denominator = (
            len(formula_columns)
            if metric_spec.get("majority_denominator") == "declared"
            else present
        )
        positives = values.fillna(0).sum(axis=1)
        scores = (positives * 2 > denominator).astype(float)
        unresolved = (present == 0) | (positives * 2 == denominator)
        if policy == "fail" and unresolved.any():
            return scored_df, "majority не вычисляется: нет голосов или получена ничья"
        if policy != "zero":
            scores = scores.mask(unresolved)

    result = scored_df.copy()
    result["main_metric"] = scores.astype("float64")
    if not scores.notna().any():
        return result, (
            f"выбранные selector-колонки {formula_columns!r} не содержат оценок"
        )
    return result, None


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


def _baseline_override_value(perv_validation_km: object) -> float | None:
    """Явный порт КМ первичной валидации: dict {name,value}, число или JSON."""
    if perv_validation_km is None:
        return None
    value = perv_validation_km
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if isinstance(value, dict):
        value = value.get('value')
    if value is None:
        return None
    return float(value)


def km_dynamics_test(
    acc_auto: float | None,
    monitoring_metric: dict,
    scored_df: pd.DataFrame,
    assessment_result: dict | None = None,
    perv_validation_km: object = None,
    metric_spec: dict | None = None,
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
        _km_compatible_metric(monitoring_metric),
        require_computed=False,
    )
    if contract["status"] != "computed":
        return refused(
            contract.get("reason", "monitoring_metric невычислим"), "upstream_not_computable"
        )

    if assessment_result is not None and not isinstance(assessment_result, dict):
        raise TypeError("assessment_result должен быть объектом")
    if assessment_result is not None and assessment_result.get("status") != "computed":
        reason = assessment_result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = f"assessment_status={assessment_result.get('status')!r}"
        return refused(reason, "assessment_not_computable")
    calibration = (assessment_result or {}).get("calibration_metrics") or {}
    admission = calibration.get("admission_status")
    warnings: list[str] = []
    if admission in {"red", "not_assessed"}:
        return refused(
            f"автоассессор не допущен (6.3.3): {calibration.get('admission_reason')}",
            "judge_not_admitted",
        )
    if admission == "amber":
        warnings.append(
            f"допуск автоассессора жёлтый: {calibration.get('admission_reason')}"
        )

    if metric_spec is not None:
        selector_spec = dict(metric_spec)
        contract_scoring = contract["scoring"]
        selector_spec.setdefault(
            "missing_policy", contract_scoring.get("missing_policy")
        )
        selector_spec.setdefault(
            "majority_denominator",
            contract_scoring.get("majority_denominator"),
        )
        if selector_spec.get("resolution_source") == "monitoring_metric_judged_total":
            selector_spec["scoring_method"] = "identity"
        scored_df, reason = materialize_main_metric(scored_df, selector_spec)
        if reason is not None:
            return refused(reason, "metric_spec")
    elif not isinstance(scored_df, pd.DataFrame) or "main_metric" not in scored_df:
        return refused(
            "scored_df не содержит main_metric; подключите "
            "kriteria-selector.metric_spec к одноимённому порту KM",
            "metric_spec",
        )

    override = _baseline_override_value(perv_validation_km)
    baseline = override if override is not None else float(contract["baseline"]["value"])
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
        if all(0.0 <= score <= 1.0 for score in summary["scores"]):
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
    if (contract.get("baseline") or {}).get("reconciliation") == "mismatch":
        warnings.append(
            "baseline.reconciliation=mismatch: пересчёт по корзине расходится с КМ "
            "отчёта о валидации; значение отчёта используется как есть"
        )

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
