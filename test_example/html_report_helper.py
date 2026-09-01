"""
Вспомогательные функции для генерации HTML-отчётов.

Содержит функции для отображения светофоров в HTML,
формирования таблиц с критериями и отображения DataFrame с светофорами.
"""

import pandas as pd
from IPython.display import HTML, display


# =============================================================================
# Отображение светофоров
# =============================================================================

def display_semaphore(colour, width=46, height=18, return_html=False):
    """
    Функция с кодами цветов светофоров (для таблицы с критериями выставления светофоров).

    Args:
        colour: Цвет светофора ('green', 'yellow', 'amber', 'red', 'grey')
        width: Ширина светофора в пикселях
        height: Высота светофора в пикселях
        return_html: Если True, возвращает HTML-строку, иначе отображает в Jupyter

    Returns:
        str или None: HTML-строка если return_html=True, иначе None
    """
    # HTML-шаблоны для каждого цвета светофора
    red_light_html = """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <title>Результаты тестов</title>
            <style>
                .traffic-light {
                    display: inline-flex;
                    gap: 5px;
                    padding: 10px;
                    align-items: center;
                    border: 2px solid #ccc;
                    border-radius: 100px;
                }
                .light {
                    width: 10px;
                    height: 10px;
                    border-radius: 50%;
                }
                .red { background-color: #ca1d1d; }
                .amber { background-color: #ffd600; }
                .green { background-color: #04d930; }
                .gray { background-color: #ccc; }
            </style>
        </head>
        <body>
        <div class="traffic-light">
                <div class="light red"></div>
                <div class="light gray"></div>
                <div class="light gray"></div>
            </div>
        </body>
        """
    yellow_light_html = """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <title>Результаты тестов</title>
            <style>
                .traffic-light {
                    display: inline-flex;
                    gap: 5px;
                    padding: 10px;
                    align-items: center;
                    border: 2px solid #ccc;
                    border-radius: 100px;
                }
                .light {
                    width: 10px;
                    height: 10px;
                    border-radius: 50%;
                }
                .red { background-color: #ca1d1d; }
                .amber { background-color: #ffd600; }
                .green { background-color: #04d930; }
                .gray { background-color: #ccc; }
            </style>
        </head>
        <body>
        <div class="traffic-light">
                <div class="light gray"></div>
                <div class="light amber"></div>
                <div class="light gray"></div>
            </div>
        </body>
        """
    green_light_html = """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <title>Результаты тестов</title>
            <style>
                .traffic-light {
                    display: inline-flex;
                    gap: 5px;
                    padding: 10px;
                    align-items: center;
                    border: 2px solid #ccc;
                    border-radius: 100px;
                }
                .light {
                    width: 10px;
                    height: 10px;
                    border-radius: 50%;
                }
                .red { background-color: #ca1d1d; }
                .amber { background-color: #ffd600; }
                .green { background-color: #04d930; }
                .gray { background-color: #ccc; }
            </style>
        </head>
        <body>
        <div class="traffic-light">
                <div class="light gray"></div>
                <div class="light gray"></div>
                <div class="light green"></div>
            </div>
        </body>
        """
    grey_light_html = """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <title>Результаты тестов</title>
            <style>
                .traffic-light {
                    display: inline-flex;
                    gap: 5px;
                    padding: 10px;
                    align-items: center;
                    border: 2px solid #ccc;
                    border-radius: 100px;
                }
                .light {
                    width: 10px;
                    height: 10px;
                    border-radius: 50%;
                }
                .red { background-color: #ca1d1d; }
                .amber { background-color: #ffd600; }
                .green { background-color: #04d930; }
                .gray { background-color: #ccc; }
            </style>
        </head>
        <body>
        <div class="traffic-light">
                <div class="light gray"></div>
                <div class="light gray"></div>
                <div class="light gray"></div>
            </div>
        </body>
        """

    if colour == "green":
        if not return_html:
            display(HTML(green_light_html))
        else:
            return green_light_html
    elif colour == "yellow" or colour == "amber":
        if not return_html:
            display(HTML(yellow_light_html))
        else:
            return yellow_light_html
    elif colour == "red":
        if not return_html:
            display(HTML(red_light_html))
        else:
            return red_light_html
    elif colour == "grey" or colour == "gray":
        if not return_html:
            display(HTML(grey_light_html))
        else:
            return grey_light_html


# =============================================================================
# Таблицы с критериями
# =============================================================================

def show_criteria_semaphore(
    green_criterion, yellow_criterion, red_criterion, gray_criterion, styles
):
    """
    Функция формирования таблицы с критериями выставления светофора по тесту.

    Args:
        green_criterion: Критерий на зелёный светофор
        yellow_criterion: Критерий на жёлтый светофор
        red_criterion: Критерий на красный светофор
        gray_criterion: Критерий на серый светофор
        styles: Список стилей для таблицы

    Returns:
        pd.DataFrame: Styled DataFrame с критериями и светофорами
    """
    criterion = pd.DataFrame(columns=["comment", "colour"])
    criterion["colour"] = ["green", "yellow", "red", "grey"]

    criterion.loc[criterion.colour == "green", "comment"] = green_criterion
    criterion.loc[criterion.colour == "yellow", "comment"] = yellow_criterion
    criterion.loc[criterion.colour == "red", "comment"] = red_criterion
    criterion.loc[criterion.colour == "grey", "comment"] = gray_criterion

    criterion.loc[criterion["colour"] == "green", "colour_img"] = display_semaphore(
        "green", return_html=True
    )
    criterion.loc[criterion["colour"] == "yellow", "colour_img"] = display_semaphore(
        "yellow", return_html=True
    )
    criterion.loc[criterion["colour"] == "red", "colour_img"] = display_semaphore(
        "red", return_html=True
    )
    criterion.loc[criterion["colour"] == "grey", "colour_img"] = display_semaphore(
        "grey", return_html=True
    )

    criterion.rename(
        {"colour_img": "Результат", "comment": "Критерий"}, axis=1, inplace=True
    )
    criterion.drop("colour", axis=1, inplace=True)

    try:
        return criterion.style.hide().set_table_styles(styles)
    except:
        return criterion.style.hide_index().set_table_styles(styles)


def show_criteria_semaphore_without_red(green_criterion, yellow_criterion, styles):
    """
    Функция формирования таблицы с критериями (без красного светофора).

    Args:
        green_criterion: Критерий на зелёный светофор
        yellow_criterion: Критерий на жёлтый светофор
        styles: Список стилей для таблицы

    Returns:
        pd.DataFrame: Styled DataFrame с критериями и светофорами
    """
    criterion = pd.DataFrame(columns=["comment", "colour"])
    criterion["colour"] = ["green", "yellow"]

    criterion.loc[criterion.colour == "green", "comment"] = green_criterion
    criterion.loc[criterion.colour == "yellow", "comment"] = yellow_criterion

    criterion.loc[criterion["colour"] == "green", "colour_img"] = display_semaphore(
        "green", return_html=True
    )
    criterion.loc[criterion["colour"] == "yellow", "colour_img"] = display_semaphore(
        "yellow", return_html=True
    )

    criterion.rename(
        {"colour_img": "Результат", "comment": "Критерий"}, axis=1, inplace=True
    )
    criterion.drop("colour", axis=1, inplace=True)

    try:
        return criterion.style.hide().set_table_styles(styles)
    except:
        return criterion.style.hide_index().set_table_styles(styles)


def show_criteria_semaphore_without_yellow(green_criterion, red_criterion, styles):
    """
    Функция формирования таблицы с критериями (без жёлтого светофора).

    Args:
        green_criterion: Критерий на зелёный светофор
        red_criterion: Критерий на красный светофор
        styles: Список стилей для таблицы

    Returns:
        pd.DataFrame: Styled DataFrame с критериями и светофорами
    """
    criterion = pd.DataFrame(columns=["comment", "colour"])
    criterion["colour"] = ["green", "red"]

    criterion.loc[criterion.colour == "green", "comment"] = green_criterion
    criterion.loc[criterion.colour == "red", "comment"] = red_criterion

    criterion.loc[criterion["colour"] == "green", "colour_img"] = display_semaphore(
        "green", return_html=True
    )
    criterion.loc[criterion["colour"] == "red", "colour_img"] = display_semaphore(
        "red", return_html=True
    )

    criterion.rename(
        {"colour_img": "Результат", "comment": "Критерий"}, axis=1, inplace=True
    )
    criterion.drop("colour", axis=1, inplace=True)

    try:
        return criterion.style.hide().set_table_styles(styles)
    except:
        return criterion.style.hide_index().set_table_styles(styles)


# =============================================================================
# Отображение DataFrame с светофорами
# =============================================================================

def show_df(
    df, green_threshold={}, red_threshold={}, type_threshold="abs", precision=2
):
    """
    Функция для отображения DataFrame с возможностью добавления колонок со светофорами.

    Псевдокод:
        1. определяем метрики для выставления светофоров исходя из ключей переданных порогов;
        2. определяем, какие величины (абсолютные или относительные) используются;
        3. для каждой метрики в зависимости от направления её оптимизации ставим светофоры;
        4. добавляем столбец светофоров сразу за столбцом метрики в итоговой таблице.

    Args:
        df: Исходный DataFrame
        green_threshold: Словарь с порогами для зелёного светофора
        red_threshold: Словарь с порогами для красного светофора
        type_threshold: Тип значений ('abs' - абсолютные, 'relative' - процент)
        precision: Количество знаков после запятой

    Returns:
        tuple: (DataFrame с добавленными светофорами, массив цветов)
    """
    import numpy as np

    df = df.copy()
    # проверка пересечения наименований метрик
    metrics = [*(set(green_threshold.keys()) & set(red_threshold.keys()))]

    # row_colours = []
    if metrics:
        if type_threshold == "relative":
            compare_base = 100 * (1 - df[metrics] / df[metrics].iloc[0])
        elif type_threshold == "abs":
            compare_base = df[metrics]

        colours_by_metric = []
        for metric in metrics:
            # для проверки длины порогов оберну их в np.array
            green, red = (
                np.array(green_threshold[metric]),
                np.array(red_threshold[metric]),
            )
            # значений порогов должно быть либо по количеству горизонтов, либо одно
            assert (len(df) == green.size) | (green.size == 1), (
                "Некорректные по длине границы светофоров."
            )
            assert (len(df) == red.size) | (red.size == 1), (
                "Некорректные по длине границы светофоров."
            )

            mvals = compare_base[metric]
            if (green <= red).all():
                condlist = np.array(
                    [
                        mvals >= red,
                        (mvals > green) & (mvals < red),
                        mvals <= green,
                        pd.isna(mvals),
                    ]
                )
                colours = np.select(condlist, ["red", "yellow", "green", "grey"])
            elif (green > red).all():
                condlist = np.array(
                    [
                        mvals >= green,
                        (mvals > red) & (mvals < green),
                        mvals <= red,
                        pd.isna(mvals),
                    ]
                )
                colours = np.select(condlist, ["green", "yellow", "red", "grey"])
            else:
                raise ValueError("Некорректно выставлены границы светофоров.")

            colours_by_metric.append(colours)
            # порядковый номер колонки с метрикой
            ncol = int(np.where(df.columns == metric)[0][0])
            if type_threshold == "relative":
                df.insert(ncol + 1, metric + "Прирост, п.п.", mvals)
                ncol = ncol + 1
            df.insert(ncol + 1, metric + "<br>semaphore", colours)

        colours_by_metric = np.array(colours_by_metric).T

    for column in df.columns:
        if "semaphore" in str(column):
            df["Результат"] = np.array(
                [
                    display_semaphore(colour, return_html=True)
                    for colour in df[column].values
                ]
            )
    df.drop(columns=[metrics[0] + "<br>semaphore"], inplace=True)

    return df, colours_by_metric