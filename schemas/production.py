from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class BOMItemInput(BaseModel):
    material_id: int
    standard_quantity: Decimal = Field(..., gt=0)


class BOMCreate(BaseModel):
    product_id: int
    packaging_config_id: int | None = None
    version_no: str = "v1"
    notes: str | None = None
    items: list[BOMItemInput]


class MaterialUsageInput(BaseModel):
    material_id: int
    requested_quantity: Decimal = Field(..., gt=0)
    reason: str | None = None


class RawMaterialDemandInput(BaseModel):
    material_id: int = Field(..., description="原料ID")
    shortage_quantity: Decimal = Field(..., gt=0, description="原料补货数量(默认缺口,可修改)")


class ProductionTaskCreate(BaseModel):
    source_type: str = "manual"
    sales_order_id: int | None = None
    fulfillment_demand_id: int | None = None
    product_id: int
    planned_quantity: Decimal = Field(..., gt=0)
    fluctuation_ratio: Decimal = 0
    notes: str | None = None
    warehouse_id: int | None = None
    material_usages: list[MaterialUsageInput] = []
    create_raw_material_demands: bool = False
    raw_material_demands: list[RawMaterialDemandInput] = []


class ProductionPlanCreate(BaseModel):
    plan_name: str | None = Field(default=None, max_length=120)
    period_type: str = Field(default="custom", pattern="^(annual|quarterly|monthly|weekly|custom)$")
    period_start: str | None = None
    period_end: str | None = None
    product_id: int
    sales_order_id: int | None = None
    assigned_user_id: int | None = None
    planned_quantity: Decimal = Field(..., gt=0)
    scheduled_start_at: str | None = None
    scheduled_end_at: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    line_name: str | None = Field(default=None, max_length=100)
    scheduling_note: str | None = None
    detail_json: dict = Field(default_factory=dict)


class ProductionPlanUpdate(BaseModel):
    plan_name: str | None = Field(default=None, max_length=120)
    period_type: str = Field(default="custom", pattern="^(annual|quarterly|monthly|weekly|custom)$")
    period_start: str | None = None
    period_end: str | None = None
    product_id: int
    sales_order_id: int | None = None
    assigned_user_id: int | None = None
    planned_quantity: Decimal = Field(..., gt=0)
    scheduled_start_at: str | None = None
    scheduled_end_at: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    line_name: str | None = Field(default=None, max_length=100)
    scheduling_note: str | None = None
    detail_json: dict = Field(default_factory=dict)
    status: str = Field(default="planned", max_length=30)


class SpecialMaterialRequestCreate(BaseModel):
    actual_usages: list[MaterialUsageInput]


class MaterialRequestCreate(BaseModel):
    material_usages: list[MaterialUsageInput]


class SpecialMaterialApproval(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    approved_quantity: Decimal | None = Field(default=None, gt=0)
    remark: str | None = None
    create_replenishment: bool = False


class ProductionCompleteRequest(BaseModel):
    produced_quantity: Decimal = Field(..., ge=0)
    scrap_quantity: Decimal = Field(default=0, ge=0)
    requires_quality: bool = True
    preliminary_acceptance_note: str | None = Field(default=None, max_length=1000)
    actual_usages: list[MaterialUsageInput] = []
    assigned_inspector_id: int | None = Field(default=None, description="指定质检员(不指定则由该仓库质检员自行领取)")


class ProductionTaskCancelRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500)


class ProductionInboundRequest(BaseModel):
    warehouse_id: int | None = None
    remark: str | None = Field(default=None, max_length=500)
    preliminary_acceptance_note: str | None = Field(default=None, max_length=1000)


class FulfillmentDemandCancelRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500)


class FulfillmentDemandUpdateRequest(BaseModel):
    shortage_quantity: float = Field(..., gt=0, description="调整后的缺口数量")
    note: str | None = Field(default=None, max_length=500)


class FulfillmentDemandManualCreateRequest(BaseModel):
    product_id: int
    shortage_quantity: float = Field(..., gt=0)
    remark: str | None = Field(default=None, max_length=500)


class SalesOrderReplenishmentItem(BaseModel):
    sales_order_item_id: int | None = Field(default=None, description="销售订单明细ID(与product_id二选一)")
    product_id: int | None = Field(default=None, description="产品ID(与sales_order_item_id二选一)")
    shortage_quantity: float = Field(..., gt=0, description="补货数量(默认缺口,可修改)")


class SalesOrderReplenishmentCreateRequest(BaseModel):
    items: list[SalesOrderReplenishmentItem] = Field(..., min_length=1, description="补货明细列表")


class DemandRawMaterialDemandCreateRequest(BaseModel):
    items: list[RawMaterialDemandInput] = Field(..., min_length=1, description="原料补货明细(默认缺口,可修改)")
