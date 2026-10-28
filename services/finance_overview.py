"""Financial operating overview with explicit, reconcilable date bases."""

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from models import (
    CashFlowRecord, PayableRecord, PurchaseOrder, PurchasePaymentRequest,
    ReceivablePlan, ReceivableRecord, RefundRecord, Reimbursement, SalesOrder, SalesOrderItem,
    SalesReceiptRequest, SalesReturn, User,
)
from services.display import status_label


ZERO = Decimal("0")
PAYMENT_RECORDED_STATUSES = {"paid", "partial_settled", "settled"}


def _money(value) -> str:
    return str(Decimal(value or 0).quantize(Decimal("0.0001")))


def _bounds(date_from: date, date_to: date):
    return datetime.combine(date_from, time.min), datetime.combine(date_to, time.max)


def _inside(value: datetime | None, start: datetime, end: datetime) -> bool:
    return value is not None and start <= value <= end


def _user_name(user: User | None) -> str:
    return user.full_name if user else "-"


def build_finance_overview(db: Session, *, date_from: date, date_to: date, current_user: User) -> dict:
    start, end = _bounds(date_from, date_to)
    role_code = current_user.role.code if current_user.role else ""
    personal_queue = role_code in {"finance", "finance_super"}

    sales = db.execute(
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user), selectinload(SalesOrder.items).joinedload(SalesOrderItem.product))
        .where(SalesOrder.status.in_(["shipped", "completed"]), func.coalesce(SalesOrder.shipped_at, SalesOrder.completed_at).between(start, end))
        .order_by(SalesOrder.shipped_at.desc(), SalesOrder.id.desc())
    ).unique().scalars().all()
    sales_rows = []
    product_map: dict[int, dict] = {}
    for order in sales:
        happened_at = order.shipped_at or order.completed_at
        sales_rows.append({
            "id": order.id, "no": order.order_no, "date": happened_at.isoformat(),
            "party": order.customer.name if order.customer else "-", "owner": _user_name(order.sales_user),
            "amount": _money(order.total_amount), "status": status_label(order.status),
            "items": "、".join(dict.fromkeys(item.product.name for item in order.items if item.product)),
            "route": "/sales/orders", "focus_type": "sales_order",
        })
        for item in order.items:
            product = item.product
            if product is None:
                continue
            bucket = product_map.setdefault(product.id, {
                "id": product.id, "no": product.product_code, "name": product.name,
                "quantity": ZERO, "amount": ZERO, "order_ids": set(),
                "route": "/products", "focus_type": "product",
            })
            bucket["quantity"] += Decimal(item.quantity or 0)
            bucket["amount"] += Decimal(item.total_amount or 0)
            bucket["order_ids"].add(order.id)
    product_rows = [
        {**row, "quantity": _money(row["quantity"]), "amount": _money(row["amount"]),
         "order_count": len(row["order_ids"])}
        for row in product_map.values()
    ]
    for row in product_rows:
        row.pop("order_ids", None)
    product_rows.sort(key=lambda row: Decimal(row["amount"]), reverse=True)

    plans = db.execute(
        select(ReceivablePlan)
        .join(SalesOrder, SalesOrder.id == ReceivablePlan.sales_order_id)
        .where(ReceivablePlan.created_at.between(start, end), SalesOrder.status != "cancelled")
        .order_by(ReceivablePlan.created_at.desc(), ReceivablePlan.id.desc())
    ).scalars().all()
    plan_orders = {row.id: row for row in db.execute(
        select(SalesOrder).options(joinedload(SalesOrder.customer))
        .where(SalesOrder.id.in_([plan.sales_order_id for plan in plans] or [-1]))
    ).scalars().all()}
    receivable_map: dict[int, dict] = {}
    for plan in plans:
        order = plan_orders.get(plan.sales_order_id)
        if order is None:
            continue
        bucket = receivable_map.setdefault(order.id, {
            "id": order.id, "no": order.order_no, "date": plan.created_at.isoformat(),
            "party": order.customer.name if order.customer else "-", "amount": ZERO,
            "received": ZERO, "balance": ZERO, "route": "/finance/receivables", "focus_type": "sales_order",
        })
        bucket["amount"] += Decimal(plan.planned_amount or 0)
        bucket["received"] += Decimal(plan.received_amount or 0)
        bucket["balance"] += max(Decimal(plan.planned_amount or 0) - Decimal(plan.received_amount or 0), ZERO)
    receivable_rows = [
        {**row, "amount": _money(row["amount"]), "received": _money(row["received"]), "balance": _money(row["balance"])}
        for row in receivable_map.values()
    ]

    # 采购订单生效不代表已经形成应付；以采购提交的付款申请为确认时点。
    requests = db.execute(
        select(PurchasePaymentRequest)
        .options(joinedload(PurchasePaymentRequest.purchase_order).joinedload(PurchaseOrder.supplier))
        .where(PurchasePaymentRequest.created_at.between(start, end), PurchasePaymentRequest.status != "rejected")
        .order_by(PurchasePaymentRequest.created_at.desc(), PurchasePaymentRequest.id.desc())
    ).scalars().all()
    request_ids = [row.id for row in requests]
    paid_totals = dict(db.execute(
        select(PayableRecord.purchase_payment_request_id, func.sum(PayableRecord.amount))
        .where(PayableRecord.purchase_payment_request_id.in_(request_ids or [-1]),
               PayableRecord.status.in_(PAYMENT_RECORDED_STATUSES))
        .group_by(PayableRecord.purchase_payment_request_id)
    ).all())
    payable_rows = []
    for request in requests:
        order = request.purchase_order
        amount = Decimal(request.requested_amount or 0)
        paid = Decimal(paid_totals.get(request.id) or 0)
        payable_rows.append({
            "id": request.id, "no": request.request_no, "date": request.created_at.isoformat(),
            "party": order.supplier.name if order and order.supplier else "-", "amount": _money(amount),
            "paid": _money(paid), "balance": _money(max(amount - paid, ZERO)),
            "status": status_label(request.status), "route": "/finance/payables", "focus_type": "purchase_payment_request",
        })

    reimbursements = db.execute(
        select(Reimbursement).options(joinedload(Reimbursement.applicant))
        .where(Reimbursement.created_at.between(start, end), Reimbursement.status != "rejected")
        .order_by(Reimbursement.created_at.desc(), Reimbursement.id.desc())
    ).scalars().all()
    reimbursement_rows = [{
        "id": row.id, "no": row.reimbursement_no, "date": row.created_at.isoformat(),
        "party": _user_name(row.applicant), "name": row.expense_subcategory,
        "amount": _money(row.amount), "status": status_label(row.status),
        "route": "/finance/reimbursements", "focus_type": "reimbursement",
    } for row in reimbursements]
    paid_reimbursements = db.execute(
        select(Reimbursement).options(joinedload(Reimbursement.applicant))
        .where(Reimbursement.status == "paid", Reimbursement.paid_at.between(start, end))
        .order_by(Reimbursement.paid_at.desc(), Reimbursement.id.desc())
    ).scalars().all()
    reimbursement_payment_rows = [{
        "id": row.id, "no": row.reimbursement_no, "date": row.paid_at.isoformat(),
        "party": _user_name(row.applicant), "name": row.expense_subcategory,
        "amount": _money(row.amount), "status": status_label(row.status),
        "route": "/finance/reimbursements", "focus_type": "reimbursement",
    } for row in paid_reimbursements]

    refunds = db.execute(
        select(RefundRecord).where(
            RefundRecord.business_type == "sales_return", RefundRecord.refunded_at.between(start, end),
        ).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())
    ).scalars().all()
    refund_returns = {row.id: row for row in db.execute(
        select(SalesReturn).where(SalesReturn.id.in_([refund.sales_return_id for refund in refunds if refund.sales_return_id] or [-1]))
    ).scalars().all()}
    refund_rows = [{
        "id": row.id, "no": refund_returns[row.sales_return_id].return_no if row.sales_return_id in refund_returns else f"退款#{row.id}",
        "date": row.refunded_at.isoformat(), "party": "销售退货", "name": row.remark or "销售退款",
        "amount": _money(row.amount), "route": "/sales/returns", "focus_type": "sales_return",
        "focus_id": row.sales_return_id or row.id,
    } for row in refunds]

    collections = db.execute(
        select(ReceivableRecord)
        .where(ReceivableRecord.received_at.between(start, end))
        .order_by(ReceivableRecord.received_at.desc(), ReceivableRecord.id.desc())
    ).scalars().all()
    collection_orders = {row.id: row for row in db.execute(
        select(SalesOrder).options(joinedload(SalesOrder.customer))
        .where(SalesOrder.id.in_([receipt.sales_order_id for receipt in collections] or [-1]))
    ).scalars().all()}
    collection_rows = [{
        "id": row.id, "no": collection_orders[row.sales_order_id].order_no, "date": row.received_at.isoformat(),
        "party": collection_orders[row.sales_order_id].customer.name if collection_orders[row.sales_order_id].customer else "-",
        "amount": _money(row.amount), "name": row.receipt_method or "收款",
        "route": "/finance/receivables", "focus_type": "sales_order", "focus_id": row.sales_order_id,
    } for row in collections if row.sales_order_id in collection_orders]

    payments = db.execute(
        select(PayableRecord)
        .where(PayableRecord.paid_at.between(start, end),
               PayableRecord.status.in_(PAYMENT_RECORDED_STATUSES))
        .order_by(PayableRecord.paid_at.desc(), PayableRecord.id.desc())
    ).scalars().all()
    payment_orders = {row.id: row for row in db.execute(
        select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier))
        .where(PurchaseOrder.id.in_([payment.purchase_order_id for payment in payments] or [-1]))
    ).scalars().all()}
    payment_rows = [{
        "id": row.id, "no": payment_orders[row.purchase_order_id].order_no, "date": row.paid_at.isoformat(),
        "party": payment_orders[row.purchase_order_id].supplier.name if payment_orders[row.purchase_order_id].supplier else "-",
        "amount": _money(row.amount), "name": row.payment_method or "付款",
        "route": "/finance/payables", "focus_type": "payable_record",
    } for row in payments if row.purchase_order_id in payment_orders]

    cash_flows = db.execute(
        select(CashFlowRecord).where(CashFlowRecord.happened_at.between(start, end))
        .order_by(CashFlowRecord.happened_at.desc(), CashFlowRecord.id.desc())
    ).scalars().all()
    cash_rows = [{
        "id": row.id, "no": row.record_no, "date": row.happened_at.isoformat(),
        "party": row.related_no or "-", "amount": _money(row.amount),
        "name": row.remark or row.business_type, "direction": row.flow_type,
        "route": "/finance/cash-flows", "focus_type": "cash_flow",
    } for row in cash_flows]

    def assigned_to_current(assigned_id: int | None) -> bool:
        return not personal_queue or assigned_id is None or assigned_id == current_user.id

    finance_tasks = []
    pending_orders = db.execute(select(SalesOrder).options(joinedload(SalesOrder.customer)).where(
        SalesOrder.status.in_(["pending_confirmation", "pending_price_approval"])
    )).scalars().all()
    for row in pending_orders:
        task_date = row.updated_at or row.created_at
        if _inside(task_date, start, end) and assigned_to_current(row.assigned_finance_user_id):
            finance_tasks.append({
                "id": row.id, "no": row.order_no, "date": task_date.isoformat(),
                "party": row.customer.name if row.customer else "-", "amount": _money(row.total_amount),
                "name": "客户订单审批" if row.status == "pending_confirmation" else "销售改价审批",
                "status": status_label(row.status), "route": "/sales/approvals", "focus_type": "sales_order",
            })
    pending_payments = db.execute(select(PurchasePaymentRequest).where(
        PurchasePaymentRequest.status == "pending_finance_payment", PurchasePaymentRequest.created_at.between(start, end)
    )).scalars().all()
    for row in pending_payments:
        if assigned_to_current(row.assigned_finance_user_id):
            finance_tasks.append({
                "id": row.id, "no": row.request_no, "date": row.created_at.isoformat(),
                "party": "采购付款", "amount": _money(row.requested_amount), "name": "待财务出账",
                "status": status_label(row.status), "route": "/finance/payables", "focus_type": "purchase_payment_request",
            })
    pending_receipts = db.execute(select(SalesReceiptRequest).where(
        SalesReceiptRequest.status == "pending_finance_check", SalesReceiptRequest.created_at.between(start, end)
    )).scalars().all()
    for row in pending_receipts:
        if assigned_to_current(row.assigned_finance_user_id):
            finance_tasks.append({
                "id": row.id, "no": row.request_no, "date": row.created_at.isoformat(),
                "party": "销售回款", "amount": _money(row.requested_amount), "name": "待财务核对",
                "status": status_label(row.status), "route": "/finance/receivables", "focus_type": "sales_receipt_request",
            })
    approved_reimbursements = db.execute(select(Reimbursement).where(
        Reimbursement.status == "approved", Reimbursement.created_at.between(start, end)
    )).scalars().all()
    for row in approved_reimbursements:
        if assigned_to_current(row.assigned_finance_user_id):
            finance_tasks.append({
                "id": row.id, "no": row.reimbursement_no, "date": row.created_at.isoformat(),
                "party": row.expense_subcategory, "amount": _money(row.amount), "name": "报销待出账",
                "status": status_label(row.status), "route": "/finance/reimbursements", "focus_type": "reimbursement",
            })
    returns = db.execute(select(SalesReturn).where(
        SalesReturn.status == "pending", SalesReturn.created_at.between(start, end)
    )).scalars().all()
    for row in returns:
        finance_tasks.append({
            "id": row.id, "no": row.return_no, "date": row.created_at.isoformat(),
            "party": "销售退货", "amount": _money(row.refund_amount), "name": "退货待审批",
            "status": status_label(row.status), "route": "/sales/returns", "focus_type": "sales_return",
        })
    finance_tasks.sort(key=lambda row: row["date"], reverse=True)

    def total(rows: list[dict], field: str = "amount") -> str:
        return _money(sum((Decimal(row[field]) for row in rows), ZERO))

    trend = defaultdict(lambda: {"sales": ZERO, "collections": ZERO, "payments": ZERO})
    for rows, field in ((sales_rows, "sales"), (collection_rows, "collections"), (payment_rows, "payments")):
        for row in rows:
            trend[row["date"][:10]][field] += Decimal(row["amount"])

    return {
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "summary": {
            "sales_amount": total(sales_rows), "sales_count": len(sales_rows),
            "product_count": len(product_rows),
            "receivable_amount": total(receivable_rows), "receivable_count": len(receivable_rows),
            "receivable_balance": total(receivable_rows, "balance"),
            "payable_amount": total(payable_rows), "payable_count": len(payable_rows),
            "payable_balance": total(payable_rows, "balance"),
            "reimbursement_amount": total(reimbursement_rows), "reimbursement_count": len(reimbursement_rows),
            "reimbursement_paid_amount": total(reimbursement_payment_rows),
            "collection_amount": total(collection_rows), "collection_count": len(collection_rows),
            "sales_refund_amount": total(refund_rows),
            "payment_amount": total(payment_rows), "payment_count": len(payment_rows),
            "cash_in": total([row for row in cash_rows if row["direction"] == "incoming"]),
            "cash_out": total([row for row in cash_rows if row["direction"] == "outgoing"]),
            "finance_task_count": len(finance_tasks),
        },
        "details": {
            "sales": sales_rows, "products": product_rows, "receivables": receivable_rows,
            "payables": payable_rows, "reimbursements": reimbursement_rows,
            "reimbursement_payments": reimbursement_payment_rows, "sales_refunds": refund_rows,
            "collections": collection_rows, "payments": payment_rows,
            "cash_in": [row for row in cash_rows if row["direction"] == "incoming"],
            "cash_out": [row for row in cash_rows if row["direction"] == "outgoing"],
            "finance_tasks": finance_tasks,
        },
        "trend": [
            {"date": day, **{key: _money(value) for key, value in values.items()}}
            for day, values in sorted(trend.items())
        ],
    }
