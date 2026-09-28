from datetime import date, datetime
from decimal import Decimal
from schemas.money import Money

from pydantic import BaseModel, Field
from schemas.accounts import SettlementAccountInput


class PurchaseOrderItemInput(BaseModel):
    product_id: int | None = None
    material_id: int | None = None
    packaging_config_id: int | None = None
    quantity: Decimal = Field(..., gt=0, decimal_places=4)
    unit_price: Money = Field(..., gt=0)


class DepartmentPurchaseRequestCreate(BaseModel):
    item_type: str = Field(..., pattern="^(material|product|other)$")
    material_id: int | None = None
    product_id: int | None = None
    item_name: str | None = Field(default=None, max_length=200)
    specification: str | None = Field(default=None, max_length=200)
    quantity: Decimal = Field(..., gt=0, decimal_places=4)
    unit: str = Field(..., min_length=1, max_length=30)
    reason: str = Field(..., min_length=2, max_length=2000)


class DepartmentPurchaseRequestProcess(BaseModel):
    purchase_order_id: int | None = None
    process_note: str | None = Field(default=None, max_length=2000)


class PurchaseReplenishmentSourceInput(BaseModel):
    source_type: str
    source_id: int = Field(..., gt=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    operator_user_id: int | None = None
    payment_type: str = Field(..., max_length=50)
    expected_arrival_date: date
    items: list[PurchaseOrderItemInput]
    replenishment_source_type: str | None = Field(
        default=None,
        pattern="^(raw_material_replenishment|low_stock_replenishment|low_stock_product_material_replenishment|fulfillment_demand|sales_order_material_replenishment|production_material_replenishment)$",
    )
    replenishment_source_id: int | None = Field(default=None, gt=0)
    # 多补货需求合并建单:多个来源同时转成一个采购订单(按原料/产品聚合)
    replenishment_sources: list[PurchaseReplenishmentSourceInput] | None = None


class PurchaseOrderUpdate(BaseModel):
    supplier_id: int
    operator_user_id: int | None = None
    payment_type: str = Field(..., max_length=50)
    expected_arrival_date: date
    items: list[PurchaseOrderItemInput]


class PurchaseApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = None


class PurchaseReceiptItemInput(BaseModel):
    purchase_order_item_id: int
    received_quantity: Decimal = Field(..., gt=0)
    batch_no: str = Field(..., max_length=80)
    unit_cost: Money = Field(..., ge=0)
    difference_remark: str | None = Field(default=None, max_length=500)


class PurchaseReceiptCreate(BaseModel):
    purchase_order_id: int
    warehouse_id: int
    requires_quality: bool = False
    remark: str | None = None
    preliminary_acceptance_note: str | None = None
    recorded_at: datetime | None = None
    items: list[PurchaseReceiptItemInput]


class PurchaseReturnItemInput(BaseModel):
    purchase_order_item_id: int
    returned_quantity: Decimal = Field(..., gt=0)


class PurchaseReturnCreate(BaseModel):
    purchase_order_id: int
    warehouse_id: int
    return_type: str = Field(default="return", pattern="^(return|exchange)$")
    reason: str
    refund_amount: Money = 0
    requires_reinspection: bool = False
    items: list[PurchaseReturnItemInput]


class PurchaseFundRequestCreate(BaseModel):
    purchase_order_id: int
    amount: Money = Field(..., gt=0)
    remark: str | None = None


class PurchaseFundApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    amount: Money | None = Field(default=None, gt=0)
    note: str | None = None


class PurchasePaymentRequestCreate(BaseModel):
    counterparty_account_id: int | None = Field(default=None, gt=0)
    purchase_order_id: int
    requested_amount: Money = Field(..., gt=0)
    order_amount: Money = Field(..., ge=0)
    other_fee_amount: Money = Field(default=0, ge=0)
    payment_detail_remark: str | None = None
    assigned_finance_user_id: int | None = Field(default=None, description="指定财务处理人(不指定则通知全部财务)")


class PurchaseMergedPaymentItem(BaseModel):
    purchase_order_id: int
    order_amount: Money = Field(..., ge=0)
    other_fee_amount: Money = Field(default=0, ge=0)


class PurchaseMergedPaymentRequestCreate(BaseModel):
    counterparty_account_id: int | None = Field(default=None, gt=0)
    supplier_id: int | None = None
    items: list[PurchaseMergedPaymentItem]
    payment_detail_remark: str | None = None
    assigned_finance_user_id: int | None = Field(default=None, description="指定财务处理人(不指定则通知全部财务)")


class PurchasePaymentApprovalRequest(SettlementAccountInput):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    actual_amount: Money = Field(..., gt=0)
    manager_remark: str | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    paid_at: datetime | None = None


class PurchasePaymentCompleteRequest(BaseModel):
    completion_remark: str | None = None


class PurchasePaymentInvoiceUpdate(BaseModel):
    invoice_no: str | None = Field(default=None, max_length=80)
    invoice_amount: Money | None = None
    invoice_date: date | None = None
    invoice_attachment_id: int | None = None
