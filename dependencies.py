from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserSession
from security import decode_token
from services.auth_service import get_access_token_from_request, touch_session_activity


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    token = get_access_token_from_request(request, authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌")
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    session = db.execute(
        select(UserSession).where(
            UserSession.session_token == token,
            UserSession.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效，请重新登录")

    user = db.execute(select(User).where(User.id == int(payload["sub"]), User.is_active.is_(True))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")

    touch_session_activity(token, session.last_seen_at)
    return user


# 超级角色 → 基础角色映射:超级角色拥有普通角色全部权限
SUPER_ROLE_BASE_MAP = {
    "inventory_super": "inventory",
    "quality_super": "quality",
    "finance_super": "finance",
}


def require_roles(*allowed_codes: str):
    def dependency(current_user: User = Depends(get_current_user)):
        role_code = current_user.role.code if current_user.role else None
        if current_user.role and current_user.role.is_admin:
            return current_user
        effective_codes = {role_code}
        base_code = SUPER_ROLE_BASE_MAP.get(role_code or "")
        if base_code:
            effective_codes.add(base_code)
        if not effective_codes.intersection(allowed_codes):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return current_user

    return dependency


def is_super_role(current_user: User) -> bool:
    """是否任一超级角色。"""
    return bool(current_user.role and current_user.role.code in SUPER_ROLE_BASE_MAP)
