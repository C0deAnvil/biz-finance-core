"""Server-side request and SQL tracing for diagnostics."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any

from config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
operation_id_var: ContextVar[str | None] = ContextVar("operation_id", default=None)

trace_logger = logging.getLogger("yecai.trace")


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
    trace_logger.info(json.dumps(payload, ensure_ascii=False, default=str))


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
