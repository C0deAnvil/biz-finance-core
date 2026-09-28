from datetime import date, datetime
from decimal import Decimal
from schemas.money import Money

from pydantic import BaseModel, Field


class LogisticsRecordCreate(BaseModel):
    module: str = Field(..., pattern="^(sales|purchase|inventory)$")
    business_type: str = Field(..., pattern="^(sales_order|purchase_order)$")
    business_id: int
    warehouse_id: int | None = None
    logistics_mode: str = Field(default="road", max_length=30)
    carrier_name: str = Field(..., max_length=120)
    driver_name: str | None = Field(default=None, max_length=80)
    driver_phone: str | None = Field(default=None, max_length=30)
    vehicle_no: str | None = Field(default=None, max_length=60)
    tracking_no: str | None = Field(default=None, max_length=80)
    bill_date: date | None = None
    origin_station: str | None = Field(default=None, max_length=120)
    destination_station: str | None = Field(default=None, max_length=120)
    transfer_station: str | None = Field(default=None, max_length=120)
    consignor_name: str | None = Field(default=None, max_length=100)
    consignor_phone: str | None = Field(default=None, max_length=30)
    consignor_address: str | None = Field(default=None, max_length=255)
    consignee_name: str | None = Field(default=None, max_length=100)
    consignee_phone: str | None = Field(default=None, max_length=30)
    consignee_address: str | None = Field(default=None, max_length=255)
    delivery_fee: Money = Field(default=0, ge=0)
    freight_payment_type: str | None = Field(default=None, max_length=30)
    handover_type: str | None = Field(default=None, max_length=30)
    document_attachment_id: int | None = None
    dispatched_at: datetime | None = None
    arrived_at: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)


class LogisticsRecordUpdate(BaseModel):
    logistics_mode: str = Field(default="road", max_length=30)
    carrier_name: str = Field(..., max_length=120)
    driver_name: str | None = Field(default=None, max_length=80)
    driver_phone: str | None = Field(default=None, max_length=30)
    vehicle_no: str | None = Field(default=None, max_length=60)
    tracking_no: str | None = Field(default=None, max_length=80)
    bill_date: date | None = None
    origin_station: str | None = Field(default=None, max_length=120)
    destination_station: str | None = Field(default=None, max_length=120)
    transfer_station: str | None = Field(default=None, max_length=120)
    consignor_name: str | None = Field(default=None, max_length=100)
    consignor_phone: str | None = Field(default=None, max_length=30)
    consignor_address: str | None = Field(default=None, max_length=255)
    consignee_name: str | None = Field(default=None, max_length=100)
    consignee_phone: str | None = Field(default=None, max_length=30)
    consignee_address: str | None = Field(default=None, max_length=255)
    delivery_fee: Money = Field(default=0, ge=0)
    freight_payment_type: str | None = Field(default=None, max_length=30)
    handover_type: str | None = Field(default=None, max_length=30)
    document_attachment_id: int | None = None
    dispatched_at: datetime | None = None
    arrived_at: datetime | None = None
    remark: str | None = Field(default=None, max_length=255)
