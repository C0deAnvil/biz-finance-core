from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field
from schemas.accounts import SettlementAccountInput


class SalesOrderItemInput(BaseModel):
    product_id: int
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)
    packaging_config_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)
    price_change_remark: str | None = Field(default=None, max_length=255)


class SalesOrderDraftItemInput(BaseModel):
    """草稿允许商品行尚未填写完整，完整性在提交时统一校验。"""
    product_id: int | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    packaging_config_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)
    price_change_remark: str | None = Field(default=None, max_length=255)


class SalesOrderDraftPayload(BaseModel):
    update_customer_address: bool = False
    customer_address_before: str | None = Field(default=None, max_length=255)
    customer_id: int | None = None
    operator_user_id: int | None = None
    consignee: str | None = Field(default=None, max_length=100)
    consignee_phone: str | None = Field(default=None, max_length=30)
    delivery_address: str | None = Field(default=None, max_length=255)
    payment_type: str | None = Field(default=None, max_length=50)
    order_type: str | None = Field(default="normal", max_length=30)
    expected_delivery_date: date | None = None
    deposit_amount: Decimal | None = 0
    transport_fee: Decimal | None = 0
    order_remark: str | None = None
    items: list[SalesOrderDraftItemInput] = Field(default_factory=list)


class SalesOrderCreate(BaseModel):
    update_customer_address: bool = False
    customer_address_before: str | None = Field(default=None, max_length=255)
    customer_id: int
    operator_user_id: int | None = None
    consignee: str = Field(..., max_length=100)
    consignee_phone: str = Field(..., max_length=30)
    delivery_address: str = Field(..., max_length=255)
    payment_type: str = Field(..., max_length=50)
    order_type: str = Field(default="normal", max_length=30)
    expected_delivery_date: date | None = None
    deposit_amount: Decimal = 0
    transport_fee: Decimal = 0
    order_remark: str | None = None
    items: list[SalesOrderItemInput]


class SalesOrderUpdate(BaseModel):
    update_customer_address: bool = False
    customer_address_before: str | None = Field(default=None, max_length=255)
    customer_id: int
    operator_user_id: int | None = None
    consignee: str = Field(..., max_length=100)
    consignee_phone: str = Field(..., max_length=30)
    delivery_address: str = Field(..., max_length=255)
    payment_type: str = Field(..., max_length=50)
    order_type: str = Field(default="normal", max_length=30)
    expected_delivery_date: date | None = None
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


class PlacementItemInput(BaseModel):
    sales_order_item_id: int
    placed_quantity: Decimal = Field(..., ge=0)


class PlacementActionRequest(BaseModel):
    action: str = Field(..., pattern="^placed$")
    items: list[PlacementItemInput]


class SalesShipmentItemInput(BaseModel):
    sales_order_item_id: int
    shipped_quantity: Decimal = Field(..., ge=0)


class SalesShipmentRequest(BaseModel):
    items: list[SalesShipmentItemInput]
    remark: str | None = None
    force: bool = False


class SalesSettlementCollectRequest(BaseModel):
    sales_order_id: int
    amount: Decimal = Field(..., gt=0)
    stage: str = Field(..., pattern="^(deposit|balance)$")
    remark: str | None = None


class SalesOrderSubmitRequest(BaseModel):
    """提交客户订单审批。可指定处理该单的财务人员,不指定则系统轮询分配。"""

    assigned_finance_user_id: int | None = Field(default=None, description="指定财务处理人(不指定则轮询分配)")
    update_customer_address: bool = False
    customer_address_before: str | None = Field(default=None, max_length=255)


class SalesReceiptRequestCreate(SettlementAccountInput):
    sales_order_id: int
    requested_amount: Decimal = Field(..., gt=0)
    payment_stage: str = Field(..., pattern="^(deposit|balance|cod_full|pre_shipment_full|installment)$")
    installment_total: int | None = Field(default=None, ge=1, le=24)
    installment_index: int | None = Field(default=None, ge=1, le=24)
    remark: str | None = None
    assigned_finance_user_id: int | None = Field(default=None, description="指定财务处理人(不指定则轮询分配)")


class SalesMergedReceiptRequestItem(BaseModel):
    sales_order_id: int
    requested_amount: Decimal = Field(..., gt=0)


class SalesMergedReceiptRequestCreate(SettlementAccountInput):
    customer_id: int | None = None
    payment_stage: str = Field(..., pattern="^(deposit|balance|cod_full|pre_shipment_full|installment|mixed)$")
    installment_total: int | None = Field(default=None, ge=1, le=24)
    installment_index: int | None = Field(default=None, ge=1, le=24)
    remark: str | None = None
    items: list[SalesMergedReceiptRequestItem]
    # 合并收款同样是财务事务,与单笔收款申请一致需选择处理财务
    assigned_finance_user_id: int | None = Field(default=None, description="指定处理该收款申请的财务(不指定则自动分配)")


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
