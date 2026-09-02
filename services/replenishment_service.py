from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models import BOM, FulfillmentDemand, Inventory, Material, PayableRecord, Product, ProductionMaterialUsage, ProductionTask, PurchaseOrder, PurchaseOrderItem, Role, SalesOrder, SalesOrderItem, TodoItem, User, Warehouse
from services.core import create_notification, create_todo, ensure_notification, next_business_no, resolve_workflow_recipients, write_operation_log
from services.display import status_label
from services.request_trace import emit

# ===== 补货需求进度状态机(实时推导,不落库) =====
# 状态集:待处理/采购中/生产中/原料采购中/已下单/待收货/待财务付款/待入库/已完成/已取消
DEMAND_PROGRESS_LABELS = {
    "pending": "待处理",
    "purchasing": "采购中",
    "ordered": "已下单",
    "receiving": "待收货",
    "pending_payment": "待财务付款",
    "raw_purchasing": "原料采购中",
    "producing": "生产中",
    "pending_inbound": "待入库",
    "completed": "已完成",
    "cancelled": "已取消",
}


def _demand_related_purchase_order(db: Session, demand_id: int) -> PurchaseOrder | None:
    return db.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.replenishment_source_type == "fulfillment_demand",
            PurchaseOrder.replenishment_source_id == demand_id,
        )
    ).scalar_one_or_none()


def _demand_related_production_task(db: Session, demand_id: int) -> ProductionTask | None:
    return db.execute(
        select(ProductionTask).where(ProductionTask.fulfillment_demand_id == demand_id)
    ).scalar_one_or_none()


def _purchase_order_paid_amount(db: Session, order: PurchaseOrder) -> Decimal:
    rows = db.execute(
        select(PayableRecord).where(PayableRecord.purchase_order_id == order.id)
    ).scalars().all()
    return sum(
        (Decimal(row.order_amount or 0) for row in rows if row.status in {"paid", "partial_settled", "settled"}),
        Decimal("0"),
    )


def resolve_demand_progress(db: Session, demand: FulfillmentDemand) -> dict:
    """实时推导补货需求进度状态。

    贸易型:待处理 → 采购中 → 已下单 → 待收货 → 待财务付款 → 已完成
    生产型:待处理 → 原料采购中 → 生产中 → 待入库 → 已完成
    """
    if demand.status == "cancelled":
        return {"progress_status": "cancelled", "progress_label": "已取消", "related_no": None, "related_status": None, "related_status_label": None}
    if demand.status == "pending_material":
        return {"progress_status": "pending_material", "progress_label": "待补料", "related_no": None, "related_status": None, "related_status_label": None}
    if demand.status != "converted":
        return {"progress_status": "pending", "progress_label": "待处理", "related_no": None, "related_status": None, "related_status_label": None}

    if demand.fulfillment_mode == "trade":
        po = _demand_related_purchase_order(db, demand.id)
        if po is None:
            return {"progress_status": "pending", "progress_label": "待处理", "related_no": None, "related_status": None, "related_status_label": None}
        po_label = status_label(po.status)
        if po.status in {"draft", "pending_approval", "rejected"}:
            return {"progress_status": "purchasing", "progress_label": "采购中", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
        if po.status == "purchasing":
            return {"progress_status": "ordered", "progress_label": "已下单", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
        if po.status in {"partial_received", "pending_purchase_confirm"}:
            return {"progress_status": "receiving", "progress_label": "待收货", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
        if po.status == "fully_received":
            paid = _purchase_order_paid_amount(db, po)
            if paid >= Decimal(po.total_amount or 0):
                return {"progress_status": "completed", "progress_label": "已完成", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
            return {"progress_status": "pending_payment", "progress_label": "待财务付款", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
        if po.status == "completed":
            return {"progress_status": "completed", "progress_label": "已完成", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
        if po.status == "cancelled":
            return {"progress_status": "pending", "progress_label": "待处理", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}
        return {"progress_status": "purchasing", "progress_label": "采购中", "related_no": po.order_no, "related_status": po.status, "related_status_label": po_label}

    # 生产型
    task = _demand_related_production_task(db, demand.id)
    if task is None:
        return {"progress_status": "pending", "progress_label": "待处理", "related_no": None, "related_status": None, "related_status_label": None}
    task_label = status_label(task.status)
    if task.status in {"pending_material", "pending_material_approval", "pending_material_outbound"}:
        return {"progress_status": "raw_purchasing", "progress_label": "原料采购中", "related_no": task.task_no, "related_status": task.status, "related_status_label": task_label}
    if task.status in {"material_issued", "in_production", "reworked"}:
        return {"progress_status": "producing", "progress_label": "生产中", "related_no": task.task_no, "related_status": task.status, "related_status_label": task_label}
    if task.status in {"waiting_quality", "ready_for_inbound"}:
        return {"progress_status": "pending_inbound", "progress_label": "待入库", "related_no": task.task_no, "related_status": task.status, "related_status_label": task_label}
    if task.status == "completed":
        return {"progress_status": "completed", "progress_label": "已完成", "related_no": task.task_no, "related_status": task.status, "related_status_label": task_label}
    if task.status in {"scrapped", "partially_scrapped", "cancelled"}:
        return {"progress_status": "pending", "progress_label": "待处理", "related_no": task.task_no, "related_status": task.status, "related_status_label": task_label}
    return {"progress_status": "producing", "progress_label": "生产中", "related_no": task.task_no, "related_status": task.status, "related_status_label": task_label}


def role_user_ids(db: Session, *role_codes: str) -> list[int]:
    if not role_codes:
        return []
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def ensure_fulfillment_demand_routing(
    db: Session,
    *,
    demand: FulfillmentDemand,
    order: SalesOrder | None = None,
    product: Product | None = None,
    current_user: User | None = None,
) -> list[int]:
    """Ensure an existing shortage demand has active owner todos."""
    if demand.status != "pending":
        return []
    order = order or demand.sales_order
    product = product or demand.product
    assigned_role = "purchase" if demand.fulfillment_mode == "trade" else "production"
    recipient_ids = resolve_workflow_recipients(
        db,
        assigned_role,
        workflow="fulfillment_demand",
        business_type="fulfillment_demand",
        business_id=demand.id,
    )
    if recipient_ids and demand.assigned_user_id not in recipient_ids:
        demand.assigned_user_id = recipient_ids[0]

    if order is not None:
        order_no = order.order_no
    elif demand.source_order_no:
        order_no = demand.source_order_no
    elif demand.sales_order_id:
        order_no = str(demand.sales_order_id)
    else:
        order_no = "手动补货"
    product_name = product.name if product else str(demand.product_id)
    action_label = "创建采购单" if demand.fulfillment_mode == "trade" else "创建生产任务"
    title = f"待处理补货需求 {demand.demand_no}"
    description = (
        f"来源: {order_no} 的产品 {product_name} 缺口 {float(demand.shortage_quantity or 0)}，"
        f"请{action_label}或填写取消原因。"
    )
    for user_id in recipient_ids:
        todo = create_todo(
            db,
            title=title,
            description=description,
            module=assigned_role,
            assigned_user_id=user_id,
            business_type="fulfillment_demand",
            business_id=demand.id,
            priority="high",
        )
        todo.title = title
        todo.description = description
        todo.module = assigned_role
        todo.priority = "high"

    notification_user_ids = list(
        dict.fromkeys([
            *recipient_ids,
            order.sales_user_id if order and order.sales_user_id else None,
        ])
    )
    ensure_notification(
        db,
        title="新增履约补货需求",
        content=(
            f"来源 {order_no} 的产品 {product_name} 库存不足，"
            f"已生成{'采购' if demand.fulfillment_mode == 'trade' else '生产'}待办。"
        ),
        module=assigned_role,
        recipient_user_ids=notification_user_ids,
        created_by_id=current_user.id if current_user else None,
        category="warning",
    )
    emit(
        "fulfillment_demand_routing_synced",
        demand_id=demand.id,
        demand_no=demand.demand_no,
        sales_order_id=demand.sales_order_id,
        fulfillment_mode=demand.fulfillment_mode,
        assigned_user_id=demand.assigned_user_id,
        recipient_user_ids=recipient_ids,
    )
    return recipient_ids


def product_expected_available_quantity(db: Session, *, product_id: int, inventory: Inventory | None = None) -> Decimal:
    available_quantity = Decimal("0")
    if inventory is not None:
        available_quantity = Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0)
    pending_sales = db.execute(
        select(
            func.coalesce(func.sum(SalesOrderItem.quantity), 0),
            func.coalesce(func.sum(SalesOrderItem.shipped_quantity), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .where(SalesOrder.status == "pending_shipment", SalesOrderItem.product_id == product_id)
    ).one()
    pending_sales_quantity = max(Decimal(pending_sales[0] or 0) - Decimal(pending_sales[1] or 0), Decimal("0"))
    in_transit = db.execute(
        select(
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(
            PurchaseOrder.status.in_(["purchasing", "partial_received", "pending_purchase_confirm", "fully_received"]),
            PurchaseOrderItem.product_id == product_id,
        )
    ).one()
    in_transit_quantity = max(Decimal(in_transit[0] or 0) - Decimal(in_transit[1] or 0), Decimal("0"))
    return available_quantity - pending_sales_quantity + in_transit_quantity


def material_expected_available_quantity(db: Session, *, material_id: int, inventory: Inventory | None = None) -> Decimal:
    available_quantity = Decimal("0")
    if inventory is not None:
        available_quantity = Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0)
    planned_material_rows = db.execute(
        select(
            ProductionMaterialUsage.planned_quantity,
            ProductionMaterialUsage.actual_quantity,
        )
        .join(ProductionTask, ProductionTask.id == ProductionMaterialUsage.production_task_id)
        .where(
            ProductionMaterialUsage.material_id == material_id,
            ProductionTask.status.not_in({
                "completed",
                "cancelled",
                "scrapped",
                "partially_scrapped",
                "reworked",
                "rework_pending",
            }),
        )
    ).all()
    planned_quantity = sum(
        (
            max(Decimal(row.planned_quantity or 0) - Decimal(row.actual_quantity or 0), Decimal("0"))
            for row in planned_material_rows
        ),
        Decimal("0"),
    )
    available_quantity = max(available_quantity - planned_quantity, Decimal("0"))
    in_transit = db.execute(
        select(
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(
            PurchaseOrder.status.in_(["purchasing", "partial_received", "pending_purchase_confirm", "fully_received"]),
            PurchaseOrderItem.material_id == material_id,
        )
    ).one()
    in_transit_quantity = max(Decimal(in_transit[0] or 0) - Decimal(in_transit[1] or 0), Decimal("0"))
    return available_quantity + in_transit_quantity


def create_manual_fulfillment_demand(
    db: Session,
    *,
    product: Product,
    shortage_quantity: Decimal,
    remark: str | None,
    current_user: User,
) -> FulfillmentDemand:
    """库管/生产手动提补货需求(无客户订单来源)。"""
    if shortage_quantity <= 0:
        raise ValueError("补货数量必须大于 0")
    fulfillment_mode = product.fulfillment_mode if product else "production"
    demand = FulfillmentDemand(
        demand_no=next_business_no(db, "BH"),
        sales_order_id=None,
        sales_order_item_id=None,
        product_id=product.id,
        fulfillment_mode=fulfillment_mode,
        shortage_quantity=shortage_quantity,
        status="pending",
        assigned_user_id=None,
        created_by_id=current_user.id,
        source_order_no=None,
        process_note=remark,
    )
    db.add(demand)
    db.flush()
    ensure_fulfillment_demand_routing(
        db,
        demand=demand,
        order=None,
        product=product,
        current_user=current_user,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="inventory",
        action="create_manual_fulfillment_demand",
        content=f"手动创建补货需求 {demand.demand_no}({product.name} {float(shortage_quantity)})",
        after_data={
            "demand_no": demand.demand_no,
            "fulfillment_mode": fulfillment_mode,
            "shortage_quantity": float(shortage_quantity),
        },
    )
    return demand


def create_sales_shortage_demand(
    db: Session,
    *,
    order: SalesOrder,
    order_item: SalesOrderItem,
    product: Product,
    shortage_quantity: Decimal,
    current_user: User,
) -> FulfillmentDemand | None:
    if shortage_quantity <= 0:
        return None
    fulfillment_mode = product.fulfillment_mode if product else "production"
    assigned_role = "purchase" if fulfillment_mode == "trade" else "production"
    demand = FulfillmentDemand(
        demand_no=next_business_no(db, "BH"),
        sales_order_id=order.id,
        sales_order_item_id=order_item.id,
        product_id=order_item.product_id,
        fulfillment_mode=fulfillment_mode,
        shortage_quantity=shortage_quantity,
        status="pending",
        assigned_user_id=None,
        created_by_id=current_user.id,
        source_order_no=order.order_no,
    )
    db.add(demand)
    db.flush()
    ensure_fulfillment_demand_routing(
        db,
        demand=demand,
        order=order,
        product=product,
        current_user=current_user,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="create_fulfillment_demand",
        content=f"为客户订单 {order.order_no} 创建补货需求 {demand.demand_no}",
        after_data={
            "demand_no": demand.demand_no,
            "fulfillment_mode": fulfillment_mode,
            "shortage_quantity": float(shortage_quantity),
        },
    )
    return demand


def _sync_low_stock_product_material_replenishment(
    db: Session,
    *,
    inventory: Inventory,
    product: Product,
    production_quantity: Decimal,
) -> None:
    """Create purchase todos for raw materials needed by product low-stock production."""
    business_type = "low_stock_product_material_replenishment"
    pending_todos = db.execute(
        select(TodoItem).where(
            TodoItem.business_type == business_type,
            TodoItem.business_id == inventory.id,
            TodoItem.status != "completed",
        )
    ).scalars().all()
    if production_quantity <= 0:
        for todo in pending_todos:
            todo.status = "completed"
        return

    bom = db.execute(
        select(BOM).options(joinedload(BOM.items)).where(BOM.product_id == product.id)
    ).unique().scalar_one_or_none()
    if bom is None or not bom.items:
        emit(
            "low_stock_product_material_replenishment_skipped",
            reason="missing_bom",
            inventory_id=inventory.id,
            product_id=product.id,
            production_quantity=float(production_quantity),
        )
        return
    raw_warehouse = db.execute(
        select(Warehouse).where(Warehouse.warehouse_type == "raw").order_by(Warehouse.id.asc())
    ).scalars().first()
    if raw_warehouse is None:
        emit(
            "low_stock_product_material_replenishment_skipped",
            reason="missing_raw_warehouse",
            inventory_id=inventory.id,
            product_id=product.id,
            production_quantity=float(production_quantity),
        )
        return

    material_ids = [item.material_id for item in bom.items]
    inventory_rows = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == raw_warehouse.id,
            Inventory.material_id.in_(material_ids or [-1]),
            Inventory.product_id.is_(None),
        )
    ).scalars().all()
    inventory_map = {row.material_id: row for row in inventory_rows}
    material_map = {
        row.id: row
        for row in db.execute(select(Material).where(Material.id.in_(material_ids or [-1]))).scalars().all()
    }
    shortage_items: list[dict] = []
    for bom_item in bom.items:
        required_quantity = production_quantity * Decimal(bom_item.standard_quantity or 0)
        inventory_row = inventory_map.get(bom_item.material_id)
        expected_available = material_expected_available_quantity(
            db,
            material_id=bom_item.material_id,
            inventory=inventory_row,
        )
        shortage_quantity = max(required_quantity - expected_available, Decimal("0"))
        if shortage_quantity <= 0:
            continue
        material = material_map.get(bom_item.material_id)
        shortage_items.append(
            {
                "material_id": bom_item.material_id,
                "material_name": material.name if material else f"原料#{bom_item.material_id}",
                "required_quantity": float(required_quantity),
                "expected_available_quantity": float(expected_available),
                "shortage_quantity": float(shortage_quantity),
            }
        )

    if not shortage_items:
        for todo in pending_todos:
            todo.status = "completed"
        emit(
            "low_stock_product_material_replenishment_skipped",
            reason="no_material_shortage",
            inventory_id=inventory.id,
            product_id=product.id,
            production_quantity=float(production_quantity),
        )
        return

    purchase_user_ids = resolve_workflow_recipients(
        db,
        "purchase",
        workflow=business_type,
        business_type=business_type,
        business_id=inventory.id,
    )
    payload = {
        "source_no": f"安全库存-{product.product_code or product.id}",
        "inventory_id": inventory.id,
        "product_id": product.id,
        "product_name": product.name,
        "production_quantity": float(production_quantity),
        "items": shortage_items,
    }
    description = (
        f"产品 {product.name} 安全库存补货生产需求存在原料缺口，"
        f"共 {len(shortage_items)} 种原料，请采购人员处理。"
        f"|sales_order_material_replenishment:{json.dumps(payload, ensure_ascii=False)}"
    )
    existing_user_ids = {todo.assigned_user_id for todo in pending_todos}
    new_user_ids = [user_id for user_id in purchase_user_ids if user_id not in existing_user_ids]
    for user_id in purchase_user_ids:
        todo = create_todo(
            db,
            title=f"待采购安全库存生产原料 {product.name}",
            description=description,
            module="purchase",
            assigned_user_id=user_id,
            business_type=business_type,
            business_id=inventory.id,
            priority="high",
        )
        todo.title = f"待采购安全库存生产原料 {product.name}"
        todo.description = description
        todo.module = "purchase"
        todo.priority = "high"
    if new_user_ids:
        ensure_notification(
            db,
            title="安全库存成品生产原料采购需求",
            content=f"产品 {product.name} 的安全库存生产补货存在原料缺口，已通知采购处理。",
            module="purchase",
            recipient_user_ids=new_user_ids,
            category="warning",
        )
    emit(
        "low_stock_product_material_replenishment_synced",
        inventory_id=inventory.id,
        product_id=product.id,
        production_quantity=float(production_quantity),
        material_ids=[item["material_id"] for item in shortage_items],
        recipient_user_ids=purchase_user_ids,
        new_recipient_user_ids=new_user_ids,
    )


def sync_product_low_stock_replenishment(
    db: Session,
    *,
    inventory: Inventory,
    notify_on_recovery: bool = False,
) -> None:
    product = inventory.product
    if product is None:
        return
    if inventory.warehouse and inventory.warehouse.warehouse_type != "finished":
        return
    safety_stock_qty = Decimal(product.safety_stock_qty or 0)
    if safety_stock_qty <= 0:
        return
    expected_available_quantity = product_expected_available_quantity(db, product_id=product.id, inventory=inventory)
    shortage_quantity = safety_stock_qty - expected_available_quantity
    business_type = "low_stock_replenishment"
    pending_todos = db.execute(
        select(TodoItem).where(
            TodoItem.business_type == business_type,
            TodoItem.business_id == inventory.id,
            TodoItem.status != "completed",
        )
    ).scalars().all()
    if shortage_quantity <= 0:
        if pending_todos and notify_on_recovery:
            create_notification(
                db,
                title="安全库存补货需求恢复",
                content=f"{product.name} 预计可用库存已恢复至 {float(expected_available_quantity)}，不再低于安全库存 {float(safety_stock_qty)}。",
                module="inventory",
                recipient_user_ids=list(dict.fromkeys([todo.assigned_user_id for todo in pending_todos])),
                category="info",
                is_popup=False,
            )
        for todo in pending_todos:
            todo.status = "completed"
        if product.fulfillment_mode == "production":
            complete_low_stock_material_todos = db.execute(
                select(TodoItem).where(
                    TodoItem.business_type == "low_stock_product_material_replenishment",
                    TodoItem.business_id == inventory.id,
                    TodoItem.status != "completed",
                )
            ).scalars().all()
            for todo in complete_low_stock_material_todos:
                todo.status = "completed"
        db.flush()
        return
    fulfillment_mode = product.fulfillment_mode or "production"
    target_role = "purchase" if fulfillment_mode == "trade" else "production"
    module = "purchase" if fulfillment_mode == "trade" else "production"
    existing_user_ids = {todo.assigned_user_id for todo in pending_todos}
    target_user_ids = resolve_workflow_recipients(
        db,
        target_role,
        workflow="low_stock_replenishment",
        business_type=business_type,
        business_id=inventory.id,
    )
    new_user_ids = [user_id for user_id in target_user_ids if user_id not in existing_user_ids]
    payload = {
        "product_id": product.id,
        "product_name": product.name,
        "fulfillment_mode": fulfillment_mode,
        "shortage_quantity": float(shortage_quantity),
        "safety_stock_qty": float(safety_stock_qty),
        "expected_available_quantity": float(expected_available_quantity),
        "inventory_id": inventory.id,
    }
    description = (
        f"产品 {product.name} 预计可用库存 {float(expected_available_quantity)}，低于安全库存 {float(safety_stock_qty)}，"
        f"建议补货 {float(shortage_quantity)}。|replenishment:{json.dumps(payload, ensure_ascii=False)}"
    )
    for user_id in target_user_ids:
        create_todo(
            db,
            title=f"安全库存补货需求：{product.name}",
            description=description,
            module=module,
            assigned_user_id=user_id,
            business_type=business_type,
            business_id=inventory.id,
            priority="high",
        )
    for todo in pending_todos:
        if todo.assigned_user_id in target_user_ids:
            todo.title = f"安全库存补货需求：{product.name}"
            todo.description = description
            todo.module = module
    if new_user_ids:
        ensure_notification(
            db,
            title="安全库存补货需求",
            content=f"{product.name} 低于安全库存，已同步{'采购' if fulfillment_mode == 'trade' else '生产'}处理。",
            module=module,
            recipient_user_ids=new_user_ids,
            category="warning",
        )
    emit(
        "low_stock_replenishment_synced",
        inventory_id=inventory.id,
        item_type="product",
        item_id=product.id,
        fulfillment_mode=fulfillment_mode,
        shortage_quantity=float(shortage_quantity),
        recipient_user_ids=target_user_ids,
        new_recipient_user_ids=new_user_ids,
    )
    if fulfillment_mode == "production":
        _sync_low_stock_product_material_replenishment(
            db,
            inventory=inventory,
            product=product,
            production_quantity=shortage_quantity,
        )


def sync_material_low_stock_replenishment(
    db: Session,
    *,
    inventory: Inventory,
    notify_on_recovery: bool = False,
) -> None:
    material = inventory.material
    if material is None:
        return
    if inventory.warehouse and inventory.warehouse.warehouse_type != "raw":
        return
    safety_stock_qty = Decimal(material.safety_stock_qty or 0)
    if safety_stock_qty <= 0:
        return
    expected_available_quantity = material_expected_available_quantity(db, material_id=material.id, inventory=inventory)
    shortage_quantity = safety_stock_qty - expected_available_quantity
    business_type = "low_stock_replenishment"
    pending_todos = db.execute(
        select(TodoItem).where(
            TodoItem.business_type == business_type,
            TodoItem.business_id == inventory.id,
            TodoItem.status != "completed",
        )
    ).scalars().all()
    if shortage_quantity <= 0:
        if pending_todos and notify_on_recovery:
            create_notification(
                db,
                title="安全库存补货需求恢复",
                content=f"{material.name} 预计可用库存已恢复至 {float(expected_available_quantity)}，不再低于安全库存 {float(safety_stock_qty)}。",
                module="purchase",
                recipient_user_ids=list(dict.fromkeys([todo.assigned_user_id for todo in pending_todos])),
                category="info",
                is_popup=False,
            )
        for todo in pending_todos:
            todo.status = "completed"
        db.flush()
        return
    existing_user_ids = {todo.assigned_user_id for todo in pending_todos}
    target_user_ids = resolve_workflow_recipients(
        db,
        "purchase",
        workflow="low_stock_replenishment",
        business_type=business_type,
        business_id=inventory.id,
    )
    new_user_ids = [user_id for user_id in target_user_ids if user_id not in existing_user_ids]
    payload = {
        "material_id": material.id,
        "material_name": material.name,
        "fulfillment_mode": "purchase",
        "shortage_quantity": float(shortage_quantity),
        "safety_stock_qty": float(safety_stock_qty),
        "expected_available_quantity": float(expected_available_quantity),
        "inventory_id": inventory.id,
    }
    description = (
        f"原料 {material.name} 预计可用库存 {float(expected_available_quantity)}，低于安全库存 {float(safety_stock_qty)}，"
        f"建议补货 {float(shortage_quantity)}。|replenishment:{json.dumps(payload, ensure_ascii=False)}"
    )
    for user_id in target_user_ids:
        create_todo(
            db,
            title=f"安全库存补货需求：{material.name}",
            description=description,
            module="purchase",
            assigned_user_id=user_id,
            business_type=business_type,
            business_id=inventory.id,
            priority="high",
        )
    for todo in pending_todos:
        if todo.assigned_user_id in target_user_ids:
            todo.title = f"安全库存补货需求：{material.name}"
            todo.description = description
            todo.module = "purchase"
    if new_user_ids:
        ensure_notification(
            db,
            title="安全库存补货需求",
            content=f"{material.name} 低于安全库存，已同步采购处理。",
            module="purchase",
            recipient_user_ids=new_user_ids,
            category="warning",
        )
    emit(
        "low_stock_replenishment_synced",
        inventory_id=inventory.id,
        item_type="material",
        item_id=material.id,
        fulfillment_mode="purchase",
        shortage_quantity=float(shortage_quantity),
        recipient_user_ids=target_user_ids,
        new_recipient_user_ids=new_user_ids,
    )
