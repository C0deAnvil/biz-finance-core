"""Server-side request and SQL tracing for diagnostics."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any

from config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
operation_id_var: ContextVar[str | None] = ContextVar("operation_id", default=None)

trace_logger = logging.getLogger("yecai.trace")
trace_logger.setLevel(logging.INFO)
trace_logger.propagate = True
trace_file_path: str | None = None


def _configure_trace_file_handler() -> None:
    global trace_file_path
    file_path = settings.trace_log_file
    if not file_path:
        return
    absolute_path = os.path.abspath(file_path)
    trace_file_path = absolute_path
    if any(getattr(handler, "baseFilename", None) == absolute_path for handler in trace_logger.handlers):
        return
    try:
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        handler = logging.FileHandler(absolute_path, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        trace_logger.addHandler(handler)
    except OSError:
        trace_logger.warning("Unable to open trace log file: %s", absolute_path)


_configure_trace_file_handler()


def new_trace_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def current_trace() -> dict[str, str | None]:
    return {
        "request_id": request_id_var.get(),
        "operation_id": operation_id_var.get(),
    }


def emit(event: str, **fields: Any) -> None:
    if not settings.trace_enabled:
        return
    payload = {
        "event": event,
        "timestamp": time.time(),
        "thread_id": threading.get_ident(),
        **current_trace(),
        **fields,
    }
    line = json.dumps(payload, ensure_ascii=False, default=str)
    trace_logger.info(line)
    if trace_file_path:
        try:
            with open(trace_file_path, "a", encoding="utf-8") as trace_file:
                trace_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO yecai.trace {line}\n")
                trace_file.flush()
        except OSError:
            trace_logger.exception("Unable to append trace event to %s", trace_file_path)


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def trace_stage(stage: str, started_at: float, **fields: Any) -> None:
    duration_ms = elapsed_ms(started_at)
    if duration_ms >= settings.trace_stage_log_ms:
        emit("request_stage", stage=stage, duration_ms=duration_ms, **fields)


def trace_sql(statement: str, started_at: float, rowcount: int | None = None) -> None:
    duration_ms = elapsed_ms(started_at)
    if duration_ms < settings.slow_sql_ms:
        return
    emit(
        "slow_sql",
        duration_ms=duration_ms,
        rowcount=rowcount,
        sql=" ".join(statement.split())[:settings.trace_sql_max_length],
    )
