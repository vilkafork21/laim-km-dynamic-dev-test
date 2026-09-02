"""Sber DS entrypoint теста динамики ключевой метрики."""

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
):
    logger.info("Тест динамики ключевой метрики запущен")
    result = km_dynamics_test(
        acc_auto=acc_auto,
        monitoring_metric=monitoring_metric,
        scored_df=scored_df,
        assessment_result=assessment_result,
        perv_validation_km=perv_validation_km,
        metric_spec=metric_spec,
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
            "metric_details": details,
            "km_name": details["name"],
            "km_baseline": details["КМ на первичной валидации"],
            "km_monitoring": details["КМ на мониторинге"],
            "km_delta": details["Дельта КМ"],
            "coverage": details["coverage"],
            "thresholds": {
                "green": 0.15,
                "red": details["Порог минимальной дельты КМ"],
            },
            "reason": result["reason"],
        },
        "test_description": result["html_plot"],
    }
