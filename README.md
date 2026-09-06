# Тест динамики ключевой метрики (KM Dynamics Test)

LAIM-нода Sber DS: сравнивает ключевую метрику (КМ) агента на мониторинге со
значением первичной валидации и выставляет светофор.

## Контракт

Нода не знает формулу КМ. Формула приходит контрактом `monitoring_metric`
(`laim-monitoring-metric.v2`) от `laim-baskets-adapter` и исполняется общим
пакетом `laim_monitoring` — тем же кодом, которым ассесор считает построчный
`main_metric`. Нода только агрегирует `main_metric` по правилам контракта
(единица оценки `assessment_mode`, reducer `mean`/`frequency_weighted_mean`,
`missing_policy`) и сравнивает с `baseline.value`.

| Вход | Что это |
|------|---------|
| `monitoring_metric` | контракт: формула КМ, источники, агрегация, baseline и статус его сверки с отчётом о валидации |
| `scored_df` | monitoring UMR от ассесора: разметка судьи в колонках контракта, построчный score в `main_metric` |
| `acc_auto` | точность ассесора на holdout эталонной корзины, только для отчёта |
| `assessment_result` | машинный статус ассесора; `not_computable` → серый; `scoring_semantics=judge_final_score` → считается готовый score судьи |

Настройка `min_units` (по умолчанию 30): меньше оценённых единиц — серый.

## Когда тест серый

Светофор считается только по сопоставимым числам. Серый выставляется, если:

- контракт `not_computable` (адаптер не построил план или baseline);
- `baseline.reconciliation != "match"`: адаптер не воспроизвёл значение отчёта
  на корзине, значит baseline и КМ мониторинга посчитаны разными формулами;
- ассесор вернул `assessment_result.status != "computed"` (в том числе при
  массовых отказах судьи);
- в `scored_df` нет входов формулы (колонок разметки контракта);
- оценённых единиц меньше `min_units`;
- baseline равен нулю (относительная дельта не определена).

## Пороги

| Цвет | Относительное снижение `(baseline - monitoring) / baseline` |
|------|---------|
| 🟢 | не более 15% |
| 🟡 | от 15% до 25% |
| 🔴 | 25% и более |

## Использование

```python
from main import main

result = main(
    acc_auto=0.9,
    monitoring_metric=monitoring_metric,   # контракт от laim-baskets-adapter
    scored_df=scored_df,                   # UMR с main_metric от ассесора
    assessment_result=assessment_result,
)
result["all_results"]["color"]         # green / amber / red / gray
result["all_results"]["km_baseline"]
result["all_results"]["km_monitoring"]
```

## Структура

```
main.py                     # Sber DS entrypoint: светофор, all_results, HTML
km_dynamics.py              # расчёт динамики: контрактная агрегация + отчёт
laim_monitoring/            # общий пакет 3.0.0 (contract, units, scoring, drift, formula), та же копия, что у ассесора
laim_monitoring/canonicalizer.py
html_report_helper.py       # HTML-компоненты отчёта
tests/test_km_dynamics.py   # round-trip baseline, матрица светофора, серые случаи
```

## Тесты

```bash
python3 -m pytest -q tests
```

Ключевое свойство, которое проверяют тесты: baseline из контракта
воспроизводится агрегацией эталонной корзины через `laim_monitoring`. Если
формула в `core.py` разойдётся с адаптером, тест round-trip упадёт.
