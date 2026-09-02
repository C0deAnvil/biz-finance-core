from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user
from models import (
    Attachment,
    BusinessMatter,
    PurchasePaymentRequest,
    Reimbursement,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderApproval,
    PurchaseOrderItem,
    SalesReceiptRequest,
    QualityTask,
    ReceivableRecord,
    SalesOrder,
    SalesOrderApproval,
    SalesOrderItem,
    SalesReturn,
    SalesReturnItem,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.approvals import ApprovalDecisionRequest
from schemas.reimbursements import BusinessMatterAuditRequest, ReimbursementAuditRequest
from schemas.production import SpecialMaterialApproval
from schemas.purchases import PurchaseApprovalRequest
from schemas.sales import SalesApprovalRequest, SalesReceiptApprovalRequest, SalesReturnApprovalRequest
from services.display import clean_display_text, payment_type_label, return_type_label, status_label
from services.production_service import approve_special_request as approve_special_request_service
from services.purchase_service import approve_purchase_order as approve_purchase_order_service, approve_purchase_payment_request as approve_purchase_payment_request_service
from services.sales_service import approve_order as approve_sales_order_service, approve_return as approve_sales_return_service, approve_sales_receipt_request as approve_sales_receipt_request_service
from services.workflow_repair import repair_sales_receipt_workflows
from api.routers.reimbursements import audit_business_matter as audit_business_matter_service, audit_reimbursement as audit_reimbursement_service


router = APIRouter(prefix="/approvals", tags=["approvals"])


APPROVAL_TYPE_LABELS = {
    "sales_order": "销售订单审批",
    "purchase_order": "采购订单审批",
    "purchase_payment_request": "采购付款审批",
    "sales_receipt_request": "销售收款确认",
    "sales_return": "销售退换货审批",
    "production_material_request": "领料审批",
    "production_special_request": "特殊领用审批",
    "business_matter": "事务申请审批",
    "reimbursement": "报销审批",
}

TODO_APPROVAL_TYPE_MAP = {
    "sales_order_approval": "sales_order",
    "purchase_payment_request": "purchase_payment_request",
    "sales_receipt_request": "sales_receipt_request",
    "sales_return_approval": "sales_return",
    "production_material_request": "production_material_request",
    "production_special_request": "production_special_request",
    "business_matter": "business_matter",
    "reimbursement": "reimbursement",
}

APPROVAL_VIEW_ROLES = {
    "sales_order": {"chairman", "system_admin"},
    "purchase_order": {"manager", "system_admin"},
    "purchase_payment_request": {"purchase", "chairman", "finance", "system_admin"},
    "sales_receipt_request": {"sales", "chairman", "finance", "system_admin"},
    "sales_return": {"manager", "chairman", "system_admin"},
    "production_material_request": {"production", "inventory", "manager", "chairman", "system_admin"},
    "production_special_request": {"production", "inventory", "manager", "chairman", "system_admin"},
    "business_matter": {"manager", "finance", "chairman", "system_admin"},
    "reimbursement": {"finance", "chairman", "system_admin"},
}

APPROVAL_ACTION_ROLES = {
    "sales_order": {"chairman", "system_admin"},
    "purchase_order": {"manager", "system_admin"},
    "purchase_payment_request": {"chairman", "system_admin"},
    "sales_receipt_request": {"finance", "system_admin"},
    "sales_return": {"manager", "chairman", "system_admin"},
    "production_material_request": {"inventory", "system_admin"},
    "production_special_request": {"inventory", "manager", "system_admin"},
    "business_matter": {"manager", "system_admin"},
    "reimbursement": {"chairman", "system_admin"},
}


def _role_code(current_user: User) -> str:
    return current_user.role.code if current_user.role else ""


def _can_view_approval_type(current_user: User, approval_type: str) -> bool:
    return _role_code(current_user) in APPROVAL_VIEW_ROLES.get(approval_type, set())


def _can_act_on_approval_type(current_user: User, approval_type: str) -> bool:
    return _role_code(current_user) in APPROVAL_ACTION_ROLES.get(approval_type, set())


def _ensure_approval_permission(current_user: User, approval_type: str, *, action: str) -> None:
    allowed = _can_act_on_approval_type(current_user, approval_type) if action == "act" else _can_view_approval_type(current_user, approval_type)
    if not allowed:
        raise HTTPException(status_code=403, detail="无权访问当前审批数据")


def _build_approval_id(approval_type: str, business_id: int) -> str:
    return f"{approval_type}:{business_id}"


def _parse_approval_id(approval_id: str) -> tuple[str, int]:
    try:
        approval_type, raw_business_id = approval_id.split(":", 1)
        return approval_type, int(raw_business_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="审批标识不合法") from exc


def _base_item(
    *,
    approval_type: str,
    business_id: int,
    module: str,
    title: str,
    status: str,
    created_at,
    summary: dict,
    can_approve: bool,
    can_reject: bool,
    action_route: str | None = None,
    action_query: dict | None = None,
    approved_by_name: str | None = None,
    approved_at=None,
) -> dict:
    return {
        "id": _build_approval_id(approval_type, business_id),
        "approval_type": approval_type,
        "approval_type_label": APPROVAL_TYPE_LABELS.get(approval_type, approval_type),
        "business_id": business_id,
        "module": module,
        "title": title,
        "status": status,
        "status_label": status_label(status),
        "created_at": created_at.isoformat() if created_at else None,
        "approved_by_name": approved_by_name,
        "approved_at": approved_at.isoformat() if approved_at else None,
        "can_approve": can_approve,
        "can_reject": can_reject,
        "action_route": action_route,
        "action_query": action_query or {},
        "summary": summary,
    }


def _user_name(db: Session, user_id: int | None) -> str | None:
    """按用户ID查姓名(审批人展示用)。"""
    if not user_id:
        return None
    row = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    return row.full_name if row else None


def _latest_sales_order_approver(db: Session, order_id: int) -> tuple[str | None, object]:
    """查销售订单最新一次审批的审批人姓名与时间(审批明细表优先)。"""
    row = db.execute(
        select(SalesOrderApproval)
        .where(SalesOrderApproval.sales_order_id == order_id)
        .order_by(SalesOrderApproval.id.desc())
    ).scalars().first()
    if row is None:
        return None, None
    return _user_name(db, row.approver_user_id), row.approved_at


def _latest_purchase_order_approver(db: Session, order_id: int) -> tuple[str | None, object]:
    """查采购订单最新一次审批的审批人姓名与时间(审批明细表优先)。"""
    row = db.execute(
        select(PurchaseOrderApproval)
        .where(PurchaseOrderApproval.purchase_order_id == order_id)
        .order_by(PurchaseOrderApproval.id.desc())
    ).scalars().first()
    if row is None:
        return None, None
    return _user_name(db, row.approver_user_id), row.approved_at


def _time_range_filter(start_date: str | None, end_date: str | None, column) -> list:
    """按 YYYY-MM-DD 字符串构造时间范围过滤条件(含当天)。"""
    conditions = []
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            conditions.append(column >= start_dt)
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            conditions.append(column < end_dt)
        except ValueError:
            pass
    return conditions


def _pending_sales_orders(current_user: User, db: Session) -> list[dict]:
    return []


def _history_sales_orders(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "sales_order"):
        return []
    rows = db.execute(
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer))
        .where(
            SalesOrder.status.in_(["pending_shipment", "rejected", "shipped", "completed"]),
            *_time_range_filter(start_date, end_date, SalesOrder.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="sales_order",
            business_id=row.id,
            module="sales",
            title=f"销售订单 {row.order_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=(_latest_sales_order_approver(db, row.id)[0] or _user_name(db, row.approved_by_id)),
            approved_at=(_latest_sales_order_approver(db, row.id)[1] or row.approved_at or row.updated_at),
            can_approve=False,
            can_reject=False,
            summary={
                "order_no": row.order_no,
                "customer_name": row.customer.name if row.customer else None,
                "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
                "total_amount": float(row.total_amount),
            },
        )
        for row in rows
    ]


def _pending_purchase_orders(current_user: User, db: Session) -> list[dict]:
    if not _can_act_on_approval_type(current_user, "purchase_order"):
        return []
    rows = db.execute(
        select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier)).where(PurchaseOrder.status == "pending_approval")
    ).scalars().all()
    return [
        _base_item(
            approval_type="purchase_order",
            business_id=row.id,
            module="purchase",
            title=f"采购订单 {row.order_no}",
            status=row.status,
            created_at=row.created_at,
            can_approve=True,
            can_reject=True,
            summary={
                "order_no": row.order_no,
                "supplier_name": row.supplier.name if row.supplier else None,
                "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
                "total_amount": float(row.total_amount),
            },
        )
        for row in rows
    ]


def _pending_purchase_payment_requests(current_user: User, db: Session) -> list[dict]:
    if not _can_view_approval_type(current_user, "purchase_payment_request"):
        return []
    status_filter = ["pending_chairman_approval"]
    if (current_user.role.code if current_user.role else "") == "finance":
        status_filter = ["pending_finance_payment"]
    rows = db.execute(
        select(PurchasePaymentRequest)
        .join(PurchaseOrder, PurchasePaymentRequest.purchase_order_id == PurchaseOrder.id)
        .where(PurchasePaymentRequest.status.in_(status_filter))
    ).scalars().all()
    return [
        _base_item(
            approval_type="purchase_payment_request",
            business_id=row.id,
            module="purchase",
            title=f"采购付款申请 {row.request_no}",
            status=row.status,
            created_at=row.created_at,
            can_approve=row.status == "pending_chairman_approval",
            can_reject=row.status == "pending_chairman_approval",
            action_route="/finance/payables" if row.status == "pending_finance_payment" else None,
            action_query={"focusId": str(row.id), "focusType": "purchase_payment_request"} if row.status == "pending_finance_payment" else None,
            summary={
                "request_no": row.request_no,
                "purchase_order_id": row.purchase_order_id,
                "requested_amount": float(row.requested_amount or 0),
                "order_amount": float(row.order_amount or 0),
                "other_fee_amount": float(row.other_fee_amount or 0),
                "arrival_status": row.arrival_status,
                "status_label": status_label(row.status),
            },
        )
        for row in rows
    ]


def _history_purchase_payment_requests(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "purchase_payment_request"):
        return []
    rows = db.execute(
        select(PurchasePaymentRequest)
        .options(joinedload(PurchasePaymentRequest.approved_by))
        .where(
            PurchasePaymentRequest.status.in_(["rejected", "pending_finance_payment", "pending_completion", "completed", "pending_chairman_approval"]),
            *_time_range_filter(start_date, end_date, PurchasePaymentRequest.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="purchase_payment_request",
            business_id=row.id,
            module="purchase",
            title=f"采购付款申请 {row.request_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=row.approved_by.full_name if row.approved_by else None,
            approved_at=row.approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "request_no": row.request_no,
                "requested_amount": float(row.requested_amount or 0),
                "actual_amount": float(row.actual_amount or 0),
                "order_amount": float(row.order_amount or 0),
                "other_fee_amount": float(row.other_fee_amount or 0),
            },
        )
        for row in rows
    ]


def _pending_sales_receipt_requests(current_user: User, db: Session) -> list[dict]:
    if not _can_view_approval_type(current_user, "sales_receipt_request"):
        return []
    status_filter = ["pending_finance_check"]
    rows = db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.status.in_(status_filter))).scalars().all()
    return [
        _base_item(
            approval_type="sales_receipt_request",
            business_id=row.id,
            module="sales",
            title=f"销售收款确认 {row.request_no}",
            status=row.status,
            created_at=row.created_at,
            can_approve=row.status == "pending_finance_check",
            can_reject=row.status == "pending_finance_check",
            action_route="/finance/receivables" if row.status == "pending_finance_check" else None,
            action_query={"focusId": str(row.id), "focusType": "sales_receipt_request"} if row.status == "pending_finance_check" else None,
            summary={
                "request_no": row.request_no,
                "sales_order_id": row.sales_order_id,
                "requested_amount": float(row.requested_amount or 0),
                "payment_stage": row.payment_stage,
                "installment_total": row.installment_total,
                "installment_index": row.installment_index,
            },
        )
        for row in rows
    ]


def _history_sales_receipt_requests(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "sales_receipt_request"):
        return []
    rows = db.execute(
        select(SalesReceiptRequest)
        .options(joinedload(SalesReceiptRequest.approved_by))
        .where(
            SalesReceiptRequest.status.in_(["approved", "rejected", "receipt_abnormal", "pending_finance_check"]),
            *_time_range_filter(start_date, end_date, SalesReceiptRequest.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="sales_receipt_request",
            business_id=row.id,
            module="sales",
            title=f"销售收款确认 {row.request_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=row.approved_by.full_name if row.approved_by else None,
            approved_at=row.approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "request_no": row.request_no,
                "requested_amount": float(row.requested_amount or 0),
                "actual_amount": float(row.actual_amount or 0),
                "payment_stage": row.payment_stage,
            },
        )
        for row in rows
    ]


def _history_purchase_orders(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "purchase_order"):
        return []
    rows = db.execute(
        select(PurchaseOrder)
        .options(joinedload(PurchaseOrder.supplier))
        .where(
            PurchaseOrder.status.in_(["purchasing", "rejected", "partial_received", "fully_received", "cancelled"]),
            *_time_range_filter(start_date, end_date, PurchaseOrder.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="purchase_order",
            business_id=row.id,
            module="purchase",
            title=f"采购订单 {row.order_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=(_latest_purchase_order_approver(db, row.id)[0] or _user_name(db, row.approved_by_id)),
            approved_at=(_latest_purchase_order_approver(db, row.id)[1] or row.approved_at or row.updated_at),
            can_approve=False,
            can_reject=False,
            summary={
                "order_no": row.order_no,
                "supplier_name": row.supplier.name if row.supplier else None,
                "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
                "total_amount": float(row.total_amount),
            },
        )
        for row in rows
    ]


def _pending_sales_returns(current_user: User, db: Session) -> list[dict]:
    if not _can_act_on_approval_type(current_user, "sales_return"):
        return []
    rows = db.execute(select(SalesReturn).where(SalesReturn.status == "pending")).scalars().all()
    return [
        _base_item(
            approval_type="sales_return",
            business_id=row.id,
            module="sales",
            title=f"销售{return_type_label(row.return_type)} {row.return_no}",
            status=row.status,
            created_at=row.created_at,
            can_approve=True,
            can_reject=True,
            summary={
                "return_no": row.return_no,
                "return_type_label": return_type_label(row.return_type),
                "refund_amount": float(row.refund_amount),
                "reason": clean_display_text(row.reason, "销售退换货申请"),
            },
        )
        for row in rows
    ]


def _history_sales_returns(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "sales_return"):
        return []
    rows = db.execute(
        select(SalesReturn)
        .where(
            SalesReturn.status.in_(["approved", "rejected", "refunded"]),
            *_time_range_filter(start_date, end_date, SalesReturn.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="sales_return",
            business_id=row.id,
            module="sales",
            title=f"销售{return_type_label(row.return_type)} {row.return_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=_user_name(db, row.approved_by_id),
            approved_at=row.approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "return_no": row.return_no,
                "return_type_label": return_type_label(row.return_type),
                "refund_amount": float(row.refund_amount),
                "reason": clean_display_text(row.reason, "销售退换货申请"),
            },
        )
        for row in rows
    ]


def _pending_material_requests(current_user: User, db: Session) -> list[dict]:
    role_code = _role_code(current_user)
    if not _can_act_on_approval_type(current_user, "production_material_request"):
        return []
    rows = db.execute(
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
            joinedload(ProductionMaterialUsage.material),
        )
        .where(
            ProductionMaterialUsage.special_request_status == "not_required",
            ProductionMaterialUsage.inventory_approval_status == "pending_approval",
        )
    ).unique().scalars().all()
    if role_code not in {"inventory", "system_admin"}:
        return []
    return [
        _base_item(
            approval_type="production_material_request",
            business_id=row.id,
            module="production",
            title=f"领料审批 {row.production_task.task_no}",
            status=row.inventory_approval_status,
            created_at=row.created_at,
            can_approve=True,
            can_reject=True,
            summary={
                "task_no": row.production_task.task_no,
                "product_name": row.production_task.product.name if row.production_task and row.production_task.product else None,
                "material_name": row.material.name if row.material else None,
                "planned_quantity": float(row.planned_quantity or 0),
                "requested_quantity": float(row.requested_quantity or 0),
                "approved_quantity": float(row.approved_quantity or 0),
            },
        )
        for row in rows
    ]


def _history_material_requests(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    role_code = _role_code(current_user)
    if role_code == "production":
        query = (
            select(ProductionMaterialUsage)
            .join(ProductionTask, ProductionMaterialUsage.production_task_id == ProductionTask.id)
            .options(
                joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
                joinedload(ProductionMaterialUsage.material),
            )
            .where(
                ProductionMaterialUsage.special_request_status == "not_required",
                ProductionMaterialUsage.inventory_approval_status.in_(["approved", "rejected"]),
                ProductionTask.created_by_id == current_user.id,
                *_time_range_filter(start_date, end_date, ProductionMaterialUsage.updated_at),
            )
        )
    elif role_code in {"inventory", "manager", "chairman", "system_admin"}:
        query = (
            select(ProductionMaterialUsage)
            .options(
                joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
                joinedload(ProductionMaterialUsage.material),
            )
            .where(
                ProductionMaterialUsage.special_request_status == "not_required",
                ProductionMaterialUsage.inventory_approval_status.in_(["approved", "rejected"]),
                *_time_range_filter(start_date, end_date, ProductionMaterialUsage.updated_at),
            )
        )
    else:
        return []
    rows = db.execute(query).unique().scalars().all()
    return [
        _base_item(
            approval_type="production_material_request",
            business_id=row.id,
            module="production",
            title=f"领料审批 {row.production_task.task_no}",
            status=row.inventory_approval_status,
            created_at=row.updated_at,
            approved_by_name=_user_name(db, row.inventory_approved_by_id or row.manager_approved_by_id),
            approved_at=row.inventory_approved_at or row.manager_approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "task_no": row.production_task.task_no,
                "product_name": row.production_task.product.name if row.production_task and row.production_task.product else None,
                "material_name": row.material.name if row.material else None,
                "planned_quantity": float(row.planned_quantity or 0),
                "requested_quantity": float(row.requested_quantity or 0),
                "approved_quantity": float(row.approved_quantity or 0),
            },
        )
        for row in rows
    ]


def _pending_special_requests(current_user: User, db: Session) -> list[dict]:
    role_code = _role_code(current_user)
    if not _can_act_on_approval_type(current_user, "production_special_request"):
        return []
    rows = db.execute(
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
            joinedload(ProductionMaterialUsage.material),
        )
        .where(ProductionMaterialUsage.special_request_status == "pending_approval")
    ).unique().scalars().all()
    filtered_rows = []
    for row in rows:
        if role_code == "system_admin" and (
            row.inventory_approval_status == "pending_approval" or row.manager_approval_status == "pending_approval"
        ):
            filtered_rows.append(row)
        if role_code == "inventory" and row.inventory_approval_status == "pending_approval":
            filtered_rows.append(row)
        if role_code == "manager" and row.manager_approval_status == "pending_approval":
            filtered_rows.append(row)
    return [
        _base_item(
            approval_type="production_special_request",
            business_id=row.id,
            module="production",
            title=f"特殊领用 {row.production_task.task_no}",
            status=row.special_request_status,
            created_at=row.created_at,
            can_approve=True,
            can_reject=True,
            summary={
                "task_no": row.production_task.task_no,
                "product_name": row.production_task.product.name if row.production_task and row.production_task.product else None,
                "material_name": row.material.name if row.material else None,
                "requested_quantity": float(row.requested_quantity),
                "approved_quantity": float(row.approved_quantity),
            },
        )
        for row in filtered_rows
    ]


def _history_special_requests(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    query = (
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
            joinedload(ProductionMaterialUsage.material),
        )
        .where(
            ProductionMaterialUsage.special_request_status.in_(["approved", "rejected"]),
            *_time_range_filter(start_date, end_date, ProductionMaterialUsage.updated_at),
        )
    )
    role_code = _role_code(current_user)
    if role_code == "production":
        query = query.join(ProductionTask, ProductionMaterialUsage.production_task_id == ProductionTask.id).where(ProductionTask.created_by_id == current_user.id)
    elif role_code not in {"inventory", "manager", "chairman", "system_admin"}:
        return []
    rows = db.execute(query).unique().scalars().all()
    return [
        _base_item(
            approval_type="production_special_request",
            business_id=row.id,
            module="production",
            title=f"特殊领用 {row.production_task.task_no}",
            status=row.special_request_status,
            created_at=row.updated_at,
            approved_by_name=_user_name(db, row.inventory_approved_by_id or row.manager_approved_by_id),
            approved_at=row.inventory_approved_at or row.manager_approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "task_no": row.production_task.task_no,
                "product_name": row.production_task.product.name if row.production_task and row.production_task.product else None,
                "material_name": row.material.name if row.material else None,
                "requested_quantity": float(row.requested_quantity),
                "approved_quantity": float(row.approved_quantity),
            },
        )
        for row in rows
    ]


def _pending_business_matters(current_user: User, db: Session) -> list[dict]:
    if not _can_act_on_approval_type(current_user, "business_matter"):
        return []
    rows = db.execute(
        select(BusinessMatter)
        .options(joinedload(BusinessMatter.applicant))
        .where(BusinessMatter.status == "pending_manager_approval")
    ).scalars().all()
    return [
        _base_item(
            approval_type="business_matter",
            business_id=row.id,
            module="finance",
            title=f"事务申请 {row.matter_no}",
            status=row.status,
            created_at=row.created_at,
            can_approve=True,
            can_reject=True,
            summary={
                "matter_no": row.matter_no,
                "applicant_name": row.applicant.full_name if row.applicant else None,
                "expense_category": row.expense_category,
                "expense_subcategory": row.expense_subcategory,
                "estimated_amount": float(row.estimated_amount or 0),
                "detail": row.detail,
            },
        )
        for row in rows
    ]


def _history_business_matters(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "business_matter"):
        return []
    rows = db.execute(
        select(BusinessMatter)
        .options(joinedload(BusinessMatter.applicant), joinedload(BusinessMatter.manager_user))
        .where(
            BusinessMatter.status.in_(["approved", "rejected", "converted"]),
            *_time_range_filter(start_date, end_date, BusinessMatter.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="business_matter",
            business_id=row.id,
            module="finance",
            title=f"事务申请 {row.matter_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=row.manager_user.full_name if row.manager_user else None,
            approved_at=row.approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "matter_no": row.matter_no,
                "applicant_name": row.applicant.full_name if row.applicant else None,
                "manager_name": row.manager_user.full_name if row.manager_user else None,
                "estimated_amount": float(row.estimated_amount or 0),
                "actual_amount": float(row.actual_amount or 0),
                "detail": row.detail,
                "manager_remark": row.manager_remark,
            },
        )
        for row in rows
    ]


def _pending_reimbursements(current_user: User, db: Session) -> list[dict]:
    if not _can_act_on_approval_type(current_user, "reimbursement"):
        return []
    rows = db.execute(
        select(Reimbursement)
        .options(joinedload(Reimbursement.applicant))
        .where(Reimbursement.status.in_(["pending", "pending_chairman_approval"]))
    ).scalars().all()
    return [
        _base_item(
            approval_type="reimbursement",
            business_id=row.id,
            module="finance",
            title=f"报销申请 {row.reimbursement_no}",
            status="pending_chairman_approval" if row.status == "pending" else row.status,
            created_at=row.created_at,
            can_approve=True,
            can_reject=True,
            summary={
                "reimbursement_no": row.reimbursement_no,
                "applicant_name": row.applicant.full_name if row.applicant else None,
                "expense_category": row.expense_category,
                "expense_subcategory": row.expense_subcategory,
                "amount": float(row.amount or 0),
                "reason": row.reason,
            },
        )
        for row in rows
    ]


def _history_reimbursements(current_user: User, db: Session, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    if not _can_view_approval_type(current_user, "reimbursement"):
        return []
    rows = db.execute(
        select(Reimbursement)
        .options(joinedload(Reimbursement.applicant), joinedload(Reimbursement.chairman_user), joinedload(Reimbursement.finance_user))
        .where(
            Reimbursement.status.in_(["approved", "rejected", "paid"]),
            *_time_range_filter(start_date, end_date, Reimbursement.updated_at),
        )
    ).scalars().all()
    return [
        _base_item(
            approval_type="reimbursement",
            business_id=row.id,
            module="finance",
            title=f"报销申请 {row.reimbursement_no}",
            status=row.status,
            created_at=row.updated_at,
            approved_by_name=(row.chairman_user.full_name if row.chairman_user else None) or (row.finance_user.full_name if row.finance_user else None),
            approved_at=row.approved_at or row.updated_at,
            can_approve=False,
            can_reject=False,
            summary={
                "reimbursement_no": row.reimbursement_no,
                "applicant_name": row.applicant.full_name if row.applicant else None,
                "chairman_name": row.chairman_user.full_name if row.chairman_user else None,
                "finance_name": row.finance_user.full_name if row.finance_user else None,
                "amount": float(row.amount or 0),
                "reason": row.reason,
                "chairman_remark": row.chairman_remark,
                "finance_remark": row.finance_remark,
            },
        )
        for row in rows
    ]


def _pending_todo_approvals(current_user: User, db: Session) -> list[dict]:
    rows = db.execute(
        select(TodoItem)
        .where(
            TodoItem.assigned_user_id == current_user.id,
            TodoItem.status == "pending",
            TodoItem.business_type.in_(tuple(TODO_APPROVAL_TYPE_MAP.keys())),
        )
        .order_by(TodoItem.created_at.desc())
    ).scalars().all()
    data = []
    for row in rows:
        approval_type = TODO_APPROVAL_TYPE_MAP.get(row.business_type)
        if approval_type is None or row.business_id is None:
            continue
        if not _can_act_on_approval_type(current_user, approval_type):
            continue
        data.append(
            _base_item(
                approval_type=approval_type,
                business_id=row.business_id,
                module=row.module,
                title=row.title,
                status=row.status,
                created_at=row.created_at,
                can_approve=True,
                can_reject=True,
                summary={"description": row.description},
            )
        )
    return data


def _dedupe_approval_rows(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen_ids: set[str] = set()
    for row in rows:
        row_id = row.get("id")
        if not row_id or row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        result.append(row)
    return result


def _sales_order_detail(business_id: int, db: Session) -> dict:
    row = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.operator_user),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        )
        .where(SalesOrder.id == business_id)
    ).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="销售订单不存在")
    return {
        "order_no": row.order_no,
        "customer_name": row.customer.name if row.customer else None,
        "operator_name": row.operator_user.full_name if row.operator_user else None,
        "status": row.status,
        "status_label": status_label(row.status),
        "payment_type": row.payment_type,
        "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
        "consignee": row.consignee,
        "consignee_phone": row.consignee_phone,
        "delivery_address": row.delivery_address,
        "total_amount": float(row.total_amount),
        "deposit_amount": float(row.deposit_amount),
        "transport_fee": float(row.transport_fee),
        "packaging_fee": float(row.packaging_fee),
        "items": [
            {
                "product_name": item.product.name if item.product else None,
                "quantity": float(item.quantity),
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
            for item in row.items
        ],
    }


def _purchase_order_detail(business_id: int, db: Session) -> dict:
    row = db.execute(
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.operator_user),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
        )
        .where(PurchaseOrder.id == business_id)
    ).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    return {
        "order_no": row.order_no,
        "supplier_name": row.supplier.name if row.supplier else None,
        "operator_name": row.operator_user.full_name if row.operator_user else None,
        "status": row.status,
        "status_label": status_label(row.status),
        "payment_type": row.payment_type,
        "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
        "total_amount": float(row.total_amount),
        "items": [
            {
                "item_name": item.product.name if item.product else (item.material.name if item.material else None),
                "quantity": float(item.quantity),
                "unit_price": float(item.unit_price),
                "display_specification": item.display_specification,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "received_quantity": float(item.received_quantity),
                "returned_quantity": float(item.returned_quantity),
            }
            for item in row.items
        ],
    }


def _purchase_payment_request_detail(business_id: int, db: Session) -> dict:
    row = db.execute(select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == business_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = db.execute(
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.operator_user),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
        )
        .where(PurchaseOrder.id == row.purchase_order_id)
    ).unique().scalar_one_or_none()
    attachments = (
        db.execute(
            select(Attachment)
            .where(Attachment.business_type == "purchase_order", Attachment.business_id == row.purchase_order_id)
            .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        ).scalars().all()
        if row.purchase_order_id
        else []
    )
    return {
        "request_no": row.request_no,
        "purchase_order_no": order.order_no if order else None,
        "supplier_name": order.supplier.name if order and order.supplier else None,
        "operator_name": order.operator_user.full_name if order and order.operator_user else None,
        "payment_type": order.payment_type if order else None,
        "payment_type_label": payment_type_label(order.payment_type, module="purchase") if order else None,
        "order_status": order.status if order else None,
        "order_status_label": status_label(order.status) if order else None,
        "order_total_amount": float(order.total_amount or 0) if order else 0,
        "requested_amount": float(row.requested_amount or 0),
        "order_amount": float(row.order_amount or 0),
        "other_fee_amount": float(row.other_fee_amount or 0),
        "actual_amount": float(row.actual_amount or 0),
        "arrival_status": row.arrival_status,
        "arrival_status_label": status_label(row.arrival_status),
        "status": row.status,
        "status_label": status_label(row.status),
        "payment_detail_remark": row.payment_detail_remark,
        "manager_remark": row.manager_remark,
        "completion_remark": row.completion_remark,
        "contract_attachments": "；".join(item.original_name for item in attachments) if attachments else "-",
        "attachment_items": [
            {
                "id": item.id,
                "attachment_type": item.attachment_type,
                "original_name": item.original_name,
                "relative_path": item.relative_path,
            }
            for item in attachments
        ],
        "items": [
            {
                "item_name": item.product.name if item.product else (item.material.name if item.material else None),
                "item_code": item.product.product_code if item.product else (item.material.material_code if item.material else None),
                "quantity": float(item.quantity or 0),
                "unit_price": float(item.unit_price or 0),
                "received_quantity": float(item.received_quantity or 0),
                "returned_quantity": float(item.returned_quantity or 0),
                "display_specification": item.display_specification,
            }
            for item in (order.items if order else [])
        ],
    }


def _sales_receipt_request_detail(business_id: int, db: Session) -> dict:
    row = db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.id == business_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="销售收款确认不存在")
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.operator_user),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
        )
        .where(SalesOrder.id == row.sales_order_id)
    ).unique().scalar_one_or_none()
    attachments = (
        db.execute(
            select(Attachment)
            .where(Attachment.business_type == "sales_order", Attachment.business_id == row.sales_order_id)
            .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        ).scalars().all()
        if row.sales_order_id
        else []
    )
    receivable_rows = (
        db.execute(
            select(ReceivableRecord)
            .where(ReceivableRecord.sales_order_id == row.sales_order_id)
            .order_by(ReceivableRecord.received_at.asc(), ReceivableRecord.id.asc())
        ).scalars().all()
        if row.sales_order_id
        else []
    )
    received_amount = sum((Decimal(item.amount or 0) for item in receivable_rows), Decimal("0"))
    history_text = "；".join(
        f"{status_label(item.payment_stage) if item.payment_stage else '到账'} {float(item.amount or 0)}"
        for item in receivable_rows
    )
    return {
        "request_no": row.request_no,
        "sales_order_no": order.order_no if order else None,
        "customer_name": order.customer.name if order and order.customer else None,
        "operator_name": order.operator_user.full_name if order and order.operator_user else None,
        "payment_type": order.payment_type if order else None,
        "payment_type_label": payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales") if order else None,
        "order_status": order.status if order else None,
        "order_status_label": status_label(order.status) if order else None,
        "order_total_amount": float(order.total_amount or 0) if order else 0,
        "deposit_amount": float(order.deposit_amount or 0) if order else 0,
        "received_amount": float(received_amount),
        "remaining_amount": float(max(Decimal(order.total_amount or 0) - received_amount, Decimal("0"))) if order else 0,
        "requested_amount": float(row.requested_amount or 0),
        "actual_amount": float(row.actual_amount or 0),
        "payment_stage": row.payment_stage,
        "installment_total": row.installment_total,
        "installment_index": row.installment_index,
        "status": row.status,
        "status_label": status_label(row.status),
        "remark": row.remark,
        "approval_remark": row.approval_remark,
        "contract_attachments": "；".join(item.original_name for item in attachments) if attachments else "-",
        "attachment_items": [
            {
                "id": item.id,
                "attachment_type": item.attachment_type,
                "original_name": item.original_name,
                "relative_path": item.relative_path,
            }
            for item in attachments
        ],
        "receipt_history": history_text or "-",
        "items": [
            {
                "product_name": item.product.name if item.product else None,
                "quantity": float(item.quantity or 0),
                "unit_price": float(item.unit_price or 0),
                "total_amount": float(item.total_amount or 0),
                "packaging_config_id": item.packaging_config_id,
                "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                "display_specification": item.display_specification,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "packaging_unit_price": float(item.packaging_unit_price or 0),
                "packaging_fee": float(item.packaging_fee or 0),
                "remark": item.remark,
            }
            for item in (order.items if order else [])
        ],
    }


def _sales_return_detail(business_id: int, db: Session) -> dict:
    row = db.execute(select(SalesReturn).where(SalesReturn.id == business_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="销售退换货不存在")
    items = db.execute(select(SalesReturnItem).where(SalesReturnItem.sales_return_id == row.id)).scalars().all()
    return {
        "return_no": row.return_no,
        "return_type": row.return_type,
        "return_type_label": return_type_label(row.return_type),
        "status": row.status,
        "status_label": status_label(row.status),
        "reason": clean_display_text(row.reason, "销售退换货申请"),
        "refund_amount": float(row.refund_amount),
        "review_note": clean_display_text(row.review_note, "-") or "-",
        "items": [
            {
                "sales_order_item_id": item.sales_order_item_id,
                "quantity": float(item.quantity),
                "batch_suffix": item.batch_suffix,
            }
            for item in items
        ],
    }


def _material_request_detail(business_id: int, db: Session) -> dict:
    row = db.execute(
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
            joinedload(ProductionMaterialUsage.material),
        )
        .where(ProductionMaterialUsage.id == business_id)
    ).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="领料申请不存在")
    return {
        "task_no": row.production_task.task_no if row.production_task else None,
        "product_name": row.production_task.product.name if row.production_task and row.production_task.product else None,
        "material_name": row.material.name if row.material else None,
        "planned_quantity": float(row.planned_quantity or 0),
        "requested_quantity": float(row.requested_quantity or 0),
        "approved_quantity": float(row.approved_quantity or 0),
        "status": row.inventory_approval_status,
        "status_label": status_label(row.inventory_approval_status),
        "reason": row.reason,
        "inventory_approval_status": row.inventory_approval_status,
        "inventory_approval_status_label": status_label(row.inventory_approval_status),
        "inventory_remark": row.inventory_remark,
        "manager_approval_status": row.manager_approval_status,
        "manager_approval_status_label": status_label(row.manager_approval_status),
        "manager_remark": row.manager_remark,
    }


def _special_request_detail(business_id: int, db: Session) -> dict:
    row = db.execute(
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
            joinedload(ProductionMaterialUsage.material),
        )
        .where(ProductionMaterialUsage.id == business_id)
    ).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="特殊领用申请不存在")
    return {
        "task_no": row.production_task.task_no if row.production_task else None,
        "product_name": row.production_task.product.name if row.production_task and row.production_task.product else None,
        "material_name": row.material.name if row.material else None,
        "planned_quantity": float(row.planned_quantity),
        "requested_quantity": float(row.requested_quantity),
        "approved_quantity": float(row.approved_quantity),
        "status": row.special_request_status,
        "status_label": status_label(row.special_request_status),
        "reason": row.reason,
        "inventory_approval_status": row.inventory_approval_status,
        "inventory_approval_status_label": status_label(row.inventory_approval_status),
        "inventory_remark": row.inventory_remark,
        "manager_approval_status": row.manager_approval_status,
        "manager_approval_status_label": status_label(row.manager_approval_status),
        "manager_remark": row.manager_remark,
    }


def _business_matter_detail(business_id: int, db: Session) -> dict:
    row = db.execute(
        select(BusinessMatter)
        .options(joinedload(BusinessMatter.applicant), joinedload(BusinessMatter.manager_user))
        .where(BusinessMatter.id == business_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="事务申请不存在")
    return {
        "matter_no": row.matter_no,
        "applicant_name": row.applicant.full_name if row.applicant else None,
        "expense_category": row.expense_category,
        "expense_subcategory": row.expense_subcategory,
        "estimated_amount": float(row.estimated_amount or 0),
        "actual_amount": float(row.actual_amount or 0),
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "detail": row.detail,
        "status": row.status,
        "status_label": status_label(row.status),
        "manager_name": row.manager_user.full_name if row.manager_user else None,
        "manager_remark": row.manager_remark,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "attachment_items": ([{"id": row.attachment_id, "attachment_type": "事务附件", "original_name": "事务附件"}] if row.attachment_id else []),
    }


def _reimbursement_detail(business_id: int, db: Session) -> dict:
    row = db.execute(
        select(Reimbursement)
        .options(joinedload(Reimbursement.applicant), joinedload(Reimbursement.chairman_user), joinedload(Reimbursement.finance_user))
        .where(Reimbursement.id == business_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    return {
        "reimbursement_no": row.reimbursement_no,
        "applicant_name": row.applicant.full_name if row.applicant else None,
        "business_matter_id": row.business_matter_id,
        "expense_category": row.expense_category,
        "expense_subcategory": row.expense_subcategory,
        "amount": float(row.amount or 0),
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "reason": row.reason,
        "status": "pending_chairman_approval" if row.status == "pending" else row.status,
        "status_label": status_label("pending_chairman_approval" if row.status == "pending" else row.status),
        "chairman_name": row.chairman_user.full_name if row.chairman_user else None,
        "chairman_remark": row.chairman_remark,
        "finance_name": row.finance_user.full_name if row.finance_user else None,
        "finance_remark": row.finance_remark,
        "payment_method": row.payment_method,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
        "attachment_items": [
            item for item in [
                {"id": row.attachment_id, "attachment_type": "报销附件", "original_name": "报销附件"} if row.attachment_id else None,
                {"id": row.bank_receipt_attachment_id, "attachment_type": "出账凭证", "original_name": "出账凭证"} if row.bank_receipt_attachment_id else None,
            ] if item
        ],
    }


@router.get("/pending")
def list_pending_approvals(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repair_sales_receipt_workflows(db)
    data = _dedupe_approval_rows(
        [
            *_pending_sales_orders(current_user, db),
            *_pending_purchase_orders(current_user, db),
            *_pending_purchase_payment_requests(current_user, db),
            *_pending_sales_receipt_requests(current_user, db),
            *_pending_sales_returns(current_user, db),
            *_pending_material_requests(current_user, db),
            *_pending_special_requests(current_user, db),
            *_pending_business_matters(current_user, db),
            *_pending_reimbursements(current_user, db),
            *_pending_todo_approvals(current_user, db),
        ]
    )
    data.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return api_response(data=data)


@router.get("/history")
def list_approval_history(
    start_date: str | None = Query(default=None, description="开始日期 YYYY-MM-DD,默认本月1号"),
    end_date: str | None = Query(default=None, description="结束日期 YYYY-MM-DD,默认今天"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 默认本月:本月1号 ~ 今天
    today = datetime.now()
    if not start_date:
        start_date = today.replace(day=1).strftime("%Y-%m-%d")
    if not end_date:
        end_date = today.strftime("%Y-%m-%d")
    data = _dedupe_approval_rows(
        [
            *_history_sales_orders(current_user, db, start_date, end_date),
            *_history_purchase_orders(current_user, db, start_date, end_date),
            *_history_purchase_payment_requests(current_user, db, start_date, end_date),
            *_history_sales_receipt_requests(current_user, db, start_date, end_date),
            *_history_sales_returns(current_user, db, start_date, end_date),
            *_history_material_requests(current_user, db, start_date, end_date),
            *_history_special_requests(current_user, db, start_date, end_date),
            *_history_business_matters(current_user, db, start_date, end_date),
            *_history_reimbursements(current_user, db, start_date, end_date),
        ]
    )
    data.sort(key=lambda item: item.get("approved_at") or item.get("created_at") or "", reverse=True)
    return api_response(data={"items": data, "start_date": start_date, "end_date": end_date})


@router.get("/{approval_id}")
def get_approval_detail(approval_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    approval_type, business_id = _parse_approval_id(approval_id)
    _ensure_approval_permission(current_user, approval_type, action="view")
    if approval_type == "sales_order":
        detail = _sales_order_detail(business_id, db)
    elif approval_type == "purchase_order":
        detail = _purchase_order_detail(business_id, db)
    elif approval_type == "purchase_payment_request":
        detail = _purchase_payment_request_detail(business_id, db)
    elif approval_type == "sales_receipt_request":
        detail = _sales_receipt_request_detail(business_id, db)
    elif approval_type == "sales_return":
        detail = _sales_return_detail(business_id, db)
    elif approval_type == "production_material_request":
        detail = _material_request_detail(business_id, db)
    elif approval_type == "production_special_request":
        detail = _special_request_detail(business_id, db)
    elif approval_type == "business_matter":
        detail = _business_matter_detail(business_id, db)
    elif approval_type == "reimbursement":
        detail = _reimbursement_detail(business_id, db)
    else:
        raise HTTPException(status_code=404, detail="审批类型不存在")
    return api_response(data={"approval_id": approval_id, "approval_type": approval_type, "detail": detail, "readonly": True})


@router.post("/{approval_id}/approve")
def approve_item(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    approval_type, business_id = _parse_approval_id(approval_id)
    _ensure_approval_permission(current_user, approval_type, action="act")
    if approval_type == "sales_order":
        return approve_sales_order_service(
            business_id,
            SalesApprovalRequest(decision="approved", voucher_amount=payload.voucher_amount, actual_amount=payload.actual_amount, note=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "purchase_order":
        return approve_purchase_order_service(
            business_id,
            PurchaseApprovalRequest(decision="approved", note=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "purchase_payment_request":
        from schemas.purchases import PurchasePaymentApprovalRequest
        request = db.execute(select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == business_id)).scalar_one_or_none()
        approved_amount = payload.actual_amount if payload.actual_amount and payload.actual_amount > 0 else Decimal(request.requested_amount or 0) if request else Decimal("0.01")
        return approve_purchase_payment_request_service(
            business_id,
            PurchasePaymentApprovalRequest(decision="approved", actual_amount=approved_amount, manager_remark=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "sales_receipt_request":
        return approve_sales_receipt_request_service(
            business_id,
            SalesReceiptApprovalRequest(decision="approved", actual_amount=payload.actual_amount, approval_remark=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "sales_return":
        return approve_sales_return_service(
            business_id,
            SalesReturnApprovalRequest(decision="approved", note=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "production_material_request":
        return approve_special_request_service(
            business_id,
            SpecialMaterialApproval(
                decision="approved",
                approved_quantity=payload.approved_quantity,
                remark=payload.remark or payload.note,
                create_replenishment=payload.create_replenishment,
            ),
            current_user,
            db,
        )
    if approval_type == "production_special_request":
        return approve_special_request_service(
            business_id,
            SpecialMaterialApproval(
                decision="approved",
                approved_quantity=payload.approved_quantity,
                remark=payload.remark or payload.note,
                create_replenishment=payload.create_replenishment,
            ),
            current_user,
            db,
        )
    if approval_type == "business_matter":
        return audit_business_matter_service(business_id, BusinessMatterAuditRequest(decision="approved", manager_remark=payload.note or payload.remark), current_user, db)
    if approval_type == "reimbursement":
        return audit_reimbursement_service(business_id, ReimbursementAuditRequest(decision="approved", finance_remark=payload.note or payload.remark), current_user, db)
    raise HTTPException(status_code=404, detail="审批类型不存在")


@router.post("/{approval_id}/reject")
def reject_item(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    approval_type, business_id = _parse_approval_id(approval_id)
    _ensure_approval_permission(current_user, approval_type, action="act")
    if approval_type == "sales_order":
        return approve_sales_order_service(
            business_id,
            SalesApprovalRequest(decision="rejected", voucher_amount=payload.voucher_amount, actual_amount=payload.actual_amount, note=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "purchase_order":
        return approve_purchase_order_service(
            business_id,
            PurchaseApprovalRequest(decision="rejected", note=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "purchase_payment_request":
        from schemas.purchases import PurchasePaymentApprovalRequest
        return approve_purchase_payment_request_service(
            business_id,
            PurchasePaymentApprovalRequest(decision="rejected", actual_amount=max(payload.actual_amount, Decimal("0.01")), manager_remark=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "sales_receipt_request":
        return approve_sales_receipt_request_service(
            business_id,
            SalesReceiptApprovalRequest(decision="rejected", actual_amount=max(payload.actual_amount, Decimal("0.01")), approval_remark=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "sales_return":
        return approve_sales_return_service(
            business_id,
            SalesReturnApprovalRequest(decision="rejected", note=payload.note or payload.remark),
            current_user,
            db,
        )
    if approval_type == "production_material_request":
        return approve_special_request_service(
            business_id,
            SpecialMaterialApproval(
                decision="rejected",
                approved_quantity=payload.approved_quantity,
                remark=payload.remark or payload.note,
                create_replenishment=payload.create_replenishment,
            ),
            current_user,
            db,
        )
    if approval_type == "production_special_request":
        return approve_special_request_service(
            business_id,
            SpecialMaterialApproval(
                decision="rejected",
                approved_quantity=payload.approved_quantity,
                remark=payload.remark or payload.note,
                create_replenishment=payload.create_replenishment,
            ),
            current_user,
            db,
        )
    if approval_type == "business_matter":
        return audit_business_matter_service(business_id, BusinessMatterAuditRequest(decision="rejected", manager_remark=payload.note or payload.remark), current_user, db)
    if approval_type == "reimbursement":
        return audit_reimbursement_service(business_id, ReimbursementAuditRequest(decision="rejected", finance_remark=payload.note or payload.remark), current_user, db)
    raise HTTPException(status_code=404, detail="审批类型不存在")
