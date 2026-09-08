"""Finance domain service layer."""

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models import (
    PayableRecord,
    PurchaseOrder,
    PurchaseReturn,
    QualityCostRecord,
    QualityDisposition,
    QualityTask,
    ReceivablePlan,
    ReceivableRecord,
    RefundRecord,
    Role,
    SalesOrder,
    SalesReturn,
    User,
)
from responses import api_response
from schemas.finance import (
    PayableRecordCreate,
    QualityCostRecognize,
    ReceivablePlanCreate,
    ReceivableRecordCreate,
    RefundRecordCreate,
    SalesCostUpdate,
)
from services.core import _role_user_ids, complete_todos, create_cash_flow, create_notification, write_operation_log
from services.time_utils import now_local


def _recalculate_sales_profit(order: SalesOrder):
    order.profit_amount = (
        Decimal(order.total_amount or 0)
        - Decimal(order.cost_amount or 0)
        - Decimal(order.transport_fee or 0)
        - Decimal(order.packaging_fee or 0)
    )


def _purchase_paid_amount(db: Session, order_id: int) -> Decimal:
    total = db.execute(
        select(func.coalesce(func.sum(PayableRecord.amount), 0)).where(
            PayableRecord.purchase_order_id == order_id,
            PayableRecord.status.in_(("paid", "partial_settled", "settled")),
        )
    ).scalar_one()
    return Decimal(total or 0)


def _sales_received_amount(db: Session, order_id: int) -> Decimal:
    total = db.execute(
        select(func.coalesce(func.sum(ReceivableRecord.amount), 0)).where(ReceivableRecord.sales_order_id == order_id)
    ).scalar_one()
    return Decimal(total or 0)


def create_receivable_plan(payload: ReceivablePlanCreate, current_user: User, db: Session):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == payload.sales_order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    plan = ReceivablePlan(
        sales_order_id=payload.sales_order_id,
        due_date=payload.due_date,
        planned_amount=payload.planned_amount,
        created_by_id=current_user.id,
    )
    db.add(plan)
    db.commit()
    return api_response(data={"id": plan.id})


def create_receivable_record(payload: ReceivableRecordCreate, current_user: User, db: Session):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == payload.sales_order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="销售订单不存在")

    already_received = _sales_received_amount(db, order.id)
    remaining_amount = max(Decimal(order.total_amount or 0) - already_received, Decimal("0"))
    if remaining_amount <= 0:
        raise HTTPException(status_code=409, detail="当前客户订单已结清，无需重复登记回款")
    if Decimal(payload.amount) > remaining_amount:
        raise HTTPException(status_code=409, detail="本次回款金额不能超过订单剩余应收金额")

    record = ReceivableRecord(
        sales_order_id=payload.sales_order_id,
        receivable_plan_id=payload.receivable_plan_id,
        amount=payload.amount,
        received_at=payload.received_at,
        received_by_id=current_user.id,
        remark=payload.remark,
    )
    db.add(record)

    if payload.receivable_plan_id:
        plan = db.execute(select(ReceivablePlan).where(ReceivablePlan.id == payload.receivable_plan_id)).scalar_one_or_none()
        if plan:
            plan_remaining_amount = Decimal(plan.planned_amount or 0) - Decimal(plan.received_amount or 0)
            if Decimal(payload.amount) > plan_remaining_amount:
                raise HTTPException(status_code=409, detail="本次回款金额不能超过计划剩余金额")
            plan.received_amount = Decimal(plan.received_amount or 0) + payload.amount
            if Decimal(plan.received_amount or 0) >= Decimal(plan.planned_amount or 0):
                plan.status = "completed"

    create_cash_flow(
        db,
        flow_type="incoming",
        business_type="sales_order",
        business_id=order.id,
        related_no=order.order_no,
        amount=payload.amount,
        happened_at=payload.received_at,
        remark=payload.remark,
    )

    received_after = already_received + Decimal(payload.amount or 0)
    settled = received_after >= Decimal(order.total_amount or 0)
    create_notification(
        db,
        title="客户订单回款已登记",
        content=(
            f"客户订单 {order.order_no} 已登记回款 {Decimal(payload.amount or 0):.2f} 元，"
            f"累计回款 {received_after:.2f} / {Decimal(order.total_amount or 0):.2f} 元，"
            f"{'已结清' if settled else '尚未结清'}。"
        ),
        module="finance",
        recipient_user_ids=list(
            dict.fromkeys(
                [
                    *([order.sales_user_id] if order.sales_user_id else []),
                    *_role_user_ids(db, "finance", exclude_user_id=current_user.id),
                ]
            )
        ),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="collect_order_payment",
        content=f"登记客户订单 {order.order_no} 回款",
        after_data={
            "sales_order_id": order.id,
            "amount": float(payload.amount or 0),
            "received_amount": float(received_after),
            "total_amount": float(order.total_amount or 0),
            "settled": settled,
        },
    )
    db.commit()
    return api_response(data={"id": record.id, "settled": settled, "received_amount": float(received_after)})


def create_payable_record(payload: PayableRecordCreate, current_user: User, db: Session):
    order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == payload.purchase_order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")

    already_paid = _purchase_paid_amount(db, order.id)
    remaining_amount = max(Decimal(order.total_amount or 0) - already_paid, Decimal("0"))
    if remaining_amount <= 0:
        raise HTTPException(status_code=409, detail="当前采购订单已结算，无需重复登记付款")
    if Decimal(payload.amount) > remaining_amount:
        raise HTTPException(status_code=409, detail="本次付款金额不能超过订单剩余应付金额")

    paid_after = already_paid + Decimal(payload.amount or 0)
    settlement_status = "settled" if paid_after >= Decimal(order.total_amount or 0) else "partial_settled"
    record = PayableRecord(
        purchase_order_id=payload.purchase_order_id,
        purchase_receipt_id=payload.purchase_receipt_id,
        amount=payload.amount,
        paid_at=payload.paid_at,
        paid_by_id=current_user.id,
        status=settlement_status,
        remark=payload.remark,
    )
    db.add(record)

    create_cash_flow(
        db,
        flow_type="outgoing",
        business_type="purchase_order",
        business_id=order.id,
        related_no=order.order_no,
        amount=payload.amount,
        happened_at=payload.paid_at,
        remark=payload.remark,
    )
    create_notification(
        db,
        title="采购订单付款已登记",
        content=(
            f"采购订单 {order.order_no} 已登记付款 {Decimal(payload.amount or 0):.2f} 元，"
            f"累计付款 {paid_after:.2f} / {Decimal(order.total_amount or 0):.2f} 元，"
            f"{'已结算' if settlement_status == 'settled' else '部分结算'}。"
        ),
        module="finance",
        recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="create_payable_record",
        content=f"登记采购订单 {order.order_no} 付款",
        after_data={
            "purchase_order_id": order.id,
            "amount": float(payload.amount or 0),
            "paid_amount": float(paid_after),
            "total_amount": float(order.total_amount or 0),
            "settlement_status": settlement_status,
        },
    )
    db.commit()
    return api_response(
        data={
            "id": record.id,
            "settlement_status": settlement_status,
            "paid_amount": float(paid_after),
            "remaining_amount": float(max(Decimal(order.total_amount or 0) - paid_after, Decimal("0"))),
        }
    )


def create_refund_record(payload: RefundRecordCreate, current_user: User, db: Session):
    refund = RefundRecord(
        business_type=payload.business_type,
        sales_return_id=payload.sales_return_id,
        purchase_return_id=payload.purchase_return_id,
        amount=payload.amount,
        refunded_at=payload.refunded_at,
        processed_by_id=current_user.id,
        remark=payload.remark,
    )
    db.add(refund)

    if payload.business_type == "sales_return" and payload.sales_return_id:
        sales_return = db.execute(select(SalesReturn).where(SalesReturn.id == payload.sales_return_id)).scalar_one_or_none()
        if sales_return and sales_return.status not in {"approved", "refunded"}:
            raise HTTPException(status_code=409, detail="销售退换货未完成审核回库，不能退款")
        if sales_return:
            sales_return.status = "refunded"
            complete_todos(db, business_type="sales_return", business_id=sales_return.id)
        create_cash_flow(
            db,
            flow_type="outgoing",
            business_type="sales_return",
            business_id=payload.sales_return_id,
            related_no=sales_return.return_no if sales_return else None,
            amount=payload.amount,
            happened_at=payload.refunded_at,
            remark=payload.remark,
        )
        recipient_user_ids = [sales_return.requested_by_id] if sales_return else []
        if recipient_user_ids:
            create_notification(
                db,
                title="销售退换货退款已完成",
                content=f"退换货单 {sales_return.return_no} 已完成退款，金额 {Decimal(payload.amount or 0):.2f} 元。",
                module="finance",
                recipient_user_ids=recipient_user_ids,
                created_by_id=current_user.id,
                category="success",
            )
    elif payload.business_type == "purchase_return" and payload.purchase_return_id:
        purchase_return = db.execute(select(PurchaseReturn).where(PurchaseReturn.id == payload.purchase_return_id)).scalar_one_or_none()
        if purchase_return and purchase_return.status not in {"returned", "refunded"}:
            raise HTTPException(status_code=409, detail="采购退货状态不允许退款")
        if purchase_return:
            purchase_return.status = "refunded"
            complete_todos(db, business_type="purchase_return", business_id=purchase_return.id)
        create_cash_flow(
            db,
            flow_type="incoming",
            business_type="purchase_return",
            business_id=payload.purchase_return_id,
            related_no=purchase_return.return_no if purchase_return else None,
            amount=payload.amount,
            happened_at=payload.refunded_at,
            remark=payload.remark,
        )
        recipient_user_ids = [purchase_return.returned_by_id] if purchase_return else []
        if recipient_user_ids:
            create_notification(
                db,
                title="采购退货退款已完成",
                content=f"采购退货单 {purchase_return.return_no} 已完成退款，金额 {Decimal(payload.amount or 0):.2f} 元。",
                module="finance",
                recipient_user_ids=recipient_user_ids,
                created_by_id=current_user.id,
                category="success",
            )

    db.commit()
    return api_response(data={"id": refund.id})


def update_sales_cost(order_id: int, payload: SalesCostUpdate, current_user: User, db: Session):
    order = (
        db.execute(select(SalesOrder).options(joinedload(SalesOrder.items)).where(SalesOrder.id == order_id))
        .unique()
        .scalar_one_or_none()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    total_cost = Decimal("0")
    for item in order.items:
        total_cost += Decimal(item.product.price or 0) * Decimal(item.quantity or 0)
    order.cost_amount = total_cost
    order.transport_fee = payload.transport_fee
    order.packaging_fee = payload.packaging_fee
    _recalculate_sales_profit(order)
    db.commit()
    return api_response(data={"profit_amount": float(order.profit_amount or 0)})


def recognize_quality_cost(record_id: int, payload: QualityCostRecognize, current_user: User, db: Session):
    record = (
        db.execute(
            select(QualityCostRecord)
            .options(joinedload(QualityCostRecord.disposition).joinedload(QualityDisposition.quality_task))
            .where(QualityCostRecord.id == record_id)
        )
        .unique()
        .scalar_one_or_none()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="质量成本记录不存在")

    record.recognized_amount = payload.recognized_amount
    record.status = "recognized"
    record.remark = payload.remark
    record.recognized_at = now_local()
    record.recognized_by_id = current_user.id

    quality_task = record.disposition.quality_task if record.disposition else None
    quality_no = quality_task.quality_no if quality_task else None
    disposition_type = record.disposition.disposition_type if record.disposition else None

    complete_todos(db, business_type="quality_cost_record", business_id=record.id)
    if quality_task is not None:
        complete_todos(db, business_type="quality_scrap", business_id=quality_task.id)

    recipient_user_ids = _role_user_ids(db, "quality", "manager", "chairman", exclude_user_id=current_user.id)
    if recipient_user_ids:
        disposition_label = {
            "scrap": "报废",
            "rework": "返工",
        }.get(disposition_type, "质量异常")
        operator_name = current_user.full_name or current_user.username
        create_notification(
            db,
            title=f"财务回执：{quality_no or record.id}",
            content=(
                f"质检单 {quality_no or '-'} 已由 {operator_name} 完成财务确认，"
                f"确认金额 {Decimal(payload.recognized_amount or 0):.2f} 元，处置类型：{disposition_label}。"
            ),
            module="finance",
            recipient_user_ids=recipient_user_ids,
            category="success",
            created_by_id=current_user.id,
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="recognize_quality_cost",
        content=f"确认质量异常成本 {quality_no or record.id}",
        after_data={
            "record_id": record.id,
            "quality_no": quality_no,
            "recognized_amount": float(payload.recognized_amount or 0),
            "status": record.status,
        },
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})
