"""Атомарная канонизация monitoring-данных в плоский UMR.

Поддерживаются ровно два входных контракта:

* готовый ``laim-umr.v2`` с колонками ``query_id``, ``input_query`` и
  ``output_answer``;
* сырые AEF-строки с ``trace_id``, ``aef_kind``, ``input_text`` и
  ``output_text``. Каждая carrier-строка ``start_agent`` или
  ``input_request`` становится отдельным turn; проверяются все carrier-строки
  каждого трейса.

Если в одном трейсе несколько turns, им нужен явный уникальный ``query_id``
либо идентификатор выводится из ``trace_id`` и явного ``turn_index`` или
уникального ``start_time_ns``. Для ``turn_with_history`` и ``dialogue`` также
нужны явная группа и порядок. Физический порядок строк не используется.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from numbers import Integral, Real
from typing import Any

import pandas as pd

from .core import MonitoringContractError, validate_monitoring_metric

_METRIC_VERSION = "laim-monitoring-metric.v2"
_FLAT_REQUIRED = ("query_id", "input_query", "output_answer")
_AEF_REQUIRED = ("trace_id", "aef_kind", "input_text", "output_text")
_CARRIER_KINDS = {"start_agent", "input_request"}
_ASSESSMENT_MODES = {"qa", "turn_with_history", "dialogue"}
_CONTEXT_MODES = {"turn_with_history", "dialogue"}
_QUESTION_PATHS = (
    ("input_query",),
    ("main_prompt",),
    ("user_question",),
    ("query",),
    ("text",),
    ("message",),
    ("message", "content", "message"),
    ("incoming", "content", "message"),
)
_ANSWER_PATHS = (
    ("output_answer",),
    ("final_response",),
    ("agent_answer",),
    ("answer",),
    ("response",),
    ("text",),
    ("outgoing", "content", "message"),
    ("message", "content", "message"),
)


class MonitoringCanonicalizationError(MonitoringContractError):
    """Весь пакет отклонён; ``report`` содержит точные причины по трейсам."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _key(value: object) -> tuple[str, str]:
    return type(value).__name__, str(value)


def _error(trace_id: object, reason: str) -> dict[str, object]:
    return {"trace_id": None if trace_id is None else str(trace_id), "reason": reason}


def _report(input_traces: int, output_turns: int, errors: list[dict[str, object]]) -> dict[str, object]:
    return {
        "input_traces": input_traces,
        "output_turns": output_turns,
        "errors": errors,
    }


def _fail(input_traces: int, errors: list[dict[str, object]]) -> None:
    reason = errors[0]["reason"] if errors else "Monitoring dataset не канонизирован"
    raise MonitoringCanonicalizationError(str(reason), _report(input_traces, 0, errors))


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, (list, tuple)):
        return None
    parts = []
    for item in value:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif isinstance(item, dict):
            candidate = item.get("value")
            if item.get("type") in (None, "text") and isinstance(candidate, str) and candidate.strip():
                parts.append(candidate.strip())
    return " ".join(parts) if parts else None


def _path(payload: object, names: tuple[str, ...]) -> object | None:
    current = payload
    for name in names:
        if not isinstance(current, dict) or name not in current:
            return None
        current = current[name]
    return current


def _payload_text(value: object, paths: tuple[tuple[str, ...], ...], field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} должен быть непустой строкой")
    raw = value.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    direct = _text(payload)
    if direct is not None:
        return direct
    candidates = list(dict.fromkeys(
        text for path in paths if (text := _text(_path(payload, path))) is not None
    ))
    if not candidates:
        raise ValueError(f"{field} не содержит поддерживаемого текстового поля")
    if len(candidates) != 1:
        raise ValueError(f"{field} содержит несколько разных поддерживаемых значений")
    return candidates[0]


def _turn_index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError("turn_index должен быть целым числом типа Integral >= 1")
    return int(value)


def _timestamp(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError("start_time_ns должен быть числом")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("start_time_ns должен быть числом") from exc
    if not result.is_finite():
        raise ValueError("start_time_ns должен быть конечным числом")
    return result


def _trace_label(frame: pd.DataFrame, position: int) -> object:
    for column in ("trace_id", "query_id"):
        if column in frame and not _blank(frame.iloc[position][column]):
            return frame.iloc[position][column]
    return f"row-{position}"


def _input_trace_count(frame: pd.DataFrame) -> int:
    column = "trace_id" if "trace_id" in frame else "query_id" if "query_id" in frame else None
    if column is None:
        return 0
    return len({_key(value) for value in frame[column].tolist() if not _blank(value)})


def _validate_turns(frame: pd.DataFrame) -> list[dict[str, object]]:
    errors = []
    ids: dict[tuple[str, str], list[int]] = {}
    for position in range(len(frame)):
        query_id = frame.iloc[position]["query_id"]
        if _blank(query_id):
            errors.append(_error(_trace_label(frame, position), "query_id пуст"))
        else:
            ids.setdefault(_key(query_id), []).append(position)
        for column in ("input_query", "output_answer"):
            value = frame.iloc[position][column]
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    _error(_trace_label(frame, position), f"{column} должен быть непустой строкой")
                )
    for positions in ids.values():
        if len(positions) > 1:
            for position in positions:
                errors.append(_error(_trace_label(frame, position), "query_id должен быть уникальным"))
    return errors


def _ordered_context(frame: pd.DataFrame) -> tuple[pd.DataFrame | None, list[dict[str, object]]]:
    if "reference_group_id" not in frame:
        return None, [_error(None, "Контекстный режим требует reference_group_id")]
    groups: dict[tuple[str, str], list[int]] = {}
    errors = []
    for position, group in enumerate(frame["reference_group_id"].tolist()):
        if _blank(group):
            errors.append(_error(_trace_label(frame, position), "reference_group_id должен быть заполнен"))
        else:
            groups.setdefault(_key(group), []).append(position)
    if errors:
        return None, errors

    result = frame.copy(deep=True)
    has_explicit_order = "turn_index" in result and any(
        not _blank(value) for value in result["turn_index"].tolist()
    )
    ordered = []
    if has_explicit_order:
        for group in sorted(groups):
            indexed = []
            for position in groups[group]:
                try:
                    indexed.append((_turn_index(result.iloc[position]["turn_index"]), position))
                except ValueError as exc:
                    errors.append(_error(_trace_label(result, position), str(exc)))
            indexes = [index for index, _position in indexed]
            if (
                len(indexed) == len(groups[group])
                and (len(indexes) != len(set(indexes)) or min(indexes) != 1)
            ):
                errors.append(
                    _error(
                        _trace_label(result, groups[group][0]),
                        "turn_index внутри группы должен быть уникальным и содержать 1",
                    )
                )
            ordered.extend(position for _index, position in sorted(indexed))
    else:
        if "start_time_ns" not in result:
            return None, [_error(None, "Контекстный режим требует turn_index или start_time_ns")]
        result["turn_index"] = None
        column = result.columns.get_loc("turn_index")
        for group in sorted(groups):
            timed = []
            for position in groups[group]:
                try:
                    timed.append((_timestamp(result.iloc[position]["start_time_ns"]), position))
                except ValueError as exc:
                    errors.append(_error(_trace_label(result, position), str(exc)))
            timestamps = [timestamp for timestamp, _position in timed]
            if len(timed) == len(groups[group]) and len(timestamps) != len(set(timestamps)):
                errors.append(
                    _error(_trace_label(result, groups[group][0]), "start_time_ns внутри группы должен быть уникальным")
                )
            for index, (_time, position) in enumerate(sorted(timed), start=1):
                result.iat[position, column] = index
                ordered.append(position)
        if not errors:
            result["turn_index"] = result["turn_index"].astype(int)
    if errors:
        return None, errors
    return result.iloc[ordered].reset_index(drop=True), []


def _flat_umr(frame: pd.DataFrame, assessment_mode: str) -> tuple[pd.DataFrame, dict[str, object]]:
    input_traces = _input_trace_count(frame)
    result = frame.copy(deep=True)
    errors = _validate_turns(result)
    if errors:
        _fail(input_traces, errors)
    has_group = "reference_group_id" in result
    has_order = "turn_index" in result
    if has_group != has_order:
        _fail(
            input_traces,
            [_error(None, "Flat UMR задаёт reference_group_id и turn_index только вместе")],
        )
    if has_group or assessment_mode in _CONTEXT_MODES:
        ordered, errors = _ordered_context(result)
        if errors:
            _fail(input_traces, errors)
        if assessment_mode in _CONTEXT_MODES:
            result = ordered
    return result, _report(input_traces, len(result), [])


def _raw_query_ids(
    frame: pd.DataFrame,
    trace_id: object,
    carriers: list[int],
) -> tuple[list[object] | None, list[dict[str, object]]]:
    if "query_id" in frame:
        values = [frame.iloc[position]["query_id"] for position in carriers]
        if any(_blank(value) for value in values):
            return None, [_error(trace_id, "query_id должен быть заполнен у каждого carrier turn")]
        if len({_key(value) for value in values}) != len(values):
            return None, [_error(trace_id, "query_id должен быть уникальным внутри trace_id")]
        return values, []
    if len(carriers) == 1:
        return [trace_id], []

    if "turn_index" in frame and any(not _blank(frame.iloc[position]["turn_index"]) for position in carriers):
        try:
            indexes = [_turn_index(frame.iloc[position]["turn_index"]) for position in carriers]
        except ValueError as exc:
            return None, [_error(trace_id, str(exc))]
        if len(indexes) != len(set(indexes)):
            return None, [_error(trace_id, "turn_index должен быть уникальным внутри trace_id")]
        return [f"{trace_id}:turn:{index}" for index in indexes], []

    if "start_time_ns" in frame:
        try:
            times = [_timestamp(frame.iloc[position]["start_time_ns"]) for position in carriers]
        except ValueError as exc:
            return None, [_error(trace_id, str(exc))]
        if len(times) != len(set(times)):
            return None, [_error(trace_id, "start_time_ns должен быть уникальным внутри trace_id")]
        return [f"{trace_id}:time:{time.normalize()}" for time in times], []
    return None, [
        _error(trace_id, "Нескольким carrier turns нужны unique query_id, turn_index или start_time_ns")
    ]


def _optional(row: pd.Series, column: str) -> object | None:
    value = row[column] if column in row.index else None
    return None if _blank(value) else value


def _raw_turn(
    row: pd.Series,
    position: int,
    trace_id: object,
    query_id: object,
    assessment_mode: str,
    source_columns: list[str],
) -> tuple[dict[str, object] | None, str | None]:
    try:
        input_query = _payload_text(row["input_text"], _QUESTION_PATHS, "input_text")
        output_answer = _payload_text(row["output_text"], _ANSWER_PATHS, "output_text")
    except ValueError as exc:
        return None, f"carrier row {position}: {exc}"

    record = {
        "query_id": query_id,
        "input_query": input_query,
        "output_answer": output_answer,
        "trace_id": trace_id,
    }
    explicit_group = _optional(row, "reference_group_id")
    session_id = _optional(row, "session_id")
    raw_turn_index = _optional(row, "turn_index")
    raw_start_time = _optional(row, "start_time_ns")
    if session_id is not None:
        record["session_id"] = session_id
    has_order = raw_turn_index is not None or raw_start_time is not None
    if explicit_group is not None and not has_order:
        return None, f"carrier row {position}: reference_group_id требует order"
    group = explicit_group if explicit_group is not None else session_id
    if has_order and group is None:
        return None, f"carrier row {position}: order требует reference_group_id/session_id"
    materialize_context = assessment_mode in _CONTEXT_MODES or (group is not None and has_order)
    if materialize_context and group is not None:
        record["reference_group_id"] = group
    try:
        if materialize_context and raw_turn_index is not None:
            record["turn_index"] = _turn_index(raw_turn_index)
        if raw_start_time is not None:
            record["start_time_ns"] = _timestamp(raw_start_time)
    except ValueError as exc:
        return None, f"carrier row {position}: {exc}"
    for column in source_columns:
        if column in row.index and column not in record:
            record[column] = row[column]
    return record, None


def _raw_aef(
    frame: pd.DataFrame,
    assessment_mode: str,
    source_columns: list[str],
) -> tuple[pd.DataFrame, dict[str, object]]:
    traces: dict[tuple[str, str], list[int]] = {}
    errors = []
    for position, trace_id in enumerate(frame["trace_id"].tolist()):
        if _blank(trace_id):
            errors.append(_error(None, f"trace_id пуст в строке {position}"))
        else:
            traces.setdefault(_key(trace_id), []).append(position)

    records = []
    for trace_key in sorted(traces):
        positions = traces[trace_key]
        trace_id = frame.iloc[positions[0]]["trace_id"]
        carriers = [
            position for position in positions
            if str(frame.iloc[position]["aef_kind"]) in _CARRIER_KINDS
        ]
        if not carriers:
            errors.append(_error(trace_id, "В трейсе нет start_agent/input_request"))
            continue
        query_ids, query_errors = _raw_query_ids(frame, trace_id, carriers)
        if query_errors:
            errors.extend(query_errors)
            continue
        for position, query_id in zip(carriers, query_ids):
            record, reason = _raw_turn(
                frame.iloc[position], position, trace_id, query_id,
                assessment_mode, source_columns,
            )
            if reason is not None:
                errors.append(_error(trace_id, reason))
            else:
                records.append(record)

    input_traces = len(traces)
    if errors:
        _fail(input_traces, errors)
    result = pd.DataFrame(records)
    if result.empty:
        _fail(input_traces, [_error(None, "Ни один carrier turn не извлечён")])
    errors = _validate_turns(result)
    if errors:
        _fail(input_traces, errors)
    has_canonical_context = "reference_group_id" in result or "turn_index" in result
    if assessment_mode in _CONTEXT_MODES or has_canonical_context:
        result, errors = _ordered_context(result)
        if errors:
            _fail(input_traces, errors)
    else:
        order = sorted(range(len(result)), key=lambda position: _key(result.iloc[position]["query_id"]))
        result = result.iloc[order].reset_index(drop=True)
    return result, _report(input_traces, len(result), [])


def canonicalize_monitoring(
    trace_dataset: pd.DataFrame,
    monitoring_metric: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Вернуть ``(monitoring_umr, conversion_report)`` либо отклонить весь пакет."""

    if not isinstance(trace_dataset, pd.DataFrame):
        _fail(0, [_error(None, "trace_dataset должен быть pandas.DataFrame")])
    if trace_dataset.empty:
        _fail(0, [_error(None, "trace_dataset пуст")])
    input_traces = _input_trace_count(trace_dataset)
    if not isinstance(monitoring_metric, dict) or monitoring_metric.get("contract_version") != _METRIC_VERSION:
        _fail(input_traces, [_error(None, "Требуется laim-monitoring-metric.v2")])
    try:
        contract = validate_monitoring_metric(monitoring_metric, require_computed=False)
    except MonitoringContractError as exc:
        _fail(input_traces, [_error(None, str(exc))])
    assessment_mode = contract.get("assessment_mode")
    if assessment_mode not in _ASSESSMENT_MODES:
        _fail(input_traces, [_error(None, f"Недопустимое assessment_mode: {assessment_mode!r}")])
    source_columns = [
        source["column_name"]
        for source in contract.get("scoring", {}).get("sources", [])
        if isinstance(source, dict) and isinstance(source.get("column_name"), str)
    ]

    columns = set(trace_dataset.columns)
    if set(_FLAT_REQUIRED).issubset(columns):
        return _flat_umr(trace_dataset, assessment_mode)
    if set(_AEF_REQUIRED).issubset(columns):
        return _raw_aef(trace_dataset, assessment_mode, source_columns)
    _fail(
        input_traces,
        [_error(None, "trace_dataset не соответствует flat UMR или fixed AEF контракту")],
    )
    raise AssertionError("unreachable")
