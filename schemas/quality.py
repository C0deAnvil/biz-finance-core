from decimal import Decimal

from pydantic import BaseModel, Field


class InspectionCreate(BaseModel):
    quality_task_id: int
    sequence_no: int = Field(..., ge=1)
    result_text: str
    grade: str = Field(..., max_length=30)
    is_passed: bool = True
    inspected_quantity: Decimal = Field(..., gt=0)
    sample_consumption_qty: Decimal = Field(default=0, ge=0)
    report_summary: str | None = Field(default=None, max_length=1000)


class QualityInboundCreate(BaseModel):
    quality_task_id: int
    warehouse_id: int
    remark: str | None = None


class QualityInboundApproval(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    remark: str | None = None


class QualityReworkCreate(BaseModel):
    quantity: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=2, max_length=500)


class QualityScrapCreate(BaseModel):
    quantity: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=2, max_length=500)


class QualityTaskManualCreate(BaseModel):
    purchase_order_id: int | None = None
    product_id: int | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    batch_no: str | None = Field(default=None, min_length=2, max_length=80)
    required_tests: int = Field(1, ge=1, le=20)
    remark: str | None = Field(default=None, max_length=500)
    assigned_inspector_id: int | None = Field(default=None, description="指定质检员(不指定则由该仓库质检员自行领取)")
    target_warehouse_id: int | None = Field(default=None, description="质检任务归属仓库(决定普通质检员可见范围)")


class QualityAllowInbound(BaseModel):
    remark: str | None = Field(default=None, max_length=500)
