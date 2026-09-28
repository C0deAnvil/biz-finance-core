from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import BusinessMatter, Reimbursement, Role, User
from responses import api_response
from schemas.reimbursements import (
    BusinessMatterAuditRequest,
    BusinessMatterConvertRequest,
    BusinessMatterCreate,
    ReimbursementAuditRequest,
    ReimbursementCreate,
    ReimbursementPayRequest,
)
from services.core import _role_user_ids, complete_todos, create_cash_flow, create_notification, create_todo, next_business_no, resolve_assigned_finance_user_id, write_operation_log
from services.time_utils import now_local


router = APIRouter(prefix="/reimbursements", tags=["reimbursements"])

CATEGORY_LABELS = {
    "management": "管理费用",
    "sales": "销售费用",
    "finance": "财务费用",
    "other": "其他",
}

SUBCATEGORY_OPTIONS = {
    "management": ["薪酬费用", "办公费用", "保洁费", "职工福利", "水电燃气", "会议费", "招聘费用", "交通费用", "通讯费用", "差旅费用", "出勤费用", "资质费用", "软件服务费", "检测费用", "车辆费用", "营业外支出"],
    "sales": ["客户费用", "展会费用", "平台费用", "平台服务费", "交易理赔"],
    "finance": ["财务费用"],
    "other": ["其他"],
}

REIMBURSEMENT_STATUS_LABELS = {
    "pending_chairman_approval": "待董事长审批",
    "approved": "待财务出账",
    "rejected": "已驳回",
    "paid": "已报销",
    "pending": "待董事长审批",
}

MATTER_STATUS_LABELS = {
    "pending_manager_approval": "待经理审批",
    "approved": "事务已通过",
    "rejected": "已驳回",
    "converted": "已转报销",
}


def _validate_category(expense_category: str, expense_subcategory: str) -> None:
    if expense_category in SUBCATEGORY_OPTIONS and expense_subcategory not in SUBCATEGORY_OPTIONS[expense_category]:
        raise HTTPException(status_code=409, detail="费用小类不属于所选费用大类")


def _serialize_reimbursement(row: Reimbursement) -> dict:
    return {
        "id": row.id,
        "reimbursement_no": row.reimbursement_no,
        "business_matter_id": row.business_matter_id,
        "applicant_user_id": row.applicant_user_id,
        "applicant_name": row.applicant.full_name if row.applicant else None,
        "expense_category": row.expense_category,
        "expense_category_label": CATEGORY_LABELS.get(row.expense_category, row.expense_category),
        "expense_subcategory": row.expense_subcategory,
        "amount": float(row.amount or 0),
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "reason": row.reason,
        "status": "pending_chairman_approval" if row.status == "pending" else row.status,
        "status_label": REIMBURSEMENT_STATUS_LABELS.get(row.status, row.status),
        "chairman_user_id": row.chairman_user_id,
        "chairman_user_name": row.chairman_user.full_name if row.chairman_user else None,
        "chairman_remark": row.chairman_remark,
        "finance_user_id": row.finance_user_id,
        "assigned_finance_user_id": row.assigned_finance_user_id,
        "finance_user_name": row.finance_user.full_name if row.finance_user else None,
        "finance_remark": row.finance_remark,
        "payment_method": row.payment_method,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
        "attachment_id": row.attachment_id,
        "bank_receipt_attachment_id": row.bank_receipt_attachment_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_matter(row: BusinessMatter) -> dict:
    return {
        "id": row.id,
        "matter_no": row.matter_no,
        "applicant_user_id": row.applicant_user_id,
        "applicant_name": row.applicant.full_name if row.applicant else None,
        "expense_category": row.expense_category,
        "expense_category_label": CATEGORY_LABELS.get(row.expense_category, row.expense_category),
        "expense_subcategory": row.expense_subcategory,
        "estimated_amount": float(row.estimated_amount or 0),
        "actual_amount": float(row.actual_amount or 0),
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "detail": row.detail,
        "status": row.status,
        "status_label": MATTER_STATUS_LABELS.get(row.status, row.status),
        "manager_user_id": row.manager_user_id,
        "manager_user_name": row.manager_user.full_name if row.manager_user else None,
        "manager_remark": row.manager_remark,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "converted_reimbursement_id": row.converted_reimbursement_id,
        "attachment_id": row.attachment_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _can_view_all(current_user: User) -> bool:
    return (current_user.role.code if current_user.role else "") in {"finance", "manager", "chairman", "system_admin"}


@router.get("/options")
def reimbursement_options(current_user: User = Depends(get_current_user)):
    return api_response(
        data={
            "categories": [
                {"value": key, "label": label, "children": [{"value": item, "label": item} for item in SUBCATEGORY_OPTIONS[key]]}
                for key, label in CATEGORY_LABELS.items()
            ],
            "statuses": [{"value": key, "label": value} for key, value in REIMBURSEMENT_STATUS_LABELS.items()],
            "matter_statuses": [{"value": key, "label": value} for key, value in MATTER_STATUS_LABELS.items()],
        }
    )


@router.get("/matters")
def list_business_matters(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(BusinessMatter)
        .options(joinedload(BusinessMatter.applicant), joinedload(BusinessMatter.manager_user))
        .order_by(BusinessMatter.created_at.desc(), BusinessMatter.id.desc())
    )
    if not _can_view_all(current_user):
        query = query.where(BusinessMatter.applicant_user_id == current_user.id)
    rows = db.execute(query).scalars().all()
    return api_response(data=[_serialize_matter(row) for row in rows])


@router.post("/matters")
def create_business_matter(payload: BusinessMatterCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _validate_category(payload.expense_category, payload.expense_subcategory)
    row = BusinessMatter(
        matter_no=next_business_no(db, "SW"),
        applicant_user_id=current_user.id,
        expense_category=payload.expense_category,
        expense_subcategory=payload.expense_subcategory,
        estimated_amount=Decimal(payload.estimated_amount or 0),
        occurred_at=payload.occurred_at,
        detail=payload.detail,
        attachment_id=payload.attachment_id,
        status="pending_manager_approval",
    )
    db.add(row)
    db.flush()
    manager_user_ids = _role_user_ids(db, "manager")
    for user_id in manager_user_ids:
        create_todo(
            db,
            title=f"事务审批 {row.matter_no}",
            description=f"{current_user.full_name} 提交出账类事务申请，预估金额 {float(row.estimated_amount or 0):.4f} 元。",
            module="finance",
            assigned_user_id=user_id,
            business_type="business_matter",
            business_id=row.id,
            priority="high",
        )
    if manager_user_ids:
        create_notification(
            db,
            title="收到事务申请",
            content=f"事务申请 {row.matter_no} 待经理审批。",
            module="finance",
            recipient_user_ids=manager_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    write_operation_log(db, user_id=current_user.id, module="finance", action="create_business_matter", content=f"提交事务申请 {row.matter_no}")
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_matter(row))


@router.post("/matters/{matter_id}/audit")
def audit_business_matter(
    matter_id: int,
    payload: BusinessMatterAuditRequest,
    current_user: User = Depends(require_roles("manager", "system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(BusinessMatter).where(BusinessMatter.id == matter_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="事务申请不存在")
    if row.status != "pending_manager_approval":
        raise HTTPException(status_code=409, detail="当前事务申请不在待经理审批状态")
    row.status = "approved" if payload.decision == "approved" else "rejected"
    row.manager_user_id = current_user.id
    row.manager_remark = payload.manager_remark
    row.approved_at = now_local()
    complete_todos(db, business_type="business_matter", business_id=row.id)
    create_notification(
        db,
        title="事务审批结果",
        content=f"事务申请 {row.matter_no} {'已通过' if row.status == 'approved' else '已驳回'}。{payload.manager_remark or ''}",
        module="finance",
        recipient_user_ids=[row.applicant_user_id],
        created_by_id=current_user.id,
        category="success" if row.status == "approved" else "warning",
    )
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_matter(row))


@router.post("/matters/{matter_id}/convert-reimbursement")
def convert_matter_to_reimbursement(
    matter_id: int,
    payload: BusinessMatterConvertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    matter = db.execute(select(BusinessMatter).where(BusinessMatter.id == matter_id)).scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="事务申请不存在")
    if matter.converted_reimbursement_id:
        raise HTTPException(status_code=409, detail="该事务已转为报销，不能重复操作")
    if matter.status != "approved":
        raise HTTPException(status_code=409, detail="仅审批通过的事务可以转报销")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"finance", "manager", "chairman", "system_admin"} and matter.applicant_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅申请人、财务或领导层可以将该事务转为报销")
    row = _create_reimbursement_row(
        db,
        applicant_user_id=matter.applicant_user_id,
        expense_category=matter.expense_category,
        expense_subcategory=matter.expense_subcategory,
        amount=Decimal(payload.actual_amount or 0),
        occurred_at=payload.occurred_at or now_local(),
        reason=payload.reason or matter.detail,
        attachment_id=payload.attachment_id or matter.attachment_id,
        business_matter_id=matter.id,
        created_by_id=current_user.id,
        assigned_finance_user_id=payload.assigned_finance_user_id,
    )
    matter.status = "converted"
    matter.actual_amount = Decimal(payload.actual_amount or 0)
    matter.converted_reimbursement_id = row.id
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_reimbursement(row))


def _create_reimbursement_row(
    db: Session,
    *,
    applicant_user_id: int,
    expense_category: str,
    expense_subcategory: str,
    amount: Decimal,
    occurred_at: datetime,
    reason: str,
    attachment_id: int | None,
    business_matter_id: int | None,
    created_by_id: int,
    assigned_finance_user_id: int | None = None,
) -> Reimbursement:
    row = Reimbursement(
        reimbursement_no=next_business_no(db, "BX"),
        applicant_user_id=applicant_user_id,
        expense_category=expense_category,
        expense_subcategory=expense_subcategory,
        amount=amount,
        occurred_at=occurred_at,
        reason=reason,
        attachment_id=attachment_id,
        business_matter_id=business_matter_id,
        status="pending_chairman_approval",
        # 申请时即指定由谁出账;董事长审批通过后待办直接派给此人
        assigned_finance_user_id=resolve_assigned_finance_user_id(db, assigned_finance_user_id),
    )
    db.add(row)
    db.flush()
    chairman_user_ids = _role_user_ids(db, "chairman")
    for user_id in chairman_user_ids:
        create_todo(
            db,
            title=f"报销审批 {row.reimbursement_no}",
            description=f"报销申请金额 {float(row.amount or 0):.4f} 元，请董事长审批。",
            module="finance",
            assigned_user_id=user_id,
            business_type="reimbursement",
            business_id=row.id,
            priority="high",
        )
    create_notification(
        db,
        title="报销申请已提交",
        content=f"报销单 {row.reimbursement_no} 已提交，等待董事长审批。",
        module="finance",
        recipient_user_ids=list(dict.fromkeys([applicant_user_id, *chairman_user_ids])),
        created_by_id=created_by_id,
        category="warning",
    )
    write_operation_log(db, user_id=created_by_id, module="finance", action="create_reimbursement", content=f"提交报销 {row.reimbursement_no}")
    return row


@router.get("")
def list_reimbursements(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(Reimbursement)
        .options(joinedload(Reimbursement.applicant), joinedload(Reimbursement.finance_user), joinedload(Reimbursement.chairman_user))
        .order_by(Reimbursement.created_at.desc(), Reimbursement.id.desc())
    )
    if not _can_view_all(current_user):
        query = query.where(Reimbursement.applicant_user_id == current_user.id)
    rows = db.execute(query).scalars().all()
    return api_response(data=[_serialize_reimbursement(row) for row in rows])


@router.post("")
def create_reimbursement(payload: ReimbursementCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _validate_category(payload.expense_category, payload.expense_subcategory)
    row = _create_reimbursement_row(
        db,
        applicant_user_id=current_user.id,
        expense_category=payload.expense_category,
        expense_subcategory=payload.expense_subcategory,
        amount=Decimal(payload.amount),
        occurred_at=payload.occurred_at,
        reason=payload.reason,
        attachment_id=payload.attachment_id,
        business_matter_id=payload.business_matter_id,
        created_by_id=current_user.id,
        assigned_finance_user_id=payload.assigned_finance_user_id,
    )
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_reimbursement(row))


@router.post("/{reimbursement_id}/audit")
def audit_reimbursement(
    reimbursement_id: int,
    payload: ReimbursementAuditRequest,
    current_user: User = Depends(require_roles("chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(Reimbursement).where(Reimbursement.id == reimbursement_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    if row.status not in {"pending", "pending_chairman_approval"}:
        raise HTTPException(status_code=409, detail="当前报销单不在待董事长审批状态")
    row.status = "approved" if payload.decision == "approved" else "rejected"
    row.chairman_user_id = current_user.id
    row.chairman_remark = payload.finance_remark
    row.approved_at = now_local()
    complete_todos(db, business_type="reimbursement", business_id=row.id)
    if row.status == "approved":
        # 出账待办派给申请时指定的财务;未指定时回落到全体财务
        finance_user_ids = (
            [row.assigned_finance_user_id]
            if row.assigned_finance_user_id
            else _role_user_ids(db, "finance")
        )
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"报销出账 {row.reimbursement_no}",
                description="报销已审批通过，请财务上传出账凭证并完成实际报销出账。",
                module="finance",
                assigned_user_id=user_id,
                business_type="reimbursement_payment",
                business_id=row.id,
                priority="high",
            )
        create_notification(
            db,
            title="报销待财务出账",
            content=f"报销单 {row.reimbursement_no} 已通过董事长审批，请财务完成出账。",
            module="finance",
            recipient_user_ids=finance_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    create_notification(
        db,
        title="报销审批结果",
        content=f"报销单 {row.reimbursement_no} {'已通过' if row.status == 'approved' else '已驳回'}。{payload.finance_remark or ''}",
        module="finance",
        recipient_user_ids=[row.applicant_user_id],
        created_by_id=current_user.id,
        category="success" if row.status == "approved" else "warning",
    )
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_reimbursement(row))


@router.post("/{reimbursement_id}/pay")
def pay_reimbursement(
    reimbursement_id: int,
    payload: ReimbursementPayRequest,
    current_user: User = Depends(require_roles("finance", "system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(Reimbursement).where(Reimbursement.id == reimbursement_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    if row.status != "approved":
        raise HTTPException(status_code=409, detail="仅审批通过的报销单可以出账")
    if not payload.bank_receipt_attachment_id:
        raise HTTPException(status_code=409, detail="请先上传银行回单或出账凭证")
    paid_at = payload.paid_at or now_local()
    row.status = "paid"
    row.finance_user_id = current_user.id
    row.finance_remark = payload.finance_remark or row.finance_remark
    row.payment_method = payload.payment_method
    row.bank_receipt_attachment_id = payload.bank_receipt_attachment_id
    row.paid_at = paid_at
    create_cash_flow(
        db,
        flow_type="outgoing",
        business_type="reimbursement",
        business_id=row.id,
        related_no=row.reimbursement_no,
        amount=row.amount,
        happened_at=paid_at,
        remark=f"报销出账：{row.expense_subcategory}",
    )
    complete_todos(db, business_type="reimbursement_payment", business_id=row.id)
    create_notification(
        db,
        title="报销已出账",
        content=f"报销单 {row.reimbursement_no} 已完成实际报销，金额 {float(row.amount or 0):.4f} 元。",
        module="finance",
        recipient_user_ids=list(dict.fromkeys([row.applicant_user_id, *_role_user_ids(db, "chairman")])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(db, user_id=current_user.id, module="finance", action="pay_reimbursement", content=f"报销出账 {row.reimbursement_no}")
    db.commit()
    db.refresh(row)
    return api_response(data=_serialize_reimbursement(row))
