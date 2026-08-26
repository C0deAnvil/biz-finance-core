from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import FulfillmentDemand, Inventory, Material, Product, ProductionMaterialUsage, ProductionTask, PurchaseOrder, PurchaseOrderItem, Role, SalesOrder, SalesOrderItem, TodoItem, User
from services.core import create_notification, create_todo, next_business_no, write_operation_log


def role_user_ids(db: Session, *role_codes: str) -> list[int]:
    if not role_codes:
        return []
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


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
    assigned_user_ids = role_user_ids(db, assigned_role)
    demand = FulfillmentDemand(
        demand_no=next_business_no(db, "BH"),
        sales_order_id=order.id,
        sales_order_item_id=order_item.id,
        product_id=order_item.product_id,
        fulfillment_mode=fulfillment_mode,
        shortage_quantity=shortage_quantity,
        status="pending",
        assigned_user_id=assigned_user_ids[0] if assigned_user_ids else None,
        source_order_no=order.order_no,
    )
    db.add(demand)
    db.flush()
    for user_id in assigned_user_ids:
        create_todo(
            db,
            title=f"待处理补货需求 {demand.demand_no}",
            description=(
                f"客户订单 {order.order_no} 的产品 {product.name if product else order_item.product_id} 缺口 {float(shortage_quantity)}，"
                f"请{'创建采购单' if fulfillment_mode == 'trade' else '创建生产任务'}或填写取消原因。"
            ),
            module=assigned_role,
            assigned_user_id=user_id,
            business_type="fulfillment_demand",
            business_id=demand.id,
            priority="high",
        )
    create_notification(
        db,
        title="新增履约补货需求",
        content=(
            f"客户订单 {order.order_no} 的产品 {product.name if product else order_item.product_id} 库存不足，"
            f"已生成{'采购' if fulfillment_mode == 'trade' else '生产'}待办。"
        ),
        module=assigned_role,
        recipient_user_ids=list(dict.fromkeys([*assigned_user_ids, order.sales_user_id])),
        created_by_id=current_user.id,
        category="warning",
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
        db.flush()
        return
    fulfillment_mode = product.fulfillment_mode or "production"
    target_role = "purchase" if fulfillment_mode == "trade" else "production"
    module = "purchase" if fulfillment_mode == "trade" else "production"
    existing_user_ids = {todo.assigned_user_id for todo in pending_todos}
    target_user_ids = [user_id for user_id in role_user_ids(db, target_role) if user_id not in existing_user_ids]
    if not target_user_ids:
        return
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
    create_notification(
        db,
        title="安全库存补货需求",
        content=f"{product.name} 低于安全库存，已同步{'采购' if fulfillment_mode == 'trade' else '生产'}处理。",
        module=module,
        recipient_user_ids=target_user_ids,
        category="warning",
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
    target_user_ids = [user_id for user_id in role_user_ids(db, "purchase") if user_id not in existing_user_ids]
    if not target_user_ids:
        return
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
    create_notification(
        db,
        title="安全库存补货需求",
        content=f"{material.name} 低于安全库存，已同步采购处理。",
        module="purchase",
        recipient_user_ids=target_user_ids,
        category="warning",
    )
