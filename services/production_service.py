"""production domain service layer."""

from datetime import datetime
from decimal import Decimal
from math import ceil

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models import (
    BOM,
    BOMItem,
    FulfillmentDemand,
    Inventory,
    InventoryOperationRequest,
    Material,
    ProductionPlan,
    ProductionMaterialUsage,
    ProductionTask,
    Product,
    PurchaseOrder,
    QualityTask,
    Role,
    SalesOrder,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.production import (
    BOMCreate,
    FulfillmentDemandCancelRequest,
    MaterialUsageInput,
    MaterialRequestCreate,
    ProductionPlanCreate,
    ProductionPlanUpdate,
    ProductionCompleteRequest,
    ProductionTaskCancelRequest,
    ProductionTaskCreate,
    SpecialMaterialApproval,
    SpecialMaterialRequestCreate,
)
from services.core import _role_user_ids, apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, resolve_workflow_recipients, write_operation_log
from services.organization import get_inventory_scope_warehouse_ids, is_system_admin
from services.quality_workflow import create_production_quality_task, resolve_quality_recipients_for_warehouse
from services.time_utils import now_local
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_factory_warehouse, get_raw_warehouse


def repair_material_request_states(db: Session) -> int:
    pending_requests = db.execute(
        select(InventoryOperationRequest).where(
            InventoryOperationRequest.request_category == "production_material_issue",
            InventoryOperationRequest.source_type == "production_material_usage",
            InventoryOperationRequest.status.in_(["pending", "processing"]),
        )
    ).scalars().all()
    if not pending_requests:
        return 0

    usage_ids = [row.source_id for row in pending_requests]
    usages = db.execute(
        select(ProductionMaterialUsage).where(ProductionMaterialUsage.id.in_(usage_ids))
    ).scalars().all()

    repaired = 0
    for usage in usages:
        if (
            usage.special_request_status == "not_required"
            and usage.manager_approval_status == "not_required"
            and usage.inventory_approval_status == "approved"
            and Decimal(usage.actual_quantity or 0) <= 0
            and Decimal(usage.issued_quantity or 0) <= 0
        ):
            usage.inventory_approval_status = "pending_approval"
            usage.inventory_approved_by_id = None
            usage.inventory_approved_at = None
            usage.inventory_remark = None
            repaired += 1
    return repaired



def _raw_warehouse(db: Session) -> Warehouse:
    return get_raw_warehouse(db)


_PRODUCTION_TASK_NON_RESERVING_STATUSES = {
    "completed",
    "cancelled",
    "scrapped",
    "partially_scrapped",
    "reworked",
    "rework_pending",
}


def _inventory_user_ids_for_warehouse(db: Session, warehouse_id: int) -> list[int]:
    warehouse = db.get(Warehouse, warehouse_id)
    users = db.execute(
        select(User).join(Role, User.role_id == Role.id).where(Role.code == "inventory", User.is_active.is_(True))
    ).scalars().all()
    return [
        user.id
        for user in users
        if (warehouse and user.id == warehouse.manager_user_id)
        or warehouse_id in (get_inventory_scope_warehouse_ids(db, user) or [])
    ]


def can_review_factory_material(db: Session, current_user: User) -> bool:
    """普通领料审批与后续工厂仓出库使用同一批库管。"""
    return bool(
        current_user.role
        and current_user.role.code == "inventory"
        and current_user.id in _inventory_user_ids_for_warehouse(db, _raw_warehouse(db).id)
    )


def _allowed_quantity(task: ProductionTask, usage: ProductionMaterialUsage) -> Decimal:
    return Decimal(usage.planned_quantity or 0) * (Decimal("1") + Decimal(task.fluctuation_ratio or 0))


def _is_special_material_request(task: ProductionTask, usage: ProductionMaterialUsage) -> bool:
    return Decimal(usage.requested_quantity or 0) > _allowed_quantity(task, usage) or usage.manager_approval_status != "not_required"


def _create_raw_material_demands_for_task(db: Session, *, task, demand, raw_material_demands, current_user) -> list[str]:
    """每次人工提交创建独立 YL 需求，已有数量和采购单保持独立。"""
    import json
    from models import MaterialReplenishmentDemand
    from services.material_replenishment_service import SOURCE_TYPE, material_demand_progress

    if not raw_material_demands:
        return []
    # 同一 BH 的并发提交串行校验，避免两个请求同时补同一个缺口。
    db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id == demand.id).with_for_update()).scalar_one()
    progress = material_demand_progress(db, demand, lock_stock=True)
    progress_map = {item["material_id"]: item for item in progress["items"]}
    requested = {}
    for raw in raw_material_demands:
        requested[raw.material_id] = requested.get(raw.material_id, Decimal("0")) + Decimal(raw.shortage_quantity)
    shortage_items = []
    for mid, quantity in requested.items():
        item = progress_map.get(mid)
        if not item or item["required_quantity"] <= 0:
            raise HTTPException(status_code=409, detail="原料必须属于当前生产需求的 BOM")
        remaining = Decimal(str(item["remaining_replenishment_quantity"]))
        if quantity <= 0 or quantity > remaining:
            raise HTTPException(status_code=409, detail=f"{item['material_name']} 本次补货数量应大于 0 且不超过剩余缺口 {remaining}")
        shortage_items.append({"material_id": mid, "material_name": item["material_name"],
                               "required_quantity": item["required_quantity"],
                               "expected_available_quantity": item["stock_available_quantity"],
                               "shortage_quantity": float(quantity)})
    batch = MaterialReplenishmentDemand(demand_no=next_business_no(db, "YL"), fulfillment_demand_id=demand.id,
                                        created_by_id=current_user.id, items_json=shortage_items)
    db.add(batch)
    db.flush()
    payload = {"sales_order_id": demand.sales_order_id, "sales_order_no": demand.source_order_no,
               "fulfillment_demand_id": demand.id, "fulfillment_demand_no": demand.demand_no,
               "raw_material_demand_no": batch.demand_no, "product_id": demand.product_id,
               "product_name": demand.product.name if demand.product else None, "items": shortage_items}
    recipients = resolve_workflow_recipients(db, "purchase", workflow="sales_order_material_replenishment",
                                              business_type=SOURCE_TYPE, business_id=batch.id)
    for user_id in recipients:
        create_todo(db, title=f"待采购原料 {batch.demand_no}",
                    description=f"原料补货需求 {batch.demand_no}，来源 {demand.demand_no}。|sales_order_material_replenishment:{json.dumps(payload, ensure_ascii=False)}",
                    module="purchase", assigned_user_id=user_id, business_type=SOURCE_TYPE, business_id=batch.id, priority="high")
    create_notification(db, title="新增原料采购补货需求", content=f"{demand.demand_no} 已提交独立原料补货需求 {batch.demand_no}。",
                        module="purchase", recipient_user_ids=list(dict.fromkeys([*recipients, current_user.id])),
                        created_by_id=current_user.id, category="warning")
    return [batch.demand_no]


def _create_raw_material_replenishment_notice(
    db: Session,
    *,
    task: ProductionTask,
    usage: ProductionMaterialUsage,
    shortage_quantity: Decimal,
    current_user: User,
) -> None:
    if shortage_quantity <= 0:
        return
    existing = db.execute(
        select(TodoItem.id)
        .where(
            TodoItem.business_type == "raw_material_replenishment",
            TodoItem.business_id == usage.id,
            TodoItem.status != "completed",
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return
    purchaser_ids = resolve_workflow_recipients(
        db,
        "purchase",
        workflow="raw_material_replenishment",
        business_type="raw_material_replenishment",
        business_id=usage.id,
    )
    material_name = usage.material.name if usage.material else f"原料#{usage.material_id}"
    for user_id in purchaser_ids:
        create_todo(
            db,
            title=f"待采购原料补货 {task.task_no}",
            description=f"生产任务 {task.task_no} 领料时发现 {material_name} 缺口 {float(shortage_quantity)}，请采购补货。",
            module="purchase",
            assigned_user_id=user_id,
            business_type="raw_material_replenishment",
            business_id=usage.id,
            priority="high",
        )
    create_notification(
        db,
        title="新增原料采购补货需求",
        content=f"生产任务 {task.task_no} 领料时发现 {material_name} 库存不足，缺口 {float(shortage_quantity)}。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([*purchaser_ids, task.created_by_id])),
        created_by_id=current_user.id,
        category="warning",
    )


def _reset_usage_approval_state(usage: ProductionMaterialUsage) -> None:
    usage.approved_quantity = Decimal("0")
    usage.special_request_status = "not_required"
    usage.approved_by_id = None
    usage.inventory_approval_status = "not_required"
    usage.inventory_approved_by_id = None
    usage.inventory_approved_at = None
    usage.inventory_remark = None
    usage.manager_approval_status = "not_required"
    usage.manager_approved_by_id = None
    usage.manager_approved_at = None
    usage.manager_remark = None


def _refresh_task_material_status(task: ProductionTask) -> None:
    usages = list(task.usages or [])
    if not usages:
        task.status = "pending_material"
        return
    if any(
        usage.special_request_status == "pending_approval" or usage.manager_approval_status == "pending_approval"
        for usage in usages
    ):
        task.status = "pending_special_approval"
        return
    if any(usage.inventory_approval_status == "pending_approval" for usage in usages):
        task.status = "pending_material_approval"
        return
    approval_ready = all(
        usage.inventory_approval_status == "approved"
        and (not _is_special_material_request(task, usage) or usage.manager_approval_status == "approved")
        for usage in usages
    )
    if approval_ready:
        fully_issued = all(
            Decimal(usage.actual_quantity or 0) >= Decimal(usage.approved_quantity or usage.requested_quantity or 0)
            for usage in usages
        )
        task.status = "material_issued" if fully_issued else "pending_material_outbound"
        return
    task.status = "pending_material"


def _issue_material_if_ready(
    db: Session,
    *,
    task: ProductionTask,
    usage: ProductionMaterialUsage,
    current_user: User,
) -> bool:
    if usage.inventory_approval_status != "approved":
        return False
    if _is_special_material_request(task, usage) and usage.manager_approval_status != "approved":
        return False
    if usage.inventory_approval_status == "rejected" or usage.manager_approval_status == "rejected":
        return False

    issue_quantity = Decimal(usage.approved_quantity or usage.requested_quantity or 0)
    already_issued = Decimal(usage.actual_quantity or 0)
    delta = issue_quantity - already_issued
    if delta <= 0:
        usage.actual_quantity = issue_quantity
        return False
    ready_usages = [
        row
        for row in (task.usages or [])
        if row.inventory_approval_status == "approved"
        and row.inventory_approval_status != "rejected"
        and (not _is_special_material_request(task, row) or row.manager_approval_status == "approved")
        and Decimal(row.approved_quantity or row.requested_quantity or 0) > Decimal(row.actual_quantity or 0)
    ]
    return _create_task_material_issue_request(db, task=task, usages=ready_usages, current_user=current_user)


def _max_producible_quantity_from_raw_stock(
    db: Session,
    *,
    product_id: int,
) -> tuple[Decimal, list[dict]]:
    bom = db.execute(
        select(BOM).options(joinedload(BOM.items)).where(BOM.product_id == product_id)
    ).unique().scalar_one_or_none()
    if bom is None or not bom.items:
        return Decimal("0"), []
    from services.material_replenishment_service import material_stock_availability
    ids = [item.material_id for item in bom.items if item.standard_quantity > 0]
    stocks = material_stock_availability(db, ids, lock=True)
    names = {m.id: m.name for m in db.scalars(select(Material).where(Material.id.in_(ids or [-1])))}
    rows = []
    maximum = None
    for item in bom.items:
        standard = Decimal(item.standard_quantity or 0)
        if standard <= 0:
            continue
        stock = stocks[item.material_id]
        quantity = stock["available_quantity"] / standard
        maximum = quantity if maximum is None else min(maximum, quantity)
        rows.append({"material_id": item.material_id, "material_name": names.get(item.material_id),
                     "available_quantity": float(stock["available_quantity"]),
                     "stock_available_quantity": float(max(stock["quantity"] - stock["reserved_quantity"], Decimal("0"))),
                     "reserved_by_production_quantity": float(stock["production_reserved_quantity"]),
                     "required_per_product": float(standard)})
    return maximum or Decimal("0"), rows


def raw_material_shortage_detail(db: Session, *, product_id: int, planned_quantity: Decimal) -> list[dict]:
    _, materials = _max_producible_quantity_from_raw_stock(db, product_id=product_id)
    return [{**item, "required_quantity": float(planned_quantity * Decimal(str(item["required_per_product"]))),
             "shortage_quantity": float(max(planned_quantity * Decimal(str(item["required_per_product"])) - Decimal(str(item["available_quantity"])), Decimal("0")))}
            for item in materials]


def _ensure_inventory_stock_for_approval(
    db: Session,
    *,
    task: ProductionTask,
    usage: ProductionMaterialUsage,
    current_user: User,
    create_replenishment: bool,
) -> None:
    requested_quantity = Decimal(usage.requested_quantity or 0)
    raw_warehouse = _raw_warehouse(db)
    inventory = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == raw_warehouse.id,
            Inventory.material_id == usage.material_id,
        )
    ).scalar_one_or_none()
    available_quantity = (
        Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0)
        if inventory is not None
        else Decimal('0')
    )
    available_quantity = max(available_quantity, Decimal('0'))
    if available_quantity >= requested_quantity:
        return
    shortage_quantity = requested_quantity - available_quantity
    if not create_replenishment:
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'inventory_shortage_confirmation_required',
                'message': '原料库存不足，需要确认是否生成采购补货需求',
                'task_no': task.task_no,
                'material_name': usage.material.name if usage.material else f'原料#{usage.material_id}',
                'requested_quantity': float(requested_quantity),
                'available_quantity': float(available_quantity),
                'shortage_quantity': float(shortage_quantity),
            },
        )
    _create_raw_material_replenishment_notice(
        db,
        task=task,
        usage=usage,
        shortage_quantity=shortage_quantity,
        current_user=current_user,
    )


def _create_material_issue_request(
    db: Session,
    *,
    task: ProductionTask,
    usage: ProductionMaterialUsage,
    current_user: User,
) -> bool:
    issue_quantity = Decimal(usage.approved_quantity or usage.requested_quantity or 0)
    if issue_quantity <= 0:
        return False

    raw_warehouse = _raw_warehouse(db)
    inventory_user_ids = _inventory_user_ids_for_warehouse(db, raw_warehouse.id)
    if not inventory_user_ids:
        raise HTTPException(status_code=409, detail="廊坊工厂仓未配置库管，无法派发领料出库")
    inventory = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == raw_warehouse.id,
            Inventory.material_id == usage.material_id,
        )
    ).scalar_one_or_none()
    unit_cost = Decimal(inventory.avg_unit_cost or 0) if inventory else Decimal(usage.material.price if usage.material else 0)
    items = [
        {
            "product_id": None,
            "material_id": usage.material_id,
            "batch_no": None,
            "quantity": float(issue_quantity),
            "unit_cost": float(unit_cost),
        }
    ]
    existing = db.execute(
        select(InventoryOperationRequest).where(
            InventoryOperationRequest.source_type == "production_material_usage",
            InventoryOperationRequest.source_id == usage.id,
            InventoryOperationRequest.request_category == "production_material_issue",
            InventoryOperationRequest.status.in_(["pending", "processing"]),
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.original_items_json = items
        existing.remark = usage.reason
        return True

    create_inventory_operation_request(
        db,
        operation_type="outbound",
        request_category="production_material_issue",
        source_module="production",
        source_type="production_material_usage",
        source_id=usage.id,
        source_no=task.task_no,
        warehouse_id=raw_warehouse.id,
        requested_by_id=current_user.id,
        items=items,
        remark=usage.reason,
        recipient_user_ids=inventory_user_ids,
        title=f"待执行领料出库 {task.task_no}",
        description=f"生产任务 {task.task_no} 的原料 {usage.material.name if usage.material else usage.material_id} 已提交领料申请，请库管审核并执行出库。",
    )
    return True


def _create_task_material_issue_request(
    db: Session,
    *,
    task: ProductionTask,
    usages: list[ProductionMaterialUsage],
    current_user: User,
) -> bool:
    rows = []
    raw_warehouse = _raw_warehouse(db)
    inventory_user_ids = _inventory_user_ids_for_warehouse(db, raw_warehouse.id)
    if not inventory_user_ids:
        raise HTTPException(status_code=409, detail="廊坊工厂仓未配置库管，无法派发领料出库")
    for usage in usages:
        issue_quantity = Decimal(usage.approved_quantity or usage.requested_quantity or 0)
        if issue_quantity <= 0:
            continue
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == raw_warehouse.id,
                Inventory.material_id == usage.material_id,
            )
        ).scalar_one_or_none()
        unit_cost = Decimal(inventory.avg_unit_cost or 0) if inventory else Decimal(usage.material.price if usage.material else 0)
        rows.append(
            {
                "product_id": None,
                "material_id": usage.material_id,
                "batch_no": None,
                "quantity": float(issue_quantity),
                "unit_cost": float(unit_cost),
                "usage_id": usage.id,
            }
        )
    if not rows:
        return False
    existing = db.execute(
        select(InventoryOperationRequest).where(
            InventoryOperationRequest.source_type == "production_task",
            InventoryOperationRequest.source_id == task.id,
            InventoryOperationRequest.request_category == "production_material_issue",
            InventoryOperationRequest.status.in_(["pending", "processing"]),
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.original_items_json = rows
        existing.remark = "；".join([row.reason for row in usages if row.reason]) or None
        return True
    create_inventory_operation_request(
        db,
        operation_type="outbound",
        request_category="production_material_issue",
        source_module="production",
        source_type="production_task",
        source_id=task.id,
        source_no=task.task_no,
        warehouse_id=raw_warehouse.id,
        requested_by_id=current_user.id,
        items=rows,
        remark="；".join([row.reason for row in usages if row.reason]) or None,
        recipient_user_ids=inventory_user_ids,
        title=f"待执行领料出库 {task.task_no}",
        description=f"生产任务 {task.task_no} 已提交 {len(rows)} 项原料领料申请，请库管审核并执行一次性出库。",
    )
    return True

def _build_batch_no(db: Session, task: ProductionTask) -> str:
    if task.parent_task_id:
        parent = db.execute(select(ProductionTask).where(ProductionTask.id == task.parent_task_id)).scalar_one_or_none()
        base_batch = (parent.batch_no or "").split("-")[0] if parent and parent.batch_no else now_local().strftime("%Y%m%d")
        sibling_count = db.execute(
            select(func.count(ProductionTask.id)).where(
                ProductionTask.parent_task_id == task.parent_task_id,
                ProductionTask.batch_no.is_not(None),
                ProductionTask.id != task.id,
            )
        ).scalar_one()
        suffix = "RW" if task.source_type == "rework" else "FG"
        return f"{base_batch}-{suffix}{sibling_count + 1:02d}"
    return f"{now_local().strftime('%Y%m%d')}{task.id:04d}-A"

def create_task(
    payload: ProductionTaskCreate,
    current_user: User,
    db: Session,
):
    product = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    demand = None
    if payload.fulfillment_demand_id:
        demand = db.execute(
            select(FulfillmentDemand)
            .options(joinedload(FulfillmentDemand.sales_order))
            .where(
                FulfillmentDemand.id == payload.fulfillment_demand_id,
                FulfillmentDemand.fulfillment_mode == "production",
            )
        ).unique().scalar_one_or_none()
        if demand is None:
            raise HTTPException(status_code=404, detail="生产补货需求不存在")
        if demand.status not in {"pending", "pending_material"}:
            raise HTTPException(status_code=409, detail="当前生产补货需求已处理")
        if Decimal(payload.planned_quantity) > Decimal(demand.shortage_quantity or 0):
            raise HTTPException(status_code=409, detail="本批生产数量不能超过当前补货需求剩余数量")

    source_type = payload.source_type
    sales_order_id = payload.sales_order_id
    if demand is not None:
        # 补货需求关联了客户订单 → 任务来源为客户订单;手动补货(无订单) → 任务来源为手动补货
        if demand.sales_order_id:
            source_type = "sales_order"
            sales_order_id = demand.sales_order_id
        else:
            source_type = "fulfillment_demand"
            sales_order_id = None
    if source_type == "sales_order" and not sales_order_id:
        raise HTTPException(status_code=409, detail="请选择来源客户订单")

    order = None
    if sales_order_id:
        order = db.execute(
            select(SalesOrder).options(joinedload(SalesOrder.items)).where(SalesOrder.id == sales_order_id)
        ).unique().scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="来源销售订单不存在")
        if source_type == "sales_order":
            matched_item = next((item for item in order.items if item.product_id == payload.product_id), None)
            if matched_item is None:
                raise HTTPException(status_code=409, detail="所选产品不在来源客户订单明细中")
            if Decimal(payload.planned_quantity) > Decimal(matched_item.quantity or 0):
                raise HTTPException(status_code=409, detail="生产计划数量不能大于客户订单数量")

    bom = db.execute(select(BOM).options(joinedload(BOM.items)).where(BOM.product_id == payload.product_id)).unique().scalar_one_or_none()
    if bom is None or not bom.items:
        raise HTTPException(status_code=409, detail="当前产品尚未配置 BOM，不能创建生产任务")

    # 排产/转任务不等于领料出库；缺料任务仍需先创建，以便后续提交采购需求。
    # 原料可用量在领料审批和实际出库时再次校验。

    payload_usage_map = {item.material_id: item for item in (payload.material_usages or [])}
    bom_material_ids = {item.material_id for item in bom.items}
    if payload_usage_map and set(payload_usage_map) != bom_material_ids:
        raise HTTPException(status_code=409, detail="领料明细必须与 BOM 原料保持一致")

    warehouse_id = get_factory_warehouse(db).id
    if payload.warehouse_id:
        target_warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.warehouse_id)).scalar_one_or_none()
        if target_warehouse is None:
            raise HTTPException(status_code=404, detail="目标仓库不存在")
        if target_warehouse.warehouse_type not in {"raw", "finished"}:
            raise HTTPException(status_code=409, detail="生产目标仓库必须是廊坊工厂仓或成品仓")
        warehouse_id = target_warehouse.id

    task = ProductionTask(
        task_no=next_business_no(db, "SC"),
        source_type=source_type,
        sales_order_id=sales_order_id,
        product_id=payload.product_id,
        planned_quantity=payload.planned_quantity,
        fluctuation_ratio=payload.fluctuation_ratio,
        status="pending_material",
        warehouse_id=warehouse_id,
        created_by_id=current_user.id,
        # 必须回填来源补货需求:resolve_demand_progress / _batch_demand_progress_map
        # 都靠这个外键 join 才能推导生产型补货需求的实时进度。
        # 改造前该字段从未被赋值,导致生产型需求的进度恒为空。
        fulfillment_demand_id=demand.id if demand is not None else None,
        notes=payload.notes or (f"来源客户订单：{order.order_no}" if order and source_type == "sales_order" else None),
    )
    db.add(task)
    db.flush()
    for item in bom.items:
        planned_quantity = Decimal(payload.planned_quantity) * Decimal(item.standard_quantity)
        request_item = payload_usage_map.get(item.material_id)
        requested_quantity = Decimal(request_item.requested_quantity) if request_item else planned_quantity
        if requested_quantity <= 0:
            raise HTTPException(status_code=409, detail="领料数量必须大于 0")
        db.add(
            ProductionMaterialUsage(
                production_task_id=task.id,
                material_id=item.material_id,
                planned_quantity=planned_quantity,
                requested_quantity=requested_quantity,
                approved_quantity=0,
                actual_quantity=0,
                reason=request_item.reason if request_item else None,
            )
        )
    if demand is not None:
        # 分批生产的记账:已补货量累加,待补量 = 原始需求量 - 已补货量。
        # 改造前直接原地递减 shortage_quantity,原始需求量被覆盖,
        # "需求 100 已产 10 还差 90"这类进度事后无法还原。
        required_quantity = Decimal(demand.required_quantity or 0) or Decimal(demand.shortage_quantity or 0)
        demand.required_quantity = required_quantity
        demand.fulfilled_quantity = Decimal(demand.fulfilled_quantity or 0) + Decimal(payload.planned_quantity)
        remaining_quantity = max(required_quantity - Decimal(demand.fulfilled_quantity or 0), Decimal("0"))
        demand.shortage_quantity = remaining_quantity
        demand.processed_by_id = current_user.id
        demand.process_note = (
            f"已创建生产任务 {task.task_no}，本批 {float(payload.planned_quantity)}，"
            f"累计已补 {float(demand.fulfilled_quantity)}/{float(required_quantity)}，剩余 {float(remaining_quantity)}。"
        )
        fully_covered = remaining_quantity <= 0
        if fully_covered:
            demand.status = "converted"
            complete_todos(db, business_type="fulfillment_demand", business_id=demand.id)
        else:
            demand.status = "pending_material" if payload.create_raw_material_demands else "pending"
        # 需求:转任务时原料不足→提交原料补货需求后,生产补货需求标记"待补料"
        if payload.create_raw_material_demands and payload.raw_material_demands:
            raw_demand_nos = _create_raw_material_demands_for_task(
                db,
                task=task,
                demand=demand,
                raw_material_demands=payload.raw_material_demands,
                current_user=current_user,
            )
            # 成品需求已全部转成任务时不要退回"待补料":本单的成品缺口已经补齐了,
            # 原料补货只影响这些任务能否开工,不代表补货需求本身还没处理。
            # (改造前这里无条件覆盖,已 converted 的需求会被打回 pending_material。)
            if not fully_covered:
                demand.status = "pending_material"
            demand.process_note = (
                f"{demand.process_note or ''}\n已提交原料补货需求: {', '.join(raw_demand_nos)}。"
                f"原料到货质检合格后可继续安排剩余 {float(remaining_quantity)} 生产。"
            )
        create_notification(
            db,
            title="销售订单生产需求已分批转生产任务",
            content=(
                f"补货需求 {demand.demand_no} 已创建生产任务 {task.task_no}，本批 {float(payload.planned_quantity)}，"
                f"剩余 {float(remaining_quantity)}。"
            ),
            module="production",
            recipient_user_ids=list(dict.fromkeys([current_user.id, demand.sales_order.sales_user_id if demand.sales_order else None])),
            created_by_id=current_user.id,
            category="success",
        )
    db.commit()
    return api_response(data={"id": task.id, "task_no": task.task_no})


def submit_material_request(
    task_id: int,
    payload: MaterialRequestCreate,
    current_user: User,
    db: Session,
):
    task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.product), joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == task_id)
    ).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    if task.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可为本人生产任务提交领料申请")
    if task.status in {"waiting_quality", "ready_for_inbound", "completed", "cancelled", "scrapped", "partially_scrapped", "reworked"}:
        raise HTTPException(status_code=409, detail="当前生产任务已进入后续流程，不能再提交领料申请")
    if not task.usages:
        raise HTTPException(status_code=409, detail="当前生产任务缺少 BOM 原料明细")

    if not payload.material_usages:
        raise HTTPException(status_code=409, detail="请至少提交一条领料明细")

    usage_map = {usage.material_id: usage for usage in task.usages}
    payload_map: dict[int, MaterialUsageInput] = {}
    for item in payload.material_usages:
        if item.material_id in payload_map:
            raise HTTPException(status_code=409, detail="同一原料不能重复提交")
        payload_map[item.material_id] = item

    if set(payload_map) != set(usage_map):
        raise HTTPException(status_code=409, detail="领料申请必须覆盖任务全部原料明细")

    inventory_user_ids = _inventory_user_ids_for_warehouse(db, _raw_warehouse(db).id)
    if not inventory_user_ids:
        raise HTTPException(status_code=409, detail="廊坊工厂仓未配置库管，请先配置仓库负责人或部门仓库范围")
    manager_user_ids = _role_user_ids(db, "manager")
    requested_rows: list[ProductionMaterialUsage] = []
    special_rows: list[ProductionMaterialUsage] = []
    normal_rows: list[ProductionMaterialUsage] = []
    for material_id, usage in usage_map.items():
        item = payload_map[material_id]
        requested_quantity = Decimal(item.requested_quantity)
        allowed_quantity = _allowed_quantity(task, usage)
        usage.requested_quantity = requested_quantity
        usage.reason = item.reason
        _reset_usage_approval_state(usage)
        usage.actual_quantity = Decimal("0")

        if requested_quantity > allowed_quantity:
            if not item.reason:
                raise HTTPException(status_code=409, detail=f"原料 {usage.material.name if usage.material else material_id} 超额领用必须填写原因")
            usage.special_request_status = "pending_approval"
            usage.inventory_approval_status = "pending_approval"
            usage.manager_approval_status = "pending_approval"
            special_rows.append(usage)
        else:
            usage.inventory_approval_status = "pending_approval"
            usage.approved_quantity = requested_quantity
            normal_rows.append(usage)
        requested_rows.append(usage)

    task.status = "pending_special_approval" if special_rows else "pending_material_approval"

    for usage in normal_rows:
        complete_todos(db, business_type="production_material_request", business_id=usage.id)
        for user_id in inventory_user_ids:
            create_todo(
                db,
                title=f"待审批领料 {task.task_no}",
                description=f"原料 {usage.material.name if usage.material else usage.material_id} 申请领料 {float(usage.requested_quantity)}，请廊坊工厂仓库管审批。",
                module="production",
                assigned_user_id=user_id,
                business_type="production_material_request",
                business_id=usage.id,
            )
    for usage in special_rows:
        complete_todos(db, business_type="production_special_request", business_id=usage.id)
        for user_id in dict.fromkeys([*inventory_user_ids, *manager_user_ids]):
            create_todo(
                db,
                title=f"待审批特殊领料 {task.task_no}",
                description=f"原料 {usage.material.name if usage.material else usage.material_id} 申请领料 {float(usage.requested_quantity)}，超出浮动阈值。",
                module="production",
                assigned_user_id=user_id,
                business_type="production_special_request",
                business_id=usage.id,
                priority="high",
            )

    notification_user_ids = list(dict.fromkeys([*inventory_user_ids, *manager_user_ids]))
    if special_rows:
        notification_user_ids = list(dict.fromkeys([*inventory_user_ids, *manager_user_ids]))
    else:
        notification_user_ids = inventory_user_ids
    if notification_user_ids:
        create_notification(
            db,
            title="新增领料申请" if not special_rows else "新增特殊领料审批",
            content=(
                f"生产任务 {task.task_no} 已提交 {len(requested_rows)} 条领料申请，请原料库审核并执行出库。"
                if not special_rows
                else f"生产任务 {task.task_no} 已提交 {len(special_rows)} 条超额领料申请，请库管和经理审批。"
            ),
            module="production",
            recipient_user_ids=notification_user_ids,
            created_by_id=current_user.id,
            category="warning" if special_rows else "info",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="submit_material_request",
        content=f"提交生产任务 {task.task_no} 的领料申请",
        after_data={
            "task_no": task.task_no,
            "status": task.status,
            "special_count": len(special_rows),
            "items": [
                {
                    "material_id": row.material_id,
                    "requested_quantity": float(row.requested_quantity),
                    "special": row in special_rows,
                }
                for row in requested_rows
            ],
        },
    )
    db.commit()
    return api_response(
        data={
            "task_no": task.task_no,
            "status": task.status,
            "requested_count": len(requested_rows),
            "special_count": len(special_rows),
        }
    )

def submit_special_requests(
    task_id: int,
    payload: SpecialMaterialRequestCreate,
    current_user: User,
    db: Session,
):
    task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.product), joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == task_id)
    ).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    if not payload.actual_usages:
        raise HTTPException(status_code=409, detail="请至少提交一条特殊领用明细")

    usage_map = {usage.material_id: usage for usage in task.usages}

    submitted_rows: list[ProductionMaterialUsage] = []
    for item in payload.actual_usages:
        usage = usage_map.get(item.material_id)
        if usage is None:
            raise HTTPException(status_code=404, detail="存在未匹配的原料耗用明细")
        allowed_quantity = _allowed_quantity(task, usage)
        requested_quantity = Decimal(item.requested_quantity)
        if requested_quantity <= allowed_quantity:
            raise HTTPException(status_code=409, detail=f"原料 {usage.material.name if usage.material else item.material_id} 未超出浮动范围，无需提交流程")
        if not item.reason:
            raise HTTPException(status_code=409, detail=f"原料 {usage.material.name if usage.material else item.material_id} 超额领用必须填写原因")
        usage.requested_quantity = requested_quantity
        usage.reason = item.reason
        usage.special_request_status = "pending_approval"
        usage.approved_by_id = None
        usage.inventory_approval_status = "pending_approval"
        usage.inventory_approved_by_id = None
        usage.inventory_approved_at = None
        usage.inventory_remark = None
        usage.manager_approval_status = "pending_approval"
        usage.manager_approved_by_id = None
        usage.manager_approved_at = None
        usage.manager_remark = None
        submitted_rows.append(usage)

    task.status = "pending_special_approval"
    inventory_user_ids = _inventory_user_ids_for_warehouse(db, _raw_warehouse(db).id)
    if not inventory_user_ids:
        raise HTTPException(status_code=409, detail="廊坊工厂仓未配置库管，请先配置仓库负责人或部门仓库范围")
    approver_user_ids = list(dict.fromkeys([*inventory_user_ids, *_role_user_ids(db, "manager")]))
    for usage in submitted_rows:
        for user_id in approver_user_ids:
            create_todo(
                db,
                title=f"待审批特殊领用 {task.task_no}",
                description=f"原料 {usage.material.name if usage.material else usage.material_id} 申请超额领用，请审批。",
                module="production",
                assigned_user_id=user_id,
                business_type="production_special_request",
                business_id=usage.id,
            )
    if approver_user_ids:
        create_notification(
            db,
            title="新增特殊领用审批",
            content=f"生产任务 {task.task_no} 提交了 {len(submitted_rows)} 条特殊领用申请，请库存和经理共同审批。",
            module="production",
            recipient_user_ids=approver_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="submit_special_request",
        content=f"提交生产任务 {task.task_no} 的特殊领用申请",
        after_data={
            "task_no": task.task_no,
            "items": [
                {
                    "material_id": row.material_id,
                    "material_name": row.material.name if row.material else None,
                    "requested_quantity": float(row.requested_quantity),
                }
                for row in submitted_rows
            ],
        },
    )
    db.commit()
    return api_response(data={"count": len(submitted_rows), "task_no": task.task_no})

def approve_special_request(
    usage_id: int,
    payload: SpecialMaterialApproval,
    current_user: User,
    db: Session,
):
    usage = db.execute(
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.material),
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
        )
        .where(ProductionMaterialUsage.id == usage_id)
    ).unique().scalar_one_or_none()
    if usage is None:
        raise HTTPException(status_code=404, detail="领料申请不存在")

    task = usage.production_task
    allowed_quantity = _allowed_quantity(task, usage)
    role_code = current_user.role.code if current_user.role else ""
    if role_code == "inventory" and not can_review_factory_material(db, current_user):
        raise HTTPException(status_code=403, detail="只有廊坊工厂仓库管可以审批生产领料")
    review_note = payload.remark.strip() if payload.remark else None
    is_admin = is_system_admin(current_user)
    is_special_request = _is_special_material_request(task, usage)
    if payload.decision == 'approved' and (role_code == 'inventory' or is_admin):
        _ensure_inventory_stock_for_approval(
            db,
            task=task,
            usage=usage,
            current_user=current_user,
            create_replenishment=payload.create_replenishment,
        )

    if role_code == "inventory":
        if usage.inventory_approval_status != "pending_approval":
            raise HTTPException(status_code=409, detail="库存审批已处理，不能重复操作")
        usage.inventory_approval_status = "approved" if payload.decision == "approved" else "rejected"
        usage.inventory_approved_by_id = current_user.id
        usage.inventory_approved_at = now_local()
        usage.inventory_remark = review_note
        if not is_special_request and payload.decision == "approved":
            usage.approved_quantity = Decimal(usage.requested_quantity or 0)
    elif role_code == "manager":
        if not is_special_request and not is_admin:
            raise HTTPException(status_code=403, detail="普通领料无需经理审批")
        if usage.manager_approval_status != "pending_approval":
            raise HTTPException(status_code=409, detail="经理审批已处理，不能重复操作")
        usage.manager_approval_status = "approved" if payload.decision == "approved" else "rejected"
        usage.manager_approved_by_id = current_user.id
        usage.manager_approved_at = now_local()
        usage.manager_remark = review_note
        if payload.decision == "approved":
            approved_quantity = Decimal(payload.approved_quantity) if payload.approved_quantity is not None else Decimal(usage.requested_quantity or 0)
            if approved_quantity <= 0:
                raise HTTPException(status_code=409, detail="审批数量必须大于 0")
            if approved_quantity > Decimal(usage.requested_quantity or 0):
                raise HTTPException(status_code=409, detail="审批数量不能大于申请数量")
            usage.approved_quantity = approved_quantity
        else:
            usage.approved_quantity = allowed_quantity
    elif is_admin:
        if payload.decision == "approved":
            approved_quantity = Decimal(payload.approved_quantity) if payload.approved_quantity is not None else Decimal(usage.requested_quantity or 0)
            if approved_quantity <= 0:
                raise HTTPException(status_code=409, detail="审批数量必须大于 0")
            if approved_quantity > Decimal(usage.requested_quantity or 0):
                raise HTTPException(status_code=409, detail="审批数量不能大于申请数量")
            usage.approved_quantity = approved_quantity
        else:
            usage.approved_quantity = allowed_quantity
        if usage.inventory_approval_status == "pending_approval":
            usage.inventory_approval_status = "approved" if payload.decision == "approved" else "rejected"
            usage.inventory_approved_by_id = current_user.id
            usage.inventory_approved_at = now_local()
            usage.inventory_remark = review_note or "系统管理员代审批"
        if usage.manager_approval_status == "pending_approval":
            usage.manager_approval_status = "approved" if payload.decision == "approved" else "rejected"
            usage.manager_approved_by_id = current_user.id
            usage.manager_approved_at = now_local()
            usage.manager_remark = review_note or "系统管理员代审批"
    else:
        raise HTTPException(status_code=403, detail="仅库存或经理可以审批特殊领用申请")

    if usage.inventory_approval_status == "rejected" or usage.manager_approval_status == "rejected":
        if is_special_request:
            usage.special_request_status = "rejected"
            usage.approved_quantity = allowed_quantity
        else:
            usage.approved_quantity = Decimal("0")
    elif usage.inventory_approval_status == "approved" and (usage.manager_approval_status in {"approved", "not_required"}):
        if is_special_request:
            usage.special_request_status = "approved"
        usage.approved_by_id = usage.manager_approved_by_id or usage.inventory_approved_by_id
        _issue_material_if_ready(db, task=task, usage=usage, current_user=current_user)
    else:
        if is_special_request:
            usage.special_request_status = "pending_approval"

    db.flush()

    _refresh_task_material_status(task)

    request_business_type = "production_special_request" if is_special_request else "production_material_request"
    if (
        usage.inventory_approval_status in {"approved", "rejected"}
        and usage.manager_approval_status in {"approved", "rejected", "not_required"}
    ):
        complete_todos(db, business_type=request_business_type, business_id=usage.id)
    else:
        complete_todos(db, business_type=request_business_type, business_id=usage.id, assigned_user_id=current_user.id)

    create_notification(
        db,
        title="领料审批已处理" if not is_special_request else "特殊领料审批已处理",
        content=(
            f"生产任务 {task.task_no} 的原料 {usage.material.name if usage.material else usage.material_id} "
            f"已由{'库存' if role_code == 'inventory' else '经理'}"
            f"{'通过' if payload.decision == 'approved' else '驳回'}，"
            f"当前任务状态：{task.status}。"
        ),
        module="production",
        recipient_user_ids=[task.created_by_id],
        created_by_id=current_user.id,
        category="warning" if payload.decision == "rejected" else "info",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="approve_special_request",
        content=f"审批生产任务 {task.task_no} 的领料申请 {usage.id}",
        after_data={
            "task_no": task.task_no,
            "material_id": usage.material_id,
            "task_status": task.status,
            "request_type": "special" if is_special_request else "normal",
            "inventory_approval_status": usage.inventory_approval_status,
            "manager_approval_status": usage.manager_approval_status,
            "decision": payload.decision,
            "approved_quantity": float(usage.approved_quantity),
        },
    )
    db.commit()
    return api_response(msg="领料审批已处理")

def complete_task(
    task_id: int,
    payload: ProductionCompleteRequest,
    current_user: User,
    db: Session,
):
    task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material), joinedload(ProductionTask.product))
        .where(ProductionTask.id == task_id)
    ).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    if task.status in {"waiting_quality", "ready_for_inbound", "completed"}:
        raise HTTPException(status_code=409, detail="当前生产任务已完工，不能重复完工")
    if task.status == "pending_special_approval":
        raise HTTPException(status_code=409, detail="当前生产任务仍有特殊领用待审批，不能完工")
    if task.status in {"pending_material", "pending_material_approval"}:
        raise HTTPException(status_code=409, detail="请先完成领料审批并完成原料出库，再执行完工")
    if task.status == 'pending_material_outbound':
        raise HTTPException(status_code=409, detail='请先完成原料出库，再执行完工')
    planned_quantity = Decimal(task.planned_quantity or 0)
    produced_quantity = Decimal(payload.produced_quantity)
    scrap_quantity = Decimal(payload.scrap_quantity or 0)
    if produced_quantity <= 0:
        raise HTTPException(status_code=409, detail="合格产出数量必须大于 0")
    if scrap_quantity < 0:
        raise HTTPException(status_code=409, detail="报废数量不能小于 0")
    if planned_quantity > 0 and produced_quantity + scrap_quantity != planned_quantity:
        raise HTTPException(status_code=409, detail="合格产出数量与报废数量之和必须等于计划数量")

    raw_warehouse = _raw_warehouse(db)
    material_ids = [usage.material_id for usage in task.usages]
    inventory_rows = db.execute(
        select(Inventory).where(Inventory.warehouse_id == raw_warehouse.id, Inventory.material_id.in_(material_ids))
    ).scalars().all() if material_ids else []
    inventory_map = {row.material_id: row for row in inventory_rows}
    usage_map = {usage.material_id: usage for usage in task.usages}

    actual_payloads = payload.actual_usages or [
        MaterialUsageInput(
            material_id=usage.material_id,
            requested_quantity=Decimal(usage.approved_quantity or usage.planned_quantity),
            reason=usage.reason,
        )
        for usage in task.usages
    ]

    seen_material_ids: set[int] = set()
    for usage_payload in actual_payloads:
        if usage_payload.material_id in seen_material_ids:
            raise HTTPException(status_code=409, detail="同一原料不能重复提交实际耗用")
        seen_material_ids.add(usage_payload.material_id)
        usage = usage_map.get(usage_payload.material_id)
        if usage is None:
            raise HTTPException(status_code=404, detail="存在未匹配的原料耗用明细")
        actual_quantity = Decimal(usage_payload.requested_quantity)
        allowed_quantity = _allowed_quantity(task, usage)
        approved_quantity = Decimal(usage.approved_quantity or 0)
        issued_quantity = Decimal(usage.actual_quantity or 0)
        if actual_quantity > allowed_quantity:
            if usage.special_request_status != "approved" or actual_quantity > Decimal(usage.approved_quantity or 0):
                raise HTTPException(status_code=409, detail=f"原料 {usage.material.name if usage.material else usage.material_id} 的实际耗用超出已批准范围，请先完成特殊领用审批")
        if actual_quantity > approved_quantity:
            raise HTTPException(status_code=409, detail=f"原料 {usage.material.name if usage.material else usage.material_id} 的实际耗用超过已批准领料数量，请重新申请领料")
        usage.actual_quantity = actual_quantity
        usage.reason = usage_payload.reason
        if actual_quantity <= allowed_quantity:
            usage.special_request_status = "not_required"
            usage.approved_quantity = max(Decimal(usage.approved_quantity or 0), actual_quantity)

    task.produced_quantity = produced_quantity
    task.scrap_quantity = scrap_quantity
    task.direct_labor_cost = Decimal(payload.direct_labor_cost or 0)
    task.manufacturing_overhead_cost = Decimal(payload.manufacturing_overhead_cost or 0)
    task.requires_quality = bool(payload.requires_quality)
    task.status = "waiting_quality" if task.requires_quality else "ready_for_inbound"
    task.started_at = task.started_at or now_local()
    task.completed_at = now_local()
    task.batch_no = _build_batch_no(db, task)
    task.preliminary_acceptance_note = payload.preliminary_acceptance_note
    task.completion_recorded_by_id = current_user.id

    quality_task = None
    # 生产成品先进入任务指定仓库，未指定的历史任务默认廊坊工厂仓。
    if not task.warehouse_id:
        task.warehouse_id = get_factory_warehouse(db).id
    target_warehouse = db.get(Warehouse, task.warehouse_id)
    if task.requires_quality:
        required_tests = max(1, ceil(float(payload.produced_quantity) / 1000))
        quality_task = create_production_quality_task(
            db,
            production_task=task,
            submitted_by_id=current_user.id,
            required_tests=required_tests,
            assigned_inspector_id=payload.assigned_inspector_id,
        )
        quality_user_ids = resolve_workflow_recipients(
            db,
            "quality",
            "manager",
            "chairman",
            workflow="production_quality_task",
            business_type="quality_task",
            business_id=quality_task.id,
        )
        eligible_quality_user_ids = resolve_quality_recipients_for_warehouse(
            db, warehouse_id=task.warehouse_id, fallback_user_ids=quality_user_ids,
        )
        if quality_task.assigned_inspector_id:
            if quality_task.assigned_inspector_id not in eligible_quality_user_ids:
                raise HTTPException(status_code=409, detail="指定质检员不属于生产目标仓库")
            quality_user_ids = [quality_task.assigned_inspector_id]
        else:
            quality_user_ids = eligible_quality_user_ids
        if not quality_user_ids:
            raise HTTPException(status_code=409, detail="生产目标仓库尚未配置质检员，请先配置仓库质检员或部门仓库范围")
        for user_id in quality_user_ids:
            create_todo(
                db,
                title=f"待质检任务 {quality_task.quality_no}",
                description=f"生产任务 {task.task_no} 已完工，请录入质检结果。",
                module="quality",
                assigned_user_id=user_id,
                business_type="quality_task",
                business_id=quality_task.id,
            )
        if quality_user_ids:
            create_notification(
                db,
                title="新增质检任务",
                content=f"生产任务 {task.task_no} 已完工，质检任务 {quality_task.quality_no} 已生成。",
                module="quality",
                recipient_user_ids=quality_user_ids,
                created_by_id=current_user.id,
            )
    else:
        inbound_quantity = Decimal(task.produced_quantity or 0)
        if inbound_quantity <= 0:
            raise HTTPException(status_code=409, detail="当前生产任务产出数量无效，不能提交入库")
        create_inventory_operation_request(
            db,
            operation_type="inbound",
            request_category="production_finished_inbound",
            source_module="production",
            source_type="production_task",
            source_id=task.id,
            source_no=task.task_no,
            warehouse_id=target_warehouse.id,
            requested_by_id=current_user.id,
            items=[
                {
                    "product_id": task.product_id,
                    "material_id": None,
                    "batch_no": task.batch_no,
                    "quantity": float(inbound_quantity),
                    "unit_cost": 0,
                }
            ],
            remark=payload.notes if hasattr(payload, "notes") else None,
            recipient_user_ids=_inventory_user_ids_for_warehouse(db, target_warehouse.id),
            title=f"待执行生产入库 {task.task_no}",
            description=f"生产任务 {task.task_no} 已完工且无需质检，请库管执行成品入库。",
        )
        create_notification(
            db,
            title="生产入库申请已提交",
            content=f"生产任务 {task.task_no} 已完工且无需质检，已向库管提交成品入库申请。",
            module="production",
            recipient_user_ids=list(dict.fromkeys([task.created_by_id, *_inventory_user_ids_for_warehouse(db, target_warehouse.id)])),
            created_by_id=current_user.id,
            category="success",
        )

    complete_todos(db, business_type="production_task", business_id=task.id)

    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="complete_task",
        content=f"完成生产任务 {task.task_no}",
        after_data={
            "task_no": task.task_no,
            "quality_no": quality_task.quality_no if quality_task else None,
            "requires_quality": task.requires_quality,
            "produced_quantity": float(task.produced_quantity),
            "scrap_quantity": float(task.scrap_quantity),
            "direct_labor_cost": float(task.direct_labor_cost or 0),
            "manufacturing_overhead_cost": float(task.manufacturing_overhead_cost or 0),
            "raw_warehouse": raw_warehouse.name,
            "finished_warehouse": target_warehouse.name,
            "batch_no": task.batch_no,
        },
    )
    db.commit()
    return api_response(
        data={
            "quality_no": quality_task.quality_no if quality_task else None,
            "requires_quality": task.requires_quality,
            "batch_no": task.batch_no,
            "status": task.status,
        }
    )


def cancel_task(
    task_id: int,
    payload: ProductionTaskCancelRequest,
    current_user: User,
    db: Session,
):
    task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material), joinedload(ProductionTask.product))
        .where(ProductionTask.id == task_id)
    ).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    if current_user.role.code == "production" and task.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可取消本人创建的生产任务")
    if task.status in {"waiting_quality", "completed", "scrapped", "partially_scrapped", "reworked"}:
        raise HTTPException(status_code=409, detail="当前生产任务已进入后续流程，不能取消")

    previous_status = task.status
    cancel_reason = payload.reason.strip()
    task.status = "cancelled"
    task.completed_at = now_local()
    task.notes = f"{task.notes or ''}\n[取消] {cancel_reason}".strip()

    complete_todos(db, business_type="production_task", business_id=task.id)
    for usage in task.usages:
        if usage.special_request_status == "pending_approval":
            usage.special_request_status = "rejected"
            if usage.inventory_approval_status == "pending_approval":
                usage.inventory_approval_status = "rejected"
            if usage.manager_approval_status == "pending_approval":
                usage.manager_approval_status = "rejected"
            complete_todos(db, business_type="production_special_request", business_id=usage.id)

    recipient_user_ids = list(dict.fromkeys([task.created_by_id, *_role_user_ids(db, "manager", "chairman")]))
    create_notification(
        db,
        title="生产任务已取消",
        content=f"生产任务 {task.task_no} 已取消。原因：{cancel_reason}",
        module="production",
        recipient_user_ids=recipient_user_ids,
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="cancel_production_task",
        content=f"取消生产任务 {task.task_no}",
        before_data={"status": previous_status},
        after_data={"status": task.status, "reason": cancel_reason},
    )
    db.commit()
    return api_response(data={"id": task.id, "status": task.status})


def _parse_schedule_datetime(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def _validate_plan_times(period_start, period_end, scheduled_start_at, scheduled_end_at) -> None:
    if period_start and period_end and period_end <= period_start:
        raise HTTPException(status_code=409, detail="计划结束时间必须晚于计划开始时间")
    if scheduled_start_at and scheduled_end_at and scheduled_end_at <= scheduled_start_at:
        raise HTTPException(status_code=409, detail="计划完工时间必须晚于计划开工时间")


def create_plan(payload: ProductionPlanCreate, current_user: User, db: Session):
    product = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    assigned_user = None
    if payload.assigned_user_id:
        assigned_user = db.execute(select(User).where(User.id == payload.assigned_user_id)).scalar_one_or_none()
        if assigned_user is None:
            raise HTTPException(status_code=404, detail="生产计划指派用户不存在")
    period_start = _parse_schedule_datetime(payload.period_start)
    period_end = _parse_schedule_datetime(payload.period_end)
    scheduled_start_at = _parse_schedule_datetime(payload.scheduled_start_at)
    scheduled_end_at = _parse_schedule_datetime(payload.scheduled_end_at)
    _validate_plan_times(period_start, period_end, scheduled_start_at, scheduled_end_at)

    plan = ProductionPlan(
        plan_no=next_business_no(db, "PP"),
        plan_name=payload.plan_name,
        period_type=payload.period_type,
        period_start=period_start,
        period_end=period_end,
        product_id=payload.product_id,
        sales_order_id=payload.sales_order_id,
        assigned_user_id=payload.assigned_user_id,
        planned_quantity=payload.planned_quantity,
        scheduled_start_at=scheduled_start_at,
        scheduled_end_at=scheduled_end_at,
        priority=payload.priority,
        line_name=payload.line_name,
        scheduling_note=payload.scheduling_note,
        detail_json=payload.detail_json or {},
        status="planned",
        created_by_id=current_user.id,
    )
    db.add(plan)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="create_production_plan",
        content=f"创建生产计划 {plan.plan_no}",
        after_data={
            "product_id": plan.product_id,
            "planned_quantity": float(plan.planned_quantity or 0),
            "priority": plan.priority,
            "period_type": plan.period_type,
            "assigned_user_id": plan.assigned_user_id,
        },
    )
    db.commit()
    return api_response(data={"id": plan.id, "plan_no": plan.plan_no})


def update_plan(plan_id: int, payload: ProductionPlanUpdate, current_user: User, db: Session):
    plan = db.execute(select(ProductionPlan).where(ProductionPlan.id == plan_id)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="生产计划不存在")
    if plan.converted_task_id or plan.locked_at:
        raise HTTPException(status_code=409, detail="生产计划已转建生产任务，不能继续编辑")
    if payload.assigned_user_id:
        assigned_user = db.execute(select(User).where(User.id == payload.assigned_user_id)).scalar_one_or_none()
        if assigned_user is None:
            raise HTTPException(status_code=404, detail="生产计划指派用户不存在")
    period_start = _parse_schedule_datetime(payload.period_start)
    period_end = _parse_schedule_datetime(payload.period_end)
    scheduled_start_at = _parse_schedule_datetime(payload.scheduled_start_at)
    scheduled_end_at = _parse_schedule_datetime(payload.scheduled_end_at)
    _validate_plan_times(period_start, period_end, scheduled_start_at, scheduled_end_at)
    plan.plan_name = payload.plan_name
    plan.period_type = payload.period_type
    plan.period_start = period_start
    plan.period_end = period_end
    plan.product_id = payload.product_id
    plan.sales_order_id = payload.sales_order_id
    plan.assigned_user_id = payload.assigned_user_id
    plan.planned_quantity = payload.planned_quantity
    plan.scheduled_start_at = scheduled_start_at
    plan.scheduled_end_at = scheduled_end_at
    plan.priority = payload.priority
    plan.line_name = payload.line_name
    plan.scheduling_note = payload.scheduling_note
    plan.detail_json = payload.detail_json or {}
    plan.status = payload.status
    plan.updated_by_id = current_user.id
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="update_production_plan",
        content=f"更新生产计划 {plan.plan_no}",
        after_data={
            "status": plan.status,
            "planned_quantity": float(plan.planned_quantity or 0),
            "period_type": plan.period_type,
            "assigned_user_id": plan.assigned_user_id,
        },
    )
    db.commit()
    return api_response(data={"id": plan.id, "plan_no": plan.plan_no, "status": plan.status})


def convert_plan_to_task(plan_id: int, current_user: User, db: Session):
    plan = db.execute(select(ProductionPlan).where(ProductionPlan.id == plan_id)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="生产计划不存在")
    if plan.converted_task_id:
        raise HTTPException(status_code=409, detail="当前生产计划已转为生产任务")
    payload = ProductionTaskCreate(
        source_type="manual",
        sales_order_id=plan.sales_order_id,
        product_id=plan.product_id,
        planned_quantity=plan.planned_quantity,
        fluctuation_ratio=Decimal("0"),
        notes=f"由生产计划 {plan.plan_no} 转建。{plan.scheduling_note or ''}".strip(),
        material_usages=[],
    )
    result = create_task(payload, current_user, db)
    task_id = result["data"]["id"] if isinstance(result, dict) else None
    if not task_id:
        raise HTTPException(status_code=500, detail="生产计划转建生产任务失败")
    plan.converted_task_id = task_id
    plan.status = "converted"
    plan.locked_at = now_local()
    plan.updated_by_id = current_user.id
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="update_production_plan",
        content=f"生产计划 {plan.plan_no} 转建生产任务",
        after_data={"plan_id": plan.id, "production_task_id": task_id, "status": plan.status},
    )
    db.commit()
    return api_response(data={"id": plan.id, "plan_no": plan.plan_no, "production_task_id": task_id, "status": plan.status})
