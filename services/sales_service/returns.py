"""销售域服务。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models import (
    BOM,
    Customer,
    Inventory,
    InventoryDispatchQueue,
    FulfillmentDemand,
    PackagingConfig,
    Product,
    ReceivableRecord,
    RefundRecord,
    Role,
    SalesOrder,
    SalesOrderApproval,
    SalesOrderItem,
    SalesOrderPriceChangeLog,
    SalesReceiptRequest,
    SalesReturn,
    SalesReturnItem,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.sales import SalesApprovalRequest, SalesMergedReceiptRequestCreate, SalesOrderCreate, SalesOrderUpdate, SalesReceiptApprovalRequest, SalesReceiptRequestCreate, SalesReturnApprovalRequest, SalesReturnCreate, SalesSettlementCollectRequest
from services.core import apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, ensure_notification, next_business_no, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task, resolve_quality_recipients_for_warehouse
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity
from services.request_trace import emit
from services.sales_return_workflow import create_sales_return_inbound_request
from services.warehouse_lookup import get_finished_warehouse
from services.time_utils import now_local



from services.sales_service._common import _ensure_order_owner_or_admin, _finished_warehouse, _load_order, _role_user_ids

def create_return(payload: SalesReturnCreate, current_user: User, db: Session):
    reason = str(payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="请填写退换货申请原因")
    order = _load_order(db, payload.sales_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status not in {"shipped", "completed"}:
        raise HTTPException(status_code=409, detail="仅已发货或已完成订单允许提交退换货")
    _ensure_order_owner_or_admin(order, current_user)
    if not payload.items:
        raise HTTPException(status_code=400, detail="退换货至少需要一条明细")

    order_item_map = {item.id: item for item in order.items}
    existing_return_items = db.execute(
        select(SalesReturnItem, SalesReturn.status)
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.sales_return_id)
        .where(
            SalesReturn.sales_order_id == order.id,
            SalesReturn.status.in_(["pending", "approved", "refunded"]),
        )
    ).all()
    returned_qty_map: dict[int, Decimal] = {}
    for item_row, _status in existing_return_items:
        returned_qty_map[item_row.sales_order_item_id] = returned_qty_map.get(item_row.sales_order_item_id, Decimal("0")) + Decimal(item_row.quantity or 0)

    sales_return = SalesReturn(
        return_no=next_business_no(db, "XT"),
        sales_order_id=order.id,
        requested_by_id=current_user.id,
        reason=reason,
        status="pending",
        return_type=payload.return_type,
        refund_amount=payload.refund_amount,
        requires_reinspection=payload.requires_reinspection,
        reinspection_status="pending" if payload.requires_reinspection else "not_required",
    )
    db.add(sales_return)
    db.flush()

    detail_count = 0
    for index, item_payload in enumerate(payload.items, start=1):
        order_item = order_item_map.get(item_payload.sales_order_item_id)
        if order_item is None:
            raise HTTPException(status_code=404, detail="退换货明细对应的订单商品不存在")

        sold_quantity = Decimal(order_item.quantity or 0)
        already_returned = Decimal(returned_qty_map.get(order_item.id, 0))
        if Decimal(item_payload.quantity) + already_returned > sold_quantity:
            raise HTTPException(status_code=409, detail="退换货数量超过可退数量")

        db.add(
            SalesReturnItem(
                sales_return_id=sales_return.id,
                sales_order_item_id=order_item.id,
                quantity=item_payload.quantity,
                batch_suffix=f"RT{sales_return.id:04d}-{index:02d}",
            )
        )
        detail_count += 1

    # 退货提交 → 增加退货在途(预计可用 = 实有 + 在途 - 占用)
    return_warehouse = _finished_warehouse(db)
    for item_payload in payload.items:
        order_item = order_item_map.get(item_payload.sales_order_item_id)
        if order_item is None or order_item.product_id is None:
            continue
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == return_warehouse.id,
                Inventory.product_id == order_item.product_id,
                Inventory.material_id.is_(None),
            )
        ).scalar_one_or_none()
        if inventory is None:
            inventory = Inventory(
                warehouse_id=return_warehouse.id,
                product_id=order_item.product_id,
                material_id=None,
                quantity=0,
                reserved_quantity=0,
                in_transit=0,
                avg_unit_cost=0,
            )
            db.add(inventory)
        inventory.in_transit = Decimal(inventory.in_transit or 0) + Decimal(item_payload.quantity)

    approver_user_ids = _role_user_ids(db, "finance", "finance_super", "manager", "chairman")
    if not approver_user_ids:
        raise HTTPException(status_code=409, detail="未配置在岗财务、总经理或董事长，无法提交退换货审批")
    return_type_text = "退货" if payload.return_type == "return" else "换货"
    for user_id in approver_user_ids:
        create_todo(
            db,
            title=f"待审批{return_type_text}单 {sales_return.return_no}",
            description=reason,
            module="sales",
            assigned_user_id=user_id,
            business_type="sales_return_approval",
            business_id=sales_return.id,
            priority="high",
        )

    create_notification(
        db,
        title="销售退换货已提交",
        content=f"{return_type_text}单 {sales_return.return_no} 已提交，等待审批。",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *approver_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="create_return",
        content=f"创建销售退换货 {sales_return.return_no}",
        after_data={
            "sales_order_id": order.id,
            "return_type": sales_return.return_type,
            "refund_amount": float(sales_return.refund_amount),
            "detail_count": detail_count,
        },
    )
    db.commit()
    return api_response(data={"id": sales_return.id, "return_no": sales_return.return_no})


def approve_return(return_id: int, payload: SalesReturnApprovalRequest, current_user: User, db: Session):
    # 多名审批人共享同一待办，锁定单据确保“任一人审批一次即结束”，避免并发重复生成后续作业。
    sales_return = db.execute(
        select(SalesReturn).where(SalesReturn.id == return_id).with_for_update()
    ).scalar_one_or_none()
    if sales_return is None:
        raise HTTPException(status_code=404, detail="退换货单不存在")
    if sales_return.status != "pending":
        raise HTTPException(status_code=409, detail="当前退换货单状态不允许审批")

    return_items = db.execute(select(SalesReturnItem).where(SalesReturnItem.sales_return_id == sales_return.id)).scalars().all()
    if not return_items:
        raise HTTPException(status_code=409, detail="退换货单缺少明细")

    order_items = {
        row.id: row
        for row in db.execute(
            select(SalesOrderItem).options(joinedload(SalesOrderItem.product)).where(
                SalesOrderItem.id.in_([item.sales_order_item_id for item in return_items])
            )
        ).scalars().all()
    }

    sales_return.approved_by_id = current_user.id
    sales_return.approved_at = now_local()
    sales_return.review_note = payload.note

    if payload.decision == "approved":
        warehouse = _finished_warehouse(db)
        total_reinspection_quantity = Decimal("0")
        reinspection_names: list[str] = []
        for item in return_items:
            order_item = order_items.get(item.sales_order_item_id)
            if order_item is None:
                raise HTTPException(status_code=404, detail="退换货对应订单明细不存在")

            total_reinspection_quantity += Decimal(item.quantity or 0)
            if order_item.product and order_item.product.name:
                reinspection_names.append(order_item.product.name)

        sales_return.status = "approved"
        inventory_user_ids: list[int] = []
        if sales_return.requires_reinspection:
            quality_task = create_return_reinspection_quality_task(
                db,
                business_type="sales_return",
                business_id=sales_return.id,
                business_no=sales_return.return_no,
                source_label="销售退换货复检",
                item_label="、".join(dict.fromkeys(reinspection_names)) or "退换货产品",
                quantity=total_reinspection_quantity,
                submitted_by_id=current_user.id,
                # 退货复检归属成品仓,普通质检员按此过滤可见范围
                target_warehouse_id=warehouse.id,
            )
            quality_user_ids = resolve_quality_recipients_for_warehouse(
                db,
                warehouse_id=warehouse.id,
                fallback_user_ids=_role_user_ids(db, "quality", "quality_super"),
            )
            if not quality_user_ids:
                raise HTTPException(
                    status_code=409,
                    detail=f"{warehouse.name}未配置在岗质检员，无法提交需要质检的退换货",
                )
            for user_id in quality_user_ids:
                create_todo(
                    db,
                    title=f"待复检销售退换货 {sales_return.return_no}",
                    description=f"销售退换货单 {sales_return.return_no} 标记需要复检，请处理质检任务 {quality_task.quality_no}。",
                    module="quality",
                    assigned_user_id=user_id,
                    business_type="quality_task",
                    business_id=quality_task.id,
                    priority="high",
                )
            if quality_user_ids:
                create_notification(
                    db,
                    title="销售退换货已发起复检",
                    content=f"销售退换货单 {sales_return.return_no} 已生成复检任务 {quality_task.quality_no}。",
                    module="quality",
                    recipient_user_ids=quality_user_ids,
                    created_by_id=current_user.id,
                    category="warning",
                )
        else:
            _, inventory_user_ids = create_sales_return_inbound_request(
                db,
                sales_return,
                requested_by_id=current_user.id,
            )
        finance_user_ids = _role_user_ids(db, "finance", "finance_super", "manager", "chairman")
        if Decimal(sales_return.refund_amount or 0) > 0:
            for user_id in finance_user_ids:
                create_todo(
                    db,
                    title=f"待处理销售退款 {sales_return.return_no}",
                    description=sales_return.reason,
                    module="finance",
                    assigned_user_id=user_id,
                    business_type="sales_return",
                    business_id=sales_return.id,
                )
        create_notification(
            db,
            title="销售退换货审批通过",
            content=(
                f"{'退货' if sales_return.return_type == 'return' else '换货'}单 {sales_return.return_no} 已审批通过，"
                + ("已进入质检，质检放行后生成回库作业。" if sales_return.requires_reinspection else "已生成目标仓库回库作业。")
            ),
            module="sales",
            recipient_user_ids=list(dict.fromkeys([sales_return.requested_by_id, *finance_user_ids, *inventory_user_ids])),
            created_by_id=current_user.id,
            category="success",
        )
    else:
        sales_return.status = "rejected"
        # 审批驳回 → 退回已在途的退货数量
        _release_return_in_transit(db, sales_return, warehouse_id=_finished_warehouse(db).id)
        create_notification(
            db,
            title="销售退换货已驳回",
            content=f"{'退货' if sales_return.return_type == 'return' else '换货'}单 {sales_return.return_no} 审批未通过，请查看驳回说明。",
            module="sales",
            recipient_user_ids=[sales_return.requested_by_id],
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_return_approval", business_id=sales_return.id)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_return",
        content=f"审批销售退换货 {sales_return.return_no}",
        after_data={
            "decision": payload.decision,
            "status": sales_return.status,
            "note": payload.note,
        },
    )
    db.commit()
    return api_response(data={"id": sales_return.id, "status": sales_return.status})


def _release_return_in_transit(db: Session, sales_return: SalesReturn, *, warehouse_id: int) -> None:
    """释放退换货单占用在途的退货数量(审批驳回时调用)。"""
    items = db.execute(
        select(SalesReturnItem).where(SalesReturnItem.sales_return_id == sales_return.id)
    ).scalars().all()
    for item in items:
        if item.sales_order_item_id is None:
            continue
        order_item = db.execute(
            select(SalesOrderItem).where(SalesOrderItem.id == item.sales_order_item_id)
        ).scalar_one_or_none()
        if order_item is None or order_item.product_id is None:
            continue
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == warehouse_id,
                Inventory.product_id == order_item.product_id,
                Inventory.material_id.is_(None),
            )
        ).scalar_one_or_none()
        if inventory is None:
            continue
        inventory.in_transit = max(Decimal("0"), Decimal(inventory.in_transit or 0) - Decimal(item.quantity or 0))

