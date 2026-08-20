import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from config import settings
from services.request_trace import trace_sql


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)


@event.listens_for(engine, "before_cursor_execute")
def _trace_sql_start(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("yecai_query_start", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _trace_sql_end(conn, cursor, statement, parameters, context, executemany):
    started_at = conn.info.get("yecai_query_start", []).pop()
    trace_sql(statement, started_at, cursor.rowcount)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
