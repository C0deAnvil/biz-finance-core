from decimal import Decimal

from pydantic import BaseModel, Field


class ApprovalDecisionRequest(BaseModel):
    note: str | None = None
    remark: str | None = None
    voucher_amount: Decimal = 0
    actual_amount: Decimal = 0
    approved_quantity: Decimal | None = Field(default=None, gt=0)
