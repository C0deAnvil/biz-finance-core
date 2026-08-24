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


class ProductionTaskCreate(BaseModel):
    source_type: str = "manual"
    sales_order_id: int | None = None
    product_id: int
    planned_quantity: Decimal = Field(..., gt=0)
    fluctuation_ratio: Decimal = 0
    notes: str | None = None
    material_usages: list[MaterialUsageInput] = []


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


class ProductionTaskCancelRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500)


class ProductionInboundRequest(BaseModel):
    warehouse_id: int | None = None
    remark: str | None = Field(default=None, max_length=500)
    preliminary_acceptance_note: str | None = Field(default=None, max_length=1000)


class FulfillmentDemandCancelRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500)
