"""Интервал и правило цвета теста 6.3.4."""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("km_verdict", MODULE_DIR / "verdict.py")
verdict_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verdict_module
spec.loader.exec_module(verdict_module)


def test_effective_n_equals_count_for_equal_weights():
    assert verdict_module.effective_n([1.0] * 40) == pytest.approx(40)
    assert verdict_module.effective_n([3.0, 1.0]) == pytest.approx(16 / 10)


def test_wilson_interval_for_all_ones_is_below_one():
    ci = verdict_module.interval([1.0] * 10, [1.0] * 10)
    assert ci.method == "wilson" and ci.upper == pytest.approx(1.0)
    assert 0.65 < ci.lower < 0.75
    wider = verdict_module.interval([1.0] * 3, [1.0] * 3)
    assert wider.lower < ci.lower


def test_wilson_matches_known_value():
    scores = [1.0] * 45 + [0.0] * 5
    ci = verdict_module.interval(scores, [1.0] * 50)
    # Уилсон, p = 0.9, n = 50, z = 1.959964: центр 0.87146, полуширина 0.08507
    assert ci.lower == pytest.approx(0.7864, abs=5e-4)
    assert ci.upper == pytest.approx(0.9565, abs=5e-4)


def test_normal_interval_for_continuous_scores():
    scores = [0.5, 0.7, 0.9, 0.6, 0.8, 0.7, 0.75, 0.65]
    ci = verdict_module.interval(scores, [1.0] * len(scores))
    mean = sum(scores) / len(scores)
    assert ci.method == "normal"
    assert ci.lower < mean < ci.upper
    assert ci.upper - ci.lower == pytest.approx(2 * 1.959964 * (0.1273 / math.sqrt(8)), abs=0.01)


def test_drop_units():
    assert verdict_module.drop(0.8, 0.6, "absolute") == pytest.approx(0.2)
    assert verdict_module.drop(0.8, 0.6, "relative") == pytest.approx(0.25)
    with pytest.raises(ValueError, match="delta_unit"):
        verdict_module.drop(0.8, 0.6, "percent")


def _ci(lower, upper):
    return verdict_module.Interval(lower, upper, 0.95, "wilson")


@pytest.mark.parametrize(
    "ci, c_min, expected",
    [
        (_ci(0.85, 0.95), None, "green"),      # пессимистичное снижение 0.07 <= 0.15
        (_ci(0.70, 0.95), None, "yellow"),     # возможное снижение 0.22, не подтверждено
        (_ci(0.50, 0.60), None, "red"),        # оптимистичное снижение 0.32 >= 0.25
        (_ci(0.85, 0.95), 0.90, "yellow"),     # интервал пересекает C_MIN
        (_ci(0.80, 0.88), 0.90, "red"),        # интервал целиком ниже C_MIN
        (_ci(0.91, 0.95), 0.90, "green"),
    ],
)
def test_verdict_by_unfavourable_bound(ci, c_min, expected):
    result = verdict_module.verdict(
        0.92, ci, green_threshold=0.15, red_threshold=0.25, unit="absolute", c_min=c_min
    )
    assert result.color == expected
    assert result.reason_code and result.reason


def test_verdict_relative_unit():
    kwargs = dict(green_threshold=0.15, red_threshold=0.25, unit="relative", c_min=None)
    # оптимистичное относительное снижение (0.5 - 0.37) / 0.5 = 0.26 >= 0.25
    assert verdict_module.verdict(0.5, _ci(0.30, 0.37), **kwargs).color == "red"
    # пессимистичное 0.30 > 0.15, оптимистичное 0.12 < 0.25
    assert verdict_module.verdict(0.5, _ci(0.35, 0.44), **kwargs).color == "yellow"
