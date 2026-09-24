from fastapi import HTTPException
from decimal import Decimal
from sqlalchemy import func, select
from models import FinancialAccount, ReceivableRecord, PayableRecord, FinancialAccountTransaction


def account_balance_map(db, account_ids):
    """已登记余额来自已入账收付记录，待审批申请不计入，不另存余额缓存。"""
    incoming = dict(db.execute(select(ReceivableRecord.company_account_id, func.sum(ReceivableRecord.amount))
                              .where(ReceivableRecord.company_account_id.in_(account_ids))
                              .group_by(ReceivableRecord.company_account_id)).all())
    outgoing = dict(db.execute(select(PayableRecord.company_account_id, func.sum(PayableRecord.amount))
                              .where(PayableRecord.company_account_id.in_(account_ids),
                                     PayableRecord.status.in_(["paid", "partial_settled", "settled"]))
                              .group_by(PayableRecord.company_account_id)).all())
    adjustments = dict(db.execute(select(FinancialAccountTransaction.account_id, func.sum(FinancialAccountTransaction.amount))
                                  .where(FinancialAccountTransaction.account_id.in_(account_ids))
                                  .group_by(FinancialAccountTransaction.account_id)).all())
    return {aid: {"received_amount": float(incoming.get(aid, 0)), "paid_amount": float(outgoing.get(aid, 0)),
                  "transaction_amount": float(adjustments.get(aid, 0)),
                  "balance": float(Decimal(incoming.get(aid, 0)) - Decimal(outgoing.get(aid, 0)) + Decimal(adjustments.get(aid, 0)))} for aid in account_ids}


def validate_supplier_account(db, account_id, supplier_id):
    account = db.get(FinancialAccount, account_id) if account_id else None
    if account is None or account.owner_type != "supplier" or account.supplier_id != supplier_id or not account.is_active:
        raise HTTPException(status_code=409, detail="请选择当前供应商启用的收款账户，请在供应商基本信息中维护")
    if account.currency != "CNY":
        raise HTTPException(status_code=409, detail="当前付款以人民币计价，请选择 CNY 账户")
    return account.id


def requested_account_id(selected_id, requested_id, label):
    """财务核对申请账户；旧申请未记录账户时才允许补选。"""
    if requested_id and selected_id and selected_id != requested_id:
        raise HTTPException(status_code=409, detail=f"{label}必须与申请人选择的账户一致")
    return requested_id or selected_id


def account_label(account):
    if account is None:
        return None
    return f"{account.account_name} - {account.bank_name} - {account.account_number} ({account.currency})"


def settlement_account_fields(row):
    return {
        "company_account_id": row.company_account_id,
        "counterparty_account_id": row.counterparty_account_id,
        "company_account_label": account_label(row.company_account),
        "counterparty_account_label": account_label(row.counterparty_account),
    }


def validate_settlement_accounts(db, company_account_id, counterparty_account_id=None, *, customer_id=None, supplier_id=None):
    company = db.get(FinancialAccount, company_account_id) if company_account_id else None
    if company is None or company.owner_type != "company" or not company.is_active:
        raise HTTPException(status_code=409, detail="请选择启用的企业收付款账户（由财务在账户管理中维护）")
    if company.currency != "CNY":
        raise HTTPException(status_code=409, detail="当前结算以人民币计价，请选择 CNY 账户")
    if counterparty_account_id:
        other = db.get(FinancialAccount, counterparty_account_id)
        expected_type = "customer" if customer_id is not None else "supplier"
        expected_id = customer_id if customer_id is not None else supplier_id
        if (other is None or not other.is_active or other.owner_type != expected_type
                or getattr(other, f"{expected_type}_id") != expected_id):
            raise HTTPException(status_code=409, detail="往来账户不属于当前客户/供应商，或已停用")
        if other.currency != company.currency:
            raise HTTPException(status_code=409, detail="收付款双方账户币种必须一致")
    return {"company_account_id": company.id, "counterparty_account_id": counterparty_account_id}
