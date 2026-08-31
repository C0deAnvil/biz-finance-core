"""库存多维度重算服务：在制/在途/在订/可用 由业务单据推导，幂等可重复执行，防止增量累加漂移。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    Inventory,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    SalesOrder,
    SalesOrderItem,
)

ACTIVE_PURCHASE_STATUSES = ("purchasing", "partial_received", "pending_purchase_confirm", "fully_received")
INACTIVE_TASK_STATUSES = ("completed", "cancelled", "scrapped", "partially_scrapped", "reworked", "rework_pending")


def _pending_sales_remaining(db: Session, product_id: int) -> Decimal:
    row = db.execute(
        select(
            func.coalesce(func.sum(SalesOrderItem.quantity), 0),
            func.coalesce(func.sum(SalesOrderItem.shipped_quantity), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .where(SalesOrder.status == "pending_shipment", SalesOrderItem.product_id == product_id)
    ).one()
    return max(Decimal(row[0] or 0) - Decimal(row[1] or 0), Decimal("0"))


def _product_in_production(db: Session, product_id: int) -> Decimal:
    row = db.execute(
        select(
            func.coalesce(func.sum(ProductionTask.planned_quantity), 0),
            func.coalesce(func.sum(ProductionTask.produced_quantity), 0),
        ).where(
            ProductionTask.product_id == product_id,
            ProductionTask.status.not_in(INACTIVE_TASK_STATUSES),
            ProductionTask.source_type != "purchase_quality",
        )
    ).one()
    return max(Decimal(row[0] or 0) - Decimal(row[1] or 0), Decimal("0"))


def _material_in_production(db: Session, material_id: int) -> Decimal:
    row = db.execute(
        select(
            func.coalesce(func.sum(ProductionMaterialUsage.planned_quantity), 0),
            func.coalesce(func.sum(ProductionMaterialUsage.actual_quantity), 0),
        )
        .join(ProductionTask, ProductionTask.id == ProductionMaterialUsage.production_task_id)
        .where(
            ProductionMaterialUsage.material_id == material_id,
            ProductionTask.status.not_in(INACTIVE_TASK_STATUSES),
            ProductionTask.source_type != "purchase_quality",
        )
    ).one()
    return max(Decimal(row[0] or 0) - Decimal(row[1] or 0), Decimal("0"))


def _on_order(db: Session, *, product_id: int | None = None, material_id: int | None = None) -> Decimal:
    filters = [PurchaseOrder.status.in_(ACTIVE_PURCHASE_STATUSES)]
    if product_id is not None:
        filters.append(PurchaseOrderItem.product_id == product_id)
    if material_id is not None:
        filters.append(PurchaseOrderItem.material_id == material_id)
    row = db.execute(
        select(
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(*filters)
    ).one()
    return max(Decimal(row[0] or 0) - Decimal(row[1] or 0), Decimal("0"))


def sync_inventory_dimensions(db: Session) -> int:
    """重算所有库存行的多维度字段。返回更新行数。"""
    rows = db.execute(select(Inventory)).scalars().all()
    updated = 0
    for inv in rows:
        quantity = Decimal(inv.quantity or 0)
        reserved = Decimal(inv.reserved_quantity or 0)
        if inv.product_id is not None:
            in_production = _product_in_production(db, inv.product_id)
            on_order = _on_order(db, product_id=inv.product_id)
            pending_sales = _pending_sales_remaining(db, inv.product_id)
            available = quantity - reserved - pending_sales + on_order
        elif inv.material_id is not None:
            in_production = _material_in_production(db, inv.material_id)
            on_order = _on_order(db, material_id=inv.material_id)
            available = quantity - reserved - in_production + on_order
        else:
            in_production = Decimal("0")
            on_order = Decimal("0")
            available = quantity - reserved

        if inv.in_production != in_production:
            inv.in_production = in_production
            updated += 1
        if inv.on_order != on_order:
            inv.on_order = on_order
            updated += 1
        if inv.available != available:
            inv.available = available
            updated += 1
    if updated:
        db.flush()
    return updated
