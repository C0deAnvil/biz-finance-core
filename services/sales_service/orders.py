"""销售域服务。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from pydantic import ValidationError
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
    ReceivablePlan,
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
from schemas.sales import SalesApprovalRequest, SalesMergedReceiptRequestCreate, SalesOrderCreate, SalesOrderDraftPayload, SalesOrderUpdate, SalesReceiptApprovalRequest, SalesReceiptRequestCreate, SalesReturnApprovalRequest, SalesReturnCreate, SalesSettlementCollectRequest
from services.customer_activity import register_customer_order
from services.core import apply_inventory_change, assignable_finance_user_ids, complete_todos, create_cash_flow, create_notification, create_todo, ensure_notification, next_business_no, resolve_assigned_finance_user_id, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity, product_expected_available_quantity
from services.request_trace import emit
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_finished_warehouse
from services.time_utils import now_local



from services.sales_service._common import _build_order_items, _build_split_items, _create_dispatch_queue, _create_inventory_placement_todos, _create_order_todos_and_notice, _ensure_order_owner_or_admin, _finance_user_ids, _finished_warehouse, _has_pending_finance_edit_confirmation, _load_order, _manager_chairman_user_ids, _resolve_sales_operator_user_id, _role_user_ids, _route_order_to_inventory_shipment, occupy_inventory_for_order, release_order_reservation


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

def _sync_customer_address(db, customer, payload, current_user):
    if not payload.update_customer_address:
        return
    db.refresh(customer, with_for_update=True)
    if current_user.role.code == "sales" and customer.owner_user_id not in {None, current_user.id}:
        raise HTTPException(status_code=403, detail="仅可同步本人客户的地址")
    old_address = (customer.address or "").strip()
    new_address = (payload.delivery_address or "").strip()
    if not new_address:
        raise HTTPException(status_code=409, detail="客户地址不能为空")
    if old_address == new_address:
        return
    if payload.customer_address_before is not None and payload.customer_address_before.strip() != old_address:
        raise HTTPException(status_code=409, detail="客户档案地址已变化，请刷新客户信息后重新确认")
    customer.address = new_address
    write_operation_log(db, user_id=current_user.id, module="sales", action="sync_customer_address_from_order",
                        content=f"保存销售单时同步客户 {customer.name} 地址",
                        before_data={"customer_id": customer.id, "address": old_address},
                        after_data={"customer_id": customer.id, "address": new_address})


def _store_draft_payload(order: SalesOrder, payload: SalesOrderDraftPayload) -> None:
    """保存原始表单；草稿阶段不创建订单明细，也不触发业务校验。"""
    ext_json = dict(order.ext_json or {})
    ext_json["draft_payload"] = payload.model_dump(mode="json")
    order.ext_json = ext_json
    order.customer_id = payload.customer_id
    order.receipt_account_id = payload.receipt_account_id
    order.consignee = (payload.consignee or "").strip()
    order.consignee_phone = (payload.consignee_phone or "").strip()
    order.delivery_address = (payload.delivery_address or "").strip()
    order.payment_type = (payload.payment_type or "").strip()
    order.order_type = payload.order_type or "normal"
    order.account_set = (payload.account_set or "").strip()
    order.expected_delivery_date = payload.expected_delivery_date
    order.deposit_amount = payload.deposit_amount or Decimal("0")
    order.transport_fee = payload.transport_fee or Decimal("0")
    order.packaging_fee = Decimal("0")
    order.total_amount = Decimal("0")
    order.order_remark = payload.order_remark
    order.status = "draft"


def _draft_payload_for_submit(order: SalesOrder) -> SalesOrderCreate:
    raw_payload = (order.ext_json or {}).get("draft_payload")
    if raw_payload is None:
        # 兼容本次改造前已保存的完整草稿。
        raw_payload = {
            "customer_id": order.customer_id,
            "operator_user_id": order.operator_user_id,
            "consignee": order.consignee,
            "consignee_phone": order.consignee_phone,
            "delivery_address": order.delivery_address,
            "payment_type": order.payment_type,
            "order_type": order.order_type,
            "account_set": order.account_set,
            "expected_delivery_date": order.expected_delivery_date,
            "deposit_amount": order.deposit_amount,
            "transport_fee": order.transport_fee,
            "order_remark": order.order_remark,
            "items": [
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "packaging_config_id": item.packaging_config_id,
                    "remark": item.remark,
                }
                for item in order.items
            ],
        }
    try:
        payload = SalesOrderCreate.model_validate(raw_payload)
    except ValidationError as error:
        first_error = error.errors()[0]
        field = " / ".join(str(part) for part in first_error["loc"])
        raise HTTPException(status_code=422, detail=f"请完善草稿后再提交：{field}") from error
    required_text_fields = {
        "收货人": payload.consignee,
        "联系电话": payload.consignee_phone,
        "收货地址": payload.delivery_address,
        "收款方式": payload.payment_type,
    }
    missing_field = next((label for label, value in required_text_fields.items() if not str(value or "").strip()), None)
    if missing_field:
        raise HTTPException(status_code=422, detail=f"请完善草稿后再提交：{missing_field}不能为空")
    if payload.receipt_account_id is None:
        raise HTTPException(status_code=422, detail="请完善草稿后再提交：收款账户不能为空")
    if not payload.items:
        raise HTTPException(status_code=422, detail="请完善草稿后再提交：至少需要一条商品明细")
    if any(item.packaging_config_id is None for item in payload.items):
        raise HTTPException(status_code=422, detail="请完善草稿后再提交：商品明细必须选择包装")
    return payload


def _apply_submitted_order_payload(db: Session, *, order: SalesOrder, payload: SalesOrderCreate, current_user: User) -> Customer:
    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")
    _sync_customer_address(db, customer, payload, current_user)
    order.customer_id = payload.customer_id
    order.operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id, current_user)
    order.consignee = payload.consignee
    order.consignee_phone = payload.consignee_phone
    order.delivery_address = payload.delivery_address
    order.payment_type = payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales")
    order.order_type = payload.order_type or "normal"
    order.account_set = payload.account_set
    order.expected_delivery_date = payload.expected_delivery_date
    order.deposit_amount = payload.deposit_amount
    order.transport_fee = payload.transport_fee
    order.packaging_fee = Decimal("0")
    order.order_remark = payload.order_remark
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
    ext_json = dict(order.ext_json or {})
    ext_json.pop("draft_payload", None)
    order.ext_json = ext_json or None
    return customer


def create_order(payload: SalesOrderDraftPayload, current_user: User, db: Session):
    order = SalesOrder(
        order_no=next_business_no(db, "XS"),
        split_phase=1,
        customer_id=None,
        sales_user_id=current_user.id,
        operator_user_id=current_user.id,
        consignee="",
        consignee_phone="",
        delivery_address="",
        payment_type="",
        order_type="normal",
        account_set="",
        status="draft",
        deposit_amount=0,
        transport_fee=0,
        packaging_fee=0,
        total_amount=0,
        cost_amount=0,
        profit_amount=0,
        order_remark=payload.order_remark,
    )
    db.add(order)
    db.flush()
    order.split_group_no = order.order_no
    _store_draft_payload(order, payload)

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="create_order",
        content=f"创建客户订单 {order.order_no}(草稿)",
        after_data={
            "customer_id": order.customer_id,
            "status": order.status,
            "total_amount": 0,
            "item_count": 0,
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
            "churn_repurchase": False,
            "churn_gap_days": None,
        }
    )


def submit_order(
    order_id: int,
    current_user: User,
    db: Session,
    assigned_finance_user_id: int | None = None,
    update_customer_address: bool = False,
    customer_address_before: str | None = None,
):
    """草稿/已驳回订单提交财务审批。提交后不可编辑、不可删除。

    可指定处理该单的财务人员；不指定时向所有财务开放处理。
    """
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="仅草稿或已驳回订单可提交审批")

    payload = _draft_payload_for_submit(order)
    payload.update_customer_address = update_customer_address
    payload.customer_address_before = customer_address_before
    customer = _apply_submitted_order_payload(db, order=order, payload=payload, current_user=current_user)
    churn_result = register_customer_order(
        db,
        customer=customer,
        order_amount=order.total_amount,
        order_time=order.created_at,
        created_by_id=current_user.id,
    )
    _notify_sales_order_shortage(db, order=order, current_user=current_user)
    order.status = "pending_confirmation"
    order.rejection_reason = None
    order.assigned_finance_user_id = (
        resolve_assigned_finance_user_id(db, assigned_finance_user_id)
        if assigned_finance_user_id is not None
        else None
    )
    _create_dispatch_queue(db, order.id)

    # 指定时仅该财务处理；未指定则向全体财务（含超级财务）开放。
    assignee_ids = [order.assigned_finance_user_id] if order.assigned_finance_user_id else assignable_finance_user_ids(db)
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
        after_data={"order_id": order.id, "status": order.status, "churn_repurchase": churn_result.get("churn_repurchase", False)},
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no, "status": order.status})


def update_order(order_id: int, payload: SalesOrderDraftPayload, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="仅草稿或已驳回订单允许修改(已提交订单请通过财务改单流程)")

    before_data = {
        "customer_id": order.customer_id,
        "status": order.status,
        "total_amount": float(order.total_amount),
        "item_count": len(order.items),
    }

    order.rejection_reason = None
    _store_draft_payload(order, payload)
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
            "item_count": 0,
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no})


def _approval_receivable_amount(order: SalesOrder, *, convert_to_receivable: bool) -> Decimal:
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    if convert_to_receivable or payment_type == "先发货后收款":
        return max(Decimal(order.total_amount or 0), Decimal("0"))
    if payment_type == "先收定金后收尾款":
        return max(Decimal(order.total_amount or 0) - Decimal(order.deposit_amount or 0), Decimal("0"))
    return Decimal("0")


def _record_approval_payment_and_receivable(
    db: Session, *, order: SalesOrder, current_user: User, convert_to_receivable: bool
) -> tuple[Decimal, Decimal]:
    """财务通过即确认已收款；尚未到账部分建立应收计划，而非销售再次发起收款确认。"""
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    received_amount = Decimal("0") if convert_to_receivable else (Decimal(order.total_amount or 0) if payment_type == "全款发货" else (
        min(Decimal(order.deposit_amount or 0), Decimal(order.total_amount or 0))
        if payment_type == "先收定金后收尾款" else Decimal("0")
    ))
    if received_amount > 0:
        stage = "deposit" if payment_type == "先收定金后收尾款" else "pre_shipment_full"
        db.add(ReceivableRecord(
            sales_order_id=order.id,
            company_account_id=order.receipt_account_id,
            amount=received_amount,
            payment_stage=stage,
            received_at=now_local(),
            received_by_id=current_user.id,
            remark="财务审批确认收款",
        ))
        create_cash_flow(
            db, flow_type="incoming", business_type="sales_order", business_id=order.id,
            related_no=order.order_no, amount=received_amount, happened_at=now_local(),
            remark="财务审批确认收款", company_account_id=order.receipt_account_id,
        )
    receivable_amount = _approval_receivable_amount(order, convert_to_receivable=convert_to_receivable)
    if receivable_amount > 0:
        db.add(ReceivablePlan(
            sales_order_id=order.id,
            due_date=now_local().date(),
            planned_amount=receivable_amount,
            created_by_id=current_user.id,
        ))
    return received_amount, receivable_amount


def approve_order(order_id: int, payload: SalesApprovalRequest, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status not in {"pending_confirmation", "pending_price_approval"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许确认收款")
    role_code = current_user.role.code if current_user.role else ""
    if (role_code in {"finance", "finance_super"}
            and order.assigned_finance_user_id is not None
            and order.assigned_finance_user_id != current_user.id):
        raise HTTPException(status_code=403, detail="该客户订单已指定由其他财务人员处理")
    pending_price_logs = db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all()
    discount_logs = [item for item in pending_price_logs
                     if Decimal(item.actual_price or 0) < Decimal(item.standard_price or 0)]
    approval_time = now_local()
    if discount_logs:
        order.price_approval_status = "approved" if payload.decision == "approved" else "rejected"
        order.price_approved_by_id = current_user.id
        order.price_approved_at = approval_time
    elif order.price_approval_status == "pending":
        order.price_approval_status = "not_required"
    for item in pending_price_logs:
        item.status = ("approved" if payload.decision == "approved" else "rejected") if item in discount_logs else "not_required"
        item.approved_by_id = current_user.id
        item.approved_at = approval_time

    # 收款确认由订单收款方式确定实收及应收；主动转应收时实收始终为零。
    actual_amount = Decimal("0")

    db.add(
        SalesOrderApproval(
            sales_order_id=order.id,
            approver_user_id=current_user.id,
            decision=payload.decision,
            voucher_amount=actual_amount,
            note=(f"转为应收放行；{payload.note or ''}" if payload.convert_to_receivable else payload.note),
            approved_at=approval_time,
        )
    )

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is None:
        queue = InventoryDispatchQueue(
            sales_order_id=order.id,
            warehouse_id=_finished_warehouse(db).id,
        )
        db.add(queue)
    elif queue.warehouse_id is None:
        queue.warehouse_id = _finished_warehouse(db).id
    # 订单一旦指定处理财务，后续审批结果通知也只发给该人；不能又广播给全体财务。
    finance_recipient_ids = [order.assigned_finance_user_id] if order.assigned_finance_user_id else assignable_finance_user_ids(db)

    if payload.decision == "approved":
        order.status = "pending_shipment"
        order.approved_by_id = current_user.id
        order.approved_at = approval_time
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

        received_amount, receivable_amount = _record_approval_payment_and_receivable(
            db, order=order, current_user=current_user, convert_to_receivable=payload.convert_to_receivable
        )
        actual_amount = received_amount
        # 审批确认收款或应收放行，不再创建销售收款确认协同单。
        # 本次实收由 _record_approval_payment_and_receivable 按收款方式统一登记，
        # 同时将尚未收到的部分建立应收计划；调用方传入的凭证金额不参与入账计算。

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
            title="客户订单已转应收" if payload.convert_to_receivable else "客户订单收款确认通过",
            content=(f"客户订单 {order.order_no} 已转为应收并进入置货流程。" if payload.convert_to_receivable
                     else f"客户订单 {order.order_no} 已完成收款确认并进入置货流程。"),
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *inventory_user_ids, *finance_recipient_ids])),
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
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *finance_recipient_ids])),
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_order_approval", business_id=order.id)
    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_order",
        content=f"审批客户订单 {order.order_no}",
        after_data={
            "decision": payload.decision,
            "actual_amount": float(actual_amount),
            "receivable_amount": float(receivable_amount) if payload.decision == "approved" else 0,
            "status": order.status,
            "note": payload.note,
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status})


def approve_sales_price_change(order_id: int, payload, current_user: User, db: Session):
    """旧接口保留明确提示；改价只能随订单收款确认一起处理。"""
    raise HTTPException(status_code=409, detail="改价已合并到客户订单收款确认，请在该页面确认收款或转为应收")



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
        if payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales") == "先发货后收款":
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
