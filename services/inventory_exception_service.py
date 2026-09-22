from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from models import (
    Inventory,
    InventoryExceptionItem,
    InventoryExceptionRecord,
    PackagingConfig,
    Product,
    SalesOrder,
    SalesOrderItem,
    User,
    Warehouse,
)
from responses import api_response
from services.core import apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, write_operation_log
from services.time_utils import now_local


EXCEPTION_TYPE_LABELS = {
    "product_damage": "成品报损",
    "packaging_damage": "包装报损",
    "extra_packaging": "追加包装",
    "packaging_adjustment": "包装调整",
}


def _approval_user_ids(db: Session) -> list[int]:
    # 经理和董事长任一人审批即可；系统管理员保留处理与监管入口，但不重复发日常待办。
    from models import Role
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(
                User.is_active.is_(True), Role.code.in_(["manager", "chairman"])
            )
        ).scalars().all()
    ]


def _load_record(db: Session, record_id: int) -> InventoryExceptionRecord:
    row = db.execute(
        select(InventoryExceptionRecord)
        .options(
            joinedload(InventoryExceptionRecord.warehouse),
            joinedload(InventoryExceptionRecord.sales_order),
            joinedload(InventoryExceptionRecord.created_by),
            joinedload(InventoryExceptionRecord.approved_by),
            joinedload(InventoryExceptionRecord.rejected_by),
            joinedload(InventoryExceptionRecord.items).joinedload(InventoryExceptionItem.product),
            joinedload(InventoryExceptionRecord.items).joinedload(InventoryExceptionItem.packaging_config),
            joinedload(InventoryExceptionRecord.items).joinedload(InventoryExceptionItem.sales_order_item),
        )
        .where(InventoryExceptionRecord.id == record_id)
    ).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="库存异常记录不存在")
    return row


def serialize_inventory_exception(row: InventoryExceptionRecord) -> dict:
    return {
        "id": row.id,
        "exception_no": row.exception_no,
        "exception_type": row.exception_type,
        "exception_type_label": EXCEPTION_TYPE_LABELS.get(row.exception_type, row.exception_type),
        "warehouse_id": row.warehouse_id,
        "warehouse_name": row.warehouse.name if row.warehouse else None,
        "sales_order_id": row.sales_order_id,
        "sales_order_no": row.sales_order.order_no if row.sales_order else None,
        "status": row.status,
        "remark": row.remark,
        "created_by_name": row.created_by.full_name if row.created_by else None,
        "approved_by_name": row.approved_by.full_name if row.approved_by else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "approval_note": row.approval_note,
        "rejected_by_name": row.rejected_by.full_name if row.rejected_by else None,
        "rejected_at": row.rejected_at.isoformat() if row.rejected_at else None,
        "rejection_reason": row.rejection_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "items": [
            {
                "id": item.id,
                "item_type": item.item_type,
                "item_name": item.product.name if item.product else (item.packaging_config.packaging_name if item.packaging_config else "-"),
                "product_id": item.product_id,
                "packaging_config_id": item.packaging_config_id,
                "sales_order_item_id": item.sales_order_item_id,
                "quantity": float(item.quantity or 0),
            }
            for item in row.items
        ],
    }


def create_inventory_exception(payload, current_user: User, db: Session):
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if warehouse is None:
        raise HTTPException(status_code=404, detail="仓库不存在")
    order = db.get(SalesOrder, payload.sales_order_id) if payload.sales_order_id else None
    if payload.sales_order_id and order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if payload.exception_type.startswith("product") and any(item.item_type != "product" for item in payload.items):
        raise HTTPException(status_code=422, detail="成品报损只能登记成品")
    if payload.exception_type != "product_damage" and any(item.item_type != "packaging" for item in payload.items):
        raise HTTPException(status_code=422, detail="包装异常只能登记包装")

    row = InventoryExceptionRecord(
        exception_no=next_business_no(db, "KCYC"),
        exception_type=payload.exception_type,
        warehouse_id=payload.warehouse_id,
        sales_order_id=payload.sales_order_id,
        remark=payload.remark.strip(),
        created_by_id=current_user.id,
        status="pending_approval",
    )
    db.add(row)
    db.flush()
    for item in payload.items:
        if item.item_type == "product":
            if not item.product_id or db.get(Product, item.product_id) is None:
                raise HTTPException(status_code=422, detail="请选择有效的成品")
            if item.packaging_config_id:
                raise HTTPException(status_code=422, detail="成品异常不能同时填写包装")
        else:
            if not item.packaging_config_id or db.get(PackagingConfig, item.packaging_config_id) is None:
                raise HTTPException(status_code=422, detail="请选择有效的包装")
            if item.product_id:
                raise HTTPException(status_code=422, detail="包装异常不能同时填写成品")
        if item.sales_order_item_id:
            order_item = db.get(SalesOrderItem, item.sales_order_item_id)
            if order_item is None or (order and order_item.sales_order_id != order.id):
                raise HTTPException(status_code=422, detail="关联订单明细不正确")
        db.add(InventoryExceptionItem(
            exception_id=row.id,
            item_type=item.item_type,
            product_id=item.product_id if item.item_type == "product" else None,
            packaging_config_id=item.packaging_config_id if item.item_type == "packaging" else None,
            sales_order_item_id=item.sales_order_item_id,
            quantity=item.quantity,
        ))

    approver_ids = _approval_user_ids(db)
    for user_id in approver_ids:
        create_todo(
            db,
            title=f"待审批库存异常 {row.exception_no}",
            description=f"{EXCEPTION_TYPE_LABELS[row.exception_type]}，请审批后生成出库记录。",
            module="inventory",
            assigned_user_id=user_id,
            business_type="inventory_exception",
            business_id=row.id,
            priority="high",
        )
    create_notification(
        db,
        title="库存异常待审批",
        content=f"库存异常 {row.exception_no}（{EXCEPTION_TYPE_LABELS[row.exception_type]}）已提交，请经理或董事长审批。",
        module="inventory",
        recipient_user_ids=approver_ids,
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(db, user_id=current_user.id, module="inventory", action="create_inventory_exception", content=f"提交库存异常 {row.exception_no}")
    db.commit()
    return api_response(data={"id": row.id, "exception_no": row.exception_no, "status": row.status})


def approve_inventory_exception(record_id: int, note: str | None, current_user: User, db: Session):
    row = _load_record(db, record_id)
    if row.status != "pending_approval":
        raise HTTPException(status_code=409, detail="当前库存异常已处理，不能重复审批")
    for item in row.items:
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == row.warehouse_id,
                Inventory.product_id == item.product_id if item.item_type == "product" else Inventory.product_id.is_(None),
                Inventory.material_id.is_(None),
                Inventory.packaging_config_id == item.packaging_config_id if item.item_type == "packaging" else Inventory.packaging_config_id.is_(None),
            ).with_for_update()
        ).scalar_one_or_none()
        if inventory is None or Decimal(inventory.quantity or 0) < Decimal(item.quantity or 0):
            raise HTTPException(status_code=409, detail=f"{item.product.name if item.product else item.packaging_config.packaging_name} 实物库存不足，无法登记出库")
        if item.sales_order_item:
            if item.item_type == "product":
                release = min(Decimal(item.quantity or 0), Decimal(item.sales_order_item.reserved_quantity or 0))
                item.sales_order_item.reserved_quantity = Decimal(item.sales_order_item.reserved_quantity or 0) - release
            else:
                release = min(Decimal(item.quantity or 0), Decimal(item.sales_order_item.packaging_reserved_quantity or 0))
                item.sales_order_item.packaging_reserved_quantity = Decimal(item.sales_order_item.packaging_reserved_quantity or 0) - release
            inventory.reserved_quantity = max(Decimal(inventory.reserved_quantity or 0) - release, Decimal("0"))
        apply_inventory_change(
            db,
            warehouse_id=row.warehouse_id,
            product_id=item.product_id,
            packaging_config_id=item.packaging_config_id,
            quantity=-Decimal(item.quantity or 0),
            unit_cost=Decimal(inventory.avg_unit_cost or 0),
            transaction_type="inventory_exception_outbound",
            related_type="inventory_exception",
            related_id=row.id,
            created_by_id=current_user.id,
        )
    row.status = "approved"
    row.approved_by_id = current_user.id
    row.approved_at = now_local()
    row.approval_note = note
    complete_todos(db, business_type="inventory_exception", business_id=row.id)
    create_notification(
        db,
        title="库存异常已审批出库",
        content=f"库存异常 {row.exception_no} 已审批，相关库存已出库扣减，可继续置货或发货。",
        module="inventory",
        recipient_user_ids=[row.created_by_id],
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(db, user_id=current_user.id, module="inventory", action="approve_inventory_exception", content=f"审批库存异常并出库 {row.exception_no}")
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})


def reject_inventory_exception(record_id: int, note: str | None, current_user: User, db: Session):
    row = _load_record(db, record_id)
    if row.status != "pending_approval":
        raise HTTPException(status_code=409, detail="当前库存异常已处理，不能重复驳回")
    row.status = "rejected"
    row.rejected_by_id = current_user.id
    row.rejected_at = now_local()
    row.rejection_reason = (note or "库存异常未获批准").strip()
    complete_todos(db, business_type="inventory_exception", business_id=row.id)
    create_notification(db, title="库存异常已驳回", content=f"库存异常 {row.exception_no} 已驳回：{row.rejection_reason}", module="inventory", recipient_user_ids=[row.created_by_id], created_by_id=current_user.id, category="warning")
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})
