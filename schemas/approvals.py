from decimal import Decimal
from schemas.money import Money

from pydantic import BaseModel, Field


class ApprovalDecisionRequest(BaseModel):
    note: str | None = None
    remark: str | None = None
    voucher_amount: Money = 0
    actual_amount: Money = 0
    approved_quantity: Decimal | None = Field(default=None, gt=0)
    create_replenishment: bool = False
