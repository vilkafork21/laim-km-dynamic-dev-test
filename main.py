"""Sber DS entrypoint теста динамики ключевой метрики."""

from __future__ import annotations

import logging

import pandas as pd

import laim_monitoring
from km_dynamics import GREEN_THRESHOLD, RED_THRESHOLD, km_dynamics_test

_PLATFORM_COLOR = {"yellow": "amber"}
_TITLE = {
    "green": "Динамика ключевой метрики соответствует зеленому светофору",
    "yellow": "Динамика ключевой метрики соответствует желтому светофору",
    "red": "Динамика ключевой метрики соответствует красному светофору",
    "gray": "Динамику ключевой метрики невозможно оценить",
}


def main(
    acc_auto: float | None,
    monitoring_metric: dict,
    scored_df: pd.DataFrame,
    assessment_result: dict | None = None,
    min_units: int = 30,
):
    logging.info("Тест динамики ключевой метрики запущен")
    result = km_dynamics_test(
        acc_auto=acc_auto,
        monitoring_metric=monitoring_metric,
        scored_df=scored_df,
        assessment_result=assessment_result,
        min_units=int(min_units),
    )
    color, details = result["color"], result["details"]
    return {
        "all_results": {
            "calculated_traffic_lights": {
                "test_light": _PLATFORM_COLOR.get(color, color),
                "semaphore_title": _TITLE[color],
            },
            "color": _PLATFORM_COLOR.get(color, color),
            "test_name": "km_test",
            "laim_monitoring_version": laim_monitoring.__version__,
            "status": result["status"],
            "metric_details": details,
            "km_name": details["name"],
            "km_formula": details["formula"],
            "km_baseline": details["baseline"],
            "km_monitoring": details["monitoring"],
            "km_delta": details["delta"],
            "coverage": details["coverage"],
            "thresholds": {"green": GREEN_THRESHOLD, "red": RED_THRESHOLD},
            "reason": result["reason"],
        },
        "test_description": result["html_plot"],
    }
