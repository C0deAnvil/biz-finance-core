"""销售退换货的质检、回库衔接逻辑。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Inventory, Role, SalesOrderItem, SalesReturn, SalesReturnItem, User, Warehouse
from services.organization import get_inventory_scope_warehouse_ids
from services.warehouse_lookup import get_finished_warehouse
from services.workflow import create_inventory_operation_request


def inventory_user_ids_for_warehouse(db: Session, warehouse_id: int) -> list[int]:
    """只返回目标仓库在岗库管，避免将回库作业广播给其他仓库。"""
    warehouse = db.get(Warehouse, warehouse_id)
    users = db.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(Role.code == "inventory", User.is_active.is_(True))
    ).scalars().all()
    return list(
        dict.fromkeys(
            user.id
            for user in users
            if (warehouse and user.id == warehouse.manager_user_id)
            or warehouse_id in (get_inventory_scope_warehouse_ids(db, user) or [])
        )
    )


def create_sales_return_inbound_request(
    db: Session,
    sales_return: SalesReturn,
    *,
    requested_by_id: int,
):
    """为已审批（且需要复检时已放行）的销售退换货生成回库作业。"""
    return_items = db.execute(
        select(SalesReturnItem).where(SalesReturnItem.sales_return_id == sales_return.id)
    ).scalars().all()
    if not return_items:
        raise HTTPException(status_code=409, detail="退换货单缺少明细，无法生成入库作业")

    order_items = {
        row.id: row
        for row in db.execute(
            select(SalesOrderItem).where(
                SalesOrderItem.id.in_([item.sales_order_item_id for item in return_items])
            )
        ).scalars().all()
    }
    warehouse = get_finished_warehouse(db)
    recipients = inventory_user_ids_for_warehouse(db, warehouse.id)
    if not recipients:
        raise HTTPException(
            status_code=409,
            detail=f"{warehouse.name}未配置在岗库管，无法生成退货入库作业",
        )

    request_items: list[dict] = []
    for item in return_items:
        order_item = order_items.get(item.sales_order_item_id)
        if order_item is None or order_item.product_id is None:
            raise HTTPException(status_code=409, detail="退换货明细缺少对应产品，无法生成入库作业")
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == warehouse.id,
                Inventory.product_id == order_item.product_id,
                Inventory.material_id.is_(None),
            )
        ).scalar_one_or_none()
        request_items.append(
            {
                "product_id": order_item.product_id,
                "sales_order_item_id": order_item.id,
                "material_id": None,
                "batch_no": f"{sales_return.return_no}-{item.batch_suffix or item.id}",
                "quantity": float(Decimal(item.quantity or 0)),
                "unit_cost": float(Decimal(inventory.avg_unit_cost or 0)) if inventory else 0.0,
            }
        )

    request = create_inventory_operation_request(
        db,
        operation_type="inbound",
        request_category="sales_return_inbound",
        source_module="sales",
        source_type="sales_return",
        source_id=sales_return.id,
        source_no=sales_return.return_no,
        warehouse_id=warehouse.id,
        requested_by_id=requested_by_id,
        items=request_items,
        remark=sales_return.reason,
        recipient_user_ids=recipients,
        title=f"待执行销售退货入库 {sales_return.return_no}",
        description=f"销售退换货单 {sales_return.return_no} 已满足回库条件，请库管执行退货入库。",
    )
    return request, recipients
