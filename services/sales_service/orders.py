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
from services.core import apply_inventory_change, complete_todos, create_notification, create_todo, ensure_notification, next_business_no, resolve_assigned_finance_user_id, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity, product_expected_available_quantity
from services.request_trace import emit
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_finished_warehouse
from services.time_utils import now_local



from services.sales_service._common import _build_order_items, _build_split_items, _create_dispatch_queue, _create_fulfillment_demands, _create_inventory_placement_todos, _create_order_todos_and_notice, _ensure_order_owner_or_admin, _finance_user_ids, _finished_warehouse, _has_pending_finance_edit_confirmation, _load_order, _manager_chairman_user_ids, _resolve_sales_operator_user_id, _role_user_ids, _route_order_to_inventory_shipment, occupy_inventory_for_order, release_order_reservation


SALES_EDITABLE_STATUSES = {"draft", "rejected"}


def _cleanup_order_item_references(db: Session, order: SalesOrder) -> None:
    """删除/重建订单明细前,清理关联的补货需求引用(外键约束)。

    历史版本会在创建/审批时自动生成 FulfillmentDemand 并挂到 sales_order_item_id,
    直接删除明细会触发外键 1451 错误。pending 需求直接删除,已流转的解除明细引用。
    """
    item_ids = [item.id for item in order.items] or [-1]
    demands = db.execute(
        select(FulfillmentDemand).where(FulfillmentDemand.sales_order_item_id.in_(item_ids))
    ).scalars().all()
    for demand in demands:
        if demand.status == "pending":
            db.delete(demand)
        else:
            demand.sales_order_item_id = None
            demand.sales_order_id = None


def _notify_sales_order_shortage(db: Session, *, order: SalesOrder, current_user: User) -> None:
    """按预计可用库存(实有-占用-在产+在途+在订)计算订单缺口,仅提醒不生成补货需求。"""
    warehouse = _finished_warehouse(db)
    product_ids = [item.product_id for item in order.items] or [-1]
    inventory_map = {
        row.product_id: row
        for row in db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == warehouse.id,
                Inventory.product_id.in_(product_ids),
                Inventory.material_id.is_(None),
            )
        ).scalars().all()
    }
    shortage_lines = []
    for item in order.items:
        expected_available = product_expected_available_quantity(
            db,
            product_id=item.product_id,
            inventory=inventory_map.get(item.product_id),
        )
        shortage = max(Decimal(item.quantity or 0) - expected_available, Decimal("0"))
        if shortage > 0:
            product_name = item.product.name if item.product else f"产品#{item.product_id}"
            shortage_lines.append(f"{product_name} 缺口 {float(shortage)}")
    if not shortage_lines:
        return
    inventory_user_ids = _role_user_ids(db, "inventory")
    recipient_ids = list(dict.fromkeys([order.sales_user_id, *inventory_user_ids]))
    create_notification(
        db,
        title="销售订单存在库存缺口",
        content=(
            f"客户订单 {order.order_no} 按预计可用库存(实有-占用+在途+在订)计算存在缺口："
            f"{'、'.join(shortage_lines)}。系统不自动生成补货需求，"
            f"请财务审批通过后由库管在置货环节生成采购/生产补货需求。"
        ),
        module="sales",
        recipient_user_ids=recipient_ids,
        created_by_id=current_user.id,
        category="warning",
    )

def create_order(payload: SalesOrderCreate, current_user: User, db: Session):
    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")

    # 不再拆单：整单保留全部明细，库存缺口统一在生产/采购补货需求环节处理
    effective_items = [
        {
            "product_id": item.product_id,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "packaging_config_id": item.packaging_config_id,
            "remark": item.remark,
            "price_change_remark": getattr(item, "price_change_remark", None),
        }
        for item in payload.items
    ]

    operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id, current_user)
    order = SalesOrder(
        order_no=next_business_no(db, "XS"),
        split_phase=1,
        customer_id=payload.customer_id,
        sales_user_id=current_user.id,
        operator_user_id=operator_user_id,
        consignee=payload.consignee,
        consignee_phone=payload.consignee_phone,
        delivery_address=payload.delivery_address,
        payment_type=payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales"),
        order_type=payload.order_type or "normal",
        expected_delivery_date=payload.expected_delivery_date,
        status="draft",
        deposit_amount=payload.deposit_amount,
        transport_fee=payload.transport_fee,
        packaging_fee=0,
        total_amount=0,
        cost_amount=0,
        profit_amount=0,
        order_remark=payload.order_remark,
    )
    db.add(order)
    db.flush()
    order.split_group_no = order.order_no

    order.total_amount = _build_order_items(db, order_id=order.id, payload_items=effective_items) + Decimal(order.transport_fee or 0)

    # 草稿阶段:不创建置货队列/审批待办,不做库存占用,不生成补货需求
    # 缺口仅提醒销售与库管:财务审批通过后由库管在置货环节按需生成补货需求
    _notify_sales_order_shortage(db, order=order, current_user=current_user)

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="create_order",
        content=f"创建客户订单 {order.order_no}(草稿)",
        after_data={
            "customer_id": order.customer_id,
            "status": order.status,
            "total_amount": float(order.total_amount),
            "item_count": len(effective_items),
            "split_child_order_no": None,
        },
    )
    db.commit()
    return api_response(
        data={
            "id": order.id,
            "order_no": order.order_no,
            "split_created": False,
            "split_child_order_no": None,
            "replenishment_created": False,
            "replenishment_demands": [],
            "shipment_deferred_by_shortage": False,
        }
    )


def submit_order(order_id: int, current_user: User, db: Session, assigned_finance_user_id: int | None = None):
    """草稿/已驳回订单提交财务审批。提交后不可编辑、不可删除。

    可指定处理该单的财务人员;不指定时由系统按待办量轮询分配,
    避免所有单据都堆到同一个财务头上。
    """
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="仅草稿或已驳回订单可提交审批")

    order.status = "pending_confirmation"
    order.rejection_reason = None
    order.assigned_finance_user_id = resolve_assigned_finance_user_id(db, assigned_finance_user_id)
    _create_dispatch_queue(db, order.id)

    # 待办派给指定财务;董事长始终可见以保留监管与代审能力
    assignee_ids = [order.assigned_finance_user_id] if order.assigned_finance_user_id else _role_user_ids(db, "finance")
    approver_user_ids = list(dict.fromkeys([*assignee_ids, *_role_user_ids(db, "chairman")]))
    finance_name = None
    if order.assigned_finance_user_id:
        finance_user = db.execute(select(User).where(User.id == order.assigned_finance_user_id)).scalar_one_or_none()
        finance_name = finance_user.full_name if finance_user else None
    assign_hint = f"(指定财务:{finance_name})" if finance_name else ""
    for user_id in approver_user_ids:
        create_todo(
            db,
            title=f"待审批客户订单 {order.order_no}",
            description=f"客户订单 {order.order_no} 已提交,请财务确认收款信息并审批。{assign_hint}",
            module="sales",
            assigned_user_id=user_id,
            business_type="sales_order_approval",
            business_id=order.id,
            priority="high",
        )
    create_notification(
        db,
        title="客户订单已提交审批",
        content=f"客户订单 {order.order_no} 已提交,请财务确认收款信息并审批。{assign_hint}",
        module="sales",
        recipient_user_ids=approver_user_ids,
        created_by_id=current_user.id,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="submit_order",
        content=f"客户订单 {order.order_no} 提交财务审批",
        after_data={"order_id": order.id, "status": order.status},
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no, "status": order.status})


def update_order(order_id: int, payload: SalesOrderUpdate, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="仅草稿或已驳回订单允许修改(已提交订单请通过财务改单流程)")

    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")

    before_data = {
        "customer_id": order.customer_id,
        "status": order.status,
        "total_amount": float(order.total_amount),
        "item_count": len(order.items),
    }

    order.customer_id = payload.customer_id
    order.operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id or order.operator_user_id, current_user)
    order.consignee = payload.consignee
    order.consignee_phone = payload.consignee_phone
    order.delivery_address = payload.delivery_address
    order.payment_type = payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales")
    order.order_type = payload.order_type or "normal"
    order.expected_delivery_date = payload.expected_delivery_date
    order.deposit_amount = payload.deposit_amount
    order.transport_fee = payload.transport_fee
    order.packaging_fee = 0
    order.order_remark = payload.order_remark
    order.rejection_reason = None

    # 删除旧明细前必须先解除 / 清理关联的补货需求引用,否则外键约束报错
    _cleanup_order_item_references(db, order)
    for item in list(order.items):
        db.delete(item)
    db.flush()
    for log in db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all():
        db.delete(log)
    order.price_approval_status = "not_required"
    order.price_approved_by_id = None
    order.price_approved_at = None
    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    order.total_amount = _build_order_items(db, order_id=order.id, payload_items=payload.items) + Decimal(order.transport_fee or 0)
    order.status = "draft"

    _notify_sales_order_shortage(db, order=order, current_user=current_user)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="update_order",
        content=f"更新客户订单 {order.order_no}",
        before_data=before_data,
        after_data={
            "customer_id": order.customer_id,
            "status": order.status,
            "total_amount": float(order.total_amount),
            "item_count": len(payload.items),
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no})


def approve_order(order_id: int, payload: SalesApprovalRequest, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status != "pending_confirmation":
        raise HTTPException(status_code=409, detail="当前订单状态不允许审批")
    if order.price_approval_status == "pending":
        raise HTTPException(status_code=409, detail="当前订单存在待审批改价，需财务审批通过后再推进")

    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    # 财务审批与收款确认是两个独立操作:审批只判断"这单能不能做",不要求钱已到账。
    # 改造前这里要求定金/全款必须先到账才允许审批,把两件事强行绑在一起,
    # 导致"未收款就无法审批、不审批就不能置货"的死锁。
    # 审批通过后订单财务状态自然为"待收定金/待收货款"(由 received_amount 实时推导),
    # 实际收款走独立的收款确认流程(submit_sales_receipt_request → 财务登记)。
    # 若财务在审批时顺带登记了已到账金额,则如实记账(不丢数据),但不作为审批前置条件。
    actual_amount = Decimal(payload.actual_amount or payload.voucher_amount or 0)

    db.add(
        SalesOrderApproval(
            sales_order_id=order.id,
            approver_user_id=current_user.id,
            decision=payload.decision,
            voucher_amount=actual_amount,
            note=payload.note,
        )
    )

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is None:
        queue = InventoryDispatchQueue(sales_order_id=order.id)
        db.add(queue)

    if payload.decision == "approved":
        order.status = "pending_shipment"
        order.approved_by_id = current_user.id
        order.approved_at = now_local()
        order.rejection_reason = None
        # 财务审批通过 → 占用成品库存(仅占用实有部分,缺口留待置货环节由库管提补货需求)
        occupy_inventory_for_order(db, order=order)
        if queue.placement_status == "hidden":
            queue.shipment_status = "pending"
        else:
            queue.placement_status = "pending"
            queue.shipment_status = "waiting_approval"
            complete_todos(db, business_type="sales_order", business_id=order.id)
            _create_inventory_placement_todos(db, order=order)

        # 审批**不再**写应收与资金流水。
        # 原先只要入参带了 actual_amount>0 就记一笔客户回款,而前端(销售审批页与
        # 审批中心)都会自动预填定金/全款金额,结果财务点一次"通过"就凭空虚增一笔
        # 回款——定金单尤其严重,审批时定金根本还没收到。
        # 审批只表示"这单可以执行";钱到账走独立的收款确认流程
        # (submit_sales_receipt_request → 财务登记),由那里唯一负责记账。
        # 入参里的金额仅作为审批凭证留痕(见上方 SalesOrderApproval.voucher_amount),
        # 不产生任何账务影响,这样即便有第三方调用方仍传金额也不会造成错账。

        inventory_user_ids = _role_user_ids(db, "inventory")
        if queue.placement_status == "hidden":
            existing_todo_user_ids = {
                todo.assigned_user_id
                for todo in db.execute(
                    select(TodoItem).where(
                        TodoItem.business_type == "sales_order",
                        TodoItem.business_id == order.id,
                        TodoItem.status != "completed",
                    )
                ).scalars().all()
            }
            for user_id in inventory_user_ids:
                if user_id in existing_todo_user_ids:
                    continue
                create_todo(
                    db,
                    title=f"待发货客户订单 {order.order_no}",
                    description="订单已完成置货，请库管安排出库发货。",
                    module="inventory",
                    assigned_user_id=user_id,
                    business_type="sales_order",
                    business_id=order.id,
                    priority="high",
                )
        create_notification(
            db,
            title="客户订单审批通过",
            content=f"客户订单 {order.order_no} 已确认到账并进入待发货阶段。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *inventory_user_ids, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )
    else:
        order.status = "rejected"
        order.rejection_reason = payload.note
        queue.shipment_status = "rejected"
        # 驳回后订单可能被改动或作废,必须把已占用的库存退还,否则库存被一张
        # 未生效的订单永久锁死(改造前没有任何释放点,占用会持续泄漏)。
        release_order_reservation(db, order=order)
        create_notification(
            db,
            title="客户订单已驳回",
            content=f"客户订单 {order.order_no} 审批未通过，请根据驳回原因调整。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_order_approval", business_id=order.id)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_order",
        content=f"审批客户订单 {order.order_no}",
        after_data={
            "decision": payload.decision,
            "actual_amount": float(actual_amount),
            "status": order.status,
            "note": payload.note,
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status})


def approve_sales_price_change(order_id: int, payload, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.price_approval_status != "pending" and order.status != "pending_price_approval":
        raise HTTPException(status_code=409, detail="当前客户订单没有待审批的改价")
    logs = db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all()
    if not logs:
        raise HTTPException(status_code=409, detail="当前客户订单没有待审批的改价明细")

    now = now_local()
    if payload.decision == "approved":
        order.price_approval_status = "approved"
        order.price_approved_by_id = current_user.id
        order.price_approved_at = now
        if order.status == "pending_price_approval":
            order.status = "pending_confirmation"
        for row in logs:
            row.status = "approved"
            row.approved_by_id = current_user.id
            row.approved_at = now
        create_notification(
            db,
            title="销售改价审批通过",
            content=f"客户订单 {order.order_no} 的销售改价已通过财务审批，可继续推进收款确认或发货流程。{payload.note or ''}",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, order.operator_user_id, *_manager_chairman_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )
        if payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales") == "货到付款/分期付款":
            _route_order_to_inventory_shipment(db, order=order, current_user=current_user)
    else:
        order.price_approval_status = "rejected"
        order.status = "rejected"
        order.rejection_reason = payload.note or "销售改价审批未通过"
        # 同上:驳回即释放占用,避免库存被未生效订单锁死
        release_order_reservation(db, order=order)
        for row in logs:
            row.status = "rejected"
            row.approved_by_id = current_user.id
            row.approved_at = now
        create_notification(
            db,
            title="销售改价审批驳回",
            content=f"客户订单 {order.order_no} 的销售改价被驳回，请销售调整订单。{payload.note or ''}",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, order.operator_user_id, *_manager_chairman_user_ids(db)])),
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_sales_price_change",
        content=f"审批客户订单 {order.order_no} 的销售改价",
        after_data={"decision": payload.decision, "status": order.status, "price_approval_status": order.price_approval_status},
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status, "price_approval_status": order.price_approval_status})


def finance_edit_order(order_id: int, payload, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status in {"shipped", "completed", "cancelled"} or order.shipped_at is not None:
        raise HTTPException(status_code=409, detail="客户订单已发货、已完成或已取消，财务不能再修改")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")

    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")

    original_customer_name = order.customer.name if order.customer else None
    original_operator_name = order.operator_user.full_name if order.operator_user else None

    def _serialize_order_items(items):
        return [
            {
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else None,
                "product_code": item.product.product_code if item.product else None,
                "display_specification": item.display_specification,
                "packaging_config_id": item.packaging_config_id,
                "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "packaging_unit_price": float(item.packaging_unit_price or 0),
                "packaging_fee": float(item.packaging_fee or 0),
                "quantity": float(item.quantity or 0),
                "unit_price": float(item.unit_price or 0),
                "total_amount": float(item.total_amount or 0),
                "remark": item.remark,
            }
            for item in items
        ]

    before_data = {
        "order_id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": original_customer_name,
        "operator_user_id": order.operator_user_id,
        "operator_name": original_operator_name,
        "consignee": order.consignee,
        "consignee_phone": order.consignee_phone,
        "delivery_address": order.delivery_address,
        "payment_type": order.payment_type,
        "deposit_amount": float(order.deposit_amount or 0),
        "transport_fee": float(order.transport_fee or 0),
        "packaging_fee": float(order.packaging_fee or 0),
        "order_remark": order.order_remark,
        "total_amount": float(order.total_amount or 0),
        "items": _serialize_order_items(order.items),
    }

    order.customer_id = payload.customer_id
    order.operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id or order.operator_user_id, order.sales_user or current_user)
    order.consignee = payload.consignee
    order.consignee_phone = payload.consignee_phone
    order.delivery_address = payload.delivery_address
    order.payment_type = payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales")
    order.order_type = payload.order_type or "normal"
    order.expected_delivery_date = payload.expected_delivery_date
    order.deposit_amount = payload.deposit_amount
    order.transport_fee = payload.transport_fee
    order.packaging_fee = 0
    order.order_remark = payload.order_remark
    order.finance_edit_pending = True

    shipped_qty_map = {item.id: Decimal(item.shipped_quantity or 0) for item in order.items}
    if any(quantity > 0 for quantity in shipped_qty_map.values()):
        raise HTTPException(status_code=409, detail="客户订单存在已出库明细，不能通过财务改单修改")

    # 删除明细会一并销毁"本明细占了多少"的账(item.reserved_quantity),
    # 而仓库端 inventories.reserved_quantity 不会自动跟着减。
    # 必须先按旧明细把占用退还,否则这批货被锁死且再也找不到归属者
    # (改单后订单仍是 pending_shipment,不会重跑审批占用)。
    was_occupied = order.status == "pending_shipment"
    release_order_reservation(db, order=order)
    for item in list(order.items):
        db.delete(item)
    db.flush()
    for log in db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all():
        db.delete(log)
    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    order.price_approval_status = "not_required"
    order.price_approved_by_id = None
    order.price_approved_at = None
    order.total_amount = _build_order_items(
        db,
        order_id=order.id,
        payload_items=payload.items,
        enforce_price_remark=False,
        create_price_approval=False,
    ) + Decimal(order.transport_fee or 0)
    # 改单前已占用的订单,按新明细重新占用(数量可能增减)。
    # occupy 是幂等的,重复调用不会叠加。
    # 注意:_build_order_items 用 db.add 直接插新行,不会追加到 order.items 关系上,
    # 必须先 expire 让关系重新加载,否则 occupy 拿到的还是刚被删掉的旧明细。
    if was_occupied:
        db.flush()
        db.expire(order, ["items"])
        occupy_inventory_for_order(db, order=order)
    updated_operator = db.execute(select(User).where(User.id == order.operator_user_id)).scalar_one_or_none()
    if order.status == "pending_price_approval":
        if payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales") == "货到付款/分期付款":
            _route_order_to_inventory_shipment(db, order=order, current_user=current_user)
        else:
            order.status = "pending_confirmation"

    sales_confirm_ids = [order.sales_user_id, order.operator_user_id]
    inventory_user_ids = _role_user_ids(db, "inventory")
    complete_todos(db, business_type="sales_order_finance_edit_sales_confirm", business_id=order.id)
    complete_todos(db, business_type="sales_order_finance_edit_inventory_confirm", business_id=order.id)

    for user_id in list(dict.fromkeys([user_id for user_id in sales_confirm_ids if user_id])):
        create_todo(
            db,
            title=f"确认财务修改客户订单 {order.order_no}",
            description=f"财务已在发货前修改客户订单，请核对客户、金额、收货与商品明细后确认。{payload.edit_note or ''}",
            module="sales",
            assigned_user_id=user_id,
            business_type="sales_order_finance_edit_sales_confirm",
            business_id=order.id,
            priority="high",
        )
    for user_id in inventory_user_ids:
        create_todo(
            db,
            title=f"确认财务修改待发订单 {order.order_no}",
            description=f"财务已在发货前修改客户订单，库存确认前该订单暂不能继续发货。{payload.edit_note or ''}",
            module="inventory",
            assigned_user_id=user_id,
            business_type="sales_order_finance_edit_inventory_confirm",
            business_id=order.id,
            priority="high",
        )

    create_notification(
        db,
        title="客户订单已由财务修改",
        content=f"客户订单 {order.order_no} 已由财务在发货前修改，请销售和库存分别确认后再推进后续流程。{payload.edit_note or ''}",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([*sales_confirm_ids, *inventory_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    after_data = {
        "order_id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer.name if customer else None,
        "operator_user_id": order.operator_user_id,
        "operator_name": updated_operator.full_name if updated_operator else None,
        "consignee": order.consignee,
        "consignee_phone": order.consignee_phone,
        "delivery_address": order.delivery_address,
        "payment_type": order.payment_type,
        "deposit_amount": float(order.deposit_amount or 0),
        "transport_fee": float(order.transport_fee or 0),
        "packaging_fee": float(order.packaging_fee or 0),
        "order_remark": order.order_remark,
        "total_amount": float(order.total_amount or 0),
        "finance_edit_pending": order.finance_edit_pending,
        "note": payload.edit_note,
        "items": _serialize_order_items(order.items),
    }

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="finance_edit_order",
        content=f"财务修改客户订单 {order.order_no}",
        before_data=before_data,
        after_data=after_data,
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no, "finance_edit_pending": order.finance_edit_pending})


def confirm_finance_edit_order(order_id: int, current_user: User, db: Session, note: str | None = None):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if not order.finance_edit_pending:
        return api_response(data={"id": order.id, "finance_edit_pending": False}, msg="该订单无需确认财务修改")

    role_code = current_user.role.code if current_user.role else ""
    if role_code == "sales":
        if current_user.id not in {order.sales_user_id, order.operator_user_id}:
            raise HTTPException(status_code=403, detail="仅订单销售人员或经办人可确认该财务修改")
        complete_todos(
            db,
            business_type="sales_order_finance_edit_sales_confirm",
            business_id=order.id,
            assigned_user_id=current_user.id,
        )
        confirm_type = "sales"
    elif role_code == "inventory":
        complete_todos(
            db,
            business_type="sales_order_finance_edit_inventory_confirm",
            business_id=order.id,
            assigned_user_id=current_user.id,
        )
        confirm_type = "inventory"
    elif current_user.role and current_user.role.is_admin:
        complete_todos(db, business_type="sales_order_finance_edit_sales_confirm", business_id=order.id)
        complete_todos(db, business_type="sales_order_finance_edit_inventory_confirm", business_id=order.id)
        confirm_type = "admin"
    else:
        raise HTTPException(status_code=403, detail="当前账号无权确认财务修改")

    if not _has_pending_finance_edit_confirmation(db, order.id):
        order.finance_edit_pending = False
        create_notification(
            db,
            title="客户订单财务修改已确认完成",
            content=f"客户订单 {order.order_no} 的财务修改已由销售和库存确认，可继续后续流程。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, order.operator_user_id, *_finance_user_ids(db), *_role_user_ids(db, "inventory")])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="confirm_finance_edit_order",
        content=f"确认客户订单 {order.order_no} 的财务修改",
        after_data={"confirm_type": confirm_type, "note": note, "finance_edit_pending": order.finance_edit_pending},
    )
    db.commit()
    return api_response(data={"id": order.id, "finance_edit_pending": order.finance_edit_pending})


def complete_order(order_id: int, current_user: User, db: Session):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status != "shipped":
        raise HTTPException(status_code=409, detail="仅已发货订单允许完结")

    received_amount = db.execute(
        select(func.coalesce(func.sum(ReceivableRecord.amount), 0)).where(ReceivableRecord.sales_order_id == order.id)
    ).scalar_one()
    if Decimal(received_amount or 0) < Decimal(order.total_amount or 0):
        raise HTTPException(status_code=409, detail="当前订单回款未达订单金额，不能标记完结")

    order.status = "completed"
    order.completed_at = now_local()

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is not None:
        queue.shipment_status = "completed"
        queue.placement_status = "hidden"

    complete_todos(db, business_type="sales_order", business_id=order.id)
    create_notification(
        db,
        title="客户订单已完成",
        content=f"客户订单 {order.order_no} 已完成闭环。",
        module="sales",
        recipient_user_ids=[order.sales_user_id],
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="complete_order",
        content=f"完结客户订单 {order.order_no}",
        after_data={"status": order.status},
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status})


