"""Тест динамики КМ: значение на мониторинге против baseline контракта.

Формулу и единицы оценки считает общий пакет laim_monitoring — тот же код,
которым адаптер воспроизвёл baseline на эталонной корзине. Здесь только
сравнение, светофор и отчёт.
"""

from __future__ import annotations

import base64
import html
import io
import logging
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from laim_monitoring import (
    MonitoringContractError,
    aggregate_main_metric,
    judge_score_contract,
    validate_monitoring_metric,
)

GREEN_THRESHOLD = 0.15   # относительное снижение КМ до этого — зелёный
RED_THRESHOLD = 0.25     # от этого и выше — красный

_TABLE_STYLES = [
    {"selector": "th", "props": [("background-color", "#f5f5f5"), ("text-align", "center"),
                                 ("border", "1px solid #ddd"), ("padding", "5px")]},
    {"selector": "td", "props": [("text-align", "left"), ("border", "1px solid #ddd"), ("padding", "5px")]},
    {"selector": "", "props": [("border-collapse", "collapse"), ("border", "1px solid black")]},
]
_WIDGET_COLOR = {"yellow": "yellow", "gray": "grey"}


def _helpers():
    from html_report_helper import display_semaphore, show_criteria_semaphore
    return display_semaphore, show_criteria_semaphore


def plot_km_dynamics(
    name: str | None,
    baseline: float,
    current: float,
    accuracy: float | None,
    green_threshold: float,
    c_min_threshold: float,
) -> str:
    """График динамики КМ из dev-версии ноды: валидация против мониторинга,
    пороговые зоны и дельта; возвращает html c base64-изображением."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    GREEN, AMBER, RED, NEUTRAL = "#2E9E5B", "#E0A800", "#D64545", "#5B6B7B"
    km_delta = (baseline - current) / baseline if baseline else float("nan")
    y_green = baseline * (1.0 - green_threshold)
    y_red = baseline * (1.0 - c_min_threshold)
    if current >= y_green:
        verdict, vcolor = "В норме", GREEN
    elif current <= y_red:
        verdict, vcolor = "Критично", RED
    else:
        verdict, vcolor = "Внимание", AMBER

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", alpha=0.25, linestyle="--", linewidth=0.8)
    y_top = max(1.0, baseline, current)
    ax.axhspan(y_green, y_top, color=GREEN, alpha=0.06, zorder=0)
    ax.axhspan(y_red, y_green, color=AMBER, alpha=0.06, zorder=0)
    ax.axhspan(0, y_red, color=RED, alpha=0.06, zorder=0)
    for y, c, txt in [
        (y_green, GREEN, f"Порог «в норме» ≥ {y_green:.3f}"),
        (y_red, RED, f"Порог «критично» ≤ {y_red:.3f}"),
    ]:
        ax.axhline(y, color=c, lw=1.4, alpha=0.75, zorder=1)
        ax.text(1.58, y, txt, color=c, fontsize=9.5, va="center", ha="left", fontweight="bold")
    positions = [0, 1]
    values = [baseline, current]
    ax.bar(
        positions, values, width=0.46, color=[NEUTRAL, vcolor],
        edgecolor="white", linewidth=1.5, zorder=3,
    )
    for x, v in zip(positions, values):
        ax.text(
            x, v + 0.018, f"{v:.3f}", ha="center", va="bottom",
            fontsize=13, fontweight="bold", color="#222222", zorder=4,
        )
    ax.hlines(baseline, positions[0], positions[1], color="#888888", lw=1.2, linestyle=":", zorder=2)
    dx = positions[1] - 0.30
    y_lo, y_hi = min(baseline, current), max(baseline, current)
    if abs(y_hi - y_lo) > 1e-6:
        ax.annotate(
            "", xy=(dx, y_hi), xytext=(dx, y_lo),
            arrowprops=dict(arrowstyle="<->", color=vcolor, lw=2.2), zorder=4,
        )
    arrow = "↓" if km_delta > 0 else ("↑" if km_delta < 0 else "→")
    ax.text(
        dx - 0.05, (y_lo + y_hi) / 2.0, f"Δ = {km_delta:.3f}\n{arrow} {verdict}",
        ha="right", va="center", fontsize=11, fontweight="bold", color=vcolor,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor=vcolor, lw=1.4),
        zorder=5,
    )
    ax.set_xticks(positions)
    ax.set_xticklabels(["Первичная валидация", "Мониторинг"], fontsize=12, fontweight="bold")
    ax.set_xlim(-0.6, 2.35)
    ax.set_ylim(0, y_top * 1.10)
    ax.set_ylabel("Значение КМ", fontsize=12, fontweight="bold")
    ax.set_title("Динамика ключевой метрики", fontsize=16, fontweight="bold", pad=26)
    ax.text(
        0.0, 1.045, str(name or "Ключевая метрика"),
        transform=ax.transAxes, fontsize=10.5, color="#444444", ha="left", style="italic",
    )
    accuracy_part = "" if accuracy is None else f"Точность автоасессора: {accuracy:.3f}    •    "
    ax.text(
        0.0, 1.012,
        f"{accuracy_part}КМ: {baseline:.3f} → {current:.3f}    •    Δ = {km_delta:.3f}  ({verdict})",
        transform=ax.transAxes, fontsize=10.5, color=vcolor, ha="left", fontweight="bold",
    )
    fig.tight_layout()
    buffer = io.BytesIO()
    plt.savefig(buffer, dpi=200, bbox_inches="tight")
    buffer.seek(0)
    plot_base64 = base64.b64encode(buffer.read()).decode("ascii")
    plt.close(fig)
    return f'<img src="data:image/png;base64,{plot_base64}" alt="Динамика КМ" style="max-width:100%;">'


def _report_html(details: dict, color: str, reason: str, accuracy: float | None, assessment_mode: str | None) -> str:
    display_semaphore, show_criteria_semaphore = _helpers()
    criteria = show_criteria_semaphore(
        f"Относительное снижение КМ не более {GREEN_THRESHOLD:.0%}",
        f"Относительное снижение КМ от {GREEN_THRESHOLD:.0%} до {RED_THRESHOLD:.0%}",
        f"Относительное снижение КМ более {RED_THRESHOLD:.0%}",
        "КМ или оценка ассесора невычислимы",
        _TABLE_STYLES,
    ).to_html(border=0, classes="table")
    coverage = details["coverage"]
    scored, total = coverage.get("scored_units"), coverage.get("total_units")
    baseline, current, delta = details["baseline"], details["monitoring"], details["delta"]

    def num(value, fmt):
        return "не определено" if value is None else format(value, fmt)

    rows = pd.DataFrame({
        "Показатель": [
            "Метрика", "Формула КМ", "Значение КМ на валидации", "Значение КМ на мониторинге",
            "Относительное снижение", "Точность автоассесора (калибровка)", "Режим оценки",
            "Покрытие, scored / total", "Комментарий", "Результат теста",
        ],
        "Значение": [
            html.escape(details["name"] or "не определена"),
            html.escape(details["formula"] or "не определена"),
            num(baseline, ".6g"), num(current, ".6g"), num(delta, ".1%"), num(accuracy, ".3f"),
            html.escape(assessment_mode or "не определён"),
            f"{'?' if scored is None else scored} / {'?' if total is None else total}",
            html.escape(reason),
            display_semaphore(_WIDGET_COLOR.get(color, color), return_html=True),
        ],
    })
    try:
        table = rows.style.hide().set_table_styles(_TABLE_STYLES)
    except AttributeError:
        table = rows.style.hide_index().set_table_styles(_TABLE_STYLES)
    plot_html = ""
    if baseline is not None and current is not None:
        plot_html = plot_km_dynamics(details["name"], baseline, current, accuracy, GREEN_THRESHOLD, RED_THRESHOLD)
    return f"""
<h2 style="text-align: center;">Тест на динамику ключевой метрики</h2>
<p style="text-align: left;"><b>Цель теста</b></p>
<p style="text-align: left;">Оценить изменение ключевой метрики качества агента на мониторинговых данных относительно значения первичной валидации.</p>
<p style="text-align: left;">Разметку мониторинга выставляет автоассесор, откалиброванный на эталонной корзине; КМ считается формулой контракта — той же, которой адаптер воспроизвёл значение отчёта о валидации на корзине.</p>
<p style="text-align: left;"><b>Критерии выставления светофора</b></p>
<div style="text-align: left; width: 100%;">{criteria}</div><br>
<p style="text-align: left;"><b>Результаты теста</b></p>
<div style="text-align: left; width: 100%;">{table.to_html(border=0, classes="table")}</div><br>
{plot_html}
""".strip()


def _result(contract: dict, color: str, reason: str, details: dict, acc_auto: float | None) -> dict:
    return {
        "status": "computed" if color != "gray" else "not_computable",
        "color": color,
        "reason": reason,
        "details": details,
        "html_plot": _report_html(details, color, reason, acc_auto, contract.get("assessment_mode")),
    }


def _details(contract: dict, monitoring: dict | None = None, delta: float | None = None) -> dict:
    baseline = (contract.get("baseline") or {}).get("value")
    coverage = {key: (monitoring or {}).get(key) for key in ("total_units", "scored_units", "excluded_units", "weight_sum")}
    return {
        "name": contract.get("metric_name"),
        "formula": (monitoring or {}).get("formula") or contract.get("formula"),
        "baseline": None if baseline is None else float(baseline),
        "monitoring": None if monitoring is None else float(monitoring["value"]),
        "delta": delta,
        "red_threshold": RED_THRESHOLD,
        "coverage": coverage,
    }


def km_dynamics_test(
    acc_auto: float | None,
    monitoring_metric: dict,
    scored_df: pd.DataFrame,
    assessment_result: dict | None = None,
    min_units: int = 30,
) -> dict[str, object]:
    """Светофор по относительному снижению КМ мониторинга к baseline контракта.

    Серый (не цвет), когда сравнивать нечего или нельзя: контракт не вычислен,
    ассесор отказался, входов формулы нет в scored_df, единиц меньше min_units,
    baseline равен нулю. Контракт с невоспроизведённым baseline отвергается
    validate_monitoring_metric и до сюда не доходит.
    """
    contract = validate_monitoring_metric(monitoring_metric, require_computed=False)

    def gray(reason: str, monitoring: dict | None = None) -> dict:
        return _result(contract, "gray", reason, _details(contract, monitoring), acc_auto)

    if contract["status"] != "computed":
        return gray(contract.get("reason", "monitoring_metric невычислим"))
    if assessment_result is not None and not isinstance(assessment_result, dict):
        raise TypeError("assessment_result должен быть объектом")
    if assessment_result is not None and assessment_result.get("status") != "computed":
        reason = assessment_result.get("reason")
        return gray(reason if isinstance(reason, str) and reason.strip()
                    else f"assessment_status={assessment_result.get('status')!r}")
    if not isinstance(scored_df, pd.DataFrame) or scored_df.empty:
        return gray("scored_df пуст: ассесор не разметил мониторинг")

    # Если судья ставил готовый score (формула отчёта на трейсах неприменима),
    # считаем ровно то, что он поставил, тем же контрактом, что и ассесор.
    km_contract = (
        judge_score_contract(contract)
        if (assessment_result or {}).get("scoring_semantics") == "judge_final_score"
        else contract
    )
    try:
        monitoring = aggregate_main_metric(scored_df, km_contract)
    except MonitoringContractError as exc:
        return gray(str(exc))
    if monitoring["scored_units"] < min_units:
        return gray(
            f"Оценено единиц {monitoring['scored_units']} < min_units={min_units}: недостаточно данных",
            monitoring,
        )

    baseline, current = float(contract["baseline"]["value"]), float(monitoring["value"])
    if baseline == 0:
        return gray("Baseline КМ равен нулю, относительная динамика не определена.", monitoring)
    delta = (baseline - current) / baseline
    if delta >= RED_THRESHOLD:
        color, reason = "red", "Снижение КМ больше допустимого отклонения."
    elif delta <= GREEN_THRESHOLD:
        color, reason = "green", "Снижение КМ находится в зеленой зоне."
    else:
        color, reason = "yellow", "Снижение КМ находится в желтой зоне."
    logging.info("KM dynamics: baseline=%s current=%s delta=%s color=%s", baseline, current, delta, color)
    return _result(contract, color, reason, _details(contract, monitoring, delta), acc_auto)
