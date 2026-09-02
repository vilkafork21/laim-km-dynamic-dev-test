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
    display_semaphore, show_criteria_semaphore = _helpers()
    criteria = show_criteria_semaphore(
        f"Относительное снижение КМ не более {green_threshold:.0%}",
        f"Относительное снижение КМ от {green_threshold:.0%} до {c_min_threshold:.0%}",
        f"Относительное снижение КМ более {c_min_threshold:.0%}",
        "КМ или оценка ассесора невычислимы",
        _TABLE_STYLES,
    ).to_html(border=0, classes="table")

    total_units = coverage.get("total_units")
    scored_units = coverage.get("scored_units")
    coverage_text = (
        "не определено"
        if total_units is None and scored_units is None
        else f"{scored_units if scored_units is not None else '?'} / "
        f"{total_units if total_units is not None else '?'}"
    )
    semaphore_html = display_semaphore(_WIDGET_COLOR.get(color, color), return_html=True)
    rows = pd.DataFrame(
        {
            "Показатель": [
                "Метрика",
                "Значение КМ на валидации",
                "Значение КМ на мониторинге",
                "Относительное снижение",
                "Точность автоассесора (калибровка)",
                "Режим оценки",
                "Покрытие, scored / total",
                "Комментарий",
                "Результат теста",
            ],
            "Значение": [
                html.escape("не определена" if name is None else str(name)),
                "не определено" if baseline is None else f"{baseline:.6g}",
                "не определено" if current is None else f"{current:.6g}",
                "не определено" if delta is None else f"{delta:.1%}",
                "не определена" if accuracy is None else f"{accuracy:.3f}",
                html.escape(assessment_mode or "не определён"),
                coverage_text,
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
        plot_html = plot_km_dynamics(
            name, baseline, current, accuracy, green_threshold, c_min_threshold
        )

    return f"""
<h2 style="text-align: center;">Тест на динамику ключевой метрики</h2>
<p style="text-align: left;"><b>Цель теста</b></p>
<p style="text-align: left;">Оценить изменение ключевой метрики качества агента на мониторинговых данных относительно значения первичной валидации.</p>
<p style="text-align: left;">Оценки мониторинговых диалогов выставляет автоассесор, откалиброванный на эталонной разметке тестовой корзины.</p>
<p style="text-align: left;"><b>Алгоритм расчета</b></p>
<ol style="text-align: left; margin-left: 20px; padding-left: 20px;">
    <li style="text-align: left;">Единицы оценки формируются по assessment_mode контракта, ключевая метрика агрегируется по правилам monitoring_metric.</li>
    <li style="text-align: left;">Вычисляется относительное снижение КМ мониторинга к КМ первичной валидации.</li>
</ol>
<p style="text-align: left;"><b>Критерии выставления светофора</b></p>
<div style="text-align: left; width: 100%;">{criteria}</div><br>
<p style="text-align: left;"><b>Результаты теста</b></p>
<div style="text-align: left; width: 100%;">{results_html}</div><br>
{plot_html}
""".strip()


def _not_computable_result(
    contract: dict,
    *,
    reason: str,
    acc_auto: float | None,
    c_min_threshold: float,
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

    # Нулевой baseline не задаёт масштаб: относительная динамика не
    # определена при любом мониторинге, в том числе нулевом.
    delta = None if baseline == 0 else (baseline - current) / baseline

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

    logger.info(
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
        "status": "not_computable" if delta is None else "computed",
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
        ),
    }
