# laim-km-dynamic-test

Нода мониторингового контура LAIM: считает ключевую метрику (КМ) агента на
оценённых мониторинговых данных, сравнивает её с КМ первичной валидации и
отдаёт в агрегатор светофор с числами: baseline, КМ мониторинга, снижение, покрытие.

## Зачем нода нужна

Предыдущие шаги контура отвечают, как считать КМ и чему она равна на эталоне;
эта нода отвечает, насколько КМ просела на мониторинге. Два проектных решения:

- **Считает контракт, а не нода.** Единица наблюдения, редьюсер и политика
  пропусков берутся из `monitoring_metric` (`laim-monitoring-metric.v2`) —
  та же агрегация, что у остальных потребителей контракта. Оценки строк
  (`main_metric`) нода не пересчитывает: их уже поставил агент-ассессор.
- **Деградация вместо падения.** Невычислимый контракт, отказ ассессора или
  отсутствие `main_metric` дают серый светофор со статусом `not_computable` и
  текстовой причиной; нода падает только на нарушении контракта данных.

## Место в контуре

```text
laim-kriteria-selector.validated_monitoring_metric ─► monitoring_metric ┐
laim-kriteria-selector.metric_spec                 ─► metric_spec       │
laim-asessor-agent.scored_data                     ─► scored_df         ├─ laim-km-dynamic-test
laim-asessor-agent.acc_auto                        ─► acc_auto          │        │
laim-asessor-agent.assessment_result               ─► assessment_result ┘        │
(не подключён)                                     ─► perv_validation_km         │
                                                                                  ├─► all_results ─► laim-agg.in
                                                                                  └─► test_description (не подключён; HTML для UI ноды)
```

## Порты и настройки

### Входы

| Порт | Тип | Обязательность | Что приходит |
|---|---|---|---|
| `monitoring_metric` | default | обязательный | Контракт `laim-monitoring-metric.v2` после identity-гейта селектора: `status`, `name`, `assessment_mode`, `scoring`, `aggregation`, `baseline.value` |
| `scored_df` | dataframe | в `descriptor.json` необязательный, в `main` без значения по умолчанию | Мониторинговые строки в формате тестового датасета с колонкой `main_metric` от ассессора |
| `acc_auto` | default | необязательный | Точность калибровки автоассессора; только отображается в отчёте |
| `assessment_result` | default | необязательный | Статус расчёта ассессора: `status`, `reason`, `total_units`, `scored_units` |
| `perv_validation_km` | default | необязательный, не подключён | Явная КМ первичной валидации: `{name, value}`, число или JSON-строка; если подан — главнее `baseline.value` контракта |
| `metric_spec` | default | необязательный | Результат селектора: `status`, `main_metric`, `other_metrics`, `scoring_method`, `missing_policy`, `majority_denominator`, `resolution_source` |

### Выходы

| Порт | Тип | Что отдаёт |
|---|---|---|
| `all_results` | default (JSON) | Вердикт теста для `laim-agg` (см. «Форматы выхода») |
| `test_description` | hidden | HTML-отчёт: критерии светофора, таблица результатов, график КМ (PNG в base64) |

### Настройки

Настроек нет: форма «Настройка пределов метрики» в `descriptor.json` пуста,
пороги 0.15 и 0.25 заданы в коде (`km_dynamics_test`, аргументы
`green_threshold`, `c_min_threshold`) и с платформы не меняются.
Единственная привязка UI — компонент `html` на `$.test_description`.

## Как проходит прогон

```text
1. Контракт      validate_monitoring_metric(monitoring_metric, require_computed=False)
2. Ассессор      assessment_result.status != computed -> серый с его reason
3. main_metric   metric_spec подан -> materialize_main_metric; иначе колонка обязана быть в scored_df
4. Baseline      perv_validation_km, если подан, иначе baseline.value контракта
5. Агрегация     aggregate_main_metric: единицы по assessment_mode, редьюсер, missing_policy
6. Вердикт       delta = (baseline - current) / baseline -> цвет, reason
7. Публикация    all_results + HTML-отчёт в test_description
```

**1. Контракт.** `scoring.method = all_assessors` перед проверкой заменяется
на `identity` по `score_column`: голоса разметчиков есть только в эталонной
корзине, на мониторинге ассессор уже выдал итоговый балл.

**3. main_metric.** Если в `scored_df` уже есть `main_metric` хотя бы с одним
числом, используется он (строковый транспорт приводится к числу). Иначе балл
собирается из колонок `metric_spec.main_metric` + `other_metrics` методом
`scoring_method` (`identity`, `mean_criteria`, `all_criteria`,
`all_assessors`, `majority`); `missing_policy` и `majority_denominator`
наследуются из `scoring` контракта, если селектор их не задал;
`resolution_source = monitoring_metric_judged_total` принудительно даёт
`identity`.

**5. Агрегация.** Единица наблюдения — по `assessment_mode`: `qa` и
`turn_with_history` — строка; `dialogue` — группа строк по
`reference_group_id` (строка без группы становится своей группой), внутри
которой `main_metric` обязан быть константен. Редьюсер `mean` или
`frequency_weighted_mean` по `input_query_count`. Расчёт в `Decimal`.

**6. Вердикт.** `delta` — относительное снижение, доля от baseline, а не
процентные пункты: при baseline 0.93 и мониторинге 0.8298 снижение на 0.10
абсолютных даёт `delta = 0.1078`. Правило: `delta >= 0.25` — красный,
`delta <= 0.15` — зелёный (рост КМ тоже зелёный), между ними — жёлтый.
Baseline, равный нулю, при ненулевом мониторинге даёт серый.

### Пример реального лога успешного прогона

```text
INFO root: Тест динамики ключевой метрики запущен
INFO root: KM dynamics: baseline=0.93 current=0.8297872340425532 delta=0.10775566231983535 color=green
```

## Форматы выхода и контракты

`all_results` (пример реального прогона):

```json
{
  "test_name": "km_test",
  "status": "computed",
  "color": "green",
  "calculated_traffic_lights": {"test_light": "green",
    "semaphore_title": "Динамика ключевой метрики соответствует зеленому светофору"},
  "km_name": "Accuracy",
  "km_baseline": 0.93,
  "km_monitoring": 0.8297872340425532,
  "km_delta": 0.10775566231983535,
  "coverage": {"total_units": 94, "scored_units": 94, "excluded_units": 0, "weight_sum": 94.0},
  "thresholds": {"green": 0.15, "red": 0.25},
  "reason": "Снижение КМ находится в зеленой зоне.",
  "metric_details": {"name": "Accuracy", "КМ на мониторинге": 0.8297872340425532, "...": "..."}
}
```

- `status` — `computed` | `not_computable`; `color` и `test_light` —
  `green` | `amber` | `red` | `gray` (внутренний `yellow` переводится
  в `amber` для платформы). Платформа читает светофор из `all_results.color`
  (`uiResults.semaphore`) и `calculated_traffic_lights.test_light`.
- При `not_computable`: `km_monitoring`, `km_delta` — `null`; `km_baseline` —
  значение контракта, если оно есть; `coverage.total_units`/`scored_units` —
  из `assessment_result` или размера `scored_df`, остальные ключи `null`.
- `metric_details` — те же числа с русскими ключами (`КМ на первичной
  валидации`, `Дельта КМ`, `Порог минимальной дельты КМ`, `coverage`).

## Падение против деградации

Нода падает исключением, когда контракт данных нарушен и результат не может
быть однозначным:

| Причина | Исключение |
|---|---|
| `monitoring_metric` не dict, неизвестная версия контракта/UMR, нет обязательных полей, `score_column != main_metric` | `MonitoringContractError` |
| В `scored_df` нет `query_id`, `input_query`, `output_answer` | `MonitoringContractError` |
| Пустой `main_metric` при `missing_policy = fail`; ни одной оценённой единицы; вес `input_query_count <= 0`; `main_metric` не константен внутри `dialogue`-группы | `MonitoringContractError` |
| `assessment_result` подан и не является dict; `scored_df` не DataFrame при поданном `metric_spec` | `TypeError` |
| `perv_validation_km` не приводится к числу | `ValueError` |

Всё остальное — серый светофор, `status = not_computable`, причина в `reason`:

| Событие | `reason` |
|---|---|
| Контракт `monitoring_metric` со статусом `not_computable` | `reason` контракта или `monitoring_metric невычислим` |
| `assessment_result.status != computed` | `reason` ассессора или `assessment_status='<status>'` |
| `metric_spec.status = not_computable` | `reason` селектора или `kriteria-selector не разрешил ключевую метрику` |
| Нет `main_metric` и нет `metric_spec` | `scored_df не содержит main_metric; подключите kriteria-selector.metric_spec к одноимённому порту KM` |
| Колонки селектора отсутствуют, нечисловые, содержат пропуски при `fail`, небинарные для `all_*`/`majority`, ничья в `majority` | текст с именами колонок и числом невалидных значений |
| Baseline равен нулю, КМ мониторинга не ноль | `Baseline КМ равен нулю, относительная динамика не определена.` (при этом `status = computed`) |

Политика пропусков контракта при агрегации: `fail` — исключение;
`exclude_unit`/`exclude_value` — единица не учитывается и попадает в
`excluded_units`; `zero` — засчитывается как 0.

## Внешние сервисы

Не применимо: нода не обращается к LLM, эмбеддингам или HDFS; расчёт
детерминирован.

## Наблюдаемость

В лог платформы уходят две строки через корневой `logging` (см. пример выше);
при сером светофоре второй строки нет — причина только в поле `reason`.
Порта журнала у ноды нет, источник истины о прогоне это `all_results`: триаж
на сотне прогонов делается по `status`, `color`, `reason` и
`coverage.scored_units / total_units`.

## Карта кода

```text
main.py                    точка входа платформы: вызов теста, сборка all_results, цвет для платформы
km_dynamics.py             материализация main_metric по metric_spec, вердикт, HTML-отчёт и график
html_report_helper.py      виджет светофора и таблица критериев для HTML (display_semaphore, show_criteria_semaphore)
laim_monitoring/           контракт laim-monitoring-metric.v2 (core.py): валидация, единицы наблюдения, Decimal-агрегация
utils.py                   transform_to_int; в sourceFiles не входит и нодой не вызывается
tests/                     conftest.py (sys.path на корень ноды), test_scored_output_contract.py
```

## Что делать, если

- **Серый светофор с `reason` про `main_metric`** — на порт `metric_spec` не
  подключён `laim-kriteria-selector.metric_spec` или ассессор не отдал
  `main_metric`; проверьте `assessment_result.status` и колонки `scored_data`.
- **Ожидали жёлтый, получили зелёный при снижении на 10 п.п.** — порог
  относительный: 10 п.п. от 0.93 это `delta = 0.108 < 0.15`.
- **`MonitoringContractError` про константность внутри `dialogue`** — ассессор
  выставил разные баллы репликам одной сессии; в режиме `dialogue` балл один
  на сессию.
- **Нужен другой baseline, чем в отчёте о валидации** — подать
  `perv_validation_km` (`{"name": ..., "value": ...}`); он главнее контракта.

## Деплой

База `py312-simple`, точка входа — функция `main` в `main.py`,
`descriptor.json` перечисляет `sourceFiles`: `main.py`, `km_dynamics.py`,
`html_report_helper.py`, `laim_monitoring/__init__.py`,
`laim_monitoring/core.py`. Нода самодостаточна: контракт лежит в вендорной
копии `laim_monitoring/`. Зависимости `requirements.txt`: `pandas`,
`matplotlib` (график), `ipython` (`IPython.display` в `html_report_helper.py`),
`jinja2` (`Styler.to_html` в pandas). Проверка: `ruff check .` и
`python -m pytest -q` из корня ноды на Python 3.12 (так же настроен CI).
Отдельного теста соответствия `sourceFiles` диску в `tests/` нет.

## Глоссарий

- **КМ** — ключевая метрика качества агента; имя и baseline приходят в
  контракте `monitoring_metric`.
- **Baseline** — КМ первичной валидации: `baseline.value` контракта или `perv_validation_km`.
- **`main_metric`** — каноническая оценка единицы наблюдения (строки в `qa`
  и `turn_with_history`, сессии в `dialogue`), выставленная ассессором.
- **`delta`** — относительное снижение КМ `(baseline - current) / baseline`.
