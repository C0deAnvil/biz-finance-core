from pydantic import BaseModel, Field, model_validator


class FinancialAccountCreate(BaseModel):
    owner_type: str = Field(..., pattern="^(company|customer|supplier)$")
    customer_id: int | None = None
    supplier_id: int | None = None
    account_name: str = Field(..., min_length=1, max_length=150)
    bank_name: str | None = Field(default=None, max_length=150)
    account_number: str | None = Field(default=None, max_length=80)
    currency: str = Field(default="CNY", pattern="^[A-Z]{3}$")
    remark: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_owner(self):
        valid = {
            "company": not self.customer_id and not self.supplier_id,
            "customer": bool(self.customer_id) and not self.supplier_id,
            "supplier": bool(self.supplier_id) and not self.customer_id,
        }
        if not valid[self.owner_type]:
            raise ValueError("请选择与账户类型对应的客户或供应商")
        self.account_name = self.account_name.strip()
        if not self.account_name:
            raise ValueError("账户名称不能为空")
        self.bank_name = (self.bank_name or "").strip() or None
        self.account_number = (self.account_number or "").strip() or None
        return self


class FinancialAccountStatusUpdate(BaseModel):
    is_active: bool


class FinancialAccountUpdate(BaseModel):
    account_name: str = Field(..., min_length=1, max_length=150)
    bank_name: str | None = Field(default=None, max_length=150)
    account_number: str | None = Field(default=None, max_length=80)
    currency: str = Field(default="CNY", pattern="^[A-Z]{3}$")
    remark: str | None = Field(default=None, max_length=500)


class FinancialAccountAdjustment(BaseModel):
    amount: float
    remark: str = Field(..., min_length=1, max_length=500)


class FinancialAccountTransfer(BaseModel):
    from_account_id: int = Field(..., gt=0)
    to_account_id: int = Field(..., gt=0)
    amount: float = Field(..., gt=0)
    remark: str = Field(..., min_length=1, max_length=500)


class SettlementAccountInput(BaseModel):
    # 新交易必须选择企业账户；存量记录仍允许为空，待登记时补齐。
    company_account_id: int | None = Field(default=None, gt=0)
    counterparty_account_id: int | None = Field(default=None, gt=0)
