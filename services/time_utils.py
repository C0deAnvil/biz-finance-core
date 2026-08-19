from datetime import datetime, timedelta


LOCAL_UTC_OFFSET = timedelta(hours=8)


def now_local() -> datetime:
    return datetime.utcnow() + LOCAL_UTC_OFFSET
