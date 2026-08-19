from datetime import datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from config import settings
from database import SessionLocal
from models import User, UserSession
from security import create_token


SESSION_TOUCH_INTERVAL = timedelta(seconds=settings.session_touch_interval_seconds)


def _cookie_options(max_age: int) -> dict:
    options = {
        "max_age": max_age,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }
    if settings.cookie_domain:
        options["domain"] = settings.cookie_domain
    return options


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    access_expires_at: datetime,
    refresh_token: str,
    refresh_expires_at: datetime,
):
    access_max_age = max(int((access_expires_at - datetime.utcnow()).total_seconds()), 0)
    refresh_max_age = max(int((refresh_expires_at - datetime.utcnow()).total_seconds()), 0)
    response.set_cookie(settings.access_cookie_name, access_token, **_cookie_options(access_max_age))
    response.set_cookie(settings.refresh_cookie_name, refresh_token, **_cookie_options(refresh_max_age))


def clear_auth_cookies(response: Response):
    cookie_kwargs = {"path": "/"}
    if settings.cookie_domain:
        cookie_kwargs["domain"] = settings.cookie_domain
    response.delete_cookie(settings.access_cookie_name, **cookie_kwargs)
    response.delete_cookie(settings.refresh_cookie_name, **cookie_kwargs)


def get_access_token_from_request(request: Request, authorization: str | None = None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(settings.access_cookie_name)


def get_refresh_token_from_request(request: Request, payload_refresh_token: str | None = None) -> str | None:
    return payload_refresh_token or request.cookies.get(settings.refresh_cookie_name)


def create_user_session(db: Session, user: User, device_name: str | None = None) -> tuple[UserSession, str, datetime, str, datetime]:
    for session in db.execute(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.is_active.is_(True))
    ).scalars().all():
        session.is_active = False

    access_token, access_expires_at = create_token(
        str(user.id),
        settings.access_token_minutes,
        {"role": user.role.code},
    )
    refresh_token, refresh_expires_at = create_token(
        str(user.id),
        settings.refresh_token_minutes,
        {"type": "refresh"},
    )
    session = UserSession(
        user_id=user.id,
        session_token=access_token,
        refresh_token=refresh_token,
        device_name=device_name,
        expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        last_seen_at=datetime.utcnow(),
        is_active=True,
    )
    db.add(session)
    user.last_login_at = datetime.utcnow()
    return session, access_token, access_expires_at, refresh_token, refresh_expires_at


def load_active_refresh_session(db: Session, refresh_token: str) -> UserSession | None:
    return db.execute(
        select(UserSession)
        .options(joinedload(UserSession.user).joinedload(User.role), joinedload(UserSession.user).joinedload(User.department))
        .where(UserSession.refresh_token == refresh_token, UserSession.is_active.is_(True))
    ).scalar_one_or_none()


def rotate_access_session(db: Session, session: UserSession) -> tuple[str, datetime]:
    access_token, access_expires_at = create_token(
        str(session.user_id),
        settings.access_token_minutes,
        {"role": session.user.role.code},
    )
    session.session_token = access_token
    session.expires_at = access_expires_at
    session.last_seen_at = datetime.utcnow()
    return access_token, access_expires_at


def touch_session_activity(session_token: str, previous_last_seen_at: datetime | None):
    if previous_last_seen_at and datetime.utcnow() - previous_last_seen_at < SESSION_TOUCH_INTERVAL:
        return

    db = SessionLocal()
    try:
        session = db.execute(
            select(UserSession).where(
                UserSession.session_token == session_token,
                UserSession.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if session is None:
            return
        session.last_seen_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

