from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ReimbursementCreate(BaseModel):
    expense_category: str = Field(..., pattern="^(management|sales|finance|other)$")
    expense_subcategory: str = Field(..., max_length=80)
    amount: Decimal = Field(..., gt=0)
    occurred_at: datetime
    reason: str = Field(..., min_length=2, max_length=1000)
    attachment_id: int | None = None
    business_matter_id: int | None = None


class ReimbursementAuditRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    finance_remark: str | None = Field(default=None, max_length=1000)


class ReimbursementPayRequest(BaseModel):
    paid_at: datetime | None = None
    finance_remark: str | None = Field(default=None, max_length=1000)
    payment_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None


class BusinessMatterCreate(BaseModel):
    expense_category: str = Field(..., pattern="^(management|sales|finance|other)$")
    expense_subcategory: str = Field(..., max_length=80)
    estimated_amount: Decimal = Field(..., ge=0)
    occurred_at: datetime
    detail: str = Field(..., min_length=2, max_length=2000)
    attachment_id: int | None = None


class BusinessMatterAuditRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    manager_remark: str | None = Field(default=None, max_length=1000)


class BusinessMatterConvertRequest(BaseModel):
    actual_amount: Decimal = Field(..., gt=0)
    occurred_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=1000)
    attachment_id: int | None = None
