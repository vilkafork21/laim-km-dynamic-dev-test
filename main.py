"""Sber DS entrypoint теста динамики ключевой метрики (карточка 6.3.4)."""

from __future__ import annotations

import logging

import pandas as pd

from km_dynamics import km_dynamics_test

logger = logging.getLogger(__name__)

_PLATFORM_COLOR = {"yellow": "amber", "gray": "gray"}
_TITLE = {
    "green": "Динамика ключевой метрики соответствует зеленому светофору",
    "yellow": "Динамика ключевой метрики соответствует желтому светофору",
    "red": "Динамика ключевой метрики соответствует красному светофору",
    "gray": "Динамику ключевой метрики невозможно оценить",
}


def main(
    monitoring_metric: dict,
    acc_auto: float | None = None,
    scored_df: pd.DataFrame | None = None,
    assessment_result: dict | None = None,
    perv_validation_km: object = None,
    metric_spec: dict | None = None,
    green_threshold: float = 0.15,
    red_threshold: float = 0.25,
    delta_unit: str = "absolute",
    c_min: float = 0.0,
    min_valid_units: int = 50,
    max_invalid_share: float = 0.2,
):
    # c_min = 0 означает «минимальный уровень не задан»: любое значение метрики
    # с направлением «больше — лучше» не ниже нуля.
    minimum_level = None if c_min <= 0 else float(c_min)
    logger.info(
        "[km] пороги green<=%s red>=%s unit=%s c_min=%s min_units=%s max_refused=%s",
        green_threshold, red_threshold, delta_unit, minimum_level,
        min_valid_units, max_invalid_share,
    )
    result = km_dynamics_test(
        acc_auto=acc_auto,
        monitoring_metric=monitoring_metric,
        scored_df=scored_df,
        assessment_result=assessment_result,
        perv_validation_km=perv_validation_km,
        metric_spec=metric_spec,
        green_threshold=green_threshold,
        red_threshold=red_threshold,
        delta_unit=delta_unit,
        c_min=minimum_level,
        min_valid_units=min_valid_units,
        max_invalid_share=max_invalid_share,
    )
    color = result["trafic_light"]
    platform_color = _PLATFORM_COLOR.get(color, color)
    details = result["kluch_metric"]
    return {
        "all_results": {
            "calculated_traffic_lights": {
                "test_light": platform_color,
                "semaphore_title": _TITLE[color],
            },
            "color": platform_color,
            "test_name": "km_test",
            "status": result["status"],
            "reason": result["reason"],
            "reason_code": result["reason_code"],
            "metric_details": details,
            "km_name": details["name"],
            "km_baseline": details["КМ на первичной валидации"],
            "km_monitoring": details["КМ на мониторинге"],
            "km_delta": details["Дельта КМ"],
            "km_delta_unit": result["delta_unit"],
            "interval": result["interval"],
            "coverage": details["coverage"],
            "provenance": result["provenance"],
            "thresholds": {
                "green": green_threshold,
                "red": red_threshold,
                "unit": delta_unit,
                "c_min": minimum_level,
            },
            "warnings": result["warnings"],
            "judge_bias": result["judge_bias"],
        },
        "test_description": result["html_plot"],
    }
