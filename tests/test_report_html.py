import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "km_dynamics.py"
SPEC = importlib.util.spec_from_file_location("km_dynamics_report", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
_report_html = MODULE._report_html


def render_report(**overrides: object) -> str:
    values = {
        "name": "Доля релевантных ответов",
        "baseline": 0.85,
        "current": 0.4,
        "delta": 0.529,
        "accuracy": 0.917,
        "color": "red",
        "reason": "Порог превышен",
        "assessment_mode": "dialog",
        "coverage": {"scored_units": 60, "total_units": 60},
        "green_threshold": 0.15,
        "c_min_threshold": 0.25,
    }
    values.update(overrides)
    return _report_html(**values)


@pytest.mark.parametrize(
    ("color", "status"),
    [
        ("green", "В норме"),
        ("yellow", "Требует внимания"),
        ("red", "Критично"),
        ("gray", "Недостаточно данных"),
    ],
)
def test_report_has_text_status(color: str, status: str) -> None:
    report = render_report(color=color)

    assert f'class="km-report km-report--{color}"' in report
    assert f"<span>{status}</span>" in report


@pytest.mark.parametrize(
    ("delta", "position", "edge_class"),
    [
        (-0.2, "0", "km-report__marker--start"),
        (0.02, "2", "km-report__marker--start"),
        (0.2, "20", None),
        (0.95, "95", "km-report__marker--end"),
        (1.2, "100", "km-report__marker--end"),
    ],
)
def test_report_marker_is_clamped(
    delta: float,
    position: str,
    edge_class: str | None,
) -> None:
    report = render_report(delta=delta)

    assert f"--marker-position:{position}%" in report
    if edge_class is None:
        assert "km-report__marker--start" not in report.split("<article", 1)[1]
        assert "km-report__marker--end" not in report.split("<article", 1)[1]
    else:
        assert edge_class in report


def test_gray_report_has_no_marker() -> None:
    report = render_report(color="gray", delta=0.2)

    assert 'class="km-report__marker' not in report
    assert "--marker-position:" not in report


def test_report_is_final_and_technical_data_is_collapsed() -> None:
    report = render_report(
        reason="monitoring_metric: qa autoassessor",
        assessment_mode="qa-mode",
    )
    visible, technical = report.split("<details", 1)

    assert "Вывод" not in report
    assert "Рекомендац" not in report
    assert "<footer" not in report
    assert "monitoring_metric" not in visible
    assert "autoassessor" not in visible
    assert "qa-mode" not in visible
    assert "monitoring_metric: qa autoassessor" in technical
    assert "Точность калибровки" in technical
    assert "Формула изменения" in technical


def test_report_escapes_values_and_uses_russian_numbers() -> None:
    report = render_report(
        name="<b>Качество & риск</b>",
        reason="<script>причина</script>",
        assessment_mode='dialog"><img src=x>',
        green_threshold=0.125,
        c_min_threshold=0.325,
    )

    assert "&lt;b&gt;Качество &amp; риск&lt;/b&gt;" in report
    assert "&lt;script&gt;причина&lt;/script&gt;" in report
    assert "dialog&quot;&gt;&lt;img src=x&gt;" in report
    assert "0,85" in report
    assert "0,40" in report
    assert "52,9%" in report
    assert "−52,9%" in report
    assert "Снижение 52,9%" in report
    assert "12,5%–32,5%" in report
    assert "--normal-width:12.5%" in report
    assert "--attention-width:20%" in report


def test_report_is_standalone_fragment() -> None:
    report = render_report()

    assert report.count("<style>") == 1
    assert report.count("</style>") == 1
    assert '<article class="km-report km-report--red" lang="ru">' in report
    assert '<dl class="km-report__facts">' in report
    assert "<script" not in report
    assert " href=" not in report
    assert " src=" not in report
    assert "@media print" in report


def test_report_shows_growth_as_positive_change() -> None:
    report = render_report(delta=-0.2, color="green")

    assert "+20%" in report
    assert "Рост 20%" in report
