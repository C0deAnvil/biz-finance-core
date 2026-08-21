import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import Customer, InventoryDispatchQueue, InventoryOperationRequest, InventoryTransaction, Notification, NotificationRecipient, OperationLog, Product, ReceivableRecord, RefundRecord, Role, SalesOrder, SalesOrderItem, SalesOrderPriceChangeLog, SalesReceiptRequest, SalesReturn, SalesReturnItem, TodoItem, User, Warehouse
from responses import api_response
from schemas.sales import SalesApprovalRequest, SalesFinanceEditConfirmRequest, SalesMergedReceiptRequestCreate, SalesOrderCreate, SalesOrderFinanceEditRequest, SalesOrderUpdate, SalesPriceApprovalRequest, SalesReceiptApprovalRequest, SalesReceiptRequestCreate, SalesReturnApprovalRequest, SalesReturnCreate
from services.display import clean_display_text, format_legacy_remark, payment_stage_label, payment_type_label, return_type_label, status_label
from services.organization import get_scoped_user_ids, user_in_scope
from services.sales_service import (
    approve_order as approve_order_service,
    approve_sales_price_change as approve_sales_price_change_service,
    approve_sales_receipt_request as approve_sales_receipt_request_service,
    approve_return as approve_return_service,
    confirm_finance_edit_order as confirm_finance_edit_order_service,
    complete_order as complete_order_service,
    create_order as create_order_service,
    create_return as create_return_service,
    finance_edit_order as finance_edit_order_service,
    submit_merged_sales_receipt_request as submit_merged_sales_receipt_request_service,
    submit_sales_receipt_request as submit_sales_receipt_request_service,
    update_order as update_order_service,
)
from services.workflow import sales_flow_steps


router = APIRouter(prefix="/sales", tags=["销售"])
RECEIPT_MERGE_NO_PATTERN = re.compile(r"合并收款批次[:：]\s*(?P<merge_no>[A-Za-z0-9-]+)")


def _find_role_user_ids(db: Session, *role_codes: str) -> list[int]:
    return [
        user.id
        for user in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def _ensure_sales_scope(db: Session, current_user: User, owner_user_id: int | None) -> None:
    if not user_in_scope(db, current_user, business_role_code="sales", owner_user_id=owner_user_id):
        raise HTTPException(status_code=403, detail="仅可查看数据范围内的客户订单")


def _received_amount_map(rows: list[ReceivableRecord]) -> dict[int, Decimal]:
    amount_map: dict[int, Decimal] = {}
    for row in rows:
        amount_map[row.sales_order_id] = amount_map.get(row.sales_order_id, Decimal("0")) + Decimal(row.amount or 0)
    return amount_map


def _settlement_status(total_amount: Decimal, received_amount: Decimal) -> str:
    if received_amount <= 0:
        return "unpaid"
    if received_amount >= total_amount:
        return "settled"
    return "partial_settled"


def _current_finance_edit_items(order: SalesOrder) -> list[dict]:
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
        for item in order.items
    ]


def _enrich_finance_edit_snapshot_data(db: Session, data: dict, order: SalesOrder, fallback_items: list[dict]) -> dict:
    result = dict(data or {})
    result.setdefault("order_id", order.id)
    result.setdefault("order_no", order.order_no)
    if not result.get("customer_name"):
        customer_id = result.get("customer_id") or order.customer_id
        customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none() if customer_id else None
        result["customer_name"] = customer.name if customer else (order.customer.name if order.customer else None)
    if not result.get("operator_name"):
        operator_id = result.get("operator_user_id") or order.operator_user_id
        operator = db.execute(select(User).where(User.id == operator_id)).scalar_one_or_none() if operator_id else None
        result["operator_name"] = operator.full_name if operator else (order.operator_user.full_name if order.operator_user else None)

    raw_items = result.get("items") or []
    if not raw_items:
        result["items"] = fallback_items
        return result

    product_ids = [int(item.get("product_id")) for item in raw_items if item.get("product_id")]
    product_map = {
        product.id: product
        for product in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
    } if product_ids else {}
    enriched_items = []
    for item in raw_items:
        enriched = dict(item or {})
        product = product_map.get(enriched.get("product_id"))
        if product is not None:
            enriched.setdefault("product_name", product.name)
            enriched.setdefault("product_code", product.product_code)
            enriched.setdefault("display_specification", product.specification)
        quantity = Decimal(str(enriched.get("quantity") or 0))
        unit_price = Decimal(str(enriched.get("unit_price") or 0))
        if enriched.get("total_amount") is None:
            enriched["total_amount"] = float(quantity * unit_price)
        enriched_items.append(enriched)
    result["items"] = enriched_items
    return result


def _finance_edit_snapshot(db: Session, order: SalesOrder) -> dict | None:
    row = (
        db.execute(
            select(OperationLog)
            .where(OperationLog.module == "sales", OperationLog.action == "finance_edit_order")
            .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        )
        .scalars()
        .all()
    )
    matched = next(
        (
            item
            for item in row
            if (item.after_data or {}).get("order_id") == order.id
            or (item.before_data or {}).get("order_id") == order.id
            or (item.after_data or {}).get("order_no") == order.order_no
            or (item.before_data or {}).get("order_no") == order.order_no
            or order.order_no in str(item.content or "")
        ),
        None,
    )
    if matched is None:
        return None
    current_items = _current_finance_edit_items(order)
    before_data = _enrich_finance_edit_snapshot_data(db, matched.before_data or {}, order, current_items)
    after_data = _enrich_finance_edit_snapshot_data(db, matched.after_data or {}, order, current_items)
    if not (matched.before_data or {}).get("items"):
        before_data["legacy_snapshot_note"] = "历史改单日志未记录修改前明细，当前仅保留该次改单的基础字段。"
    return {
        "id": matched.id,
        "changed_by_id": matched.user_id,
        "changed_by_name": matched.user.full_name if matched.user else None,
        "changed_at": matched.created_at.isoformat() if matched.created_at else None,
        "before_data": before_data,
        "after_data": after_data,
        "content": matched.content,
    }


def _finance_edit_confirm_state(db: Session, order_id: int, current_user: User) -> dict:
    role_code = current_user.role.code if current_user.role else ""
    business_type = None
    if role_code == "sales":
        business_type = "sales_order_finance_edit_sales_confirm"
    elif role_code == "inventory":
        business_type = "sales_order_finance_edit_inventory_confirm"
    if business_type is None:
        return {"pending": False, "business_type": None}
    pending = (
        db.execute(
            select(TodoItem).where(
                TodoItem.business_type == business_type,
                TodoItem.business_id == order_id,
                TodoItem.assigned_user_id == current_user.id,
                TodoItem.status != "completed",
            )
        )
        .scalars()
        .first()
        is not None
    )
    return {"pending": pending, "business_type": business_type}


def _requires_settlement_collaboration(order: SalesOrder) -> bool:
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    return payment_type in {"先付定金后付尾款", "全款发货", "货到付款/分期付款"}


def _extract_receipt_merge_no(remark: str | None) -> str | None:
    text = str(remark or "").strip()
    if not text:
        return None
    match = RECEIPT_MERGE_NO_PATTERN.search(text)
    return match.group("merge_no") if match else None


@router.get("/orders")
def list_orders(
    tab: str = Query(default="reviewing", pattern="^(reviewing|shipping|completed|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user), joinedload(SalesOrder.operator_user), joinedload(SalesOrder.parent_order))
        .order_by(SalesOrder.created_at.desc())
    )
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        query = query.where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    tab_filters = {
        "reviewing": ~SalesOrder.status.in_(["pending_shipment", "shipped", "completed", "cancelled"]),
        "shipping": SalesOrder.status.in_(["pending_shipment", "shipped"]),
        "completed": SalesOrder.status.in_(["completed", "cancelled"]),
        "all": True,
    }
    query = query.where(tab_filters[tab])
    total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
    rows = db.execute(
        query.offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    order_ids = [row.id for row in rows]
    receivables = (
        db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids)).order_by(ReceivableRecord.id.desc())).scalars().all()
        if order_ids
        else []
    )
    received_amount_map = _received_amount_map(receivables)
    return api_response(
        data={
            "items": [
            {
                "id": row.id,
                "order_no": row.order_no,
                "parent_order_id": row.parent_order_id,
                "parent_order_no": row.parent_order.order_no if row.parent_order else None,
                "split_group_no": row.split_group_no,
                "split_phase": row.split_phase,
                "is_split_child": bool(row.parent_order_id),
                "customer_name": row.customer.name if row.customer else None,
                "sales_user": row.sales_user.full_name if row.sales_user else None,
                "operator_user_id": row.operator_user_id,
                "operator_name": row.operator_user.full_name if row.operator_user else None,
                "order_remark": row.order_remark,
                "status": row.status,
                "status_label": status_label(row.status),
                "price_approval_status": row.price_approval_status,
                "price_approval_status_label": status_label(row.price_approval_status),
                "finance_edit_pending": bool(row.finance_edit_pending),
                "finance_edit_confirm_state": _finance_edit_confirm_state(db, row.id, current_user),
                "payment_type": row.payment_type,
                "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
                "total_amount": float(row.total_amount),
                "deposit_amount": float(row.deposit_amount),
                "received_amount": float(received_amount_map.get(row.id, Decimal("0"))),
                "remaining_amount": float(max(Decimal(row.total_amount or 0) - received_amount_map.get(row.id, Decimal("0")), Decimal("0"))),
                "settlement_status": _settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0"))),
                "settlement_status_label": status_label(_settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0")))),
                "is_completed_order": row.status == "completed",
                "can_mark_completed": row.status == "shipped" and _settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0"))) == "settled",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
            ],
            "pagination": {"page": page, "page_size": page_size, "total": total},
        }
    )


@router.get("/orders/{order_id}")
def get_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.operator_user),
            joinedload(SalesOrder.parent_order),
        )
        .where(SalesOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_sales_scope(db, current_user, order.sales_user_id)
    receivables = db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id == order.id)).scalars().all()
    received_amount = _received_amount_map(receivables).get(order.id, Decimal("0"))
    settlement_status = _settlement_status(Decimal(order.total_amount or 0), received_amount)

    child_orders = db.execute(
        select(SalesOrder).where(SalesOrder.parent_order_id == order.id).order_by(SalesOrder.split_phase.asc(), SalesOrder.id.asc())
    ).scalars().all()
    return api_response(
        data={
            "id": order.id,
            "order_no": order.order_no,
            "parent_order_id": order.parent_order_id,
            "parent_order_no": order.parent_order.order_no if order.parent_order else None,
            "split_group_no": order.split_group_no,
            "split_phase": order.split_phase,
            "child_orders": [
                {"id": row.id, "order_no": row.order_no, "split_phase": row.split_phase, "status": row.status, "status_label": status_label(row.status)}
                for row in child_orders
            ],
            "status": order.status,
            "status_label": status_label(order.status),
            "price_approval_status": order.price_approval_status,
            "price_approval_status_label": status_label(order.price_approval_status),
            "finance_edit_pending": bool(order.finance_edit_pending),
            "price_approved_by_id": order.price_approved_by_id,
            "price_approved_at": order.price_approved_at.isoformat() if order.price_approved_at else None,
            "customer": {"id": order.customer.id, "name": order.customer.name} if order.customer else None,
            "operator_user_id": order.operator_user_id,
            "operator_name": order.operator_user.full_name if order.operator_user else None,
            "consignee": order.consignee,
            "consignee_phone": order.consignee_phone,
            "delivery_address": order.delivery_address,
            "order_remark": order.order_remark,
            "payment_type": order.payment_type,
            "payment_type_label": payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales"),
            "total_amount": float(order.total_amount),
            "deposit_amount": float(order.deposit_amount),
            "received_amount": float(received_amount),
            "remaining_amount": float(max(Decimal(order.total_amount or 0) - received_amount, Decimal("0"))),
            "settlement_status": settlement_status,
            "settlement_status_label": status_label(settlement_status),
            "flow_steps": sales_flow_steps(order, settlement_status=settlement_status),
            "transport_fee": float(order.transport_fee),
            "packaging_fee": float(order.packaging_fee),
            "finance_edit_snapshot": _finance_edit_snapshot(db, order),
            "finance_edit_confirm_state": _finance_edit_confirm_state(db, order.id, current_user),
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else None,
                    "quantity": float(item.quantity),
                    "shipped_quantity": float(item.shipped_quantity or 0),
                    "remaining_shipment_quantity": float(max(Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0), Decimal("0"))),
                    "unit_price": float(item.unit_price),
                    "total_amount": float(item.total_amount),
                    "packaging_config_id": item.packaging_config_id,
                    "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                    "display_specification": item.display_specification,
                    "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                    "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                    "packaging_unit_price": float(item.packaging_unit_price or 0),
                    "packaging_fee": float(item.packaging_fee or 0),
                    "remark": item.remark,
                }
                for item in order.items
            ],
            "price_change_logs": [
                {
                    "id": row.id,
                    "sales_order_item_id": row.sales_order_item_id,
                    "product_id": row.product_id,
                    "product_name": row.product.name if row.product else None,
                    "standard_price": float(row.standard_price or 0),
                    "actual_price": float(row.actual_price or 0),
                    "remark": row.remark,
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "changed_by_name": row.changed_by.full_name if row.changed_by else None,
                    "approved_by_name": row.approved_by.full_name if row.approved_by else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in db.execute(
                    select(SalesOrderPriceChangeLog)
                    .options(joinedload(SalesOrderPriceChangeLog.product), joinedload(SalesOrderPriceChangeLog.changed_by), joinedload(SalesOrderPriceChangeLog.approved_by))
                    .where(SalesOrderPriceChangeLog.sales_order_id == order.id)
                    .order_by(SalesOrderPriceChangeLog.created_at.desc(), SalesOrderPriceChangeLog.id.desc())
                ).scalars().all()
            ],
        }
    )


@router.post("/orders")
def create_order(payload: SalesOrderCreate, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    return create_order_service(payload, current_user, db)


@router.put("/orders/{order_id}")
def update_order(order_id: int, payload: SalesOrderUpdate, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    return update_order_service(order_id, payload, current_user, db)


@router.put("/orders/{order_id}/finance-edit")
def finance_edit_order(
    order_id: int,
    payload: SalesOrderFinanceEditRequest,
    current_user: User = Depends(require_roles("finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return finance_edit_order_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/finance-edit/confirm")
def confirm_finance_edit_order(
    order_id: int,
    payload: SalesFinanceEditConfirmRequest,
    current_user: User = Depends(require_roles("sales", "inventory", "system_admin")),
    db: Session = Depends(get_db),
):
    return confirm_finance_edit_order_service(order_id, current_user, db, note=payload.note)


@router.delete("/orders/{order_id}")
def delete_order(order_id: int, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.sales_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可删除本人订单")
    if order.status != "pending_confirmation":
        raise HTTPException(status_code=409, detail="仅待确认订单允许删除")

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue:
        db.delete(queue)
    db.delete(order)
    db.commit()
    return api_response(msg="客户订单已删除")


@router.get("/approvals/pending")
def pending_approvals(current_user: User = Depends(require_roles("chairman", "system_admin")), db: Session = Depends(get_db)):
    return api_response(data=[])


@router.post("/orders/{order_id}/approve")
def approve_order(
    order_id: int,
    payload: SalesApprovalRequest,
    current_user: User = Depends(require_roles("chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_order_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/price-approval")
def approve_price_change(
    order_id: int,
    payload: SalesPriceApprovalRequest,
    current_user: User = Depends(require_roles("finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_sales_price_change_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/complete")
def complete_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return complete_order_service(order_id, current_user, db)


@router.post("/settlements/receipt-requests")
def submit_sales_receipt_request(
    payload: SalesReceiptRequestCreate,
    current_user: User = Depends(require_roles("sales", "finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return submit_sales_receipt_request_service(payload, current_user, db)


@router.post("/settlements/receipt-requests/merged")
def submit_merged_sales_receipt_request(
    payload: SalesMergedReceiptRequestCreate,
    current_user: User = Depends(require_roles("sales", "finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return submit_merged_sales_receipt_request_service(payload, current_user, db)


@router.post("/settlements/receipt-requests/{request_id}/approve")
def approve_sales_receipt_request(
    request_id: int,
    payload: SalesReceiptApprovalRequest,
    current_user: User = Depends(require_roles("chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_sales_receipt_request_service(request_id, payload, current_user, db)


@router.get("/settlements")
def list_sales_settlements(
    current_user: User = Depends(require_roles("sales", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    query = (
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.parent_order),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        )
        .where(SalesOrder.status != "cancelled")
        .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
    )
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        query = query.where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    orders = db.execute(query).unique().scalars().all()
    order_ids = [row.id for row in orders]

    receivables = (
        db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids)).order_by(ReceivableRecord.received_at.desc(), ReceivableRecord.id.desc())).scalars().all()
        if order_ids
        else []
    )
    receipt_requests = (
        db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.sales_order_id.in_(order_ids)).order_by(SalesReceiptRequest.created_at.desc(), SalesReceiptRequest.id.desc())).scalars().all()
        if order_ids
        else []
    )
    records_user_ids = [row.received_by_id for row in receivables if row.received_by_id]
    records_user_ids.extend([row.requested_by_id for row in receipt_requests if row.requested_by_id])
    records_user_ids.extend([row.approved_by_id for row in receipt_requests if row.approved_by_id])

    sales_returns = (
        db.execute(select(SalesReturn).where(SalesReturn.sales_order_id.in_(order_ids)).order_by(SalesReturn.created_at.desc(), SalesReturn.id.desc())).scalars().all()
        if order_ids
        else []
    )
    return_ids = [row.id for row in sales_returns]
    refunds = (
        db.execute(select(RefundRecord).where(RefundRecord.sales_return_id.in_(return_ids)).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())).scalars().all()
        if return_ids
        else []
    )
    records_user_ids.extend([row.processed_by_id for row in refunds if row.processed_by_id])
    user_map = {
        row.id: row
        for row in db.execute(select(User).where(User.id.in_(list({user_id for user_id in records_user_ids if user_id})))).scalars().all()
    } if records_user_ids else {}
    return_map = {row.id: row for row in sales_returns}
    received_amount_map = _received_amount_map(receivables)

    notifications = db.execute(
        select(NotificationRecipient)
        .options(joinedload(NotificationRecipient.notification))
        .join(Notification, Notification.id == NotificationRecipient.notification_id)
        .where(NotificationRecipient.user_id == current_user.id, Notification.module.in_(["sales", "finance"]))
        .order_by(NotificationRecipient.created_at.desc(), NotificationRecipient.id.desc())
    ).scalars().all()

    order_rows = []
    completed_order_rows = []
    for row in orders:
        total_amount = Decimal(row.total_amount or 0)
        received_amount = received_amount_map.get(row.id, Decimal("0"))
        settlement_status = _settlement_status(total_amount, received_amount)
        if not _requires_settlement_collaboration(row):
            continue
        serialized = {
            "id": row.id,
            "order_no": row.order_no,
            "parent_order_no": row.parent_order.order_no if row.parent_order else None,
            "split_phase": row.split_phase,
            "customer_name": row.customer.name if row.customer else None,
            "customer_id": row.customer_id,
            "status": row.status,
            "status_label": status_label(row.status),
            "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
            "total_amount": float(total_amount),
            "deposit_amount": float(row.deposit_amount or 0),
            "received_amount": float(received_amount),
            "remaining_amount": float(max(total_amount - received_amount, Decimal("0"))),
            "settlement_status": settlement_status,
            "settlement_status_label": status_label(settlement_status),
            "order_remark": row.order_remark,
            "settlement_completed": settlement_status == "settled",
            "can_mark_completed": row.status == "shipped" and settlement_status == "settled",
            "items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else None,
                    "product_code": item.product.product_code if item.product else None,
                    "quantity": float(item.quantity or 0),
                    "shipped_quantity": float(item.shipped_quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "total_amount": float(item.total_amount or 0),
                    "display_specification": item.display_specification,
                    "packaging_config_id": item.packaging_config_id,
                    "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                    "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                    "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                    "packaging_unit_price": float(item.packaging_unit_price or 0),
                    "packaging_fee": float(item.packaging_fee or 0),
                }
                for item in row.items
            ],
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if row.status == "completed":
            completed_order_rows.append(serialized)
        else:
            order_rows.append(serialized)

    return api_response(
        data={
            "summary": {
                "order_count": len(order_rows),
                "receipt_request_count": len(receipt_requests),
                "receivable_count": len(receivables),
                "refund_count": len(refunds),
                "notification_count": len(notifications),
            },
            "notifications": [
                {
                    "id": row.id,
                    "title": row.notification.title if row.notification else "",
                    "content": row.notification.content if row.notification else "",
                    "module": row.notification.module if row.notification else "",
                    "category": row.notification.category if row.notification else "info",
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in notifications
            ],
            "orders": order_rows,
            "completed_orders": completed_order_rows,
            "receivables": [
                {
                    "id": row.id,
                    "sales_order_id": row.sales_order_id,
                    "sales_order_no": next((order.order_no for order in orders if order.id == row.sales_order_id), None),
                    "amount": float(row.amount),
                    "payment_stage": row.payment_stage,
                    "payment_stage_label": payment_stage_label(row.payment_stage),
                    "received_at": row.received_at.isoformat() if row.received_at else None,
                    "received_by_name": user_map[row.received_by_id].full_name if row.received_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="receivable"),
                }
                for row in receivables
            ],
            "receipt_requests": [
                {
                    "id": row.id,
                    "request_no": row.request_no,
                    "sales_order_id": row.sales_order_id,
                    "sales_order_no": next((order.order_no for order in orders if order.id == row.sales_order_id), None),
                    "requested_amount": float(row.requested_amount or 0),
                    "actual_amount": float(row.actual_amount or 0),
                    "payment_stage": row.payment_stage,
                    "payment_stage_label": payment_stage_label(row.payment_stage),
                    "installment_total": row.installment_total,
                    "installment_index": row.installment_index,
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "merge_no": _extract_receipt_merge_no(row.remark),
                    "remark": row.remark,
                    "approval_remark": row.approval_remark,
                    "requested_by_name": user_map[row.requested_by_id].full_name if row.requested_by_id in user_map else None,
                    "approved_by_name": user_map[row.approved_by_id].full_name if row.approved_by_id in user_map else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in receipt_requests
            ],
            "refunds": [
                {
                    "id": row.id,
                    "sales_return_id": row.sales_return_id,
                    "sales_return_no": return_map[row.sales_return_id].return_no if row.sales_return_id in return_map else None,
                    "sales_order_id": return_map[row.sales_return_id].sales_order_id if row.sales_return_id in return_map else None,
                    "sales_order_no": next((order.order_no for order in orders if row.sales_return_id in return_map and order.id == return_map[row.sales_return_id].sales_order_id), None),
                    "amount": float(row.amount),
                    "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
                    "processed_by_name": user_map[row.processed_by_id].full_name if row.processed_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="refund"),
                }
                for row in refunds
            ],
        }
    )


@router.get("/returns")
def list_returns(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(SalesReturn).order_by(SalesReturn.created_at.desc())
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        query = query.where(SalesReturn.requested_by_id.in_(scoped_user_ids))
    rows = db.execute(query).scalars().all()
    order_ids = [row.sales_order_id for row in rows]
    return_ids = [row.id for row in rows]
    orders = {row.id: row for row in db.execute(select(SalesOrder).where(SalesOrder.id.in_(order_ids))).scalars().all()} if order_ids else {}
    refunds = db.execute(select(RefundRecord).where(RefundRecord.sales_return_id.in_(return_ids))).scalars().all() if return_ids else []

    refund_map: dict[int, dict] = {}
    for row in refunds:
        bucket = refund_map.setdefault(row.sales_return_id, {"refunded_amount": Decimal("0"), "last_refunded_at": None})
        bucket["refunded_amount"] += Decimal(row.amount or 0)
        bucket["last_refunded_at"] = row.refunded_at.isoformat() if row.refunded_at else bucket["last_refunded_at"]

    return api_response(
        data=[
            {
                "id": row.id,
                "return_no": row.return_no,
                "sales_order_id": row.sales_order_id,
                "sales_order_no": orders[row.sales_order_id].order_no if row.sales_order_id in orders else None,
                "return_type": row.return_type,
                "return_type_label": return_type_label(row.return_type),
                "reason": clean_display_text(row.reason, "销售退换货申请"),
                "status": row.status,
                "status_label": status_label(row.status),
                "refund_amount": float(row.refund_amount),
                "refunded_amount": float(refund_map.get(row.id, {}).get("refunded_amount", 0)),
                "last_refunded_at": refund_map.get(row.id, {}).get("last_refunded_at"),
                "requires_reinspection": row.requires_reinspection,
                "reinspection_status": row.reinspection_status,
                "reinspection_status_label": status_label(row.reinspection_status),
                "review_note": clean_display_text(row.review_note, "") if row.review_note else "",
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/returns/{return_id}")
def get_return_detail(return_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sales_return = db.execute(select(SalesReturn).where(SalesReturn.id == return_id)).scalar_one_or_none()
    if sales_return is None:
        raise HTTPException(status_code=404, detail="销售退换货单不存在")
    _ensure_sales_scope(db, current_user, sales_return.requested_by_id)

    order = db.execute(select(SalesOrder).where(SalesOrder.id == sales_return.sales_order_id)).scalar_one_or_none()
    return_items = db.execute(select(SalesReturnItem).where(SalesReturnItem.sales_return_id == sales_return.id)).scalars().all()
    order_item_ids = [item.sales_order_item_id for item in return_items]
    order_items = {
        row.id: row
        for row in db.execute(
            select(SalesOrderItem).options(joinedload(SalesOrderItem.product)).where(SalesOrderItem.id.in_(order_item_ids))
        ).scalars().all()
    } if order_item_ids else {}
    refund_records = db.execute(
        select(RefundRecord).where(RefundRecord.sales_return_id == sales_return.id).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())
    ).scalars().all()
    inventory_request = db.execute(
        select(InventoryOperationRequest)
        .where(
            InventoryOperationRequest.source_type == "sales_return",
            InventoryOperationRequest.source_id == sales_return.id,
            InventoryOperationRequest.request_category == "sales_return_inbound",
        )
        .order_by(InventoryOperationRequest.id.desc())
    ).scalars().first()

    user_ids = [user_id for user_id in {sales_return.requested_by_id, sales_return.approved_by_id} if user_id]
    user_ids.extend([row.processed_by_id for row in refund_records if row.processed_by_id])
    user_map = {row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()} if user_ids else {}
    inventory_transactions = db.execute(
        select(InventoryTransaction)
        .where(InventoryTransaction.related_type == "inventory_operation_request")
        .where(InventoryTransaction.related_id == (inventory_request.id if inventory_request else -1))
        .order_by(InventoryTransaction.created_at.asc(), InventoryTransaction.id.asc())
    ).scalars().all()
    warehouse_ids = [row.warehouse_id for row in inventory_transactions if row.warehouse_id]
    transaction_product_ids = [row.product_id for row in inventory_transactions if row.product_id]
    transaction_user_ids = [row.created_by_id for row in inventory_transactions if row.created_by_id]
    request_product_ids = [
        item.get("product_id")
        for item in ((inventory_request.handled_items_json or inventory_request.original_items_json or []) if inventory_request else [])
        if item.get("product_id")
    ]
    product_ids = list(dict.fromkeys([*transaction_product_ids, *request_product_ids]))
    warehouse_map = {row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids))).scalars().all()} if warehouse_ids else {}
    product_map = {row.id: row for row in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()} if product_ids else {}
    if transaction_user_ids:
        for row in db.execute(select(User).where(User.id.in_(transaction_user_ids))).scalars().all():
            user_map[row.id] = row

    return api_response(
        data={
            "id": sales_return.id,
            "return_no": sales_return.return_no,
            "sales_order_id": sales_return.sales_order_id,
            "sales_order_no": order.order_no if order else None,
            "requested_by_name": user_map[sales_return.requested_by_id].full_name if sales_return.requested_by_id in user_map else None,
            "approved_by_name": user_map[sales_return.approved_by_id].full_name if sales_return.approved_by_id in user_map else None,
            "return_type": sales_return.return_type,
            "return_type_label": return_type_label(sales_return.return_type),
            "reason": clean_display_text(sales_return.reason, "销售退换货申请"),
            "status": sales_return.status,
            "status_label": status_label(sales_return.status),
            "inventory_request_id": inventory_request.id if inventory_request else None,
            "inventory_request_no": inventory_request.request_no if inventory_request else None,
            "inventory_request_status": inventory_request.status if inventory_request else None,
            "inventory_request_status_label": status_label(inventory_request.status) if inventory_request else None,
            "refund_amount": float(sales_return.refund_amount),
            "requires_reinspection": sales_return.requires_reinspection,
            "reinspection_status": sales_return.reinspection_status,
            "reinspection_status_label": status_label(sales_return.reinspection_status),
            "refund_records": [
                {
                    "id": row.id,
                    "amount": float(row.amount),
                    "remark": row.remark,
                    "processed_by_name": user_map[row.processed_by_id].full_name if row.processed_by_id in user_map else None,
                    "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
                }
                for row in refund_records
            ],
            "review_note": clean_display_text(sales_return.review_note, "") if sales_return.review_note else "",
            "approved_at": sales_return.approved_at.isoformat() if sales_return.approved_at else None,
            "created_at": sales_return.created_at.isoformat() if sales_return.created_at else None,
            "items": [
                {
                    "id": item.id,
                    "sales_order_item_id": item.sales_order_item_id,
                    "product_id": order_items[item.sales_order_item_id].product_id if item.sales_order_item_id in order_items else None,
                    "product_name": order_items[item.sales_order_item_id].product.name if item.sales_order_item_id in order_items and order_items[item.sales_order_item_id].product else None,
                    "quantity": float(item.quantity),
                    "batch_suffix": item.batch_suffix,
                }
                for item in return_items
            ],
            "inventory_transactions": [
                {
                    "id": row.id,
                    "transaction_no": row.transaction_no,
                    "transaction_type": row.transaction_type,
                    "warehouse_name": warehouse_map[row.warehouse_id].name if row.warehouse_id in warehouse_map else None,
                    "product_name": product_map[row.product_id].name if row.product_id in product_map else None,
                    "quantity": float(row.quantity),
                    "unit_cost": float(row.unit_cost),
                    "total_cost": float(row.total_cost),
                    "batch_no": row.batch_no,
                    "operator_name": user_map[row.created_by_id].full_name if row.created_by_id in user_map else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in inventory_transactions
            ] or [
                {
                    "id": None,
                    "transaction_no": inventory_request.request_no if inventory_request else None,
                    "transaction_type": "sales_return_inbound",
                    "warehouse_name": None,
                    "product_name": product_map[item.get("product_id")].name if item.get("product_id") in product_map else None,
                    "quantity": float(item.get("quantity") or 0),
                    "unit_cost": float(item.get("unit_cost") or 0),
                    "total_cost": float(Decimal(item.get("quantity") or 0) * Decimal(item.get("unit_cost") or 0)),
                    "batch_no": item.get("batch_no"),
                    "operator_name": None,
                    "created_at": inventory_request.created_at.isoformat() if inventory_request and inventory_request.created_at else None,
                }
                for item in ((inventory_request.handled_items_json or inventory_request.original_items_json or []) if inventory_request else [])
            ],
        }
    )


@router.post("/returns")
def create_return(payload: SalesReturnCreate, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    return create_return_service(payload, current_user, db)


@router.post("/returns/{return_id}/approve")
def approve_return(
    return_id: int,
    payload: SalesReturnApprovalRequest,
    current_user: User = Depends(require_roles("manager", "chairman")),
    db: Session = Depends(get_db),
):
    return approve_return_service(return_id, payload, current_user, db)
