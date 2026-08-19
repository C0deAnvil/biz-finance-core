from pydantic import BaseModel, Field


class CustomerInquiryCreate(BaseModel):
    customer_id: int
    inquiry_text: str = Field(..., min_length=1, max_length=2000)
    source_channel: str | None = Field(default=None, max_length=50)
    product_hint: str | None = Field(default=None, max_length=150)


class OrderRecommendationRequest(BaseModel):
    customer_id: int
    product_ids: list[int] = Field(default_factory=list)
