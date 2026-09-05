"""HTML-отчёт и график теста динамики КМ."""

from __future__ import annotations

import base64
import html
import io

import pandas as pd

from verdict import drop


def _helpers():
    from html_report_helper import (
        display_semaphore,
        show_criteria_semaphore,
    )
    return display_semaphore, show_criteria_semaphore


_TABLE_STYLES = [
    {
        "selector": "th",
        "props": [
            ("background-color", "#f5f5f5"),
            ("text-align", "center"),
            ("border", "1px solid #ddd"),
            ("padding", "5px"),
        ],
    },
    {
        "selector": "td",
        "props": [
            ("text-align", "left"),
            ("border", "1px solid #ddd"),
            ("padding", "5px"),
        ],
    },
    {
        "selector": "",
        "props": [("border-collapse", "collapse"), ("border", "1px solid black")],
    },
]

_WIDGET_COLOR = {"yellow": "yellow", "gray": "grey"}


def plot_km_dynamics(
    name: str | None,
    baseline: float,
    current: float,
    accuracy: float | None,
    thresholds: dict[str, object],
) -> str:
    """График динамики КМ: валидация против мониторинга, пороговые зоны в
    выбранных единицах снижения и дельта; возвращает html c base64-изображением."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    GREEN, AMBER, RED, NEUTRAL = "#2E9E5B", "#E0A800", "#D64545", "#5B6B7B"
    unit = str(thresholds["unit"])
    km_delta = drop(baseline, current, unit)
    if unit == "absolute":
        y_green = baseline - float(thresholds["green"])
        y_red = baseline - float(thresholds["red"])
    else:
        y_green = baseline * (1.0 - float(thresholds["green"]))
        y_red = baseline * (1.0 - float(thresholds["red"]))
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


def report_html(
    name: str | None,
    baseline: float | None,
    current: float | None,
    delta: float | None,
    accuracy: float | None,
    color: str,
    *,
    reason: str,
    assessment_mode: str | None,
    thresholds: dict[str, object],
    interval_: object | None = None,
    provenance: dict[str, object] | None = None,
) -> str:
    display_semaphore, show_criteria_semaphore = _helpers()
    unit_word = "в единицах шкалы" if thresholds["unit"] == "absolute" else "долей от базы"
    c_min = thresholds.get("c_min")
    c_min_text = "не задан" if c_min is None else f"{float(c_min):.6g}"
    criteria = show_criteria_semaphore(
        f"Пессимистичная граница интервала: снижение КМ не более {thresholds['green']:g} "
        f"({unit_word}) и не ниже C_MIN",
        "Снижение или нарушение C_MIN возможно, но интервалом не подтверждено",
        f"Оптимистичная граница интервала: снижение КМ не менее {thresholds['red']:g} "
        f"({unit_word}) либо интервал целиком ниже C_MIN",
        "Единиц меньше минимума, есть неизвестные оценки, база или оценка невычислимы",
        _TABLE_STYLES,
    ).to_html(border=0, classes="table")

    details = provenance or {}
    units_text = (
        "не определено"
        if details.get("total_units") is None
        else f"{details.get('scored_units')} оценено / {details.get('refused_units')} отказов / "
        f"{details.get('total_units')} всего"
    )
    bounds = details.get("completion_bounds")
    missing_text = ""
    if details.get("refused_units") and bounds is not None:
        observed = details.get("observed_mean")
        observed_text = "не определено" if observed is None else f"{observed:.6g}"
        missing_text = (
            "<h3>Неизвестные оценки в полученном наборе</h3>"
            f"<p>Среднее оценённой части: {observed_text}. "
            f"Масса отказов: {details['refused_weight']:.6g} из {details['total_weight']:.6g} "
            f"({details['refused_weight_share']:.1%}).</p>"
            f"<p>Границы заполнения: [{bounds['lower']:.6g}; {bounds['upper']:.6g}]. "
            "Это не доверительный интервал и не оценка всего отчётного периода. "
            "Известные оценки здесь считаются фиксированными; неизвестным допускается "
            "любое значение утверждённой шкалы. Ошибка судьи и неотобранные обращения "
            "этими границами не покрываются.</p>"
        )
    interval_text = (
        "не определён"
        if interval_ is None
        else f"[{interval_.lower:.4f}; {interval_.upper:.4f}] ({interval_.method})"
    )
    semaphore_html = display_semaphore(_WIDGET_COLOR.get(color, color), return_html=True)
    rows = pd.DataFrame(
        {
            "Показатель": [
                "Метрика",
                "Значение КМ на валидации",
                "Значение КМ на мониторинге",
                "Интервал КМ на мониторинге",
                "Снижение КМ",
                "Единицы снижения",
                "C_MIN",
                "Точность автоассесора (калибровка)",
                "Режим оценки",
                "Единицы оценки",
                "Комментарий",
                "Результат теста",
            ],
            "Значение": [
                html.escape("не определена" if name is None else str(name)),
                "не определено" if baseline is None else f"{baseline:.6g}",
                "не определено" if current is None else f"{current:.6g}",
                interval_text,
                "не определено" if delta is None else f"{delta:.4f}",
                html.escape(str(thresholds["unit"])),
                c_min_text,
                "не определена" if accuracy is None else f"{accuracy:.3f}",
                html.escape(assessment_mode or "не определён"),
                units_text,
                html.escape(reason),
                semaphore_html,
            ],
        }
    )
    try:
        results = rows.style.hide().set_table_styles(_TABLE_STYLES)
    except AttributeError:
        results = rows.style.hide_index().set_table_styles(_TABLE_STYLES)
    results_html = results.to_html(border=0, classes="table")

    plot_html = ""
    if baseline is not None and current is not None:
        plot_html = plot_km_dynamics(name, baseline, current, accuracy, thresholds)

    return f"""
<h2 style="text-align: center;">Тест на динамику ключевой метрики</h2>
<p style="text-align: left;"><b>Цель теста</b></p>
<p style="text-align: left;">Оценить изменение ключевой метрики качества агента на мониторинговых данных относительно значения первичной валидации.</p>
<p style="text-align: left;">Оценки мониторинговых диалогов выставляет автоассесор, откалиброванный на эталонной разметке тестовой корзины.</p>
<p style="text-align: left;"><b>Алгоритм расчета</b></p>
<ol style="text-align: left; margin-left: 20px; padding-left: 20px;">
    <li style="text-align: left;">Единицы оценки формируются по assessment_mode контракта, ключевая метрика агрегируется по правилам monitoring_metric.</li>
    <li style="text-align: left;">При неизвестных оценках сохраняются их веса и границы заполнения; вывод о КМ периода не формируется. Недобор оценённых единиц также запрещает вывод.</li>
    <li style="text-align: left;">Строится интервал КМ мониторинга; снижение к КМ первичной валидации и минимальный уровень C_MIN проверяются по границам интервала.</li>
</ol>
<p style="text-align: left;"><b>Критерии выставления светофора</b></p>
<div style="text-align: left; width: 100%;">{criteria}</div><br>
<p style="text-align: left;"><b>Результаты теста</b></p>
<div style="text-align: left; width: 100%;">{results_html}</div><br>
{missing_text}
{plot_html}
""".strip()
