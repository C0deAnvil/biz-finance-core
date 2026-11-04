from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import require_roles
from models import Customer, Supplier, FinancialAccount, FinancialAccountTransaction, ReceivableRecord, PayableRecord, SalesOrder, SalesReceiptRequest, PurchasePaymentRequest, User
from responses import api_response
from schemas.accounts import FinancialAccountCreate, FinancialAccountStatusUpdate, FinancialAccountUpdate, FinancialAccountAdjustment, FinancialAccountTransfer
from services.financial_accounts import account_label, account_balance_map
from services.core import write_operation_log, next_business_no

router = APIRouter(prefix="/finance/accounts", tags=["finance"])
MANAGE_ROLES = ("finance", "finance_super", "system_admin")
SUPPLIER_MANAGE_ROLES = (*MANAGE_ROLES, "purchase", "master_data_admin")
CUSTOMER_MANAGE_ROLES = (*MANAGE_ROLES, "sales", "manager", "chairman", "master_data_admin")
READ_ROLES = (*SUPPLIER_MANAGE_ROLES, *CUSTOMER_MANAGE_ROLES)


def can_manage_counterparty_account(current_user: User, owner_type: str, *, customer: Customer | None = None) -> bool:
    role = current_user.role.code
    if role in MANAGE_ROLES:
        return True
    if owner_type == "supplier":
        return role in {"purchase", "master_data_admin"}
    if owner_type == "customer":
        if role in {"manager", "chairman", "master_data_admin"}:
            return True
        return role == "sales" and customer is not None and customer.owner_user_id == current_user.id
    return False


def account_is_referenced(db: Session, account_id: int) -> bool:
    references = (
        (SalesOrder, SalesOrder.receipt_account_id),
        (ReceivableRecord, ReceivableRecord.company_account_id),
        (ReceivableRecord, ReceivableRecord.counterparty_account_id),
        (PayableRecord, PayableRecord.company_account_id),
        (PayableRecord, PayableRecord.counterparty_account_id),
        (SalesReceiptRequest, SalesReceiptRequest.company_account_id),
        (SalesReceiptRequest, SalesReceiptRequest.counterparty_account_id),
        (PurchasePaymentRequest, PurchasePaymentRequest.company_account_id),
        (PurchasePaymentRequest, PurchasePaymentRequest.counterparty_account_id),
        (FinancialAccountTransaction, FinancialAccountTransaction.account_id),
        (FinancialAccountTransaction, FinancialAccountTransaction.related_account_id),
    )
    return any(db.scalar(select(model.id).where(column == account_id).limit(1)) is not None
               for model, column in references)


def serialize(row):
    return {"id": row.id, "owner_type": row.owner_type,
            "customer_id": row.customer_id, "supplier_id": row.supplier_id,
            "owner_name": row.customer.name if row.customer else (row.supplier.name if row.supplier else "本企业"),
            "account_name": row.account_name, "bank_name": row.bank_name,
            "account_number": row.account_number, "currency": row.currency,
            "is_active": row.is_active, "remark": row.remark, "label": account_label(row)}


@router.get("")
def list_accounts(owner_type: str | None = Query(default=None, pattern="^(company|customer|supplier)$"),
                  customer_id: int | None = None,
                  supplier_id: int | None = None,
                  active_only: bool = True,
                  current_user: User = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    query = select(FinancialAccount).options(joinedload(FinancialAccount.customer), joinedload(FinancialAccount.supplier))
    if owner_type:
        query = query.where(FinancialAccount.owner_type == owner_type)
    if supplier_id:
        query = query.where(FinancialAccount.supplier_id == supplier_id)
    if customer_id:
        query = query.where(FinancialAccount.customer_id == customer_id)
    if active_only:
        query = query.where(FinancialAccount.is_active.is_(True))
    role = current_user.role.code
    if role == "sales":
        query = query.outerjoin(Customer, FinancialAccount.customer_id == Customer.id).where(
            (FinancialAccount.owner_type == "company") |
            ((FinancialAccount.owner_type == "customer") & (Customer.owner_user_id == current_user.id)))
    elif role == "purchase":
        query = query.where(FinancialAccount.owner_type.in_(["company", "supplier"]))
    rows = db.scalars(query.order_by(FinancialAccount.id.desc())).all()
    balances = account_balance_map(db, [row.id for row in rows if row.owner_type == "company"]) if role in {*MANAGE_ROLES, "manager", "chairman"} else {}
    return api_response(data=[{**serialize(row), **balances.get(row.id, {})} for row in rows])


@router.get("/{account_id}/transactions")
def list_transactions(account_id: int, current_user: User = Depends(require_roles(*MANAGE_ROLES)), db: Session = Depends(get_db)):
    if db.get(FinancialAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    rows = db.scalars(select(FinancialAccountTransaction).where(
        FinancialAccountTransaction.account_id == account_id
    ).order_by(FinancialAccountTransaction.id.desc())).all()
    return api_response(data=[{
        "id": row.id, "transaction_type": row.transaction_type, "amount": float(row.amount),
        "reference_no": row.reference_no, "remark": row.remark, "created_at": row.created_at,
        "related_account_id": row.related_account_id,
    } for row in rows])


@router.post("")
def create_account(payload: FinancialAccountCreate, current_user: User = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    customer = db.get(Customer, payload.customer_id) if payload.customer_id else None
    if payload.customer_id and customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if payload.supplier_id and db.get(Supplier, payload.supplier_id) is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if not can_manage_counterparty_account(current_user, payload.owner_type, customer=customer):
        raise HTTPException(status_code=403, detail="无权维护该客户或供应商的账户")
    duplicate = db.scalar(select(FinancialAccount.id).where(
        FinancialAccount.owner_type == payload.owner_type,
        FinancialAccount.customer_id == payload.customer_id,
        FinancialAccount.supplier_id == payload.supplier_id,
        FinancialAccount.account_name == payload.account_name,
        FinancialAccount.account_number == payload.account_number,
        FinancialAccount.bank_name == payload.bank_name,
    ))
    if duplicate:
        raise HTTPException(status_code=409, detail="该账户已存在，请检查已有账户（含停用账户）")
    row = FinancialAccount(**payload.model_dump())
    db.add(row)
    db.flush()
    write_operation_log(db, user_id=current_user.id, module="finance", action="create_financial_account",
                        content=f"新增{row.owner_type}账户 #{row.id}")
    db.commit()
    return api_response(data=serialize(row))


@router.put("/{account_id}")
def update_account_status(account_id: int, payload: FinancialAccountStatusUpdate,
                          current_user: User = Depends(require_roles(*READ_ROLES)), db: Session = Depends(get_db)):
    row = db.get(FinancialAccount, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    customer = db.get(Customer, row.customer_id) if row.customer_id else None
    if not can_manage_counterparty_account(current_user, row.owner_type, customer=customer):
        raise HTTPException(status_code=403, detail="无权维护该客户或供应商的账户")
    row.is_active = payload.is_active
    write_operation_log(db, user_id=current_user.id, module="finance", action="update_financial_account",
                        content=f"{'启用' if row.is_active else '停用'}账户 #{row.id}")
    db.commit()
    return api_response(data=serialize(row))


@router.patch("/{account_id}")
def update_account(account_id: int, payload: FinancialAccountUpdate,
                   current_user: User = Depends(require_roles(*MANAGE_ROLES)), db: Session = Depends(get_db)):
    row = db.get(FinancialAccount, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    account_name = payload.account_name.strip()
    bank_name = (payload.bank_name or "").strip() or None
    account_number = (payload.account_number or "").strip() or None
    identity_changed = (row.account_name, row.bank_name, row.account_number, row.currency) != (
        account_name, bank_name, account_number, payload.currency
    )
    if identity_changed and account_is_referenced(db, account_id):
        raise HTTPException(status_code=409, detail="该账户已有历史单据引用，不能修改开户身份信息；请停用旧账户并新增账户")
    values = {**payload.model_dump(), "account_name": account_name, "bank_name": bank_name, "account_number": account_number}
    for key, value in values.items():
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    write_operation_log(db, user_id=current_user.id, module="finance", action="update_financial_account",
                        content=f"修改账户 #{row.id}")
    db.commit()
    return api_response(data=serialize(row))


@router.delete("/{account_id}")
def delete_account(account_id: int, current_user: User = Depends(require_roles(*MANAGE_ROLES)), db: Session = Depends(get_db)):
    row = db.get(FinancialAccount, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    if account_is_referenced(db, account_id):
        raise HTTPException(status_code=409, detail="该账户已有历史单据或余额流水，不能删除；请将账户停用")
    write_operation_log(db, user_id=current_user.id, module="finance", action="delete_financial_account",
                        content=f"删除{row.owner_type}账户 #{row.id} {row.account_name} {row.account_number}")
    db.delete(row)
    db.commit()
    return api_response(msg="账户已删除")


@router.post("/{account_id}/adjustments")
def adjust_account(account_id: int, payload: FinancialAccountAdjustment,
                   current_user: User = Depends(require_roles(*MANAGE_ROLES)), db: Session = Depends(get_db)):
    row = db.get(FinancialAccount, account_id)
    if row is None or row.owner_type != "company":
        raise HTTPException(status_code=409, detail="只有企业账户可以进行余额调整")
    if payload.amount == 0:
        raise HTTPException(status_code=422, detail="调整金额不能为 0")
    entry = FinancialAccountTransaction(account_id=account_id, transaction_type="adjustment",
        amount=Decimal(str(payload.amount)), reference_no=next_business_no(db, "TZ"),
        remark=payload.remark.strip(), created_by_id=current_user.id)
    db.add(entry)
    write_operation_log(db, user_id=current_user.id, module="finance", action="adjust_financial_account",
                        content=f"调整账户 #{account_id} {payload.amount}")
    db.commit()
    return api_response(data={"reference_no": entry.reference_no, "amount": float(entry.amount)})


@router.post("/transfers")
def transfer_accounts(payload: FinancialAccountTransfer,
                      current_user: User = Depends(require_roles(*MANAGE_ROLES)), db: Session = Depends(get_db)):
    if payload.from_account_id == payload.to_account_id:
        raise HTTPException(status_code=422, detail="转出和转入账户不能相同")
    source = db.get(FinancialAccount, payload.from_account_id)
    target = db.get(FinancialAccount, payload.to_account_id)
    if not source or not target or source.owner_type != "company" or target.owner_type != "company":
        raise HTTPException(status_code=409, detail="账户转账只能在企业账户之间进行")
    if not source.is_active or not target.is_active or source.currency != target.currency:
        raise HTTPException(status_code=409, detail="请选择启用且币种一致的企业账户")
    ref = next_business_no(db, "ZZ")
    amount = Decimal(str(payload.amount))
    db.add_all([
        FinancialAccountTransaction(account_id=source.id, related_account_id=target.id, transaction_type="transfer_out", amount=-amount, reference_no=ref, remark=payload.remark.strip(), created_by_id=current_user.id),
        FinancialAccountTransaction(account_id=target.id, related_account_id=source.id, transaction_type="transfer_in", amount=amount, reference_no=ref, remark=payload.remark.strip(), created_by_id=current_user.id),
    ])
    write_operation_log(db, user_id=current_user.id, module="finance", action="transfer_financial_account",
                        content=f"账户 #{source.id} 转入账户 #{target.id} {payload.amount}")
    db.commit()
    return api_response(data={"reference_no": ref, "amount": float(amount)})
