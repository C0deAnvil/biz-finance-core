from __future__ import annotations

import json
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    Product,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    QualityInspection,
    QualityTask,
    Supplier,
    User,
    Role,
    Warehouse,
)
from services.core import next_business_no, resolve_assigned_inspector_id
from services.time_utils import now_local
from services.organization import get_inventory_scope_warehouse_ids

PURCHASE_QUALITY_NOTES_PREFIX = "PURCHASE_QUALITY::"
RETURN_QUALITY_NOTES_PREFIX = "RETURN_QUALITY::"


def parse_purchase_quality_meta(production_task: ProductionTask | None) -> dict:
    if production_task is None or production_task.source_type != "purchase_quality":
        return {}
    raw_notes = str(production_task.notes or "")
    if not raw_notes.startswith(PURCHASE_QUALITY_NOTES_PREFIX):
        return {}
    try:
        return json.loads(raw_notes[len(PURCHASE_QUALITY_NOTES_PREFIX) :])
    except Exception:
        return {}


def build_purchase_quality_notes(meta: dict) -> str:
    return f"{PURCHASE_QUALITY_NOTES_PREFIX}{json.dumps(meta, ensure_ascii=False)}"


def parse_return_quality_meta(production_task: ProductionTask | None) -> dict:
    if production_task is None or production_task.source_type not in {"sales_return_reinspection", "purchase_return_reinspection"}:
        return {}
    raw_notes = str(production_task.notes or "")
    if not raw_notes.startswith(RETURN_QUALITY_NOTES_PREFIX):
        return {}
    try:
        return json.loads(raw_notes[len(RETURN_QUALITY_NOTES_PREFIX) :])
    except Exception:
        return {}


def build_return_quality_notes(meta: dict) -> str:
    return f"{RETURN_QUALITY_NOTES_PREFIX}{json.dumps(meta, ensure_ascii=False)}"


def resolve_quality_recipients_for_warehouse(
    db: Session,
    *,
    warehouse_id: int | None,
    fallback_user_ids: list[int],
) -> list[int]:
    """有目标仓库时只通知该仓库的质检员；无仓库的旧任务才用回退名单。"""
    if warehouse_id:
        warehouse = db.execute(select(Warehouse).where(Warehouse.id == warehouse_id)).scalar_one_or_none()
        if warehouse is None:
            raise HTTPException(status_code=404, detail="质检目标仓库不存在")
        users = db.execute(
            select(User).join(Role, User.role_id == Role.id)
            .where(Role.code.in_(["quality", "quality_super"]), User.is_active.is_(True))
        ).scalars().all()
        return list(dict.fromkeys(
            user.id for user in users
            if user.id == warehouse.qc_user_id
            or (scope := get_inventory_scope_warehouse_ids(db, user)) is not None and warehouse_id in scope
        ))
    return list(dict.fromkeys(fallback_user_ids))


def quality_shadow_product(db: Session) -> Product:
    product = db.execute(select(Product).where(Product.product_code == "SYS-QUALITY-MATERIAL")).scalar_one_or_none()
    if product is None:
        product = Product(
            product_code="SYS-QUALITY-MATERIAL",
            name="采购来料质检占位产品",
            specification="系统占位",
            unit="kg",
            price=0,
            safety_stock_qty=0,
            purchase_threshold_qty=0,
            is_active=False,
        )
        db.add(product)
        db.flush()
    return product


def purchase_quality_batch_no(order_no: str, index: int, total: int) -> str:
    return f"{order_no}-ZJ" if total <= 1 else f"{order_no}-ZJ-{index + 1:02d}"


def create_production_quality_task(
    db: Session,
    *,
    production_task: ProductionTask,
    submitted_by_id: int,
    required_tests: int,
    assigned_inspector_id: int | None = None,
) -> QualityTask:
    quality_task = QualityTask(
        quality_no=next_business_no(db, "ZJY"),
        production_task_id=production_task.id,
        batch_no=production_task.batch_no,
        source_category="production",
        required_tests=required_tests,
        status="pending",
        submitted_by_id=submitted_by_id,
        # 指定质检员(未指定则轮询分配),归属仓库用于普通质检员的可见范围过滤
        assigned_inspector_id=resolve_assigned_inspector_id(db, assigned_inspector_id),
        target_warehouse_id=production_task.warehouse_id,
    )
    if quality_task.assigned_inspector_id:
        quality_task.assigned_by_id = submitted_by_id
        quality_task.assigned_at = now_local()
    db.add(quality_task)
    db.flush()
    return quality_task


def create_purchase_quality_tasks(
    db: Session,
    *,
    order: PurchaseOrder,
    receipt: PurchaseReceipt,
    submitted_by_id: int,
    required_tests: int = 1,
    skip_receipt_item_ids: set[int] | None = None,
    assigned_inspector_id: int | None = None,
) -> list[QualityTask]:
    order_items = {
        row.id: row
        for row in db.execute(
            select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == order.id)
        ).scalars().all()
    }
    receipt_items = db.execute(
        select(PurchaseReceiptItem).where(PurchaseReceiptItem.purchase_receipt_id == receipt.id).order_by(PurchaseReceiptItem.id.asc())
    ).scalars().all()
    if not receipt_items:
        return []

    supplier = db.execute(select(Supplier).where(Supplier.id == order.supplier_id)).scalar_one_or_none()
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == receipt.warehouse_id)).scalar_one_or_none()
    placeholder_product = quality_shadow_product(db)
    total_items = len(receipt_items)
    created_tasks: list[QualityTask] = []
    # 同一批到货共用同一个指定质检员:在循环外校验一次,避免重复查库。
    batch_inspector_id = resolve_assigned_inspector_id(db, assigned_inspector_id)

    for index, receipt_item in enumerate(receipt_items):
        if skip_receipt_item_ids and receipt_item.id in skip_receipt_item_ids:
            continue
        order_item = order_items.get(receipt_item.purchase_order_item_id)
        if order_item is None:
            continue

        item_type = "product" if order_item.product_id else ("material" if order_item.material_id else "packaging")
        item_id = order_item.product_id or order_item.material_id or order_item.packaging_config_id
        item_name = order_item.product.name if order_item.product else (order_item.material.name if order_item.material else (order_item.packaging_config.packaging_name if order_item.packaging_config else f"采购明细{index + 1}"))
        item_code = order_item.product.product_code if order_item.product else (order_item.material.material_code if order_item.material else None)
        quantity = Decimal(receipt_item.received_quantity or 0)
        if quantity <= 0:
            continue

        meta = {
            "purchase_order_id": order.id,
            "purchase_order_no": order.order_no,
            "purchase_order_item_id": order_item.id,
            "purchase_receipt_id": receipt.id,
            "purchase_receipt_no": receipt.receipt_no,
            "purchase_receipt_item_id": receipt_item.id,
            "supplier_name": supplier.name if supplier else None,
            "warehouse_id": receipt.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else None,
            "item_type": item_type,
            "item_id": item_id,
            "item_code": item_code,
            "item_name": item_name,
            "display_specification": order_item.display_specification,
            "quantity": float(quantity),
            "unit_cost": float(receipt_item.unit_cost or 0),
            "batch_no": receipt_item.batch_no,
            "remark": receipt.remark,
        }

        shadow_task = ProductionTask(
            task_no=next_business_no(db, "SC"),
            source_type="purchase_quality",
            product_id=order_item.product_id or placeholder_product.id,
            planned_quantity=quantity,
            produced_quantity=quantity,
            scrap_quantity=0,
            fluctuation_ratio=0,
            requires_quality=True,
            status="waiting_quality",
            created_by_id=submitted_by_id,
            notes=build_purchase_quality_notes(meta),
        )
        db.add(shadow_task)
        db.flush()

        quality_task = QualityTask(
            quality_no=next_business_no(db, "ZJY"),
            production_task_id=shadow_task.id,
            batch_no=purchase_quality_batch_no(order.order_no, index, total_items),
            source_category="purchase",
            required_tests=required_tests,
            status="pending",
            submitted_by_id=submitted_by_id,
            # 采购到货质检:归属收货仓库,整批派给同一质检员(见上方 batch_inspector_id)
            assigned_inspector_id=batch_inspector_id,
            target_warehouse_id=receipt.warehouse_id,
        )
        if quality_task.assigned_inspector_id:
            quality_task.assigned_by_id = submitted_by_id
            quality_task.assigned_at = now_local()
        db.add(quality_task)
        db.flush()
        created_tasks.append(quality_task)

    return created_tasks


def create_return_reinspection_quality_task(
    db: Session,
    *,
    business_type: str,
    business_id: int,
    business_no: str,
    source_label: str,
    item_label: str,
    quantity: Decimal,
    submitted_by_id: int,
    required_tests: int = 1,
    assigned_inspector_id: int | None = None,
    target_warehouse_id: int | None = None,
) -> QualityTask:
    placeholder_product = quality_shadow_product(db)
    source_type = "sales_return_reinspection" if business_type == "sales_return" else "purchase_return_reinspection"
    meta = {
        "business_type": business_type,
        "business_id": business_id,
        "business_no": business_no,
        "source_label": source_label,
        "item_label": item_label,
        "quantity": float(quantity),
    }
    shadow_task = ProductionTask(
        task_no=next_business_no(db, "SC"),
        source_type=source_type,
        product_id=placeholder_product.id,
        planned_quantity=quantity,
        produced_quantity=quantity,
        scrap_quantity=0,
        fluctuation_ratio=0,
        requires_quality=True,
        status="waiting_quality",
        created_by_id=submitted_by_id,
        notes=build_return_quality_notes(meta),
    )
    db.add(shadow_task)
    db.flush()

    quality_task = QualityTask(
        quality_no=next_business_no(db, "ZJY"),
        production_task_id=shadow_task.id,
        batch_no=f"{business_no}-FJ",
        source_category="return",
        required_tests=required_tests,
        status="pending",
        submitted_by_id=submitted_by_id,
        # 退货复检:指定质检员(未指定则留空,由该仓库质检员自行领取)
        assigned_inspector_id=resolve_assigned_inspector_id(db, assigned_inspector_id),
        target_warehouse_id=target_warehouse_id,
    )
    if quality_task.assigned_inspector_id:
        quality_task.assigned_by_id = submitted_by_id
        quality_task.assigned_at = now_local()
    db.add(quality_task)
    db.flush()
    return quality_task


def quality_sample_consumption(db: Session, quality_task_id: int) -> Decimal:
    rows = db.execute(
        select(QualityInspection.sample_consumption_qty).where(QualityInspection.quality_task_id == quality_task_id)
    ).all()
    return sum((Decimal(value or 0) for (value,) in rows), Decimal("0"))


def resolve_purchase_quality_context(
    db: Session,
    quality_task: QualityTask,
) -> tuple[ProductionTask, PurchaseReceipt, PurchaseReceiptItem, PurchaseOrder, PurchaseOrderItem]:
    production_task = db.execute(select(ProductionTask).where(ProductionTask.id == quality_task.production_task_id)).scalar_one_or_none()
    if production_task is None:
        raise HTTPException(status_code=404, detail="质检来源任务不存在")
    meta = parse_purchase_quality_meta(production_task)
    if not meta:
        raise HTTPException(status_code=409, detail="采购质检元数据缺失")

    receipt = db.execute(select(PurchaseReceipt).where(PurchaseReceipt.id == int(meta["purchase_receipt_id"]))).scalar_one_or_none()
    receipt_item = db.execute(
        select(PurchaseReceiptItem).where(PurchaseReceiptItem.id == int(meta["purchase_receipt_item_id"]))
    ).scalar_one_or_none()
    order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == int(meta["purchase_order_id"]))).scalar_one_or_none()
    order_item = db.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.id == int(meta["purchase_order_item_id"]))
    ).scalar_one_or_none()
    if not all([receipt, receipt_item, order, order_item]):
        raise HTTPException(status_code=404, detail="采购质检关联单据不存在")
    return production_task, receipt, receipt_item, order, order_item
