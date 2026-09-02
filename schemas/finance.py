from datetime import datetime, date
from decimal import Decimal

from pydantic import BaseModel, Field


class ReceivablePlanCreate(BaseModel):
    sales_order_id: int
    due_date: date
    planned_amount: Decimal = Field(..., gt=0)


class ReceivableRecordCreate(BaseModel):
    sales_order_id: int
    receivable_plan_id: int | None = None
    amount: Decimal = Field(..., gt=0)
    received_at: datetime
    remark: str | None = None


class PayableRecordCreate(BaseModel):
    purchase_order_id: int
    purchase_receipt_id: int | None = None
    amount: Decimal = Field(..., gt=0)
    paid_at: datetime
    remark: str | None = None


class RefundRecordCreate(BaseModel):
    business_type: str = Field(..., pattern="^(sales_return|purchase_return)$")
    sales_return_id: int | None = None
    purchase_return_id: int | None = None
    amount: Decimal = Field(..., gt=0)
    refunded_at: datetime
    remark: str | None = None


class SalesCostUpdate(BaseModel):
    transport_fee: Decimal = 0
    packaging_fee: Decimal = 0


class QualityCostRecognize(BaseModel):
    recognized_amount: Decimal = Field(..., ge=0)
    remark: str | None = None


class FinancePaymentAuditRequest(BaseModel):
    purchase_payment_request_id: int
    actual_amount: Decimal = Field(..., gt=0)
    remark: str | None = None


class FinanceReceiptNoticeRequest(BaseModel):
    sales_receipt_request_id: int
    actual_amount: Decimal = Field(..., gt=0)
    remark: str | None = None


class FinancePurchasePaymentRegisterRequest(BaseModel):
    purchase_payment_request_id: int
    actual_amount: Decimal = Field(..., gt=0)
    remark: str | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    paid_at: datetime | None = None


class FinanceMergedPurchasePaymentRegisterItem(BaseModel):
    purchase_order_id: int
    order_amount: Decimal = Field(..., ge=0)
    other_fee_amount: Decimal = Field(default=0, ge=0)


class FinanceMergedPurchasePaymentRegisterRequest(BaseModel):
    supplier_id: int | None = None
    items: list[FinanceMergedPurchasePaymentRegisterItem]
    actual_amount: Decimal = Field(..., gt=0)
    remark: str | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    paid_at: datetime | None = None


class FinanceSalesReceiptRegisterRequest(BaseModel):
    sales_receipt_request_id: int
    actual_amount: Decimal = Field(..., gt=0)
    remark: str | None = None
    payment_type: str | None = None
    receipt_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    received_at: datetime | None = None
    release_to_inventory: bool = False


class FinanceMergedPurchasePaymentRequestRegisterItem(BaseModel):
    purchase_payment_request_id: int
    actual_amount: Decimal = Field(..., gt=0)


class FinanceMergedPurchasePaymentRequestRegisterRequest(BaseModel):
    items: list[FinanceMergedPurchasePaymentRequestRegisterItem]
    remark: str | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    paid_at: datetime | None = None


class FinanceDirectSalesReceiptRegisterItem(BaseModel):
    sales_order_id: int
    amount: Decimal = Field(..., gt=0)


class FinanceDirectSalesReceiptRegisterRequest(BaseModel):
    customer_id: int | None = None
    payment_stage: str = Field(default="direct", max_length=30)
    items: list[FinanceDirectSalesReceiptRegisterItem]
    remark: str | None = None
    receipt_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    received_at: datetime | None = None
    release_to_inventory: bool = False


class FinanceMergedSalesReceiptRequestRegisterItem(BaseModel):
    sales_receipt_request_id: int
    actual_amount: Decimal = Field(..., gt=0)


class FinanceMergedSalesReceiptRequestRegisterRequest(BaseModel):
    items: list[FinanceMergedSalesReceiptRequestRegisterItem]
    remark: str | None = None
    payment_type: str | None = None
    receipt_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    received_at: datetime | None = None
    release_to_inventory: bool = False


class PaymentMethodOptionCreate(BaseModel):
    method_type: str = Field(..., pattern="^(receipt|payment)$")
    name: str = Field(..., min_length=1, max_length=80)
    is_active: bool = True
    sort_order: int = 0
