from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import require_roles
from models import (
    Department,
    FulfillmentDemand,
    NotificationRecipient,
    OperationLog,
    PurchaseOrder,
    PurchasePaymentRequest,
    PurchaseReturn,
    Role,
    SalesOrder,
    SalesReceiptRequest,
    SalesReturn,
    TodoItem,
    User,
    UserModulePermission,
    ProductionTask,
)
from responses import api_response
from schemas.admin import OrgUnitCreate, OrgUnitUpdate, PasswordResetRequest, UserCreate, UserModulePermissionUpdate, UserToggleRequest, UserUpdate
from security import hash_password
from services.display import action_label, status_label
from services.navigation import build_role_menus, flatten_menu_paths, manageable_route_items
from services.organization import build_department_tree, can_manage_user_modules, get_department_path_names, get_scoped_user_ids, is_system_admin


router = APIRouter(prefix="/admin", tags=["系统管理"])


def _serialize_department(row: Department, db: Session) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "parent_id": row.parent_id,
        "unit_type": row.unit_type,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "warehouse_scope": row.warehouse_scope,
        "path_name": " / ".join(get_department_path_names(db, row.id)),
    }


def _serialize_user(row: User, db: Session) -> dict:
    return {
        "id": row.id,
        "username": row.username,
        "full_name": row.full_name,
        "department_id": row.department_id,
        "department": row.department.name if row.department else None,
        "department_path": " / ".join(get_department_path_names(db, row.department_id)) if row.department_id else None,
        "role_id": row.role_id,
        "role": row.role.name if row.role else None,
        "role_code": row.role.code if row.role else None,
        "job_title": row.job_title,
        "data_scope": row.data_scope,
        "phone": row.phone,
        "email": row.email,
        "is_active": row.is_active,
        "inherited_from_user_id": row.inherited_from_user_id,
        "data_successor_user_id": row.data_successor_user_id,
        "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
    }


def _transfer_user_data(db: Session, source_user_id: int, target_user_id: int) -> None:
    if source_user_id == target_user_id:
        raise HTTPException(status_code=409, detail="数据继承人不能与原用户相同")
    source_user = db.execute(select(User).where(User.id == source_user_id)).scalar_one_or_none()
    if source_user is None:
        raise HTTPException(status_code=404, detail="数据继承来源用户不存在")
    target_user = db.execute(select(User).where(User.id == target_user_id)).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="数据继承目标用户不存在")

    for row in db.execute(select(SalesOrder).where(SalesOrder.sales_user_id == source_user_id)).scalars().all():
        row.sales_user_id = target_user_id
    for row in db.execute(select(PurchaseOrder).where(PurchaseOrder.purchaser_user_id == source_user_id)).scalars().all():
        row.purchaser_user_id = target_user_id
    for row in db.execute(select(ProductionTask).where(ProductionTask.created_by_id == source_user_id)).scalars().all():
        row.created_by_id = target_user_id
    for row in db.execute(select(FulfillmentDemand).where(FulfillmentDemand.assigned_user_id == source_user_id)).scalars().all():
        row.assigned_user_id = target_user_id
    for row in db.execute(select(FulfillmentDemand).where(FulfillmentDemand.processed_by_id == source_user_id)).scalars().all():
        row.processed_by_id = target_user_id
    for row in db.execute(select(PurchasePaymentRequest).where(PurchasePaymentRequest.requested_by_id == source_user_id)).scalars().all():
        row.requested_by_id = target_user_id
    for row in db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.requested_by_id == source_user_id)).scalars().all():
        row.requested_by_id = target_user_id
    for row in db.execute(select(SalesReturn).where(SalesReturn.requested_by_id == source_user_id)).scalars().all():
        row.requested_by_id = target_user_id
    for row in db.execute(select(PurchaseReturn).where(PurchaseReturn.returned_by_id == source_user_id)).scalars().all():
        row.returned_by_id = target_user_id
    for row in db.execute(select(TodoItem).where(TodoItem.assigned_user_id == source_user_id, TodoItem.status != "completed")).scalars().all():
        row.assigned_user_id = target_user_id


def _allowed_routes_for_user(user: User) -> set[str]:
    if not user.role:
        return set()
    # 使用当前路由清单而非用户上次登录时写入的导航快照，新增可授权模块可立即出现在权限分配页。
    role_navigation = build_role_menus().get(user.role.code, user.role.navigation_json or [])
    return flatten_menu_paths(role_navigation) - {"/dashboard", "/approvals"}


def _assigned_routes_for_user(db: Session, user_id: int) -> set[str]:
    return {
        row.route_path
        for row in db.execute(select(UserModulePermission).where(UserModulePermission.user_id == user_id)).scalars().all()
    }


def _managed_user_query(db: Session, current_user: User):
    query = select(User).options(joinedload(User.role), joinedload(User.department)).where(User.is_active.is_(True))
    if is_system_admin(current_user):
        return query.where(User.id != current_user.id)
    role_code = current_user.role.code if current_user.role else ""
    if not role_code or current_user.data_scope not in {"team", "sub_department", "department", "all"}:
        return query.where(User.id.in_([-1]))
    scoped_user_ids = get_scoped_user_ids(db, current_user, role_code)
    return query.where(User.id.in_(scoped_user_ids or [-1]), User.id != current_user.id)


@router.get("/org-units")
def list_org_units(
    flat: bool = Query(default=False),
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    rows = db.execute(select(Department).order_by(Department.sort_order.asc(), Department.id.asc())).scalars().all()
    if flat:
        return api_response(data=[_serialize_department(row, db) for row in rows])
    return api_response(data=build_department_tree(rows))


@router.post("/org-units")
def create_org_unit(
    payload: OrgUnitCreate,
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    duplicate_code = db.execute(select(Department).where(Department.code == payload.code)).scalar_one_or_none()
    if duplicate_code is not None:
        raise HTTPException(status_code=409, detail="组织编码已存在")
    duplicate_name = db.execute(select(Department).where(Department.name == payload.name)).scalar_one_or_none()
    if duplicate_name is not None:
        raise HTTPException(status_code=409, detail="组织名称已存在")
    if payload.parent_id is not None:
        parent = db.execute(select(Department).where(Department.id == payload.parent_id)).scalar_one_or_none()
        if parent is None:
            raise HTTPException(status_code=404, detail="上级组织不存在")
    row = Department(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return api_response(msg="组织单元已创建", data=_serialize_department(row, db))


@router.put("/org-units/{org_unit_id}")
def update_org_unit(
    org_unit_id: int,
    payload: OrgUnitUpdate,
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(Department).where(Department.id == org_unit_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="组织单元不存在")
    if payload.parent_id == org_unit_id:
        raise HTTPException(status_code=400, detail="上级组织不能设置为自身")
    if payload.parent_id is not None:
        parent = db.execute(select(Department).where(Department.id == payload.parent_id)).scalar_one_or_none()
        if parent is None:
            raise HTTPException(status_code=404, detail="上级组织不存在")
    duplicate_name = db.execute(select(Department).where(Department.name == payload.name, Department.id != org_unit_id)).scalar_one_or_none()
    if duplicate_name is not None:
        raise HTTPException(status_code=409, detail="组织名称已存在")
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return api_response(msg="组织单元已更新", data=_serialize_department(row, db))


@router.delete("/org-units/{org_unit_id}")
def delete_org_unit(
    org_unit_id: int,
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(Department).where(Department.id == org_unit_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="组织单元不存在")
    child_count = db.execute(select(func.count(Department.id)).where(Department.parent_id == org_unit_id)).scalar_one()
    user_count = db.execute(select(func.count(User.id)).where(User.department_id == org_unit_id)).scalar_one()
    if child_count or user_count:
        row.is_active = False
        db.commit()
        return api_response(msg="组织单元已停用，因存在下级组织或关联用户未执行物理删除", data={"id": row.id, "disabled": True})
    db.delete(row)
    db.commit()
    return api_response(msg="组织单元已删除")


@router.get("/users")
def list_users(current_user: User = Depends(require_roles("system_admin")), db: Session = Depends(get_db)):
    rows = db.execute(select(User).options(joinedload(User.role), joinedload(User.department)).order_by(User.created_at.desc())).scalars().all()
    return api_response(data=[_serialize_user(row, db) for row in rows])


@router.get("/users/options")
def list_user_options(
    current_user: User = Depends(require_roles("system_admin", "sales", "purchase", "inventory", "production", "quality", "finance", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(User).options(joinedload(User.role), joinedload(User.department)).where(User.is_active.is_(True)).order_by(User.full_name.asc(), User.id.asc())
    ).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "username": row.username,
                "full_name": row.full_name,
                "role_code": row.role.code if row.role else None,
                "role_name": row.role.name if row.role else None,
                "department_path": " / ".join(get_department_path_names(db, row.department_id)) if row.department_id else None,
            }
            for row in rows
        ]
    )


@router.get("/module-permissions/options")
def module_permission_options(current_user: User = Depends(require_roles("system_admin", "sales", "inventory", "purchase", "production", "quality", "finance")), db: Session = Depends(get_db)):
    assignable_users = db.execute(_managed_user_query(db, current_user).order_by(User.department_id.asc(), User.id.asc())).scalars().all()
    current_allowed = _allowed_routes_for_user(current_user)
    modules = []
    for item in manageable_route_items():
        if is_system_admin(current_user) or item["path"] in current_allowed:
            modules.append(item)
    return api_response(
        data={
            "users": [_serialize_user(row, db) for row in assignable_users if can_manage_user_modules(db, current_user, row)],
            "modules": modules,
        }
    )


@router.get("/module-permissions/{user_id}")
def get_module_permissions(
    user_id: int,
    current_user: User = Depends(require_roles("system_admin", "sales", "inventory", "purchase", "production", "quality", "finance")),
    db: Session = Depends(get_db),
):
    target_user = db.execute(select(User).options(joinedload(User.role), joinedload(User.department)).where(User.id == user_id)).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not can_manage_user_modules(db, current_user, target_user):
        raise HTTPException(status_code=403, detail="无权分配该用户权限")

    target_allowed = _allowed_routes_for_user(target_user)
    manager_allowed = _allowed_routes_for_user(current_user)
    allowed_paths = target_allowed if is_system_admin(current_user) else target_allowed & manager_allowed
    assigned_paths = _assigned_routes_for_user(db, target_user.id) & allowed_paths
    available_modules = [item for item in manageable_route_items() if item["path"] in allowed_paths]
    return api_response(
        data={
            "user": _serialize_user(target_user, db),
            "modules": available_modules,
            "route_paths": sorted(assigned_paths),
        }
    )


@router.put("/module-permissions/{user_id}")
def update_module_permissions(
    user_id: int,
    payload: UserModulePermissionUpdate,
    current_user: User = Depends(require_roles("system_admin", "sales", "inventory", "purchase", "production", "quality", "finance")),
    db: Session = Depends(get_db),
):
    target_user = db.execute(select(User).options(joinedload(User.role), joinedload(User.department)).where(User.id == user_id)).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not can_manage_user_modules(db, current_user, target_user):
        raise HTTPException(status_code=403, detail="无权分配该用户权限")

    target_allowed = _allowed_routes_for_user(target_user)
    manager_allowed = _allowed_routes_for_user(current_user)
    allowed_paths = target_allowed if is_system_admin(current_user) else target_allowed & manager_allowed
    requested_paths = {path for path in payload.route_paths if path in allowed_paths}

    existing_rows = db.execute(select(UserModulePermission).where(UserModulePermission.user_id == target_user.id)).scalars().all()
    existing_paths = {row.route_path for row in existing_rows}
    for row in existing_rows:
        if row.route_path in allowed_paths and row.route_path not in requested_paths:
            db.delete(row)
    for path in requested_paths - existing_paths:
        db.add(UserModulePermission(user_id=target_user.id, route_path=path, assigned_by_id=current_user.id))
    db.commit()
    return api_response(msg="模块权限已更新", data={"user_id": target_user.id, "route_paths": sorted(requested_paths)})


@router.post("/users")
def create_user(payload: UserCreate, current_user: User = Depends(require_roles("system_admin")), db: Session = Depends(get_db)):
    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="用户名已存在")
    department = db.execute(select(Department).where(Department.id == payload.department_id)).scalar_one_or_none()
    role = db.execute(select(Role).where(Role.id == payload.role_id)).scalar_one_or_none()
    if department is None or role is None:
        raise HTTPException(status_code=404, detail="组织单元或角色不存在")
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=hash_password("ChangeMe123!"),
        department_id=payload.department_id,
        role_id=payload.role_id,
        job_title=payload.job_title,
        data_scope=payload.data_scope,
        phone=payload.phone,
        email=payload.email,
        is_active=True,
        inherited_from_user_id=payload.inherit_from_user_id,
    )
    db.add(user)
    db.flush()
    if payload.inherit_from_user_id:
        _transfer_user_data(db, payload.inherit_from_user_id, user.id)
    db.commit()
    return api_response(data={"id": user.id, "default_password": "ChangeMe123!"})


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    department = db.execute(select(Department).where(Department.id == payload.department_id)).scalar_one_or_none()
    role = db.execute(select(Role).where(Role.id == payload.role_id)).scalar_one_or_none()
    if department is None or role is None:
        raise HTTPException(status_code=404, detail="组织单元或角色不存在")
    user.full_name = payload.full_name
    user.department_id = payload.department_id
    user.role_id = payload.role_id
    user.job_title = payload.job_title
    user.data_scope = payload.data_scope
    user.phone = payload.phone
    user.email = payload.email
    if payload.inherit_from_user_id and payload.inherit_from_user_id != user.inherited_from_user_id:
        _transfer_user_data(db, payload.inherit_from_user_id, user.id)
        user.inherited_from_user_id = payload.inherit_from_user_id
    db.commit()
    return api_response(data={"id": user.id})


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return api_response(msg="密码已重置")


@router.post("/users/{user_id}/toggle")
def toggle_user_status(
    user_id: int,
    payload: UserToggleRequest | None = None,
    current_user: User = Depends(require_roles("system_admin")),
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    next_status = not user.is_active
    if not next_status and payload and payload.successor_user_id:
        _transfer_user_data(db, user.id, payload.successor_user_id)
        user.data_successor_user_id = payload.successor_user_id
    user.is_active = not user.is_active
    db.commit()
    return api_response(data={"is_active": user.is_active, "data_successor_user_id": user.data_successor_user_id})


@router.get("/logs")
def list_logs(current_user: User = Depends(require_roles("system_admin")), db: Session = Depends(get_db)):
    rows = db.execute(select(OperationLog).options(joinedload(OperationLog.user)).order_by(OperationLog.created_at.desc())).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "user_name": row.user.full_name if row.user else None,
                "module": row.module,
                "action": row.action,
                "action_label": action_label(row.action),
                "content": row.content,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/todos")
def list_all_todos(current_user: User = Depends(require_roles("system_admin")), db: Session = Depends(get_db)):
    rows = db.execute(select(TodoItem).options(joinedload(TodoItem.assigned_user)).order_by(TodoItem.created_at.desc())).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "title": row.title,
                "module": row.module,
                "status": row.status,
                "status_label": status_label(row.status),
                "assigned_user": row.assigned_user.full_name if row.assigned_user else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/notifications")
def list_all_notifications(current_user: User = Depends(require_roles("system_admin")), db: Session = Depends(get_db)):
    rows = db.execute(
        select(NotificationRecipient)
        .options(joinedload(NotificationRecipient.notification), joinedload(NotificationRecipient.user))
        .order_by(NotificationRecipient.created_at.desc())
    ).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "notification_id": row.notification_id,
                "user_id": row.user_id,
                "user_name": row.user.full_name if row.user else None,
                "title": row.notification.title if row.notification else None,
                "content": row.notification.content if row.notification else None,
                "module": row.notification.module if row.notification else None,
                "is_read": row.is_read,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )
