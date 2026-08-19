from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user
from models import User, UserModulePermission, UserSession
from responses import api_response
from schemas.auth import LoginRequest, LoginResponse, RefreshRequest, UserProfile
from security import decode_token, verify_password
from services.auth_service import (
    clear_auth_cookies,
    create_user_session,
    get_refresh_token_from_request,
    load_active_refresh_session,
    rotate_access_session,
    set_auth_cookies,
)
from services.organization import get_department_path_names
from services.navigation import build_role_menus, filter_navigation_by_paths, flatten_menu_paths, merge_navigation_items


router = APIRouter(prefix="/auth", tags=["认证"])


def build_user_profile(user: User, db: Session) -> UserProfile:
    role_menus = build_role_menus()
    target_navigation = role_menus.get(user.role.code, [])
    if user.role.navigation_json != target_navigation:
        user.role.navigation_json = merge_navigation_items(user.role.navigation_json or [], target_navigation)
        db.flush()
    base_navigation = target_navigation
    base_paths = flatten_menu_paths(base_navigation)
    is_supervisor = (user.data_scope or "self") in {"team", "sub_department", "department", "all"}
    if not user.role.is_admin and not is_supervisor:
        base_paths.discard("/admin/module-permissions")
    permission_rows = db.execute(select(UserModulePermission).where(UserModulePermission.user_id == user.id)).scalars().all()
    assigned_paths = {row.route_path for row in permission_rows}
    if user.role.is_admin:
        allowed_paths = base_paths
    elif assigned_paths:
        core_paths = {"/dashboard", "/approvals"}
        if is_supervisor:
            core_paths.add("/admin/module-permissions")
        allowed_paths = (assigned_paths & base_paths) | core_paths
    else:
        allowed_paths = {"/dashboard", "/approvals"}
        if is_supervisor:
            allowed_paths.add("/admin/module-permissions")
    if user.role.code in {"sales", "purchase", "manager", "chairman", "system_admin", "master_data_admin"} and "/standard-prices" in base_paths:
        allowed_paths.add("/standard-prices")
    navigation = filter_navigation_by_paths(base_navigation, allowed_paths)
    return UserProfile(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role_code=user.role.code,
        role_name=user.role.name,
        department_id=user.department_id,
        department_name=user.department.name if user.department else None,
        department_path=" / ".join(get_department_path_names(db, user.department_id)) if user.department_id else None,
        job_title=user.job_title,
        data_scope=user.data_scope,
        is_admin=user.role.is_admin,
        navigation=navigation,
    )


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.execute(
        select(User)
        .options(joinedload(User.role), joinedload(User.department))
        .where(User.username == payload.username)
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")

    _, access_token, access_expires_at, refresh_token, refresh_expires_at = create_user_session(db, user, payload.device_name)
    db.commit()
    set_auth_cookies(
        response,
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires_at,
    )

    data = LoginResponse(
        expires_at=access_expires_at,
        refresh_expires_at=refresh_expires_at,
        user=build_user_profile(user, db),
        auth_mode="cookie",
    )
    return api_response(data=data.model_dump())


@router.post("/refresh")
def refresh_token(
    response: Response,
    request: Request,
    payload: RefreshRequest | None = Body(default=None),
    db: Session = Depends(get_db),
):
    refresh_token_value = get_refresh_token_from_request(request, payload.refresh_token if payload else None)
    if not refresh_token_value:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")

    try:
        claims = decode_token(refresh_token_value)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="刷新令牌无效")

    session = load_active_refresh_session(db, refresh_token_value)
    if session is None:
        raise HTTPException(status_code=401, detail="刷新会话无效")

    access_token, access_expires_at = rotate_access_session(db, session)
    db.commit()
    set_auth_cookies(
        response,
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=refresh_token_value,
        refresh_expires_at=session.refresh_expires_at,
    )

    return api_response(
        data={
            "expires_at": access_expires_at.isoformat(),
            "refresh_expires_at": session.refresh_expires_at.isoformat(),
            "user": build_user_profile(session.user, db).model_dump(),
            "auth_mode": "cookie",
        }
    )


@router.get("/me")
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return api_response(data=build_user_profile(current_user, db).model_dump())


@router.post("/logout")
def logout(response: Response, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sessions = db.execute(
        select(UserSession).where(UserSession.user_id == current_user.id, UserSession.is_active.is_(True))
    ).scalars().all()
    for session in sessions:
        session.is_active = False
    db.commit()
    clear_auth_cookies(response)
    return api_response(msg="已退出登录")
