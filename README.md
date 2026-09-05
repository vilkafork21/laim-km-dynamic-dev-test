# laim-km-dynamic-test

Нода мониторингового контура LAIM: тест 6.3.4 методики — уровень и динамика
ключевой метрики (КМ). Считает КМ агента на оценённых мониторинговых
единицах, строит интервал неопределённости, сравнивает с КМ первичной
валидации и минимальным уровнем и отдаёт в агрегатор светофор с числами:
baseline, КМ мониторинга, интервал, снижение, знаменатели.

## Зачем нода нужна

Предыдущие шаги контура отвечают, как считать КМ и чему она равна на эталоне;
эта нода отвечает, просела ли КМ на мониторинге, и насколько этому выводу
можно верить. Решения:

- **Считает контракт, а не нода.** Единица наблюдения и веса берутся из
  `monitoring_metric` (`laim-monitoring-metric.v2`); оценки единиц
  (`main_metric`) выставил автоассессор, нода их не пересчитывает.
- **Цвет по границе интервала, а не по точке.** Зелёный — когда даже
  пессимистичная граница интервала КМ не выходит за допустимое снижение и
  минимальный уровень; красный — когда даже оптимистичная граница
  подтверждает нарушение; иначе жёлтый. Малая выборка не даёт уверенного
  цвета: меньше минимума единиц — тест не оценивается.
- **Отказы судьи — не пропуски разметки.** Единица без оценки (`NaN`)
  исключается из числителя и знаменателя независимо от `missing_policy`
  контракта и считается отдельно; выше допустимой доли — тест не оценивается.
- **Деградация вместо падения.** Невычислимый контракт, отказ ассессора,
  отсутствие `main_metric`, недобор единиц дают серый светофор со статусом
  `not_computable`, машинным `reason_code` и текстовой причиной.

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
| `monitoring_metric` | default | обязательный | Контракт `laim-monitoring-metric.v2` после identity-гейта селектора: `status`, `name`, `assessment_mode`, `scoring`, `aggregation`, `baseline.value`, `baseline.reconciliation` |
| `scored_df` | dataframe | нет: без него исход `not_computable` | Мониторинговые строки в формате тестового датасета с колонкой `main_metric` от ассессора |
| `acc_auto` | default | необязательный | Точность калибровки автоассессора; только отображается в отчёте |
| `assessment_result` | default | необязательный | Статус расчёта ассессора: `status`, `reason`, `total_units`, `scored_units` |
| `perv_validation_km` | default | необязательный, не подключён | Явная КМ первичной валидации: `{name, value}`, число или JSON-строка; если подан — главнее `baseline.value` контракта |
| `metric_spec` | default | необязательный | Результат селектора: `status`, `main_metric`, `other_metrics`, `scoring_method`, `missing_policy`, `majority_denominator`, `resolution_source` |

### Выходы

| Порт | Тип | Что отдаёт |
|---|---|---|
| `all_results` | default (JSON) | Вердикт теста для `laim-agg` (см. «Форматы выхода») |
| `test_description` | hidden | HTML-отчёт: критерии светофора, таблица результатов с интервалом и знаменателями, график КМ (PNG в base64) |

### Настройки

| Настройка | По умолчанию | Зачем |
|---|---|---|
| `green_threshold` | `0.15` | Допустимое снижение КМ δ: зелёный, если пессимистичная граница интервала снижается не больше порога |
| `red_threshold` | `0.25` | Подтверждённое снижение: красный, если оптимистичная граница интервала снижается не меньше порога |
| `delta_unit` | `absolute` | Единицы порогов: `absolute` — в единицах шкалы метрики (для долей это п.п.), `relative` — доля от baseline |
| `c_min` | `0.0` | Минимально допустимый уровень КМ; `0` — не задан (для метрики «больше — лучше» любое значение не ниже нуля) |
| `min_valid_units` | `50` | Меньше оценённых единиц — тест не оценивается (`insufficient_units`) |
| `max_invalid_share` | `0.2` | Выше доля отказов судьи — тест не оценивается (`judge_refusals`) |

Пороги и минимумы — временные параметры мониторинга: значения по умолчанию
сохраняют прежние границы 0.15/0.25, минимум 50 единиц выбран по
биномиальной модели (при доле верных ответов 0.85 ошибка цвета от шума
около 3–4 %) и подлежит калибровке на реальных агентах.

## Как проходит прогон

```text
1. Контракт      validate_monitoring_metric(monitoring_metric, require_computed=False)
2. Ассессор      assessment_result.status != computed -> серый с его reason; calibration_metrics.admission_status red/not_assessed -> серый judge_not_admitted, amber -> warning
3. main_metric   metric_spec подан -> materialize_main_metric; иначе колонка обязана быть в scored_df
4. Baseline      perv_validation_km, если подан, иначе baseline.value контракта; <= 0 -> серый
5. Единицы       unitize по assessment_mode; отказы судьи (NaN) отдельно; веса по aggregation
6. Минимумы      доля отказов > max_invalid_share -> серый; оценённых < min_valid_units -> серый
7. Интервал      Уилсон (оценки 0/1) или нормальная аппроксимация (иные шкалы), эффективное n по Кишу
8. Смещение      calibration_metrics.bias_mean задан -> КМ_тек − b, интервал расширен на половину интервала смещения; интервал смещения шире δ -> серый judge_bias_uncertain
9. Вердикт       границы интервала против green_threshold / red_threshold (в delta_unit) и c_min
10. Публикация   all_results + HTML-отчёт в test_description
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

**5. Единицы.** Единица наблюдения — по `assessment_mode`: `qa` и
`turn_with_history` — строка; `dialogue` — группа строк по
`reference_group_id`, внутри которой `main_metric` обязан быть константен.
Вес единицы — `input_query_count` при `frequency_weighted_mean`, иначе 1.
Единица с `NaN` в `main_metric` — отказ судьи: не входит ни в среднее, ни в
знаменатель среднего, но входит в `total_units` и `refused_units`.

**7–8. Интервал и вердикт.** КМ мониторинга — взвешенное среднее оценённых
единиц; интервал 95 %: Уилсон при оценках только 0/1, иначе нормальная
аппроксимация с эффективным `n = (Σw)² / Σw²`. Снижение считается от
baseline к границам интервала в единицах `delta_unit`. Красный: интервал
целиком ниже `c_min` (`below_c_min`) или оптимистичное снижение `>=
red_threshold` (`drop_confirmed`). Зелёный: пессимистичное снижение `<=
green_threshold` и интервал не ниже `c_min` (`within_tolerance`). Иначе
жёлтый (`drop_possible`; на порту `amber`). Рост КМ — зелёный.

### Пример лога прогона

```text
INFO main: [km] пороги green<=0.15 red>=0.25 unit=absolute c_min=None min_units=50 max_refused=0.2
INFO km_dynamics: [km] baseline=0.93 current=0.8297872340425532 interval=[0.7405; 0.8930] drop=0.1002 unit=absolute color=yellow units={'unit': 'qa', 'total_units': 94, 'scored_units': 94, 'refused_units': 0, 'refused_share': 0.0, 'weight_sum': 94.0, 'n_effective': 94.0}
```

## Форматы выхода и контракты

`all_results` (пример):

```json
{
  "test_name": "km_test",
  "status": "computed",
  "color": "amber",
  "calculated_traffic_lights": {"test_light": "amber",
    "semaphore_title": "Динамика ключевой метрики соответствует желтому светофору"},
  "reason": "Снижение КМ или нарушение минимального уровня возможно, но интервалом не подтверждено.",
  "reason_code": "drop_possible",
  "km_name": "Accuracy",
  "km_baseline": 0.93,
  "km_monitoring": 0.8297872340425532,
  "km_delta": 0.10021276595744685,
  "km_delta_unit": "absolute",
  "interval": {"lower": 0.7405, "upper": 0.893, "level": 0.95, "method": "wilson"},
  "coverage": {"total_units": 94, "scored_units": 94, "excluded_units": 0, "weight_sum": 94.0},
  "provenance": {"unit": "qa", "total_units": 94, "scored_units": 94, "refused_units": 0,
    "refused_share": 0.0, "weight_sum": 94.0, "n_effective": 94.0},
  "thresholds": {"green": 0.15, "red": 0.25, "unit": "absolute", "c_min": null},
  "warnings": [],
  "metric_details": {"name": "Accuracy", "КМ на мониторинге": 0.8297872340425532, "...": "..."}
}
```

- `status` — `computed` | `not_computable`; `color` и `test_light` —
  `green` | `amber` | `red` | `gray`. Платформа читает светофор из
  `all_results.color` и `calculated_traffic_lights.test_light`.
- `reason_code` при `computed`: `within_tolerance`, `drop_possible`,
  `drop_confirmed`, `below_c_min`; при `not_computable`:
  `upstream_not_computable`, `assessment_not_computable`, `metric_spec`,
  `baseline_not_positive`, `judge_refusals`, `insufficient_units`.
- При `not_computable`: `km_monitoring`, `km_delta`, `interval` — `null`;
  `km_baseline` — значение контракта, если оно есть; `provenance` заполнен,
  если единицы удалось построить.
- `warnings` — строки, не меняющие цвет: `baseline.reconciliation=mismatch`
  (пересчёт по корзине расходится с отчётом; используется значение отчёта),
  жёлтый допуск автоассессора.
- `judge_bias` — применённая поправка на смещение судьи `{mean, ci_lower,
  ci_upper, applied}` из `calibration_metrics` ассессора; `null`, если
  ассессор смещение не публикует. `km_monitoring` и `interval` уже с поправкой,
  `interval.method` получает суффикс `+bias`.
- `metric_details` — те же числа с русскими ключами; `Порог минимальной
  дельты КМ` равен `red_threshold`.

## Падение против деградации

Нода падает исключением, когда контракт данных нарушен:

| Причина | Исключение |
|---|---|
| `monitoring_metric` не dict, неизвестная версия контракта/UMR, нет обязательных полей, `score_column != main_metric` | `MonitoringContractError` |
| `scored_df` не приводится к UMR (нет `query_id`/`input_query`/`output_answer`, пустой `query_id`, смешаны формы); `main_metric` не константен внутри `dialogue`-группы; вес `input_query_count` не число или `<= 0` | `MonitoringContractError` |
| `assessment_result` подан и не является dict; `scored_df` не DataFrame при поданном `metric_spec` | `TypeError` |
| `perv_validation_km` не приводится к числу; `delta_unit` вне `absolute`/`relative` | `ValueError` |

Всё остальное — серый светофор, `status = not_computable`, причина в `reason` и `reason_code`:

| Событие | `reason_code` |
|---|---|
| Контракт `monitoring_metric` со статусом `not_computable` | `upstream_not_computable` |
| `assessment_result.status != computed` | `assessment_not_computable` |
| `metric_spec.status = not_computable`, нет `main_metric`, колонки селектора непригодны | `metric_spec` |
| Baseline отсутствует или `<= 0` | `baseline_not_positive` |
| Доля отказов судьи выше `max_invalid_share` | `judge_refusals` |
| Оценённых единиц меньше `min_valid_units` | `insufficient_units` |
| `calibration_metrics.admission_status` ассессора `red` или `not_assessed` | `judge_not_admitted` |
| Интервал смещения судьи шире допустимого снижения δ | `judge_bias_uncertain` |

`missing_policy` контракта применяется к пропускам разметки в эталоне; на
отказы судьи в мониторинге она не действует.

## Внешние сервисы

Не применимо: нода не обращается к LLM, эмбеддингам или HDFS; расчёт
детерминирован.

## Наблюдаемость

В лог платформы уходят две строки через логгер модуля (см. пример выше):
настройки прогона и итог с интервалом и знаменателями. Порта журнала нет,
источник истины о прогоне — `all_results`: триаж на сотне прогонов делается
по `status`, `color`, `reason_code`, `interval` и `provenance`.

## Карта кода

```text
main.py                    точка входа платформы: настройки, вызов теста, сборка all_results, цвет для платформы
km_dynamics.py             материализация main_metric по metric_spec, единицы и отказы, минимумы, вызов вердикта
verdict.py                 интервал (Уилсон / нормальный, n по Кишу), снижение в единицах δ, правило цвета
km_report.py               HTML-отчёт и график КМ
html_report_helper.py      виджет светофора и таблица критериев для HTML
laim_monitoring/           контракт laim-monitoring-metric.v2 (core.py): валидация, нормализация UMR, единицы
utils.py                   transform_to_int; в sourceFiles не входит и нодой не вызывается
tests/                     test_verdict.py (интервал и правило цвета), test_scored_output_contract.py (контракт выхода, минимумы, отказы, настройки)
```

## Что делать, если

- **Серый `insufficient_units` при живом агенте** — оценённых единиц меньше
  `min_valid_units`: увеличьте окно, выборку ассессора или, для пилота,
  снизьте минимум осознанно и зафиксируйте это в параметрах мониторинга.
- **Жёлтый при небольшом снижении** — интервал широк или судья строже
  разметчиков (точечное снижение 0.10 при пессимистичной границе 0.19):
  нужны единицы либо поправка на смещение судьи; красным это не станет без
  подтверждения интервалом.
- **Серый с `reason_code = metric_spec`** — на порт `metric_spec` не
  подключён `laim-kriteria-selector.metric_spec` или ассессор не отдал
  `main_metric`; проверьте `assessment_result.status` и колонки `scored_data`.
- **`MonitoringContractError` про константность внутри `dialogue`** — ассессор
  выставил разные баллы репликам одной сессии; в режиме `dialogue` балл один
  на сессию.
- **Нужен другой baseline, чем в отчёте о валидации** — подать
  `perv_validation_km` (`{"name": ..., "value": ...}`); он главнее контракта.

## Деплой

База `py312-simple`, точка входа — функция `main` в `main.py`,
`descriptor.json` перечисляет `sourceFiles`: `main.py`, `km_dynamics.py`,
`km_report.py`, `verdict.py`, `html_report_helper.py`,
`laim_monitoring/__init__.py`, `laim_monitoring/core.py`; тест
`test_descriptor_declares_settings_and_sources` закрепляет настройки и
`verdict.py` в списке. Нода самодостаточна: контракт лежит в вендорной копии
`laim_monitoring/` (совпадает с копиями local-drift, oos-oot, assessor).
Зависимости `requirements.txt`: `pandas`, `matplotlib` (график), `ipython`
(`IPython.display` в `html_report_helper.py`), `jinja2` (`Styler.to_html` в
pandas). Проверка: `ruff check .` и `python -m pytest -q` из корня ноды на
Python 3.12 (так же настроен CI).

## Глоссарий

- **КМ** — ключевая метрика качества агента; имя и baseline приходят в
  контракте `monitoring_metric`.
- **Baseline (КМ_база)** — КМ первичной валидации: `baseline.value` контракта или `perv_validation_km`.
- **δ (delta)** — допустимое снижение КМ; `km_delta` — точечное снижение в единицах `delta_unit`.
- **C_MIN** — минимально допустимый уровень КМ (`c_min`).
- **Единица оценки** — строка в `qa`/`turn_with_history`, сессия в `dialogue`; оценка единицы — `main_metric` ассессора.
- **Отказ судьи** — единица без оценки (`NaN`), считается в `refused_units`.
- **Интервал** — 95 % интервал КМ мониторинга; цвет выставляется по его границам.

### Обязательный допуск судьи

Для вычисления требуется `assessment_result.status=computed` и
`calibration_metrics.admission_status` со значением `green` или `amber`.
Отсутствие допуска, неизвестное значение, `red` и `not_assessed` дают
`not_computable/gray`, `reason_code=judge_not_admitted`. Одного `acc_auto`,
даже равного 1, недостаточно. Порты остаются необязательными для корректной
передачи отказа; отсутствие порта не означает допуск.

Этот гейт проверяет наличие допуска, но не доказывает корректность его
расчёта и перенос на новую популяцию. Учет кластеров и актуальная оценка
ошибки судьи остаются отдельными условиями достоверности интервала.
