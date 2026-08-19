from decimal import Decimal

from pydantic import BaseModel, Field


class SalesOrderItemInput(BaseModel):
    product_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0)
    packaging_config_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)
    price_change_remark: str | None = Field(default=None, max_length=255)


class SalesOrderCreate(BaseModel):
    customer_id: int
    operator_user_id: int | None = None
    consignee: str = Field(..., max_length=100)
    consignee_phone: str = Field(..., max_length=30)
    delivery_address: str = Field(..., max_length=255)
    payment_type: str = Field(..., max_length=50)
    deposit_amount: Decimal = 0
    transport_fee: Decimal = 0
    order_remark: str | None = None
    items: list[SalesOrderItemInput]


class SalesOrderUpdate(BaseModel):
    customer_id: int
    operator_user_id: int | None = None
    consignee: str = Field(..., max_length=100)
    consignee_phone: str = Field(..., max_length=30)
    delivery_address: str = Field(..., max_length=255)
    payment_type: str = Field(..., max_length=50)
    deposit_amount: Decimal = 0
    transport_fee: Decimal = 0
    order_remark: str | None = None
    items: list[SalesOrderItemInput]


class SalesOrderFinanceEditRequest(SalesOrderUpdate):
    edit_note: str | None = Field(default=None, max_length=1000)


class SalesApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    voucher_amount: Decimal = 0
    actual_amount: Decimal = 0
    note: str | None = None


class SalesReturnItemInput(BaseModel):
    sales_order_item_id: int
    quantity: Decimal = Field(..., gt=0)


class SalesReturnCreate(BaseModel):
    sales_order_id: int
    return_type: str = Field(default="return", pattern="^(return|exchange)$")
    reason: str
    refund_amount: Decimal = 0
    requires_reinspection: bool = False
    items: list[SalesReturnItemInput]


class SalesReturnApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = None


class QueueActionRequest(BaseModel):
    action: str = Field(..., pattern="^(placed|shipped)$")


class SalesShipmentItemInput(BaseModel):
    sales_order_item_id: int
    shipped_quantity: Decimal = Field(..., ge=0)


class SalesShipmentRequest(BaseModel):
    items: list[SalesShipmentItemInput]
    remark: str | None = None


class SalesSettlementCollectRequest(BaseModel):
    sales_order_id: int
    amount: Decimal = Field(..., gt=0)
    stage: str = Field(..., pattern="^(deposit|balance)$")
    remark: str | None = None


class SalesReceiptRequestCreate(BaseModel):
    sales_order_id: int
    requested_amount: Decimal = Field(..., gt=0)
    payment_stage: str = Field(..., pattern="^(deposit|balance|cod_full|pre_shipment_full|installment)$")
    installment_total: int | None = Field(default=None, ge=1, le=24)
    installment_index: int | None = Field(default=None, ge=1, le=24)
    remark: str | None = None


class SalesMergedReceiptRequestItem(BaseModel):
    sales_order_id: int
    requested_amount: Decimal = Field(..., gt=0)


class SalesMergedReceiptRequestCreate(BaseModel):
    customer_id: int | None = None
    payment_stage: str = Field(..., pattern="^(deposit|balance|cod_full|pre_shipment_full|installment|mixed)$")
    installment_total: int | None = Field(default=None, ge=1, le=24)
    installment_index: int | None = Field(default=None, ge=1, le=24)
    remark: str | None = None
    items: list[SalesMergedReceiptRequestItem]


class SalesReceiptApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    actual_amount: Decimal = Field(..., gt=0)
    approval_remark: str | None = None
    payment_type: str | None = None


class SalesOrderCompleteConfirm(BaseModel):
    completion_remark: str | None = None


class SalesPriceApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=1000)


class SalesFinanceEditConfirmRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
