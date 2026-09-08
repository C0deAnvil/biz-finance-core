from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Department, Role, User, Warehouse


def is_system_admin(user: User | None) -> bool:
    if user is None or user.role is None:
        return False
    return bool(user.role.is_admin) or user.role.code == "system_admin"


def is_master_data_admin(user: User | None) -> bool:
    return bool(user and user.role and user.role.code == "master_data_admin")


def _department_structures(db: Session) -> tuple[dict[int, Department], dict[int | None, list[int]]]:
    rows = db.execute(select(Department)).scalars().all()
    department_map = {row.id: row for row in rows}
    children_map: dict[int | None, list[int]] = defaultdict(list)
    for row in rows:
        children_map[row.parent_id].append(row.id)
    return department_map, children_map


def get_department_descendant_ids(db: Session, department_id: int | None) -> list[int]:
    if not department_id:
        return []
    _, children_map = _department_structures(db)
    result: list[int] = []
    stack = [department_id]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        stack.extend(children_map.get(current, []))
    return result


def get_department_path_names(db: Session, department_id: int | None) -> list[str]:
    if not department_id:
        return []
    unit_map, _ = _department_structures(db)
    result: list[str] = []
    cursor = unit_map.get(department_id)
    seen: set[int] = set()
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        result.append(cursor.name)
        cursor = unit_map.get(cursor.parent_id)
    return list(reversed(result))


def get_department_ancestor_ids(db: Session, department_id: int | None) -> list[int]:
    if not department_id:
        return []
    department_map, _ = _department_structures(db)
    result: list[int] = []
    seen: set[int] = set()
    cursor = department_map.get(department_id)
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        result.append(cursor.id)
        cursor = department_map.get(cursor.parent_id)
    return result


def _nearest_department_unit(db: Session, department_id: int | None, unit_type: str) -> Department | None:
    if not department_id:
        return None
    department_map, _ = _department_structures(db)
    seen: set[int] = set()
    cursor = department_map.get(department_id)
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        if cursor.unit_type == unit_type:
            return cursor
        cursor = department_map.get(cursor.parent_id)
    return None


def _descendant_ids_from_children(children_map: dict[int | None, list[int]], department_id: int | None) -> list[int]:
    if not department_id:
        return []
    result: list[int] = []
    stack = [department_id]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        stack.extend(children_map.get(current, []))
    return result


def get_scope_department_ids(db: Session, current_user: User) -> list[int]:
    department_id = current_user.department_id
    if not department_id:
        return []
    department_map, children_map = _department_structures(db)
    current_department = department_map.get(department_id)
    if current_department is None:
        return []

    scope = (current_user.data_scope or "self").strip()
    if scope == "team":
        if current_department.unit_type == "team":
            return _descendant_ids_from_children(children_map, current_department.id)
        return [current_department.id]
    if scope == "sub_department":
        anchor = current_department if current_department.unit_type == "sub_department" else _nearest_department_unit(
            db, current_department.id, "sub_department"
        )
        if anchor:
            return _descendant_ids_from_children(children_map, anchor.id)
        return [current_department.id]
    if scope == "department":
        anchor = current_department if current_department.unit_type == "department" else _nearest_department_unit(
            db, current_department.id, "department"
        )
        if anchor:
            return _descendant_ids_from_children(children_map, anchor.id)
        return [current_department.id]
    return [current_department.id]


def get_scoped_user_ids(db: Session, current_user: User, business_role_code: str) -> list[int] | None:
    if is_system_admin(current_user) or is_master_data_admin(current_user):
        return None
    role_code = current_user.role.code if current_user.role else None
    if role_code != business_role_code:
        return None
    if current_user.data_scope == "all":
        return [
            row.id
            for row in db.execute(
                select(User)
                .join(Role, User.role_id == Role.id)
                .where(
                    Role.code == business_role_code,
                    User.is_active.is_(True),
                )
            ).scalars().all()
        ]
    if current_user.data_scope == "self" or not current_user.department_id:
        return [current_user.id]
    if current_user.data_scope in {"team", "sub_department", "department"}:
        department_ids = get_scope_department_ids(db, current_user)
        if not department_ids:
            return [current_user.id]
        return [
            row.id
            for row in db.execute(
                select(User)
                .join(Role, User.role_id == Role.id)
                .where(
                    Role.code == business_role_code,
                    User.is_active.is_(True),
                    User.department_id.in_(department_ids),
                )
            ).scalars().all()
        ]
    return [current_user.id]


def user_in_scope(db: Session, current_user: User, *, business_role_code: str, owner_user_id: int | None) -> bool:
    if owner_user_id is None:
        return True
    scoped_user_ids = get_scoped_user_ids(db, current_user, business_role_code)
    if scoped_user_ids is None:
        return True
    return owner_user_id in scoped_user_ids


def can_manage_user_modules(db: Session, manager: User, target_user: User) -> bool:
    if not target_user or not target_user.is_active:
        return False
    if is_system_admin(manager):
        return manager.id != target_user.id
    if manager.role is None or target_user.role is None:
        return False
    if manager.data_scope not in {"team", "sub_department", "department", "all"}:
        return False
    if manager.role.code != target_user.role.code:
        return False
    scoped_user_ids = get_scoped_user_ids(db, manager, manager.role.code)
    if scoped_user_ids is None:
        return manager.id != target_user.id
    return target_user.id in scoped_user_ids and target_user.id != manager.id


def get_inventory_scope_warehouse_ids(db: Session, current_user: User) -> list[int] | None:
    """返回该用户可操作的仓库 id 列表;None 表示不受仓库限制(全部可见)。

    普通库管、普通质检员都按所属部门的仓库范围受限;
    对应的超级角色(inventory_super / quality_super)不受限。
    改造前本函数只对 inventory 生效,对 quality 直接返回 None,
    导致质检模块的仓库过滤形同虚设——普通质检员能看到所有仓库的任务。
    """
    if is_system_admin(current_user) or is_master_data_admin(current_user):
        return None
    role_code = current_user.role.code if current_user.role else None
    if role_code in {"inventory_super", "quality_super"}:
        return None  # 超级角色:所有仓库
    if role_code not in {"inventory", "quality"} or current_user.department_id is None:
        return None
    department_map, _ = _department_structures(db)
    warehouse_scope = ""
    seen: set[int] = set()
    cursor = department_map.get(current_user.department_id)
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        warehouse_scope = (cursor.warehouse_scope or "").strip()
        if warehouse_scope:
            break
        cursor = department_map.get(cursor.parent_id)
    if not warehouse_scope:
        return None
    return [
        row.id
        for row in db.execute(
            select(Warehouse).where(Warehouse.warehouse_type == warehouse_scope)
        ).scalars().all()
    ]


def warehouse_in_scope(db: Session, current_user: User, warehouse_id: int | None) -> bool:
    if warehouse_id is None:
        return True
    warehouse_ids = get_inventory_scope_warehouse_ids(db, current_user)
    if warehouse_ids is None:
        return True
    return warehouse_id in warehouse_ids


def build_department_tree(rows: list[Department]) -> list[dict]:
    children_map: dict[int | None, list[Department]] = defaultdict(list)
    for row in sorted(rows, key=lambda item: (item.sort_order, item.id)):
        children_map[row.parent_id].append(row)

    def serialize(node: Department) -> dict:
        return {
            "id": node.id,
            "code": node.code,
            "name": node.name,
            "description": node.description,
            "parent_id": node.parent_id,
            "unit_type": node.unit_type,
            "sort_order": node.sort_order,
            "is_active": node.is_active,
            "warehouse_scope": node.warehouse_scope,
            "path_name": " / ".join(_build_path_name(rows, node.id)),
            "children": [serialize(child) for child in children_map.get(node.id, [])],
        }

    return [serialize(node) for node in children_map.get(None, [])]


def _build_path_name(rows: list[Department], department_id: int) -> list[str]:
    unit_map = {row.id: row for row in rows}
    result: list[str] = []
    cursor = unit_map.get(department_id)
    seen: set[int] = set()
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        result.append(cursor.name)
        cursor = unit_map.get(cursor.parent_id)
    return list(reversed(result))
