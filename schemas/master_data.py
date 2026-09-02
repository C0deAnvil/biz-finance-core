from decimal import Decimal

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


class CustomerTransferCreate(BaseModel):
    to_user_id: int
    reason: str | None = Field(default=None, max_length=500)


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


class SupplierCatalogItemInput(BaseModel):
    product_id: int | None = None
    material_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)


class StandardPriceUpdate(BaseModel):
    price: Decimal = Field(..., gt=0)
    remark: str = Field(..., min_length=2, max_length=255)


class ProductCreate(BaseModel):
    product_code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=200)
    price: Decimal
    specification: str | None = Field(default=None, max_length=255)
    unit: str = Field(default="kg", max_length=20)
    safety_stock_qty: Decimal = 0
    purchase_threshold_qty: Decimal = 0
    fulfillment_mode: str = Field(default="production", pattern="^(production|trade)$")
    is_active: bool = True


class PackagingConfigCreate(BaseModel):
    entity_type: str = Field(..., pattern="^(product|material|global)$")
    entity_id: int = 0
    packaging_name: str = Field(..., max_length=100)
    kg_per_bag: Decimal = Field(..., gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    is_default: bool = True
    is_active: bool = True


class MaterialCreate(BaseModel):
    material_code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=200)
    price: Decimal
    specification: str | None = Field(default=None, max_length=255)
    unit: str = Field(default="kg", max_length=20)
    safety_stock_qty: Decimal = 0
    purchase_threshold_qty: Decimal = 0
    is_active: bool = True
