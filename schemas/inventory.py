from decimal import Decimal
from schemas.money import Money

from pydantic import BaseModel, Field


class StockTakeItemInput(BaseModel):
    stock_take_item_id: int | None = None
    inventory_id: int | None = None
    counted_quantity: Decimal = Field(..., ge=0)
    reason: str | None = None


class StockTakeSubmit(BaseModel):
    items: list[StockTakeItemInput]
    difference_note: str | None = None


class StockTakeCreate(BaseModel):
    warehouse_id: int
    items: list[StockTakeItemInput]
    difference_note: str | None = None


class InventoryOperationItemInput(BaseModel):
    product_id: int | None = None
    material_id: int | None = None
    packaging_config_id: int | None = None
    batch_no: str | None = None
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Money = Field(default=0, ge=0)


class InventoryOperationHandleRequest(BaseModel):
    items: list[InventoryOperationItemInput]
    handled_remark: str | None = None
    adjust_reason: str | None = None
    quantity_difference_remark: str | None = Field(default=None, max_length=500)


class InventoryOperationApprovalRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=500)


class InventoryManualOperationCreateRequest(BaseModel):
    warehouse_id: int
    operation_type: str = Field(..., pattern="^(inbound|outbound)$")
    source_no: str | None = None
    remark: str = Field(..., min_length=2, max_length=500)
    items: list[InventoryOperationItemInput]


class InventoryOperationRejectRequest(BaseModel):
    reject_reason: str = Field(..., min_length=2, max_length=500)


class StockTransferCreateRequest(BaseModel):
    source_warehouse_id: int
    target_warehouse_id: int
    product_id: int | None = None
    material_id: int | None = None
    packaging_config_id: int | None = None
    quantity: float = Field(..., gt=0)
    requires_quality: bool = False
    remark: str | None = Field(default=None, max_length=500)


class TransferQualityRequest(BaseModel):
    result: str = Field(..., pattern="^(passed|failed)$")
    reject_reason: str | None = Field(default=None, max_length=500)


class InventoryExceptionItemInput(BaseModel):
    item_type: str = Field(..., pattern="^(product|packaging)$")
    product_id: int | None = None
    packaging_config_id: int | None = None
    sales_order_item_id: int | None = None
    quantity: Decimal = Field(..., gt=0)


class InventoryExceptionCreate(BaseModel):
    exception_type: str = Field(..., pattern="^(product_damage|packaging_damage|extra_packaging|packaging_adjustment)$")
    warehouse_id: int
    sales_order_id: int | None = None
    remark: str = Field(..., min_length=2, max_length=2000)
    items: list[InventoryExceptionItemInput] = Field(..., min_length=1)


class InventoryExceptionDecision(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
