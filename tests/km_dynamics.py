"""
Тест на динамику ключевой метрики (КМ).

Модуль содержит функции для расчёта ключевой метрики из размеченных данных,
вычисления дельты между валидацией и мониторингом, определения цвета светофора
и генерации визуализации динамики КМ.
"""

import base64
import io
import logging

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
from utils import transform_to_int


# =============================================================================
# Расчёт ключевой метрики
# =============================================================================

def compute_cluch_metrics(df_with_scores: pd.DataFrame | None, main_metric: str):
    """
    Расчёт ключевой метрики для агента.

    Args:
        df_with_scores: DataFrame с размеченными данными (столбец main_metric содержит метки)
        main_metric: Название столбца с целевой метрикой

    Returns:
        dict: Словарь с ключами 'name' (название метрики) и 'value' (значение от 0 до 1)
    """
    cluch_metric_value = (
        df_with_scores[main_metric].apply(
            lambda x: transform_to_int(x)).dropna().mean()
    )
    cluch_metric = {
        "name": "Доля успешных диалогов с правильно составленным резюме по цели и плану",
        "value": cluch_metric_value,
    }
    return cluch_metric


# =============================================================================
# Визуализация динамики КМ
# =============================================================================

def plot_km_dynamics(
    acc_auto: float,
    perv_validation_km: dict[str, str | float],
    monitoring_km: dict[str, str | float],
    green_threshold: float,
    c_min: float,
):
    """
    Построение графика динамики ключевой метрики (КМ).

    Чистый, интуитивный вид: столбец валидации — нейтральный (референс),
    столбец мониторинга — окрашен по вердикту (зелёный/жёлтый/красный).
    Лёгкие фоновые зоны + пороговые линии с подписями справа.
    Дельта показана направляющей и двунаправленной стрелкой между уровнями.
    Возвращает HTML-строку с встроенным изображением (base64).
    """
    GREEN, AMBER, RED, NEUTRAL = "#2E9E5B", "#E0A800", "#D64545", "#5B6B7B"

    km_val = float(perv_validation_km["value"])
    km_mon = float(monitoring_km["value"])
    km_delta = (km_val - km_mon) / km_val if km_val else float("nan")

    # Пороги в единицах метрики
    y_green = km_val * (1.0 - green_threshold)   # >= этого — зелёная зона
    y_red = km_val * (1.0 - c_min)               # <= этого — красная зона

    # Вердикт по уровню мониторинга
    if km_mon >= y_green:
        verdict, vcolor = "В норме", GREEN
    elif km_mon <= y_red:
        verdict, vcolor = "Критично", RED
    else:
        verdict, vcolor = "Внимание", AMBER

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", alpha=0.25, linestyle="--", linewidth=0.8)

    y_top = max(1.0, km_val, km_mon)

    # Лёгкие фоновые зоны (контекст, не перетягивают внимание)
    ax.axhspan(y_green, y_top, color=GREEN, alpha=0.06, zorder=0)
    ax.axhspan(y_red, y_green, color=AMBER, alpha=0.06, zorder=0)
    ax.axhspan(0, y_red, color=RED, alpha=0.06, zorder=0)

    # Пороговые линии + подписи справа.
    # ФИКС: подпись НЕ по центру линии (va="center" → линия перечёркивала текст),
    # а смещена от линии (зелёная — над линией, красная — под линией) + белая
    # полупрозрачная подложка, чтобы не сливалась с фоновыми зонами.
    _off = max(0.012, y_top * 0.015)   # отступ подписи от линии
    for y, c, txt, _va, _dy in [
        (y_green, GREEN, f"Порог «в норме» ≥ {y_green:.3f}", "bottom", +_off),
        (y_red, RED, f"Порог «критично» ≤ {y_red:.3f}", "top", -_off),
    ]:
        ax.axhline(y, color=c, lw=1.4, alpha=0.75, zorder=1)
        ax.text(1.58, y + _dy, txt, color=c, fontsize=9.5, va=_va, ha="left",
                fontweight="bold", zorder=4,
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                          edgecolor="none", alpha=0.85))

    # Столбцы: валидация — нейтральная, мониторинг — цвет вердикта
    positions = [0, 1]
    labels = ["Первичная валидация", "Мониторинг"]
    vals = [km_val, km_mon]
    colors = [NEUTRAL, vcolor]
    ax.bar(positions, vals, width=0.46, color=colors, edgecolor="white", linewidth=1.5, zorder=3)

    for x, v in zip(positions, vals):
        ax.text(x, v + 0.018, f"{v:.3f}", ha="center", va="bottom",
                fontsize=13, fontweight="bold", color="#222222", zorder=4)

    # Дельта: направляющая на уровне валидации + двунаправленная стрелка
    ax.hlines(km_val, positions[0], positions[1], color="#888888", lw=1.2, linestyle=":", zorder=2)
    dx = positions[1] - 0.30
    y_lo, y_hi = min(km_val, km_mon), max(km_val, km_mon)
    if abs(y_hi - y_lo) > 1e-6:
        ax.annotate("", xy=(dx, y_hi), xytext=(dx, y_lo),
                    arrowprops=dict(arrowstyle="<->", color=vcolor, lw=2.2), zorder=4)
    arrow_dir = "↓" if km_delta > 0 else ("↑" if km_delta < 0 else "→")
    ax.text(dx - 0.05, (y_lo + y_hi) / 2.0, f"Δ = {km_delta:.3f}\n{arrow_dir} {verdict}",
            ha="right", va="center", fontsize=11, fontweight="bold", color=vcolor,
            bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor=vcolor, lw=1.4),
            zorder=5)

    # Оси и заголовок
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=12, fontweight="bold")
    ax.set_xlim(-0.6, 2.35)
    ax.set_ylim(0, y_top * 1.10)
    ax.set_ylabel("Значение КМ", fontsize=12, fontweight="bold")
    metric_name = str(perv_validation_km.get("name", "Ключевая метрика"))
    ax.set_title("Динамика ключевой метрики", fontsize=16, fontweight="bold", pad=26)
    # Подзаголовок: метрика + сводка
    ax.text(0.0, 1.045,
            metric_name,
            transform=ax.transAxes, fontsize=10.5, color="#444444", ha="left", style="italic")
    # Подпись-сводка НЕЙТРАЛЬНАЯ (#333): точность автоасессора НЕ красим в цвет
    # вердикта — у судьи точность высокая, а красный цвет вводил в заблуждение.
    # Вердикт и так виден по цвету столбца «Мониторинг» и боксу Δ.
    ax.text(0.0, 1.012,
            f"Точность автоасессора: {acc_auto:.3f}    •    КМ: {km_val:.3f} → {km_mon:.3f}    •    Δ = {km_delta:.3f}  ({verdict})",
            transform=ax.transAxes, fontsize=10.5, color="#333333", ha="left", fontweight="bold")

    fig.tight_layout()

    stringIObytes = io.BytesIO()
    plt.savefig(stringIObytes, dpi=200, bbox_inches="tight")
    stringIObytes.seek(0)
    plot_base64 = base64.b64encode(stringIObytes.read()).decode("ascii")
    plt.close(fig)
    plot_html = f"""<!DOCTYPE html>
<img src="data:image/png;base64,{plot_base64}" alt="КМ динамика">
"""
    return plot_html


# =============================================================================
# Основной тест
# =============================================================================

def km_dynamics_test(
    acc_auto: float,
    main_metric: str,
    perv_validation_km: dict[str, str | int | float],
    scored_df: pd.DataFrame,
    c_min_threshold: float = 0.25,
    green_threshold: float = 0.15,
):
    """
    Тест на динамику ключевой метрики.

    Вычисляет значение КМ на мониторинге, сравнивает с КМ на валидации,
    определяет цвет светофора на основе порогов и генерирует визуализацию.

    Args:
        acc_auto: Значение автоасессора
        main_metric: Название столбца с целевой метрикой в scored_df
        perv_validation_km: Словарь с КМ первичной валидации (ключи: 'name', 'value')
        scored_df: DataFrame с данными для мониторинга
        c_min_threshold: Порог для красного светофора (по умолчанию 0.25 = 25pp)
        green_threshold: Порог для зелёного светофора (по умолчанию 0.15 = 15pp)

    Returns:
        dict: Результат теста с ключами:
            - trafic_light: цвет светофора ('green', 'yellow', 'red', 'gray')
            - reason: текстовое описание результата
            - kluch_metric: словарь с метриками
            - html_plot: HTML с графиком
    """
    logging.info(f"""
C_min: {c_min_threshold}
Green: {green_threshold}
""")
    trafic_light = "gray"
    monitoring_km = compute_cluch_metrics(
        df_with_scores=scored_df, main_metric=main_metric
    )
    km_delta = (perv_validation_km["value"] -
                monitoring_km["value"]) / perv_validation_km["value"]
    logging.info(f"Дельта КМ: {km_delta}")

    reason = ""
    formal_reason = ""
    if km_delta >= c_min_threshold:
        trafic_light = "red"
        reason += (
            "Различие КМ на валидации и мониторинге больше допустимого отклонения. \n"
        )

    elif km_delta <= green_threshold:
        trafic_light = "green"
        reason += (
            "Различие КМ на валидации и мониторинге в пределах разрешенной зелёной зоны."
        )
    else:
        trafic_light = "yellow"
        reason += (
            "Различие КМ на валидации и мониторинге в пределах разрешенной желтой зоны."
        )
    formal_reason += f"Различие КМ на валидации и мониторинге: {round(km_delta, 3)}.\n"

    html_plot = plot_km_dynamics(
        acc_auto=acc_auto,
        perv_validation_km=perv_validation_km,
        monitoring_km=monitoring_km,
        green_threshold=green_threshold,
        c_min=c_min_threshold,
    )
    kluch_metric_dict = monitoring_km
    kluch_metric_dict["КМ на мониторинге"] = kluch_metric_dict["value"]
    del kluch_metric_dict["value"]
    kluch_metric_dict["КМ на первичной валидации"] = perv_validation_km["value"]
    kluch_metric_dict["Дельта КМ"] = km_delta
    kluch_metric_dict["Порог минимальной дельты КМ"] = c_min_threshold

    return {
        "trafic_light": trafic_light,
        "reason": reason,
        "kluch_metric": kluch_metric_dict,
        "html_plot": html_plot,
    }
