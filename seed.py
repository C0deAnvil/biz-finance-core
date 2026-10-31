from sqlalchemy import select
from sqlalchemy.orm import Session

from models import BOM, Department, PackagingConfig, PurchaseOrderItem, Role, SalesOrderItem, User, UserModulePermission, Warehouse
from security import hash_password
from services.display import calculate_bag_count, packaging_name_needs_repair
from services.navigation import build_role_menus


ROLE_MENUS = build_role_menus()


def _menu_paths(navigation: list[dict]) -> set[str]:
    result: set[str] = set()
    for item in navigation:
        children = item.get("children") or []
        if children:
            result.update(_menu_paths(children))
        elif item.get("path") not in {None, "/dashboard", "/approvals"}:
            result.add(item["path"])
    return result


def _upsert_department(
    db: Session,
    *,
    code: str,
    name: str,
    description: str,
    parent_id: int | None = None,
    unit_type: str = "department",
    sort_order: int = 0,
    warehouse_scope: str | None = None,
) -> Department:
    department = db.execute(select(Department).where(Department.code == code)).scalar_one_or_none()
    if department is None:
        department = Department(code=code, name=name)
        db.add(department)
        db.flush()
    department.name = name
    department.description = description
    department.parent_id = parent_id
    department.unit_type = unit_type
    department.sort_order = sort_order
    department.is_active = True
    department.warehouse_scope = warehouse_scope
    return department


def _upsert_role(
    db: Session,
    *,
    code: str,
    name: str,
    description: str,
    is_admin: bool = False,
) -> Role:
    role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
    if role is None:
        role = Role(code=code, name=name, home_route="/dashboard")
        db.add(role)
        db.flush()
    role.name = name
    role.description = description
    role.home_route = "/dashboard"
    role.navigation_json = ROLE_MENUS.get(code, [])
    role.scope_json = ["*"] if code in {"system_admin", "master_data_admin"} else [code]
    role.is_admin = is_admin
    return role


def _upsert_user(
    db: Session,
    *,
    username: str,
    full_name: str,
    department_id: int | None,
    role_id: int,
    password: str,
    job_title: str,
    data_scope: str = "self",
    is_active: bool = True,
):
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        user = User(username=username, hashed_password=hash_password(password))
        db.add(user)
    user.full_name = full_name
    user.department_id = department_id
    user.role_id = role_id
    user.job_title = job_title
    user.data_scope = data_scope
    user.is_active = is_active


def _grant_role_modules(db: Session, user: User, role: Role) -> None:
    existing_paths = {
        row.route_path
        for row in db.execute(select(UserModulePermission).where(UserModulePermission.user_id == user.id)).scalars().all()
    }
    for path in _menu_paths(role.navigation_json or []):
        if path not in existing_paths:
            db.add(UserModulePermission(user_id=user.id, route_path=path, assigned_by_id=user.id))


def init_seed_data(db: Session, default_password: str):
    sales = _upsert_department(db, code="sales", name="销售中心", description="销售业务一级部门", sort_order=10)
    purchase = _upsert_department(db, code="purchase", name="采购中心", description="采购业务一级部门", sort_order=20)
    inventory = _upsert_department(db, code="inventory", name="仓储中心", description="仓储与库存管理一级部门", sort_order=30)
    production = _upsert_department(db, code="production", name="生产中心", description="生产业务一级部门", sort_order=40)
    quality = _upsert_department(db, code="quality", name="质量中心", description="质量检验一级部门", sort_order=50)
    finance = _upsert_department(db, code="finance", name="财务中心", description="财务业务一级部门", sort_order=60)
    executive = _upsert_department(db, code="executive", name="经营管理层", description="经理与董事长所属组织", sort_order=70)
    it_center = _upsert_department(db, code="it_center", name="信息管理中心", description="系统运维与平台管理", sort_order=80)
    master_data = _upsert_department(db, code="master_data", name="主数据中心", description="主数据与参数维护", sort_order=90)

    _upsert_department(
        db,
        code="sales_division_1",
        name="销售一部",
        description="销售中心下属子部门",
        parent_id=sales.id,
        unit_type="sub_department",
        sort_order=1,
    )
    _upsert_department(
        db,
        code="sales_division_2",
        name="销售二部",
        description="销售中心下属子部门",
        parent_id=sales.id,
        unit_type="sub_department",
        sort_order=2,
    )
    _upsert_department(
        db,
        code="purchase_division_1",
        name="采购一部",
        description="采购中心下属子部门",
        parent_id=purchase.id,
        unit_type="sub_department",
        sort_order=1,
    )
    _upsert_department(
        db,
        code="purchase_division_2",
        name="采购二部",
        description="采购中心下属子部门",
        parent_id=purchase.id,
        unit_type="sub_department",
        sort_order=2,
    )
    _upsert_department(
        db,
        code="inventory_cangzhou_finished",
        name="沧州成品库管",
        description="沧州成品仓库存作业单元",
        parent_id=inventory.id,
        unit_type="sub_department",
        sort_order=1,
        warehouse_scope="finished",
    )
    _upsert_department(
        db,
        code="inventory_langfang_raw",
        name="廊坊原料库管",
        description="廊坊原料仓库存作业单元",
        parent_id=inventory.id,
        unit_type="sub_department",
        sort_order=2,
        warehouse_scope="raw",
    )
    db.flush()
    department_map = {item.code: item for item in db.execute(select(Department)).scalars().all()}

    team_seed = [
        ("sales_division_1_team_a", "销售一部一组", "销售一部下属小组", "sales_division_1", "team", 1, None),
        ("sales_division_2_team_a", "销售二部一组", "销售二部下属小组", "sales_division_2", "team", 1, None),
        ("purchase_division_1_team_a", "采购一部一组", "采购一部下属小组", "purchase_division_1", "team", 1, None),
        ("purchase_division_2_team_a", "采购二部一组", "采购二部下属小组", "purchase_division_2", "team", 1, None),
        ("inventory_finished_team_a", "沧州成品库一组", "沧州成品仓库管理小组", "inventory_cangzhou_finished", "team", 1, "finished"),
        ("inventory_raw_team_a", "廊坊原料库一组", "廊坊原料仓库管理小组", "inventory_langfang_raw", "team", 1, "raw"),
        ("production_management", "廊坊生产管理组", "生产总负责人及生产调度", "production", "team", 1, "raw"),
        ("production_slow_release", "缓释尿素车间", "缓释尿素生产单元", "production", "team", 2, "raw"),
        ("production_crystal", "结晶尿素车间", "结晶尿素生产单元", "production", "team", 3, "raw"),
        ("production_general_urea", "普通尿素车间", "普通尿素及通用生产单元", "production", "team", 4, "raw"),
    ]
    for code, name, description, parent_code, unit_type, sort_order, warehouse_scope in team_seed:
        _upsert_department(
            db,
            code=code,
            name=name,
            description=description,
            parent_id=department_map[parent_code].id,
            unit_type=unit_type,
            sort_order=sort_order,
            warehouse_scope=warehouse_scope,
        )
    db.flush()

    role_map = {
        "sales": _upsert_role(db, code="sales", name="销售专员", description="负责客户与客户订单维护"),
        "inventory": _upsert_role(db, code="inventory", name="库管专员", description="负责库存与仓储作业"),
        "purchase": _upsert_role(db, code="purchase", name="采购专员", description="负责采购订单与到货处理"),
        "production": _upsert_role(db, code="production", name="生产专员", description="负责生产任务与领料"),
        "quality": _upsert_role(db, code="quality", name="质检专员", description="负责质检任务与检验记录"),
        "finance": _upsert_role(db, code="finance", name="财务专员", description="负责应收应付与资金流水"),
        "manager": _upsert_role(db, code="manager", name="总经理", description="负责跨部门管理与审批"),
        "chairman": _upsert_role(db, code="chairman", name="董事长", description="负责经营决策与关键审批"),
        "system_admin": _upsert_role(db, code="system_admin", name="系统管理员", description="系统最高权限与运维管理", is_admin=True),
        "master_data_admin": _upsert_role(db, code="master_data_admin", name="主数据管理员", description="负责主数据和参数维护"),
    }
    db.flush()

    department_map = {item.code: item for item in db.execute(select(Department)).scalars().all()}
    default_users = [
        ("sales01", "销售员1", "sales_division_1_team_a", "sales", "销售专员", "self"),
        ("inventory01", "库管员1", "inventory_finished_team_a", "inventory", "库管专员", "department"),
        ("inventoryraw01", "原料库管员1", "inventory_raw_team_a", "inventory", "原料库管专员", "department"),
        ("langfang_inventory_head", "廊坊总库存管理员", "inventory_langfang_raw", "inventory", "总库存管理员负责人", "department"),
        ("purchase01", "采购员1", "purchase_division_1_team_a", "purchase", "采购专员", "self"),
        ("production01", "生产员1", "production", "production", "生产专员", "department"),
        ("langfang_production_head", "廊坊生产总负责人", "production_management", "production", "生产总负责人", "department"),
        ("slow_release_head", "缓释尿素车间负责人", "production_slow_release", "production", "缓释尿素车间负责人", "department"),
        ("crystal_urea_head", "结晶尿素车间负责人", "production_crystal", "production", "结晶尿素车间负责人", "department"),
        ("general_urea_head", "普通尿素车间负责人", "production_general_urea", "production", "普通尿素车间负责人", "department"),
        ("quality01", "质检员1", "quality", "quality", "质检专员", "department"),
        ("finance01", "财务员1", "finance", "finance", "财务专员", "department"),
        ("manager01", "总经理账号", "executive", "manager", "总经理", "all"),
        ("chairman01", "董事长账号", "executive", "chairman", "董事长", "all"),
        ("sysadmin01", "系统管理员账号", "it_center", "system_admin", "系统管理员", "all"),
        ("masterdata01", "主数据管理员账号", "master_data", "master_data_admin", "主数据管理员", "all"),
    ]
    for username, full_name, department_code, role_code, job_title, data_scope in default_users:
        _upsert_user(
            db,
            username=username,
            full_name=full_name,
            department_id=department_map[department_code].id,
            role_id=role_map[role_code].id,
            password=default_password,
            job_title=job_title,
            data_scope=data_scope,
            is_active=True,
        )
    db.flush()
    for username, _full_name, _department_code, role_code, _job_title, _data_scope in default_users:
        seeded_user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if seeded_user is not None:
            _grant_role_modules(db, seeded_user, role_map[role_code])

    warehouse_seed = [
        ("CANGZHOU_FINISHED", "沧州仓库", "沧州", "finished"),
        ("LANGFANG_RAW", "廊坊仓库", "廊坊", "raw"),
    ]
    for code, name, location, warehouse_type in warehouse_seed:
        warehouse = db.execute(select(Warehouse).where(Warehouse.code == code)).scalar_one_or_none()
        if warehouse is None:
            warehouse = Warehouse(code=code, name=name, location=location, warehouse_type=warehouse_type)
            db.add(warehouse)
        else:
            warehouse.name = name
            warehouse.location = location
            warehouse.warehouse_type = warehouse_type

    packaging_seed = [
        ("25kg编织袋", 25, 1.20),
        ("50kg编织袋", 50, 1.80),
        ("吨袋", 1000, 35.00),
        ("25kg纸塑复合袋", 25, 2.20),
        ("散装", 1000, 0.00),
    ]
    for packaging_name, kg_per_bag, unit_price in packaging_seed:
        row = db.execute(
            select(PackagingConfig).where(
                PackagingConfig.entity_type == "global",
                PackagingConfig.entity_id == 0,
                PackagingConfig.packaging_name == packaging_name,
            )
        ).scalar_one_or_none()
        if row is None:
            db.add(
                PackagingConfig(
                    entity_type="global",
                    entity_id=0,
                    packaging_name=packaging_name,
                    kg_per_bag=kg_per_bag,
                    unit_price=unit_price,
                    is_default=packaging_name == "25kg编织袋",
                    is_active=True,
                )
            )
        else:
            row.kg_per_bag = kg_per_bag
            row.unit_price = unit_price
            row.is_default = packaging_name == "25kg编织袋"
            row.is_active = True

    global_packaging_rows = db.execute(
        select(PackagingConfig).where(
            PackagingConfig.entity_type == "global",
            PackagingConfig.entity_id == 0,
            PackagingConfig.is_active.is_(True),
        )
    ).scalars().all()
    global_packaging_by_weight = {}
    for row in sorted(global_packaging_rows, key=lambda item: (not item.is_default, item.id)):
        global_packaging_by_weight.setdefault(str(row.kg_per_bag), row)
    for row in db.execute(
        select(PackagingConfig).where(PackagingConfig.entity_type.in_(["product", "material"]))
    ).scalars().all():
        if not packaging_name_needs_repair(row.packaging_name):
            continue
        matched = global_packaging_by_weight.get(str(row.kg_per_bag))
        if matched is None:
            continue
        row.packaging_name = matched.packaging_name
        row.unit_price = matched.unit_price

    default_global_packaging = next((row for row in global_packaging_rows if row.is_default), None) or (global_packaging_rows[0] if global_packaging_rows else None)
    if default_global_packaging is not None:
        for row in db.execute(select(BOM).where(BOM.packaging_config_id.is_(None))).scalars().all():
            row.packaging_config_id = default_global_packaging.id

    for row in db.execute(select(SalesOrderItem).where(SalesOrderItem.kg_per_bag.is_not(None))).scalars().all():
        row.bag_count = calculate_bag_count(row.quantity, row.kg_per_bag)
    for row in db.execute(select(PurchaseOrderItem).where(PurchaseOrderItem.kg_per_bag.is_not(None))).scalars().all():
        row.bag_count = calculate_bag_count(row.quantity, row.kg_per_bag)

    db.commit()
