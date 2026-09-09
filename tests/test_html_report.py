"""Проверки HTML: светофор, пропуски, экранирование и сохранность результата."""

import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("node_report", ROOT / "km_dynamics.py")
NODE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NODE)


@pytest.mark.parametrize("color", ["green", "yellow", "red", "gray"])
def test_report_preserves_result_and_explains_color(color):
    payload = {
        "report": {"semaphore": color},
        "precomputed": {
            "metric_value": 0.69, "metric_value_estimate": 0.62,
            "reliability": {"mean": 0.83, "median": 0.85, "q05": 0.71,
                            "share_below_threshold": 0.042},
            "gini_value": 0.52, "gini_std": 0.06,
            "gini_ci_lower": 0.49, "gini_ci_upper": 0.55,
            "n_oos_groups": 312, "n_oot_groups": 1240,
            "selected_features": ["<script>alert(1)</script>"],
            "reason": "Нет данных <script>alert(1)</script>",
        },
    }
    before = copy.deepcopy(payload)
    html = NODE._report_html("<script>alert(1)</script>", 0.69, None, None, None, color, reason=payload["precomputed"]["reason"], assessment_mode="qa", coverage={"scored_units": 1216, "total_units": 1240})
    assert payload == before
    assert 'class="laim-test-report"' in html
    assert "Тест 6.3.4" in html
    assert "Цель теста" in html and "Алгоритм расчёта" in html
    assert html.index("Критерии выставления светофора") < html.index('<table class="results-table"')
    assert "<details>" not in html and 'class="test-number"' not in html
    assert '<th scope="col">Показатель</th>' in html
    assert "Результат теста" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert {"green": "Зелёный", "yellow": "Жёлтый", "red": "Красный", "gray": "Не оценено"}[color] in html
    assert "4,2 %" in html if "local" in "km" else True


def test_missing_numbers_are_not_zero():
    from html_report import format_report_number
    assert format_report_number(None) == "Не рассчитано"
    assert format_report_number(float("nan")) == "Не рассчитано"
    assert format_report_number(float("inf")) == "Не рассчитано"
    assert format_report_number(0.0) == "0,000"


def test_gray_result_does_not_show_green_chart():
    html = NODE._report_html(
        "Точность", 0.0, 0.5, None, 0.74, "gray", reason="База равна нулю",
        assessment_mode="qa", coverage={"scored_units": 10, "total_units": 10},
    )
    assert "Не оценено" in html and "База равна нулю" in html
    assert '<img' not in html
    assert 'class="km-chart"' not in html


@pytest.mark.parametrize("accuracy", [None, 0.583])
def test_report_contains_only_available_assessor_statistics(accuracy):
    html = NODE._report_html(
        "F1-мера", .76, None, None, accuracy, "gray", reason="Нет оценок",
        assessment_mode="qa", coverage={"scored_units": 0, "total_units": 988},
    )
    assert "доверия" not in html and "R κ" not in html
    assert ("Точность Автоасессора (Acc auto)" in html) == (accuracy is not None)


@pytest.mark.parametrize("current", [1.1, 1., .85, .84, .75, 0.])
def test_horizontal_scale_marks_actual_decline(current):
    import re
    from html_report import format_report_number

    html = NODE.plot_km_dynamics("F1-мера", 1., current, .583, .15, .25)
    assert 'role="img"' in html
    assert "Изменение метрики: " + format_report_number(1. - current, 1, percent=True) in html
    assert "15 %" in html and "25 %" in html
    marker = float(re.search(r'class="km-marker" style="position:absolute;left:([0-9.]+)%', html)[1])
    left = min(0., 1. - current - .05)
    right = max(.35, 1. - current + .05)
    assert marker == pytest.approx(100 * (1. - current - left) / (right - left))
    assert 0 <= marker <= 100
    widths = [float(value) for value in re.findall(r'display:block;width:([0-9.]+)%', html)]
    assert len(widths) == 3 and all(value >= 0 for value in widths)
    assert sum(widths) == pytest.approx(100)
