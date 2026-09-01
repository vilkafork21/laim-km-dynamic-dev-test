"""
Утилиты для расчёта светофоров (traffic lights).

Содержит различные функции для определения цвета светофора на основе
пороговых значений, частот, квантилей и других метрик.
"""

import numpy as np
import pandas as pd
import typing as tp


# =============================================================================
# Пороговые светофоры
# =============================================================================

def semaphore_by_threshold(value: float,
                           threshold: tp.Tuple[float, float],
                           greater_is_better: bool=True,
                           left_border_is_bigger: bool=True,
                           right_border_is_bigger: bool=True) -> str:
    """
    Расчёт цвета светофора в зависимости от границ.

    Args:
        value: Значение метрики
        threshold: Границы светофора (нижняя, верхняя)
        greater_is_better: True, если чем больше метрика, тем лучше
        left_border_is_bigger: При равенстве границе - использовать цвет справа от неё
        right_border_is_bigger: При равенстве границе - использовать цвет справа от неё

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    semaphore = {0: 'red', 1: 'amber', 2: 'green'}
    if (value < threshold[0]) or (not left_border_is_bigger and value == threshold[0]):
        interval = 0
    elif (value < threshold[1]) or (not right_border_is_bigger and value == threshold[1]):
        interval = 1
    else:
        interval = 2
    if not greater_is_better:
        interval = abs(interval - 2)
    return semaphore[interval]


def semaphore_by_threshold_without_yellow(value: float,
                                          threshold: float,
                                          greater_is_better: bool=True,
                                          border_is_bigger: bool=True) -> str:
    """
    Расчёт цвета светофора в зависимости от границ (без жёлтого светофора).

    Args:
        value: Значение метрики
        threshold: Граница светофора
        greater_is_better: True, если чем больше метрика, тем лучше
        border_is_bigger: При равенстве границе - использовать цвет справа от неё

    Returns:
        str: Цвет светофора ('red' или 'green')
    """
    semaphore = {0: 'red', 1: 'green'}
    if (value < threshold) or (not border_is_bigger and value == threshold):
        interval = 0
    else:
        interval = 1
    if not greater_is_better:
        interval = abs(interval - 1)
    return semaphore[interval]


def semaphore_by_threshold_without_red(value: float,
                                       threshold: float,
                                       greater_is_better: bool=True,
                                       border_is_bigger: bool=True) -> str:
    """
    Расчёт цвета светофора в зависимости от границ (без красного светофора).

    Args:
        value: Значение метрики
        threshold: Граница светофора
        greater_is_better: True, если чем больше метрика, тем лучше
        border_is_bigger: При равенстве границе - использовать цвет справа от неё

    Returns:
        str: Цвет светофора ('amber' или 'green')
    """
    semaphore = {0: 'amber', 1: 'green'}
    if (value < threshold) or (not border_is_bigger and value == threshold):
        interval = 0
    else:
        interval = 1
    if not greater_is_better:
        interval = abs(interval - 1)
    return semaphore[interval]


# =============================================================================
# Комбинирование светофоров
# =============================================================================

def worst_semaphore(semaphore_list: tp.List[str]) -> str:
    """
    Расчёт худшего светофора из списка.

    Приоритет: red < amber < green < gray

    Args:
        semaphore_list: Список со светофорами

    Returns:
        str: Цвет худшего светофора
    """
    if (semaphore_list is None) or (len(semaphore_list) == 0):
        return 'gray'
    semaphore_to_value = {'red': 0, 'amber': 1, 'green': 2, 'gray': 3}
    value_to_semaphore = {value: key for key, value in semaphore_to_value.items()}
    worst_value = min([semaphore_to_value[i] for i in semaphore_list])
    return value_to_semaphore[worst_value]


def tricky_semaphore(value: float,
                     semaphore_list: tp.List[str],
                     greater_is_better: bool=True,
                     threshold_metric: tp.Tuple[float, float]=(0.4, 0.6),
                     red_freq_threshold: float=0.2,
                     yellow_freq_threshold: float=0.2) -> str:
    """
    Хитрый светофор - комбинирует значение метрики и частоты светофоров асессоров.

    Args:
        value: Ключевая метрика качества
        semaphore_list: Список светофоров за критерии асессоров
        greater_is_better: Чем больше агрегированная метрика, тем лучше?
        threshold_metric: Границы светофора для агрегированного значения
        red_freq_threshold: Граница по относительному числу "красных" светофоров
        yellow_freq_threshold: Граница по относительному числу "жёлтых" светофоров

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    color = 'amber'
    value_color = semaphore_by_threshold(value,
                                         threshold_metric,
                                         greater_is_better)
    freq_color = pd.Series(semaphore_list, dtype='str').value_counts(normalize=True).to_dict()
    if (value_color == 'red') or ((freq_color.get('red', 0) > red_freq_threshold)):
        color = 'red'
    elif (value_color == 'green') and (freq_color.get('amber', 0) < yellow_freq_threshold) and (freq_color.get('red', 0) <= 0):
        color = 'green'
    return color


def proportion_semaphore(semaphores: tp.Union[pd.Series, tp.List[str]],
                         freq_threshold: tp.Tuple[float, float]) -> str:
    """
    Расчёт цвета итогового светофора в зависимости от доли различных светофоров.

    Args:
        semaphores: Список светофоров для каждого элемента выборки
        freq_threshold: Доля допустимых жёлтых и красных светофоров (амбер, ред)

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    semaphores_series = pd.Series(semaphores)
    yellows = (semaphores_series == 'amber').sum() / len(semaphores_series)
    reds = (semaphores_series == 'red').sum() / len(semaphores_series)
    if reds > freq_threshold[1]:
        color = 'red'
    elif yellows + reds > freq_threshold[0]:
        color = 'amber'
    else:
        color = 'green'
    return color


def quanity_semaphore(semaphore_list: tp.List[str],
                      threshold: tp.Tuple[int, int]) -> str:
    """
    Расчёт цвета итогового светофора в зависимости от количества вхождений частных светофоров.

    Args:
        semaphore_list: Массив с цветами частных светофоров
        threshold: Границы светофора (красный, жёлтый)

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    yellow_count = sum(1 for color in semaphore_list if color == 'amber')
    red_count = sum(1 for color in semaphore_list if color == 'red')
    if red_count >= threshold[0]:
        overall_semaphore = 'red'
    elif yellow_count >= threshold[1]:
        overall_semaphore = 'amber'
    else:
        overall_semaphore = 'green'
    return overall_semaphore


# =============================================================================
# Квантильные светофоры
# =============================================================================

def quantile_semaphore(value: float,
                       threshold: tp.Tuple[float, float],
                       inner_is_better: bool=True) -> str:
    """
    Расчёт цвета светофора в зависимости от квантиля при двусторонних границах.

    Args:
        value: Значение метрики
        threshold: Границы светофора
        inner_is_better: True, если светофор лучше при попадании внутрь границ

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    semaphore = {0: 'red', 1: 'amber', 2: 'green'}
    if (1 - threshold[0]) / 2 < value < (1 + threshold[0]) / 2:
        interval = 2
    elif (1 - threshold[1]) / 2 <= value <= (1 + threshold[1]) / 2:
        interval = 1
    else:
        interval = 0
    if not inner_is_better:
        interval = abs(interval - 2)
    return semaphore[interval]


def quantile_semaphore_without_yellow(value: float,
                                      threshold: tp.Tuple[float, float],
                                      inner_is_better: bool = True) -> str:
    """
    Расчёт цвета светофора в зависимости от квантиля (без жёлтого светофора).

    Args:
        value: Значение метрики
        threshold: Границы светофора (нижняя и верхняя границы)
        inner_is_better: True, если светофор лучше при попадании внутрь границ

    Returns:
        str: Цвет светофора ('red' или 'green')
    """
    if inner_is_better:
        if threshold[0] <= value <= threshold[1]:
            return 'green'
        else:
            return 'red'
    else:
        if value < threshold[0] or value > threshold[1]:
            return 'green'
        else:
            return 'red'


# =============================================================================
# Частотные светофоры
# =============================================================================

def shuffle_ci_semaphore(mean_metric: float,
                         std_metric: float,
                         interval: tp.Tuple[float, float],
                         std_threshold: float) -> str:
    """
    Расчёт цвета светофора для теста с перемешиванием вариантов.

    Args:
        mean_metric: Среднее значение целевой метрики
        std_metric: Значение стандартного отклонения целевой метрики
        interval: Границы доверительного интервала
        std_threshold: Порог для светофора по стандартному отклонению

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    if (mean_metric < interval[0]) or (mean_metric > interval[1]):
        return 'red'
    if std_metric > std_threshold:
        return 'amber'
    return 'green'


def local_frequency_semaphore(value: float,
                              ideal_responses: float,
                              threshold: tp.Tuple[float, float]) -> str:
    """
    Расчёт цвета светофора в зависимости от частоты элемента относительно средней частоты.

    Args:
        value: Элемент массива частот
        ideal_responses: Математическое ожидание частот элементов
        threshold: Границы светофора

    Returns:
        str: Цвет светофора ('red', 'amber', 'green')
    """
    color = 'red'
    yellow_bound, red_bound = threshold[0], threshold[1]
    if (value >= red_bound * ideal_responses) or (value <= ideal_responses / red_bound):
        color = 'red'
    elif (value >= yellow_bound * ideal_responses) or (value <= ideal_responses / yellow_bound):
        color = 'amber'
    else:
        color = 'green'
    return color


def frequency_semaphore(values: tp.List[float],
                        threshold: tp.Tuple[float, float]) -> tp.List[str]:
    """
    Расчёт цвета светофора для каждой частоты элемента относительно среднего числа элементов.

    Args:
        values: Массив частот элементов
        threshold: Границы светофора

    Returns:
        list: Список цветов светофора для каждой частоты
    """
    semaphore_list, color = [], 'red'
    ideal_responses = np.mean(values)
    for value in values:
        color = local_frequency_semaphore(value=value,
                                          ideal_responses=ideal_responses,
                                          threshold=threshold)
        semaphore_list.append(color)
    return semaphore_list