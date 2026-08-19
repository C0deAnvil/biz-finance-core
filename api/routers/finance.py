import re
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import (
    CashFlowRecord,
    Inventory,
    InventoryDispatchQueue,
    PayableRecord,
    PaymentMethodOption,
    ProductionTask,
    PurchasePaymentRequest,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
    QualityCostRecord,
    QualityDisposition,
    QualityTask,
    ReceivableRecord,
    RefundRecord,
    Role,
    SalesOrder,
    SalesOrderApproval,
    SalesOrderItem,
    SalesReceiptRequest,
    SalesReturn,
    Attachment,
    User,
)
from responses import api_response
from schemas.finance import (
    FinanceDirectSalesReceiptRegisterRequest,
    FinanceMergedPurchasePaymentRegisterRequest,
    FinanceMergedPurchasePaymentRequestRegisterRequest,
    FinanceMergedSalesReceiptRequestRegisterRequest,
    FinancePurchasePaymentRegisterRequest,
    FinanceSalesReceiptRegisterRequest,
    PaymentMethodOptionCreate,
    QualityCostRecognize,
    RefundRecordCreate,
    SalesCostUpdate,
)
from services.core import complete_todos, create_cash_flow, create_notification, create_todo, write_operation_log
from services.finance_service import create_refund_record as create_refund_record_service, update_sales_cost as update_sales_cost_service, recognize_quality_cost as recognize_quality_cost_service
from services.purchase_service import (
    register_merged_purchase_payment as register_merged_purchase_payment_service,
    register_purchase_payment_requests_batch as register_purchase_payment_requests_batch_service,
    register_purchase_payment_request as register_purchase_payment_request_service,
)
from schemas.purchases import PurchasePaymentApprovalRequest
from services.display import format_legacy_remark, payment_stage_label, payment_type_label, return_type_label, status_label
from services.time_utils import now_local


router = APIRouter(prefix="/finance", tags=["finance"])
FINANCE_READ_ROLES = ("finance", "manager", "chairman", "system_admin")
FINANCE_POSTING_ROLES = ("finance", "system_admin")
PAYMENT_RECORDED_STATUSES = {"paid", "partial_settled", "settled"}
RECEIPT_MERGE_NO_PATTERN = re.compile(r"合并收款批次[:：]\s*(?P<merge_no>[A-Za-z0-9-]+)")


def _recalculate_sales_profit(order: SalesOrder):
    order.profit_amount = (
        Decimal(order.total_amount or 0)
        - Decimal(order.cost_amount or 0)
        - Decimal(order.transport_fee or 0)
        - Decimal(order.packaging_fee or 0)
    )


def _role_user_ids(db: Session, *role_codes: str, exclude_user_id: int | None = None) -> list[int]:
    user_ids = [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]
    if exclude_user_id is not None:
        user_ids = [user_id for user_id in user_ids if user_id != exclude_user_id]
    return user_ids


def _ensure_payment_method_options(db: Session) -> None:
    defaults = {
        "receipt": ["银行转账", "现金", "承兑汇票", "微信/支付宝", "其他"],
        "payment": ["银行转账", "现金", "承兑汇票", "其他"],
    }
    existing = {
        (row.method_type, row.name)
        for row in db.execute(select(PaymentMethodOption)).scalars().all()
    }
    changed = False
    for method_type, names in defaults.items():
        for sort_order, name in enumerate(names, 1):
            if (method_type, name) in existing:
                continue
            db.add(
                PaymentMethodOption(
                    method_type=method_type,
                    name=name,
                    is_active=True,
                    sort_order=sort_order,
                )
            )
            changed = True
    if changed:
        db.flush()


def _serialize_payment_method_option(row: PaymentMethodOption) -> dict:
    return {
        "id": row.id,
        "method_type": row.method_type,
        "name": row.name,
        "is_active": row.is_active,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/payment-method-options")
def list_payment_method_options(
    method_type: str | None = Query(default=None, pattern="^(receipt|payment)$"),
    current_user: User = Depends(require_roles(*FINANCE_READ_ROLES)),
    db: Session = Depends(get_db),
):
    _ensure_payment_method_options(db)
    query = select(PaymentMethodOption).order_by(PaymentMethodOption.method_type.asc(), PaymentMethodOption.sort_order.asc(), PaymentMethodOption.id.asc())
    if method_type:
        query = query.where(PaymentMethodOption.method_type == method_type)
    rows = db.execute(query).scalars().all()
    db.commit()
    return api_response(data=[_serialize_payment_method_option(row) for row in rows])


@router.post("/payment-method-options")
def create_payment_method_option(
    payload: PaymentMethodOptionCreate,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=409, detail="收付款方式名称不能为空")
    duplicate = db.execute(
        select(PaymentMethodOption).where(PaymentMethodOption.method_type == payload.method_type, PaymentMethodOption.name == name)
    ).scalar_one_or_none()
    if duplicate:
        duplicate.is_active = payload.is_active
        duplicate.sort_order = payload.sort_order
        db.commit()
        return api_response(data=_serialize_payment_method_option(duplicate))
    row = PaymentMethodOption(
        method_type=payload.method_type,
        name=name,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
        created_by_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_payment_method_option(row))


@router.put("/payment-method-options/{option_id}")
def update_payment_method_option(
    option_id: int,
    payload: PaymentMethodOptionCreate,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.execute(select(PaymentMethodOption).where(PaymentMethodOption.id == option_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="收付款方式不存在")
    row.method_type = payload.method_type
    row.name = payload.name.strip()
    row.is_active = payload.is_active
    row.sort_order = payload.sort_order
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_payment_method_option(row))


@router.delete("/payment-method-options/{option_id}")
def delete_payment_method_option(
    option_id: int,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    row = db.execute(select(PaymentMethodOption).where(PaymentMethodOption.id == option_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="收付款方式不存在")
    row.is_active = False
    db.commit()
    return api_response(msg="收付款方式已停用")


def _purchase_paid_amount_map(rows: list[PurchaseOrder], db: Session) -> dict[int, Decimal]:
    order_ids = [row.id for row in rows]
    if not order_ids:
        return {}
    payables = db.execute(
        select(PayableRecord).where(PayableRecord.purchase_order_id.in_(order_ids)).order_by(PayableRecord.paid_at.desc(), PayableRecord.id.desc())
    ).scalars().all()
    amount_map: dict[int, Decimal] = {}
    for row in payables:
        if row.status not in PAYMENT_RECORDED_STATUSES:
            continue
        amount_map[row.purchase_order_id] = amount_map.get(row.purchase_order_id, Decimal("0")) + Decimal(row.amount or 0)
    return amount_map


def _sales_received_amount_map(rows: list[SalesOrder], db: Session) -> dict[int, Decimal]:
    order_ids = [row.id for row in rows]
    if not order_ids:
        return {}
    receivables = db.execute(
        select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids)).order_by(ReceivableRecord.received_at.desc(), ReceivableRecord.id.desc())
    ).scalars().all()
    amount_map: dict[int, Decimal] = {}
    for row in receivables:
        amount_map[row.sales_order_id] = amount_map.get(row.sales_order_id, Decimal("0")) + Decimal(row.amount or 0)
    return amount_map


def _settlement_status(total_amount: Decimal, current_amount: Decimal) -> str:
    if current_amount <= 0:
        return "unpaid"
    if current_amount >= total_amount:
        return "settled"
    return "partial_settled"


def _serialize_purchase_items(order: PurchaseOrder | None) -> list[dict]:
    if order is None:
        return []
    return [
        {
            "product_id": item.product_id,
            "material_id": item.material_id,
            "item_name": item.product.name if item.product else (item.material.name if item.material else None),
            "item_code": item.product.product_code if item.product else (item.material.material_code if item.material else None),
            "quantity": float(item.quantity or 0),
            "received_quantity": float(item.received_quantity or 0),
            "unit_price": float(item.unit_price or 0),
            "total_amount": float(item.total_amount or 0),
            "packaging_config_id": item.packaging_config_id,
            "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
            "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
            "bag_count": float(item.bag_count) if item.bag_count is not None else None,
            "display_specification": item.display_specification,
        }
        for item in order.items
    ]


def _serialize_attachments(db: Session, business_type: str, business_id: int | None) -> list[dict]:
    if not business_id:
        return []
    rows = db.execute(
        select(Attachment)
        .where(Attachment.business_type == business_type, Attachment.business_id == business_id)
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
    ).scalars().all()
    return [
        {
            "id": row.id,
            "attachment_type": row.attachment_type,
            "original_name": row.original_name,
            "relative_path": row.relative_path,
        }
        for row in rows
    ]


def _serialize_sales_items(order: SalesOrder | None) -> list[dict]:
    if order is None:
        return []
    return [
        {
            "product_id": item.product_id,
            "item_name": item.product.name if item.product else None,
            "item_code": item.product.product_code if item.product else None,
            "quantity": float(item.quantity or 0),
            "shipped_quantity": float(item.shipped_quantity or 0),
            "unit_price": float(item.unit_price or 0),
            "total_amount": float(item.total_amount or 0),
            "display_specification": item.display_specification,
        }
        for item in order.items
    ]


def _extract_receipt_merge_no(remark: str | None) -> str | None:
    text = str(remark or "").strip()
    if not text:
        return None
    match = RECEIPT_MERGE_NO_PATTERN.search(text)
    return match.group("merge_no") if match else None


def _route_sales_order_to_inventory_shipment(db: Session, order: SalesOrder, current_user: User) -> None:
    order.status = "pending_shipment"
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is None:
        queue = InventoryDispatchQueue(sales_order_id=order.id, placement_status="pending", shipment_status="pending")
        db.add(queue)
        db.flush()
    else:
        queue.shipment_status = "pending"
    inventory_user_ids = _role_user_ids(db, "inventory")
    for user_id in inventory_user_ids:
        create_todo(
            db,
            title=f"待发货客户订单 {order.order_no}",
            description="订单已满足发货条件，请库管安排出库发货。",
            module="inventory",
            assigned_user_id=user_id,
            business_type="sales_order",
            business_id=order.id,
            priority="high",
        )
    create_notification(
        db,
        title="客户订单已进入待发货",
        content=f"客户订单 {order.order_no} 已满足发货条件，已同步库管处理发货。",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *inventory_user_ids, *_role_user_ids(db, "finance", "chairman")])),
        created_by_id=current_user.id,
        category="success",
    )


def _finish_sales_receipt_finance_register(
    *,
    db: Session,
    request: SalesReceiptRequest,
    amount: Decimal,
    remark: str | None,
    payment_type: str | None,
    receipt_method: str | None,
    bank_receipt_attachment_id: int | None,
    received_at: datetime | None,
    release_to_inventory: bool,
    current_user: User,
) -> dict:
    order = (
        db.execute(
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.customer),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            )
            .where(SalesOrder.id == request.sales_order_id)
        )
        .unique()
        .scalar_one_or_none()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if request.status not in {"pending_finance_check", "pending_chairman_approval"}:
        raise HTTPException(status_code=409, detail="当前收款确认申请状态不允许财务核对")
    if amount <= 0:
        raise HTTPException(status_code=409, detail="实际入账金额必须大于 0")
    if not bank_receipt_attachment_id:
        raise HTTPException(status_code=409, detail="请先上传银行回单或回款凭证")
    already_received = _sales_received_amount_map([order], db).get(order.id, Decimal("0"))
    remaining_amount = max(Decimal(order.total_amount or 0) - already_received, Decimal("0"))
    if amount > remaining_amount:
        raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 本次入账金额不能超过待收金额")

    now = received_at or now_local()
    receipt_at = received_at or now
    if payment_type:
        order.payment_type = payment_type_label(payment_type, deposit_amount=order.deposit_amount, module="sales")
    request.actual_amount = amount
    request.status = "approved"
    request.finance_status = "checked"
    request.finance_checked_by_id = current_user.id
    request.finance_checked_at = now
    request.receipt_method = receipt_method
    request.receipt_at = receipt_at
    request.bank_receipt_attachment_id = bank_receipt_attachment_id
    complete_todos(db, business_type="sales_receipt_request", business_id=request.id)

    record = ReceivableRecord(
        sales_order_id=order.id,
        sales_receipt_request_id=request.id,
        amount=amount,
        payment_stage=request.payment_stage,
        installment_total=request.installment_total,
        installment_index=request.installment_index,
        received_at=receipt_at,
        received_by_id=current_user.id,
        remark=remark or request.remark or f"销售收款确认 {request.request_no}",
        receipt_method=receipt_method,
        bank_receipt_attachment_id=bank_receipt_attachment_id,
    )
    db.add(record)
    db.flush()
    create_cash_flow(
        db,
        flow_type="incoming",
        business_type="sales_order",
        business_id=order.id,
        related_no=order.order_no,
        amount=amount,
        happened_at=receipt_at,
        remark=remark or request.remark or f"销售收款确认 {request.request_no}",
    )

    received_after = already_received + amount
    normalized_payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    should_release_shipment = False
    if release_to_inventory and order.status in {"pending_confirmation", "receipt_abnormal"}:
        if normalized_payment_type == "先付定金后付尾款":
            should_release_shipment = Decimal(order.deposit_amount or 0) <= 0 or received_after >= Decimal(order.deposit_amount or 0)
        elif normalized_payment_type == "全款发货":
            should_release_shipment = received_after >= Decimal(order.total_amount or 0)
    if should_release_shipment:
        order.approved_by_id = current_user.id
        order.approved_at = now
        order.rejection_reason = None
        _route_sales_order_to_inventory_shipment(db, order, current_user)
        complete_todos(db, business_type="sales_order_approval", business_id=order.id)

    create_notification(
        db,
        title="客户回款已核对入账",
        content=(
            f"客户订单 {order.order_no} 的收款确认 {request.request_no} 已由财务核对入账，"
            f"本次金额 {float(amount)}，累计回款 {float(received_after)} / {float(order.total_amount or 0)}。"
            f"{' 订单已同步库管进入待发货。' if should_release_shipment else ''}"
        ),
        module="finance",
        recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *_role_user_ids(db, "chairman")])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_sales_receipt_request",
        content=f"财务核对客户订单 {order.order_no} 的收款确认 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "record_id": record.id,
            "actual_amount": float(amount),
            "status": request.status,
            "released_shipment": should_release_shipment,
        },
    )
    return {"request_id": request.id, "record_id": record.id, "status": request.status, "released_shipment": should_release_shipment}


def _serialize_quality_cost_rows(rows: list[QualityCostRecord], db: Session) -> list[dict]:
    disposition_map = {row.quality_disposition_id: row.disposition for row in rows if row.disposition}
    quality_task_ids = [item.quality_task_id for item in disposition_map.values() if item.quality_task_id]
    related_task_ids = [item.related_production_task_id for item in disposition_map.values() if item.related_production_task_id]

    quality_tasks = db.execute(select(QualityTask).where(QualityTask.id.in_(quality_task_ids))).scalars().all() if quality_task_ids else []
    quality_task_map = {item.id: item for item in quality_tasks}

    source_task_ids = [item.production_task_id for item in quality_tasks if item.production_task_id]
    all_task_ids = {task_id for task_id in source_task_ids + related_task_ids if task_id}
    production_tasks = (
        db.execute(
            select(ProductionTask)
            .options(joinedload(ProductionTask.product))
            .where(ProductionTask.id.in_(all_task_ids))
        )
        .unique()
        .scalars()
        .all()
        if all_task_ids
        else []
    )
    production_task_map = {item.id: item for item in production_tasks}

    data = []
    for row in rows:
        disposition = row.disposition
        quality_task = quality_task_map.get(disposition.quality_task_id) if disposition else None
        source_task = production_task_map.get(quality_task.production_task_id) if quality_task else None
        related_task = production_task_map.get(disposition.related_production_task_id) if disposition and disposition.related_production_task_id else None
        recognized_amount = Decimal(row.recognized_amount or 0)
        estimated_amount = Decimal(row.estimated_amount or 0)
        data.append(
            {
                "id": row.id,
                "quality_disposition_id": row.quality_disposition_id,
                "quality_task_id": quality_task.id if quality_task else None,
                "quality_no": quality_task.quality_no if quality_task else None,
                "batch_no": quality_task.batch_no if quality_task else None,
                "source_production_task_id": source_task.id if source_task else None,
                "source_production_task_no": source_task.task_no if source_task else None,
                "product_name": source_task.product.name if source_task and source_task.product else None,
                "disposition_type": disposition.disposition_type if disposition else None,
                "disposition_quantity": float(disposition.quantity) if disposition else 0,
                "reason": disposition.reason if disposition else None,
                "related_production_task_id": related_task.id if related_task else None,
                "related_production_task_no": related_task.task_no if related_task else None,
                "estimated_amount": float(estimated_amount),
                "recognized_amount": float(recognized_amount),
                "variance_amount": float(recognized_amount - estimated_amount),
                "status": row.status,
                "status_label": status_label(row.status),
                "remark": row.remark,
                "remark_display": format_legacy_remark(row.remark),
                "recognized_by_id": row.recognized_by_id,
                "recognized_by_name": row.recognized_by.full_name if row.recognized_by else None,
                "recognized_at": row.recognized_at.isoformat() if row.recognized_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return data


@router.get("/receivables")
def list_receivable_records(current_user: User = Depends(require_roles(*FINANCE_READ_ROLES)), db: Session = Depends(get_db)):
    records = db.execute(select(ReceivableRecord).order_by(ReceivableRecord.received_at.desc(), ReceivableRecord.id.desc())).scalars().all()
    receipt_requests = db.execute(
        select(SalesReceiptRequest).order_by(SalesReceiptRequest.created_at.desc(), SalesReceiptRequest.id.desc())
    ).scalars().all()
    record_order_ids = [row.sales_order_id for row in records]
    request_order_ids = [row.sales_order_id for row in receipt_requests]
    user_ids = [row.received_by_id for row in records if row.received_by_id]
    user_ids.extend([row.requested_by_id for row in receipt_requests if row.requested_by_id])
    user_ids.extend([row.approved_by_id for row in receipt_requests if row.approved_by_id])
    order_map = {
        row.id: row
        for row in db.execute(
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.customer),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            )
            .where(SalesOrder.id.in_(list({*record_order_ids, *request_order_ids})))
        ).unique().scalars().all()
    } if (record_order_ids or request_order_ids) else {}
    user_map = {row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()} if user_ids else {}

    orders = (
        db.execute(
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.customer),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            )
            .where(SalesOrder.status.notin_(["cancelled"]))
            .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        )
        .unique()
        .scalars()
        .all()
    )
    received_amount_map = _sales_received_amount_map(orders, db)
    pending_orders = []
    for row in orders:
        total_amount = Decimal(row.total_amount or 0)
        received_amount = received_amount_map.get(row.id, Decimal("0"))
        remaining_amount = max(total_amount - received_amount, Decimal("0"))
        settlement_status = _settlement_status(total_amount, received_amount)
        if settlement_status == "settled":
            continue
        pending_orders.append(
            {
                "id": row.id,
                "order_no": row.order_no,
                "customer_id": row.customer_id,
                "customer_name": row.customer.name if row.customer else None,
                "status": row.status,
                "status_label": status_label(row.status),
                "payment_type": row.payment_type,
                "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
                "total_amount": float(total_amount),
                "deposit_amount": float(row.deposit_amount or 0),
                "received_amount": float(received_amount),
                "remaining_amount": float(remaining_amount),
                "settlement_status": settlement_status,
                "settlement_status_label": status_label(settlement_status),
                "item_summary": "、".join(dict.fromkeys(item.product.name for item in row.items if item.product and item.product.name)),
                "item_count": len(row.items),
                "items": _serialize_sales_items(row),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return api_response(
        data={
            "summary": {
                "pending_order_count": len(pending_orders),
                "record_count": len(records),
                "receipt_request_count": len(receipt_requests),
            },
            "pending_orders": pending_orders,
            "receipt_requests": [
                {
                    "id": row.id,
                    "request_no": row.request_no,
                    "sales_order_id": row.sales_order_id,
                    "sales_order_no": order_map[row.sales_order_id].order_no if row.sales_order_id in order_map else None,
                    "customer_name": order_map[row.sales_order_id].customer.name if row.sales_order_id in order_map and order_map[row.sales_order_id].customer else None,
                    "payment_type": order_map[row.sales_order_id].payment_type if row.sales_order_id in order_map else None,
                    "payment_type_label": (
                        payment_type_label(
                            order_map[row.sales_order_id].payment_type,
                            deposit_amount=order_map[row.sales_order_id].deposit_amount,
                            module="sales",
                        )
                        if row.sales_order_id in order_map
                        else None
                    ),
                    "requested_amount": float(row.requested_amount or 0),
                    "actual_amount": float(row.actual_amount or 0),
                    "payment_stage": row.payment_stage,
                    "payment_stage_label": payment_stage_label(row.payment_stage),
                    "merge_no": _extract_receipt_merge_no(row.remark),
                    "installment_total": row.installment_total,
                    "installment_index": row.installment_index,
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="receivable"),
                    "approval_remark": row.approval_remark,
                    "requested_by_name": user_map[row.requested_by_id].full_name if row.requested_by_id in user_map else None,
                    "approved_by_name": user_map[row.approved_by_id].full_name if row.approved_by_id in user_map else None,
                    "items": _serialize_sales_items(order_map.get(row.sales_order_id)),
                    "attachments": _serialize_attachments(db, "sales_order", row.sales_order_id),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in receipt_requests
            ],
            "records": [
                {
                    "id": row.id,
                    "sales_order_id": row.sales_order_id,
                    "sales_order_no": order_map[row.sales_order_id].order_no if row.sales_order_id in order_map else None,
                    "amount": float(row.amount),
                    "received_at": row.received_at.isoformat() if row.received_at else None,
                    "receipt_method": row.receipt_method,
                    "bank_receipt_attachment_id": row.bank_receipt_attachment_id,
                    "received_by_name": user_map[row.received_by_id].full_name if row.received_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="receivable"),
                }
                for row in records
            ],
        }
    )


@router.post("/receivables/receipt-requests/{request_id}/register")
def register_sales_receipt_request(
    request_id: int,
    payload: FinanceSalesReceiptRegisterRequest,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    if payload.sales_receipt_request_id != request_id:
        raise HTTPException(status_code=409, detail="收款确认单号不一致")
    request = db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.id == request_id)).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="收款确认申请不存在")
    result = _finish_sales_receipt_finance_register(
        db=db,
        request=request,
        amount=Decimal(payload.actual_amount or 0),
        remark=payload.remark,
        payment_type=payload.payment_type,
        receipt_method=payload.receipt_method,
        bank_receipt_attachment_id=payload.bank_receipt_attachment_id,
        received_at=payload.received_at,
        release_to_inventory=payload.release_to_inventory,
        current_user=current_user,
    )
    db.commit()
    return api_response(data=result)


@router.post("/receivables/receipt-requests/merged-register")
def register_merged_sales_receipt_requests(
    payload: FinanceMergedSalesReceiptRequestRegisterRequest,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一条收款确认申请")
    request_ids = [item.sales_receipt_request_id for item in payload.items]
    if len(set(request_ids)) != len(request_ids):
        raise HTTPException(status_code=409, detail="存在重复的收款确认申请")
    requests = db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.id.in_(request_ids))).scalars().all()
    if len(requests) != len(request_ids):
        raise HTTPException(status_code=404, detail="存在不存在的收款确认申请")
    request_map = {row.id: row for row in requests}
    amount_map = {item.sales_receipt_request_id: Decimal(item.actual_amount or 0) for item in payload.items}
    results = []
    for request_id in request_ids:
        result = _finish_sales_receipt_finance_register(
            db=db,
            request=request_map[request_id],
            amount=amount_map[request_id],
            remark=payload.remark,
            payment_type=payload.payment_type,
            receipt_method=payload.receipt_method,
            bank_receipt_attachment_id=payload.bank_receipt_attachment_id,
            received_at=payload.received_at,
            release_to_inventory=payload.release_to_inventory,
            current_user=current_user,
        )
        results.append({"sales_receipt_request_id": request_id, **result})
    db.commit()
    return api_response(data={"results": results})


@router.get("/payables")
def list_payables(current_user: User = Depends(require_roles(*FINANCE_READ_ROLES)), db: Session = Depends(get_db)):
    records = db.execute(select(PayableRecord).order_by(PayableRecord.paid_at.desc(), PayableRecord.id.desc())).scalars().all()
    payment_requests = db.execute(
        select(PurchasePaymentRequest).order_by(PurchasePaymentRequest.created_at.desc(), PurchasePaymentRequest.id.desc())
    ).scalars().all()
    record_order_ids = [row.purchase_order_id for row in records]
    request_order_ids = [row.purchase_order_id for row in payment_requests]
    user_ids = [row.paid_by_id for row in records if row.paid_by_id]
    user_ids.extend([row.requested_by_id for row in payment_requests if row.requested_by_id])
    user_ids.extend([row.approved_by_id for row in payment_requests if row.approved_by_id])
    user_ids.extend([row.completed_by_id for row in payment_requests if row.completed_by_id])
    order_map = {
        row.id: row
        for row in db.execute(
            select(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.supplier),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            )
            .where(PurchaseOrder.id.in_(list({*record_order_ids, *request_order_ids})))
        ).unique().scalars().all()
    } if (record_order_ids or request_order_ids) else {}
    user_map = {row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()} if user_ids else {}

    orders = (
        db.execute(
            select(PurchaseOrder)
            .options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product), joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material))
            .where(PurchaseOrder.status.notin_(["draft", "cancelled", "rejected"]))
            .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
        )
        .unique()
        .scalars()
        .all()
    )
    paid_amount_map = _purchase_paid_amount_map(orders, db)
    pending_orders = []
    for row in orders:
        total_amount = Decimal(row.total_amount or 0)
        paid_amount = paid_amount_map.get(row.id, Decimal("0"))
        remaining_amount = max(total_amount - paid_amount, Decimal("0"))
        settlement_status = _settlement_status(total_amount, paid_amount)
        if settlement_status == "settled":
            continue
        pending_orders.append(
            {
                "id": row.id,
                "order_no": row.order_no,
                "supplier_id": row.supplier_id,
                "supplier_name": row.supplier.name if row.supplier else None,
                "status": row.status,
                "status_label": status_label(row.status),
                "payment_type": row.payment_type,
                "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
                "total_amount": float(total_amount),
                "paid_amount": float(paid_amount),
                "remaining_amount": float(remaining_amount),
                "settlement_status": settlement_status,
                "settlement_status_label": status_label(settlement_status),
                "item_summary": "；".join(
                    dict.fromkeys(
                        (item.product.name if item.product else (item.material.name if item.material else None))
                        for item in row.items
                        if (item.product and item.product.name) or (item.material and item.material.name)
                    )
                ),
                "item_count": len(row.items),
                "items": _serialize_purchase_items(row),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return api_response(
        data={
            "summary": {
                "pending_order_count": len(pending_orders),
                "record_count": len(records),
                "payment_request_count": len(payment_requests),
            },
            "pending_orders": pending_orders,
            "payment_requests": [
                {
                    "id": row.id,
                    "merge_no": row.merge_no,
                    "request_no": row.request_no,
                    "purchase_order_id": row.purchase_order_id,
                    "purchase_order_no": order_map[row.purchase_order_id].order_no if row.purchase_order_id in order_map else None,
                    "supplier_name": order_map[row.purchase_order_id].supplier.name if row.purchase_order_id in order_map and order_map[row.purchase_order_id].supplier else None,
                    "requested_amount": float(row.requested_amount or 0),
                    "order_amount": float(row.order_amount or 0),
                    "other_fee_amount": float(row.other_fee_amount or 0),
                    "actual_amount": float(row.actual_amount or 0),
                    "arrival_status": row.arrival_status,
                    "arrival_status_label": status_label(row.arrival_status),
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "payment_detail_remark": row.payment_detail_remark,
                    "payment_detail_remark_display": format_legacy_remark(row.payment_detail_remark, business_type="payable"),
                    "manager_remark": row.manager_remark,
                    "completion_remark": row.completion_remark,
                    "requested_by_name": user_map[row.requested_by_id].full_name if row.requested_by_id in user_map else None,
                    "approved_by_name": user_map[row.approved_by_id].full_name if row.approved_by_id in user_map else None,
                    "completed_by_name": user_map[row.completed_by_id].full_name if row.completed_by_id in user_map else None,
                    "items": _serialize_purchase_items(order_map.get(row.purchase_order_id)),
                    "attachments": _serialize_attachments(db, "purchase_order", row.purchase_order_id),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
                for row in payment_requests
            ],
            "records": [
                {
                    "id": row.id,
                    "merge_no": row.merge_no,
                    "purchase_order_id": row.purchase_order_id,
                    "purchase_order_no": order_map[row.purchase_order_id].order_no if row.purchase_order_id in order_map else None,
                    "payment_type": order_map[row.purchase_order_id].payment_type if row.purchase_order_id in order_map else None,
                    "payment_type_label": payment_type_label(order_map[row.purchase_order_id].payment_type, module="purchase") if row.purchase_order_id in order_map else None,
                    "purchase_receipt_id": row.purchase_receipt_id,
                    "amount": float(row.amount),
                    "paid_at": row.paid_at.isoformat() if row.paid_at else None,
                    "payment_method": row.payment_method,
                    "bank_receipt_attachment_id": row.bank_receipt_attachment_id,
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "paid_by_name": user_map[row.paid_by_id].full_name if row.paid_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="payable"),
                }
                for row in records
            ],
        }
    )


@router.post("/payables/payment-requests/{request_id}/register")
def register_purchase_payment_request(
    request_id: int,
    payload: FinancePurchasePaymentRegisterRequest,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    if payload.purchase_payment_request_id != request_id:
        raise HTTPException(status_code=409, detail="付款申请单号不一致")
    response = register_purchase_payment_request_service(
        request_id,
        PurchasePaymentApprovalRequest(
            decision="approved",
            actual_amount=payload.actual_amount,
            manager_remark=payload.remark,
            payment_method=payload.payment_method,
            bank_receipt_attachment_id=payload.bank_receipt_attachment_id,
            paid_at=payload.paid_at,
        ),
        current_user,
        db,
    )
    record = db.execute(
        select(PayableRecord)
        .where(PayableRecord.purchase_payment_request_id == request_id)
        .order_by(PayableRecord.id.desc())
    ).scalar_one_or_none()
    if record is not None:
        record.payment_method = payload.payment_method
        record.bank_receipt_attachment_id = payload.bank_receipt_attachment_id
        if payload.paid_at:
            record.paid_at = payload.paid_at
        db.commit()
    return response


@router.post("/payables/payment-requests/merged-register")
def register_merged_purchase_payment_requests(
    payload: FinanceMergedPurchasePaymentRequestRegisterRequest,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    response = register_purchase_payment_requests_batch_service(payload, current_user, db)
    request_ids = [item.purchase_payment_request_id for item in payload.items]
    if request_ids:
        records = db.execute(
            select(PayableRecord)
            .where(PayableRecord.purchase_payment_request_id.in_(request_ids))
            .order_by(PayableRecord.id.desc())
        ).scalars().all()
        for record in records:
            record.payment_method = payload.payment_method
            record.bank_receipt_attachment_id = payload.bank_receipt_attachment_id
            if payload.paid_at:
                record.paid_at = payload.paid_at
        db.commit()
    return response


@router.post("/payables/merged-register")
def register_merged_purchase_payment(
    payload: FinanceMergedPurchasePaymentRegisterRequest,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    response = register_merged_purchase_payment_service(payload, current_user, db)
    order_ids = [item.purchase_order_id for item in payload.items]
    if order_ids:
        records = db.execute(
            select(PayableRecord)
            .where(PayableRecord.purchase_order_id.in_(order_ids))
            .order_by(PayableRecord.id.desc())
        ).scalars().all()
        for record in records:
            if payload.payment_method:
                record.payment_method = payload.payment_method
            if payload.bank_receipt_attachment_id:
                record.bank_receipt_attachment_id = payload.bank_receipt_attachment_id
            if payload.paid_at:
                record.paid_at = payload.paid_at
        db.commit()
    return response


@router.post("/receivables/direct-register")
def direct_register_receivables(
    payload: FinanceDirectSalesReceiptRegisterRequest,
    current_user: User = Depends(require_roles(*FINANCE_POSTING_ROLES)),
    db: Session = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一张客户订单")
    if not payload.bank_receipt_attachment_id:
        raise HTTPException(status_code=409, detail="请先上传银行回单或回款凭证")
    order_ids = [item.sales_order_id for item in payload.items]
    if len(set(order_ids)) != len(order_ids):
        raise HTTPException(status_code=409, detail="存在重复的客户订单")
    orders = (
        db.execute(
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.customer),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            )
            .where(SalesOrder.id.in_(order_ids))
        )
        .unique()
        .scalars()
        .all()
    )
    if len(orders) != len(order_ids):
        raise HTTPException(status_code=404, detail="存在不存在的客户订单")
    order_map = {row.id: row for row in orders}
    customer_ids = {row.customer_id for row in orders}
    if payload.customer_id is not None:
        customer_ids.add(payload.customer_id)
    if len({customer_id for customer_id in customer_ids if customer_id is not None}) != 1:
        raise HTTPException(status_code=409, detail="仅支持同一客户的客户订单合并登记回款")

    amount_map = {item.sales_order_id: Decimal(item.amount or 0) for item in payload.items}
    created_record_ids: list[int] = []
    released_order_ids: list[int] = []
    total_amount = Decimal("0")
    now = now_local()

    for order_id in order_ids:
        order = order_map[order_id]
        amount = amount_map[order_id]
        if amount <= 0:
            raise HTTPException(status_code=409, detail="回款金额必须大于 0")
        already_received = _sales_received_amount_map([order], db).get(order.id, Decimal("0"))
        remaining_amount = max(Decimal(order.total_amount or 0) - already_received, Decimal("0"))
        if amount > remaining_amount:
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 本次回款金额不能超过待收金额")

    for order_id in order_ids:
        order = order_map[order_id]
        amount = amount_map[order_id]
        received_before = _sales_received_amount_map([order], db).get(order.id, Decimal("0"))
        record = ReceivableRecord(
            sales_order_id=order.id,
            amount=amount,
            payment_stage=payload.payment_stage,
            received_at=now,
            received_by_id=current_user.id,
            remark=payload.remark or "应收管理直接登记回款",
            receipt_method=payload.receipt_method,
            bank_receipt_attachment_id=payload.bank_receipt_attachment_id,
        )
        db.add(record)
        db.flush()
        created_record_ids.append(record.id)
        total_amount += amount
        create_cash_flow(
            db,
            flow_type="incoming",
            business_type="sales_order",
            business_id=order.id,
            related_no=order.order_no,
            amount=amount,
            happened_at=now,
            remark=payload.remark or "应收管理直接登记回款",
        )
        received_after = received_before + amount
        payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
        should_release_shipment = False
        if payload.release_to_inventory and order.status in {"pending_confirmation", "receipt_abnormal"}:
            if payment_type == "先付定金后付尾款":
                should_release_shipment = Decimal(order.deposit_amount or 0) <= 0 or received_after >= Decimal(order.deposit_amount or 0)
            elif payment_type == "全款发货":
                should_release_shipment = received_after >= Decimal(order.total_amount or 0)
        if should_release_shipment:
            order.approved_by_id = current_user.id
            order.approved_at = now
            order.rejection_reason = None
            _route_sales_order_to_inventory_shipment(db, order, current_user)
            complete_todos(db, business_type="sales_order_approval", business_id=order.id)
            released_order_ids.append(order.id)
        create_notification(
            db,
            title="客户订单回款已登记",
            content=(
                f"客户订单 {order.order_no} 已登记回款 {float(amount)}，"
                f"累计回款 {float(received_after)} / {float(order.total_amount or 0)}。"
                f"{' 订单已同步库管进入待发货。' if should_release_shipment else ''}"
            ),
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *_role_user_ids(db, "finance")])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="direct_register_receivables",
        content="应收管理直接登记回款",
        after_data={
            "order_ids": order_ids,
            "record_ids": created_record_ids,
            "released_order_ids": released_order_ids,
            "total_amount": float(total_amount),
        },
    )
    db.commit()
    return api_response(
        data={
            "record_ids": created_record_ids,
            "released_order_ids": released_order_ids,
            "total_amount": float(total_amount),
        }
    )


@router.get("/refunds")
def list_refunds(current_user: User = Depends(require_roles(*FINANCE_READ_ROLES)), db: Session = Depends(get_db)):
    rows = db.execute(select(RefundRecord).order_by(RefundRecord.refunded_at.desc())).scalars().all()
    sales_return_ids = [row.sales_return_id for row in rows if row.sales_return_id]
    purchase_return_ids = [row.purchase_return_id for row in rows if row.purchase_return_id]
    user_ids = [row.processed_by_id for row in rows if row.processed_by_id]
    sales_return_map = {row.id: row for row in db.execute(select(SalesReturn).where(SalesReturn.id.in_(sales_return_ids))).scalars().all()} if sales_return_ids else {}
    purchase_return_map = {row.id: row for row in db.execute(select(PurchaseReturn).where(PurchaseReturn.id.in_(purchase_return_ids))).scalars().all()} if purchase_return_ids else {}
    user_map = {row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()} if user_ids else {}
    return api_response(
        data=[
            {
                "id": row.id,
                "business_type": row.business_type,
                "sales_return_id": row.sales_return_id,
                "sales_return_no": sales_return_map[row.sales_return_id].return_no if row.sales_return_id in sales_return_map else None,
                "purchase_return_id": row.purchase_return_id,
                "purchase_return_no": purchase_return_map[row.purchase_return_id].return_no if row.purchase_return_id in purchase_return_map else None,
                "return_type_label": (
                    return_type_label(sales_return_map[row.sales_return_id].return_type)
                    if row.sales_return_id in sales_return_map
                    else (
                        return_type_label(purchase_return_map[row.purchase_return_id].return_type)
                        if row.purchase_return_id in purchase_return_map
                        else None
                    )
                ),
                "amount": float(row.amount),
                "refunded_at": row.refunded_at.isoformat(),
                "processed_by_name": user_map[row.processed_by_id].full_name if row.processed_by_id in user_map else None,
                "remark": row.remark,
                "remark_display": format_legacy_remark(row.remark),
            }
            for row in rows
        ]
    )


@router.post("/refunds")
def create_refund_record(
    payload: RefundRecordCreate,
    current_user: User = Depends(require_roles("finance")),
    db: Session = Depends(get_db),
):
    return create_refund_record_service(payload, current_user, db)


@router.post("/sales-orders/{order_id}/cost")
def update_sales_cost(
    order_id: int,
    payload: SalesCostUpdate,
    current_user: User = Depends(require_roles("finance")),
    db: Session = Depends(get_db),
):
    return update_sales_cost_service(order_id, payload, current_user, db)


@router.get("/quality-costs")
def list_quality_cost_records(
    current_user: User = Depends(require_roles("finance", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(
            select(QualityCostRecord)
            .options(joinedload(QualityCostRecord.disposition), joinedload(QualityCostRecord.recognized_by))
            .order_by(QualityCostRecord.created_at.desc())
        )
        .unique()
        .scalars()
        .all()
    )
    return api_response(data=_serialize_quality_cost_rows(rows, db))


@router.post("/quality-costs/{record_id}/recognize")
def recognize_quality_cost(
    record_id: int,
    payload: QualityCostRecognize,
    current_user: User = Depends(require_roles("finance")),
    db: Session = Depends(get_db),
):
    return recognize_quality_cost_service(record_id, payload, current_user, db)


@router.get("/cash-flows")
def list_cash_flows(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(require_roles(*FINANCE_READ_ROLES)),
    db: Session = Depends(get_db),
):
    query = select(CashFlowRecord).order_by(CashFlowRecord.happened_at.desc())
    if date_from is not None:
        query = query.where(CashFlowRecord.happened_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to is not None:
        query = query.where(CashFlowRecord.happened_at <= datetime.combine(date_to, datetime.max.time()))
    rows = db.execute(query).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "record_no": row.record_no,
                "flow_type": row.flow_type,
                "business_type": row.business_type,
                "business_id": row.business_id,
                "related_no": row.related_no,
                "amount": float(row.amount),
                "happened_at": row.happened_at.isoformat(),
                "remark": row.remark,
            }
            for row in rows
        ]
    )


@router.get("/reports/summary")
def report_summary(
    current_user: User = Depends(require_roles(*FINANCE_READ_ROLES)),
    db: Session = Depends(get_db),
):
    today = date.today()
    total_sales = db.execute(select(func.coalesce(func.sum(SalesOrder.total_amount), 0))).scalar_one()
    total_profit = db.execute(select(func.coalesce(func.sum(SalesOrder.profit_amount), 0))).scalar_one()
    total_purchase = db.execute(select(func.coalesce(func.sum(PurchaseOrder.total_amount), 0))).scalar_one()
    incoming = db.execute(select(func.coalesce(func.sum(CashFlowRecord.amount), 0)).where(CashFlowRecord.flow_type == "incoming")).scalar_one()
    outgoing = db.execute(select(func.coalesce(func.sum(CashFlowRecord.amount), 0)).where(CashFlowRecord.flow_type == "outgoing")).scalar_one()
    pending_quality_cost = db.execute(
        select(func.coalesce(func.sum(QualityCostRecord.estimated_amount), 0)).where(QualityCostRecord.status == "pending")
    ).scalar_one()
    recognized_quality_cost = db.execute(
        select(func.coalesce(func.sum(QualityCostRecord.recognized_amount), 0)).where(QualityCostRecord.status == "recognized")
    ).scalar_one()
    scrap_quality_cost = db.execute(
        select(func.coalesce(func.sum(QualityCostRecord.estimated_amount), 0))
        .join(QualityDisposition, QualityCostRecord.quality_disposition_id == QualityDisposition.id)
        .where(QualityDisposition.disposition_type == "scrap")
    ).scalar_one()
    rework_quality_cost = db.execute(
        select(func.coalesce(func.sum(QualityCostRecord.estimated_amount), 0))
        .join(QualityDisposition, QualityCostRecord.quality_disposition_id == QualityDisposition.id)
        .where(QualityDisposition.disposition_type == "rework")
    ).scalar_one()
    return api_response(
        data={
            "generated_at": today.isoformat(),
            "sales_total": float(total_sales or 0),
            "profit_total": float(total_profit or 0),
            "purchase_total": float(total_purchase or 0),
            "incoming_total": float(incoming or 0),
            "outgoing_total": float(outgoing or 0),
            "pending_quality_cost_total": float(pending_quality_cost or 0),
            "recognized_quality_cost_total": float(recognized_quality_cost or 0),
            "scrap_quality_cost_total": float(scrap_quality_cost or 0),
            "rework_quality_cost_total": float(rework_quality_cost or 0),
        }
    )


def _first_report_datetime(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _in_date_range(value: datetime | date | None, date_from: date | None, date_to: date | None) -> bool:
    if value is None:
        return date_from is None and date_to is None
    target_date = value if isinstance(value, date) and not isinstance(value, datetime) else value.date()
    if date_from and target_date < date_from:
        return False
    if date_to and target_date > date_to:
        return False
    return True


def _match_keyword(keyword: str | None, *values) -> bool:
    normalized = str(keyword or '').strip().lower()
    if not normalized:
        return True
    return any(normalized in str(value).lower() for value in values if value not in {None, ''})


def _build_sales_profit_report_rows(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    customer_id: int | None = None,
    product_id: int | None = None,
    keyword: str | None = None,
) -> list[dict]:
    orders = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.sales_user),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        )
        .order_by(SalesOrder.created_at.desc())
    ).unique().scalars().all()
    data = []
    for row in orders:
        if customer_id is not None and row.customer_id != customer_id:
            continue
        matched_items = [item for item in row.items if product_id is None or item.product_id == product_id]
        if product_id is not None and not matched_items:
            continue
        report_at = _first_report_datetime(row.completed_at, row.shipped_at, row.approved_at, row.created_at)
        if not _in_date_range(report_at, date_from, date_to):
            continue
        display_items = matched_items or list(row.items)
        product_names = '、'.join(
            dict.fromkeys(item.product.name if item.product else str(item.product_id) for item in display_items)
        )
        total_quantity = sum(Decimal(item.quantity or 0) for item in display_items)
        if not _match_keyword(keyword, row.order_no, row.customer.name if row.customer else None, product_names, row.payment_type, row.status):
            continue
        data.append(
            {
                'id': row.id,
                'report_date': report_at.isoformat() if report_at else None,
                'order_no': row.order_no,
                'customer_id': row.customer_id,
                'customer_name': row.customer.name if row.customer else None,
                'sales_user_name': row.sales_user.full_name if row.sales_user else None,
                'product_names': product_names,
                'total_quantity': float(total_quantity),
                'payment_type': row.payment_type,
                'status': row.status,
                'sales_amount': float(Decimal(row.total_amount or 0)),
                'cost_amount': float(Decimal(row.cost_amount or 0)),
                'transport_fee': float(Decimal(row.transport_fee or 0)),
                'packaging_fee': float(Decimal(row.packaging_fee or 0)),
                'profit_amount': float(Decimal(row.profit_amount or 0)),
            }
        )
    return data


def _build_purchase_payment_report_rows(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    supplier_id: int | None = None,
    product_id: int | None = None,
    keyword: str | None = None,
) -> list[dict]:
    payables = db.execute(select(PayableRecord).order_by(PayableRecord.paid_at.desc())).scalars().all()
    order_ids = [row.purchase_order_id for row in payables]
    order_map = {
        row.id: row
        for row in db.execute(
            select(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.supplier),
                joinedload(PurchaseOrder.purchaser),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            )
            .where(PurchaseOrder.id.in_(order_ids))
        ).unique().scalars().all()
    } if order_ids else {}
    data = []
    for row in payables:
        if not _in_date_range(row.paid_at, date_from, date_to):
            continue
        order = order_map.get(row.purchase_order_id)
        if order is None:
            continue
        if supplier_id is not None and order.supplier_id != supplier_id:
            continue
        matched_items = [item for item in order.items if product_id is None or item.product_id == product_id]
        if product_id is not None and not matched_items:
            continue
        item_names = '、'.join(
            dict.fromkeys(
                (item.product.name if item.product else (item.material.name if item.material else str(item.id)))
                for item in (matched_items or order.items)
            )
        )
        if not _match_keyword(keyword, order.order_no, order.supplier.name if order.supplier else None, item_names, row.remark, order.payment_type):
            continue
        data.append(
            {
                'id': row.id,
                'paid_at': row.paid_at.isoformat() if row.paid_at else None,
                'purchase_order_id': order.id,
                'purchase_order_no': order.order_no,
                'supplier_id': order.supplier_id,
                'supplier_name': order.supplier.name if order.supplier else None,
                'purchaser_name': order.purchaser.full_name if order.purchaser else None,
                'payment_type': order.payment_type,
                'items_summary': item_names,
                'amount': float(Decimal(row.amount or 0)),
                'status': row.status,
                'remark': row.remark,
            }
        )
    return data


def _build_cash_flow_report_rows(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
) -> list[dict]:
    rows = db.execute(select(CashFlowRecord).order_by(CashFlowRecord.happened_at.desc())).scalars().all()
    data = []
    for row in rows:
        if not _in_date_range(row.happened_at, date_from, date_to):
            continue
        if not _match_keyword(keyword, row.record_no, row.business_type, row.related_no, row.remark):
            continue
        data.append(
            {
                'id': row.id,
                'record_no': row.record_no,
                'flow_type': row.flow_type,
                'business_type': row.business_type,
                'business_id': row.business_id,
                'related_no': row.related_no,
                'amount': float(Decimal(row.amount or 0)),
                'happened_at': row.happened_at.isoformat() if row.happened_at else None,
                'remark': row.remark,
            }
        )
    return data


def _build_inventory_capital_report_rows(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    product_id: int | None = None,
    keyword: str | None = None,
) -> list[dict]:
    rows = db.execute(
        select(Inventory)
        .options(joinedload(Inventory.warehouse), joinedload(Inventory.product), joinedload(Inventory.material))
        .order_by(Inventory.updated_at.desc())
    ).scalars().all()
    data = []
    for row in rows:
        if product_id is not None and row.product_id != product_id:
            continue
        if not _in_date_range(row.updated_at, date_from, date_to):
            continue
        item = row.product or row.material
        item_name = item.name if item else None
        item_code = getattr(item, 'product_code', None) or getattr(item, 'material_code', None)
        if not _match_keyword(keyword, item_name, item_code, row.warehouse.name if row.warehouse else None):
            continue
        quantity = Decimal(row.quantity or 0)
        reserved_quantity = Decimal(row.reserved_quantity or 0)
        available_quantity = quantity - reserved_quantity
        avg_unit_cost = Decimal(row.avg_unit_cost or 0)
        safety_stock_qty = Decimal(getattr(item, 'safety_stock_qty', 0) or 0)
        data.append(
            {
                'id': row.id,
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
                'warehouse_id': row.warehouse_id,
                'warehouse_name': row.warehouse.name if row.warehouse else None,
                'item_type': 'product' if row.product_id else 'material',
                'item_id': row.product_id or row.material_id,
                'item_code': item_code,
                'item_name': item_name,
                'quantity': float(quantity),
                'reserved_quantity': float(reserved_quantity),
                'available_quantity': float(available_quantity),
                'avg_unit_cost': float(avg_unit_cost),
                'inventory_amount': float(quantity * avg_unit_cost),
                'safety_stock_qty': float(safety_stock_qty),
                'below_safety_stock': available_quantity < safety_stock_qty,
            }
        )
    return data


@router.get('/reports/detail')
def report_detail(
    date_from: date | None = None,
    date_to: date | None = None,
    customer_id: int | None = None,
    supplier_id: int | None = None,
    product_id: int | None = None,
    keyword: str | None = None,
    current_user: User = Depends(require_roles('finance', 'manager', 'chairman')),
    db: Session = Depends(get_db),
):
    sales_profit_rows = _build_sales_profit_report_rows(
        db,
        date_from=date_from,
        date_to=date_to,
        customer_id=customer_id,
        product_id=product_id,
        keyword=keyword,
    )
    purchase_payment_rows = _build_purchase_payment_report_rows(
        db,
        date_from=date_from,
        date_to=date_to,
        supplier_id=supplier_id,
        product_id=product_id,
        keyword=keyword,
    )
    cash_flow_rows = _build_cash_flow_report_rows(db, date_from=date_from, date_to=date_to, keyword=keyword)
    inventory_capital_rows = _build_inventory_capital_report_rows(
        db,
        date_from=date_from,
        date_to=date_to,
        product_id=product_id,
        keyword=keyword,
    )

    incoming_total = sum(row['amount'] for row in cash_flow_rows if row['flow_type'] == 'incoming')
    outgoing_total = sum(row['amount'] for row in cash_flow_rows if row['flow_type'] == 'outgoing')

    return api_response(
        data={
            'filters': {
                'date_from': date_from.isoformat() if date_from else None,
                'date_to': date_to.isoformat() if date_to else None,
                'customer_id': customer_id,
                'supplier_id': supplier_id,
                'product_id': product_id,
                'keyword': keyword,
            },
            'summary': {
                'sales_order_count': len(sales_profit_rows),
                'sales_total': round(sum(row['sales_amount'] for row in sales_profit_rows), 2),
                'profit_total': round(sum(row['profit_amount'] for row in sales_profit_rows), 2),
                'purchase_payment_total': round(sum(row['amount'] for row in purchase_payment_rows), 2),
                'incoming_total': round(incoming_total, 2),
                'outgoing_total': round(outgoing_total, 2),
                'inventory_capital_total': round(sum(row['inventory_amount'] for row in inventory_capital_rows), 2),
                'below_safety_stock_count': sum(1 for row in inventory_capital_rows if row['below_safety_stock']),
            },
            'sales_profit_rows': sales_profit_rows,
            'purchase_payment_rows': purchase_payment_rows,
            'cash_flow_rows': cash_flow_rows,
            'inventory_capital_rows': inventory_capital_rows,
        }
    )
