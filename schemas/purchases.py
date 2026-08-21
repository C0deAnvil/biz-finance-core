from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PurchaseOrderItemInput(BaseModel):
    product_id: int | None = None
    material_id: int | None = None
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    operator_user_id: int | None = None
    payment_type: str = Field(..., max_length=50)
    items: list[PurchaseOrderItemInput]
    replenishment_source_type: str | None = Field(
        default=None,
        pattern="^(raw_material_replenishment|low_stock_replenishment|fulfillment_demand)$",
    )
    replenishment_source_id: int | None = Field(default=None, gt=0)


class PurchaseOrderUpdate(BaseModel):
    supplier_id: int
    operator_user_id: int | None = None
    payment_type: str = Field(..., max_length=50)
    items: list[PurchaseOrderItemInput]


class PurchaseApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = None


class PurchaseReceiptItemInput(BaseModel):
    purchase_order_item_id: int
    received_quantity: Decimal = Field(..., gt=0)
    batch_no: str = Field(..., max_length=80)
    unit_cost: Decimal = Field(..., ge=0)
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
    refund_amount: Decimal = 0
    requires_reinspection: bool = False
    items: list[PurchaseReturnItemInput]


class PurchaseFundRequestCreate(BaseModel):
    purchase_order_id: int
    amount: Decimal = Field(..., gt=0)
    remark: str | None = None


class PurchaseFundApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    amount: Decimal | None = Field(default=None, gt=0)
    note: str | None = None


class PurchasePaymentRequestCreate(BaseModel):
    purchase_order_id: int
    requested_amount: Decimal = Field(..., gt=0)
    order_amount: Decimal = Field(..., ge=0)
    other_fee_amount: Decimal = Field(default=0, ge=0)
    payment_detail_remark: str | None = None


class PurchaseMergedPaymentItem(BaseModel):
    purchase_order_id: int
    order_amount: Decimal = Field(..., ge=0)
    other_fee_amount: Decimal = Field(default=0, ge=0)


class PurchaseMergedPaymentRequestCreate(BaseModel):
    supplier_id: int | None = None
    items: list[PurchaseMergedPaymentItem]
    payment_detail_remark: str | None = None


class PurchasePaymentApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    actual_amount: Decimal = Field(..., gt=0)
    manager_remark: str | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    bank_receipt_attachment_id: int | None = None
    paid_at: datetime | None = None


class PurchasePaymentCompleteRequest(BaseModel):
    completion_remark: str | None = None
