"""Правило цвета теста 6.3.4: интервал неопределённости КМ и снижение к базе.

Цвет выставляется по неблагоприятной границе интервала: зелёный, когда даже
пессимистичная оценка не выходит за допустимое снижение и минимальный уровень;
красный, когда даже оптимистичная оценка подтверждает нарушение; иначе жёлтый.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_Z = {0.95: 1.959964}
UNITS = ("absolute", "relative")


@dataclass(frozen=True)
class Interval:
    lower: float
    upper: float
    level: float
    method: str


@dataclass(frozen=True)
class Verdict:
    color: str
    reason_code: str
    reason: str


def effective_n(weights: list[float]) -> float:
    """Эффективный объём по Кишу: (Σw)² / Σw²."""
    total = sum(weights)
    squares = sum(weight * weight for weight in weights)
    return total * total / squares if squares else 0.0


def _weighted_mean(scores: list[float], weights: list[float]) -> float:
    return sum(s * w for s, w in zip(scores, weights)) / sum(weights)


def interval(scores: list[float], weights: list[float], level: float = 0.95) -> Interval:
    """Интервал среднего: Уилсон для бинарных оценок, нормальная аппроксимация иначе."""
    if len(scores) != len(weights) or not scores:
        raise ValueError("interval: нужны непустые списки оценок и весов одной длины")
    z = _Z[level]
    n = effective_n(weights)
    mean = _weighted_mean(scores, weights)
    if all(score in (0.0, 1.0) for score in scores):
        centre = (mean + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(mean * (1 - mean) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return Interval(max(0.0, centre - half), min(1.0, centre + half), level, "wilson")
    if n < 2:
        return Interval(mean, mean, level, "normal")
    variance = sum(w * (s - mean) ** 2 for s, w in zip(scores, weights)) / sum(weights)
    sd = math.sqrt(variance * n / (n - 1))
    half = z * sd / math.sqrt(n)
    return Interval(mean - half, mean + half, level, "normal")


def drop(baseline: float, value: float, unit: str) -> float:
    """Снижение КМ относительно базы в единицах delta_unit."""
    if unit == "absolute":
        return baseline - value
    if unit == "relative":
        return (baseline - value) / baseline
    raise ValueError(f"delta_unit должен быть одним из {UNITS}, получено {unit!r}")


def verdict(
    baseline: float,
    ci: Interval,
    *,
    green_threshold: float,
    red_threshold: float,
    unit: str,
    c_min: float | None,
) -> Verdict:
    """Цвет по неблагоприятной границе интервала (критерии карточки 6.3.4)."""
    pessimistic = drop(baseline, ci.lower, unit)
    optimistic = drop(baseline, ci.upper, unit)
    if c_min is not None and ci.upper < c_min:
        return Verdict("red", "below_c_min", "Подтверждено нарушение минимального уровня КМ.")
    if optimistic >= red_threshold:
        return Verdict("red", "drop_confirmed", "Подтверждено снижение КМ сверх допустимого.")
    if pessimistic <= green_threshold and (c_min is None or ci.lower >= c_min):
        return Verdict(
            "green", "within_tolerance",
            "Снижение КМ с учётом неопределённости в допустимых пределах.",
        )
    return Verdict(
        "yellow", "drop_possible",
        "Снижение КМ или нарушение минимального уровня возможно, но интервалом не подтверждено.",
    )
