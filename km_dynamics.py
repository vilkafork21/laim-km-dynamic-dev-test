"""Расчет динамики КМ: контрактная агрегация + dev-отчёт с графиком."""

from __future__ import annotations

import base64
from copy import deepcopy
import html
import json
import io
import logging
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from laim_monitoring import aggregate_main_metric, validate_monitoring_metric


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


def compute_cluch_metrics(
    df_with_scores: pd.DataFrame,
    monitoring_metric: dict,
) -> dict[str, object]:
    return aggregate_main_metric(df_with_scores, monitoring_metric)


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


def _report_html(
    name: str | None,
    baseline: float | None,
    current: float | None,
    delta: float | None,
    accuracy: float | None,
    color: str,
    *,
    reason: str,
    assessment_mode: str | None,
    coverage: dict[str, object],
    green_threshold: float = 0.15,
    c_min_threshold: float = 0.25,
) -> str:
    statuses = {
        "green": "В норме",
        "yellow": "Требует внимания",
        "red": "Критично",
        "gray": "Недостаточно данных",
    }
    report_color = color if color in statuses else "gray"
    status = statuses[report_color]
    metric_name = html.escape(str(name or "Ключевая метрика"))
    reason_text = html.escape(reason or "Не указан")
    mode_text = html.escape(assessment_mode or "Не определён")

    def decimal(value: float | None, digits: int) -> str:
        return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")

    def percent(value: float) -> str:
        return f"{value:.1%}".replace(".0%", "%").replace(".", ",")

    if delta is None:
        change_text = "—"
        marker_text = ""
    elif delta > 0:
        change_text = f"−{percent(delta)}"
        marker_text = f"Снижение {percent(delta)}"
    elif delta < 0:
        change_text = f"+{percent(abs(delta))}"
        marker_text = f"Рост {percent(abs(delta))}"
    else:
        change_text = "0%"
        marker_text = "Без изменений"

    total_units = coverage.get("total_units")
    scored_units = coverage.get("scored_units")
    coverage_text = (
        "Не определено"
        if total_units is None and scored_units is None
        else f"{scored_units if scored_units is not None else '—'} / "
        f"{total_units if total_units is not None else '—'}"
    )
    coverage_text = html.escape(coverage_text)

    green_stop = min(100.0, max(0.0, green_threshold * 100.0))
    red_stop = min(100.0, max(green_stop, c_min_threshold * 100.0))
    scale_style = (
        f"--normal-width:{green_stop:.4g}%;"
        f"--attention-width:{red_stop - green_stop:.4g}%;"
        f"--critical-width:{100.0 - red_stop:.4g}%"
    )
    marker_html = ""
    if delta is not None and report_color != "gray":
        marker_position = min(100.0, max(0.0, delta * 100.0))
        marker_edge = (
            " km-report__marker--start"
            if marker_position <= 8.0
            else " km-report__marker--end" if marker_position >= 92.0 else ""
        )
        scale_style += f";--marker-position:{marker_position:.4g}%"
        marker_html = f"""
            <span class="km-report__marker{marker_edge}" aria-label="{marker_text}">
                <span class="km-report__marker-value">{marker_text}</span>
            </span>"""

    return f"""
<style>
.km-report {{
    --ink: #1c1b18;
    --muted: #747168;
    --line: #e2e0d9;
    --paper: #fffefa;
    width: min(760px, calc(100% - 32px));
    margin: 24px auto;
    padding: 42px 46px 34px;
    box-sizing: border-box;
    color: var(--ink);
    background: var(--paper);
    border: 1px solid #dedcd5;
    border-top: 3px solid var(--ink);
    box-shadow: 0 14px 36px -28px rgba(28, 27, 24, .45);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    font-size: 15px;
    line-height: 1.45;
}}
.km-report *, .km-report *::before, .km-report *::after {{ box-sizing: border-box; }}
.km-report--green {{ --status: #4f7a4e; --status-border: #b5c9b4; --status-bg: #f4f8f3; }}
.km-report--yellow {{ --status: #84671f; --status-border: #d9c58c; --status-bg: #fbf8ed; }}
.km-report--red {{ --status: #a54235; --status-border: #d9b3ac; --status-bg: #fbf4f2; }}
.km-report--gray {{ --status: #65635d; --status-border: #cfcdc6; --status-bg: #f5f4f1; }}
.km-report__header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; }}
.km-report__eyebrow,
.km-report__metric-label {{
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .12em;
    text-transform: uppercase;
}}
.km-report__title {{ margin: 8px 0 0; font: 700 32px/1.15 Georgia, serif; letter-spacing: -.01em; }}
.km-report__status {{
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 8px;
    margin-top: 3px;
    padding: 7px 12px;
    color: var(--status);
    background: var(--status-bg);
    border: 1px solid var(--status-border);
    font-size: 13px;
    font-weight: 700;
}}
.km-report__status-mark {{ width: 8px; height: 8px; border-radius: 50%; background: var(--status); }}
.km-report__metric {{ margin-top: 30px; padding-top: 26px; border-top: 1px solid var(--line); }}
.km-report__metric-name {{ margin: 7px 0 0; font-size: 17px; font-weight: 650; overflow-wrap: anywhere; }}
.km-report__facts {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    margin: 28px 0 0;
    border-top: 1px solid var(--ink);
    border-bottom: 1px solid var(--line);
}}
.km-report__fact {{ min-width: 0; padding: 14px 12px 15px 0; }}
.km-report__fact + .km-report__fact {{ padding-left: 14px; border-left: 1px solid var(--line); }}
.km-report__fact dt {{ color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }}
.km-report__fact dd {{ margin: 7px 0 0; font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.km-report__fact--change dd {{ color: var(--status); }}
.km-report__scale-section {{ margin-top: 30px; }}
.km-report__scale-heading {{ display: flex; justify-content: space-between; gap: 16px; align-items: baseline; }}
.km-report__scale-title {{ margin: 0; font-size: 14px; font-weight: 700; }}
.km-report__scale-threshold {{ color: var(--muted); font-size: 12px; text-align: right; }}
.km-report__scale {{ position: relative; margin-top: 34px; }}
.km-report__track {{ display: flex; height: 6px; overflow: hidden; border-radius: 3px; }}
.km-report__zone--normal {{ width: var(--normal-width); background: #8fa88c; }}
.km-report__zone--attention {{ width: var(--attention-width); background: #d1b467; }}
.km-report__zone--critical {{ width: var(--critical-width); background: #dfbbb4; }}
.km-report__marker {{
    position: absolute;
    top: -29px;
    bottom: -7px;
    left: var(--marker-position);
    width: 1px;
    background: var(--ink);
}}
.km-report__marker::after {{
    content: "";
    position: absolute;
    bottom: -3px;
    left: 50%;
    width: 8px;
    height: 8px;
    transform: translateX(-50%);
    border-radius: 50%;
    background: var(--ink);
}}
.km-report__marker-value {{
    position: absolute;
    top: 0;
    left: 0;
    padding: 3px 7px;
    transform: translateX(-50%);
    border-radius: 3px;
    color: #fff;
    background: var(--ink);
    font-size: 12px;
    font-weight: 700;
    line-height: 1.4;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}}
.km-report__marker--start .km-report__marker-value {{ transform: none; }}
.km-report__marker--end .km-report__marker-value {{ transform: translateX(-100%); }}
.km-report__legend {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-top: 20px; }}
.km-report__legend-item {{ min-width: 0; font-size: 12px; }}
.km-report__legend-range {{ display: flex; align-items: center; gap: 7px; font-weight: 700; }}
.km-report__legend-dot {{ width: 8px; height: 8px; flex: 0 0 auto; border-radius: 50%; }}
.km-report__legend-dot--normal {{ background: #688865; }}
.km-report__legend-dot--attention {{ background: #a8842f; }}
.km-report__legend-dot--critical {{ background: #a54235; }}
.km-report__legend-label {{ margin: 2px 0 0 15px; color: var(--muted); }}
.km-report__details {{ margin-top: 28px; border: 1px solid var(--line); }}
.km-report__details summary {{ padding: 13px 18px; cursor: pointer; color: #55524a; font-size: 13px; font-weight: 700; }}
.km-report__technical {{
    display: grid;
    grid-template-columns: minmax(150px, 210px) 1fr;
    gap: 7px 18px;
    margin: 0;
    padding: 14px 18px 18px;
    border-top: 1px solid var(--line);
    color: #55524a;
    font-size: 13px;
}}
.km-report__technical dt {{ color: var(--muted); }}
.km-report__technical dd {{ margin: 0; overflow-wrap: anywhere; }}
@media (max-width: 620px) {{
    .km-report {{ width: calc(100% - 20px); margin: 10px auto; padding: 28px 22px; }}
    .km-report__header {{ display: block; }}
    .km-report__status {{ margin-top: 18px; }}
    .km-report__title {{ font-size: 27px; }}
    .km-report__facts {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .km-report__fact:nth-child(3) {{ padding-left: 0; border-left: 0; border-top: 1px solid var(--line); }}
    .km-report__fact:nth-child(4) {{ border-top: 1px solid var(--line); }}
    .km-report__legend {{ grid-template-columns: 1fr; gap: 10px; }}
    .km-report__technical {{ grid-template-columns: 1fr; gap: 2px; }}
    .km-report__technical dd + dt {{ margin-top: 7px; }}
}}
@media print {{
    .km-report {{ width: 100%; margin: 0; border-right: 0; border-bottom: 0; border-left: 0; box-shadow: none; }}
    .km-report__details {{ display: none; }}
}}
</style>
<article class="km-report km-report--{report_color}" lang="ru">
    <header class="km-report__header">
        <div>
            <div class="km-report__eyebrow">Результат контрольной проверки</div>
            <h1 class="km-report__title">Динамика ключевой метрики</h1>
        </div>
        <div class="km-report__status" role="status">
            <span class="km-report__status-mark" aria-hidden="true"></span>
            <span>{status}</span>
        </div>
    </header>

    <section class="km-report__metric" aria-labelledby="km-report-metric">
        <div class="km-report__metric-label">Метрика</div>
        <h2 class="km-report__metric-name" id="km-report-metric">{metric_name}</h2>
    </section>

    <dl class="km-report__facts">
        <div class="km-report__fact">
            <dt>Исходное значение</dt>
            <dd>{decimal(baseline, 2)}</dd>
        </div>
        <div class="km-report__fact">
            <dt>Текущее значение</dt>
            <dd>{decimal(current, 2)}</dd>
        </div>
        <div class="km-report__fact km-report__fact--change">
            <dt>Изменение</dt>
            <dd>{change_text}</dd>
        </div>
        <div class="km-report__fact">
            <dt>Покрытие данных</dt>
            <dd>{coverage_text}</dd>
        </div>
    </dl>

    <section class="km-report__scale-section" aria-labelledby="km-report-scale">
        <div class="km-report__scale-heading">
            <h2 class="km-report__scale-title" id="km-report-scale">Снижение относительно исходного уровня</h2>
            <div class="km-report__scale-threshold">Критический порог: {percent(c_min_threshold)}</div>
        </div>
        <div class="km-report__scale" style="{scale_style}">
            <div class="km-report__track" aria-label="Зоны снижения ключевой метрики">
                <span class="km-report__zone--normal"></span>
                <span class="km-report__zone--attention"></span>
                <span class="km-report__zone--critical"></span>
            </div>
            {marker_html}
        </div>
        <div class="km-report__legend">
            <div class="km-report__legend-item">
                <div class="km-report__legend-range"><span class="km-report__legend-dot km-report__legend-dot--normal" aria-hidden="true"></span>≤ {percent(green_threshold)}</div>
                <div class="km-report__legend-label">В норме</div>
            </div>
            <div class="km-report__legend-item">
                <div class="km-report__legend-range"><span class="km-report__legend-dot km-report__legend-dot--attention" aria-hidden="true"></span>{percent(green_threshold)}–{percent(c_min_threshold)}</div>
                <div class="km-report__legend-label">Требует внимания</div>
            </div>
            <div class="km-report__legend-item">
                <div class="km-report__legend-range"><span class="km-report__legend-dot km-report__legend-dot--critical" aria-hidden="true"></span>≥ {percent(c_min_threshold)}</div>
                <div class="km-report__legend-label">Критично</div>
            </div>
        </div>
    </section>

    <details class="km-report__details">
        <summary>Параметры расчёта</summary>
        <dl class="km-report__technical">
            <dt>Комментарий</dt><dd>{reason_text}</dd>
            <dt>Режим оценки</dt><dd>{mode_text}</dd>
            <dt>Точность калибровки</dt><dd>{decimal(accuracy, 3)}</dd>
            <dt>Формула изменения</dt><dd>(исходное − текущее) / исходное × 100%</dd>
        </dl>
    </details>
</article>
""".strip()


def _not_computable_result(
    contract: dict,
    *,
    reason: str,
    acc_auto: float | None,
    c_min_threshold: float,
    green_threshold: float,
    status_details: dict | None = None,
) -> dict[str, object]:
    baseline_payload = contract.get("baseline")
    baseline_value = (
        baseline_payload.get("value")
        if isinstance(baseline_payload, dict)
        else None
    )
    baseline = None if baseline_value is None else float(baseline_value)
    name = contract.get("name")
    details = status_details or {}
    metric_details = {
        "name": name,
        "КМ на мониторинге": None,
        "КМ на первичной валидации": baseline,
        "Дельта КМ": None,
        "Порог минимальной дельты КМ": c_min_threshold,
        "coverage": {
            "total_units": details.get("total_units"),
            "scored_units": details.get("scored_units"),
            "excluded_units": None,
            "weight_sum": None,
        },
    }
    return {
        "status": "not_computable",
        "trafic_light": "gray",
        "reason": reason,
        "kluch_metric": metric_details,
        "html_plot": _report_html(
            name,
            baseline,
            None,
            None,
            acc_auto,
            "gray",
            reason=reason,
            assessment_mode=contract.get("assessment_mode"),
            coverage=metric_details["coverage"],
            green_threshold=green_threshold,
            c_min_threshold=c_min_threshold,
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
    c_min_threshold: float = 0.25,
    green_threshold: float = 0.15,
) -> dict[str, object]:
    contract = validate_monitoring_metric(
        _km_compatible_metric(monitoring_metric),
        require_computed=False,
    )
    if contract["status"] != "computed":
        return _not_computable_result(
            contract,
            reason=contract.get("reason", "monitoring_metric невычислим"),
            acc_auto=acc_auto,
            c_min_threshold=c_min_threshold,
            green_threshold=green_threshold,
        )

    if assessment_result is not None and not isinstance(assessment_result, dict):
        raise TypeError("assessment_result должен быть объектом")
    if assessment_result is not None and assessment_result.get("status") != "computed":
        reason = assessment_result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = f"assessment_status={assessment_result.get('status')!r}"
        return _not_computable_result(
            contract,
            reason=reason,
            acc_auto=acc_auto,
            c_min_threshold=c_min_threshold,
            green_threshold=green_threshold,
            status_details=assessment_result,
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
            return _not_computable_result(
                contract,
                reason=reason,
                acc_auto=acc_auto,
                c_min_threshold=c_min_threshold,
                green_threshold=green_threshold,
                status_details={
                    "total_units": len(scored_df),
                    "scored_units": 0,
                },
            )
    elif not isinstance(scored_df, pd.DataFrame) or "main_metric" not in scored_df:
        return _not_computable_result(
            contract,
            reason=(
                "scored_df не содержит main_metric; подключите "
                "kriteria-selector.metric_spec к одноимённому порту KM"
            ),
            acc_auto=acc_auto,
            c_min_threshold=c_min_threshold,
            green_threshold=green_threshold,
            status_details={
                "total_units": (
                    len(scored_df) if isinstance(scored_df, pd.DataFrame) else None
                ),
                "scored_units": 0,
            },
        )

    override = _baseline_override_value(perv_validation_km)
    baseline = override if override is not None else float(contract["baseline"]["value"])
    monitoring = compute_cluch_metrics(scored_df, contract)
    current = float(monitoring["value"])

    if baseline == 0:
        delta = 0.0 if current == 0 else None
    else:
        delta = (baseline - current) / baseline

    if delta is None:
        color = "gray"
        reason = "Baseline КМ равен нулю, относительная динамика не определена."
    elif delta >= c_min_threshold:
        color = "red"
        reason = "Снижение КМ больше допустимого отклонения."
    elif delta <= green_threshold:
        color = "green"
        reason = "Снижение КМ находится в зеленой зоне."
    else:
        color = "yellow"
        reason = "Снижение КМ находится в желтой зоне."

    logging.info(
        "KM dynamics: baseline=%s current=%s delta=%s color=%s",
        baseline,
        current,
        delta,
        color,
    )
    metric_details = {
        "name": contract["name"],
        "КМ на мониторинге": current,
        "КМ на первичной валидации": baseline,
        "Дельта КМ": delta,
        "Порог минимальной дельты КМ": c_min_threshold,
        "coverage": {
            key: monitoring[key]
            for key in ("total_units", "scored_units", "excluded_units", "weight_sum")
        },
    }
    return {
        "status": "computed",
        "trafic_light": color,
        "reason": reason,
        "kluch_metric": metric_details,
        "html_plot": _report_html(
            contract["name"],
            baseline,
            current,
            delta,
            acc_auto,
            color,
            reason=reason,
            assessment_mode=contract["assessment_mode"],
            coverage=metric_details["coverage"],
            green_threshold=green_threshold,
            c_min_threshold=c_min_threshold,
        ),
    }
