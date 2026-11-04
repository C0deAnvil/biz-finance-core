from decimal import Decimal
from schemas.money import Money

from datetime import datetime

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    name: str = Field(..., max_length=150)
    contact_name: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=30)
    address: str = Field(..., max_length=255)
    star_level: str | None = Field(default=None, max_length=30)
    unified_social_credit_code: str | None = Field(default=None, max_length=60)
    source_type: str = Field(default="personal_development", pattern="^(personal_development|company_transfer)$")
    owner_user_id: int | None = None
    customer_type: str | None = Field(default=None, pattern="^(importer|dealer|factory|trading|agent)$")
    region_type: str | None = Field(default=None, pattern="^(domestic|overseas)$")
    region_province: str | None = Field(default=None, max_length=40)
    remark: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=30)


class CustomerTransferCreate(BaseModel):
    to_user_id: int
    reason: str | None = Field(default=None, max_length=500)


class CustomerFollowUpCreate(BaseModel):
    follow_type: str = Field(default="call", pattern="^(call|visit|wechat|email|meeting|other)$")
    content: str = Field(..., min_length=1, max_length=3000)
    next_follow_at: datetime | None = None
    status: str = Field(default="open", pattern="^(open|completed|cancelled)$")


class CustomerCRMStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(lead|active|lost)$")
    reason: str | None = Field(default=None, max_length=1000)


class CustomerComplaintCreate(BaseModel):
    customer_id: int
    complainant_name: str = Field(..., max_length=100)
    complainant_phone: str = Field(..., max_length=30)
    complainant_address: str | None = Field(default=None, max_length=255)
    complaint_reason: str = Field(..., max_length=255)
    complaint_detail: str | None = None


class CustomerComplaintUpdate(BaseModel):
    status: str = Field(default="processing", pattern="^(pending|processing|resolved|closed)$")
    handled_result: str | None = None


class SupplierCreate(BaseModel):
    name: str = Field(..., max_length=150)
    contact_name: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=30)
    address: str = Field(..., max_length=255)
    status: str = Field(default="enabled", pattern="^(enabled|disabled)$")
    unified_social_credit_code: str | None = Field(default=None, max_length=60)
    supplier_type: str | None = Field(
        default=None,
        pattern="^(trader|manufacturer|manufacturer_trader|service|logistics|other)$",
    )
    website: str | None = Field(default=None, max_length=255)
    bank_name: str | None = Field(default=None, max_length=150)
    bank_account: str | None = Field(default=None, max_length=60)
    remark: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=30)


class SupplierCatalogItemInput(BaseModel):
    product_id: int | None = None
    material_id: int | None = None
    packaging_config_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)


class StandardPriceUpdate(BaseModel):
    price: Money = Field(..., gt=0)
    remark: str = Field(..., min_length=2, max_length=255)


class ProductCreate(BaseModel):
    # 新建时由服务端生成；保留该字段以兼容编辑和既有接口调用。
    product_code: str | None = Field(default=None, max_length=50)
    name: str = Field(..., max_length=200)
    price: Money
    specification: str | None = Field(default=None, max_length=255)
    unit: str = Field(default="kg", max_length=20)
    safety_stock_qty: Decimal = 0
    purchase_threshold_qty: Decimal = 0
    fulfillment_mode: str = Field(default="trade", pattern="^(production|trade)$")
    is_active: bool = True


class PackagingConfigCreate(BaseModel):
    entity_type: str = Field(..., pattern="^(product|material|global)$")
    entity_id: int = 0
    packaging_name: str = Field(..., max_length=100)
    kg_per_bag: Decimal = Field(..., gt=0)
    unit_price: Money | None = Field(default=None, ge=0)
    is_default: bool = True
    is_active: bool = True


class MaterialCreate(BaseModel):
    # 新建时由服务端生成；保留该字段以兼容编辑和既有接口调用。
    material_code: str | None = Field(default=None, max_length=50)
    name: str = Field(..., max_length=200)
    price: Money
    specification: str | None = Field(default=None, max_length=255)
    unit: str = Field(default="kg", max_length=20)
    safety_stock_qty: Decimal = 0
    purchase_threshold_qty: Decimal = 0
    is_active: bool = True
