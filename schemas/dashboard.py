from pydantic import BaseModel, Field


class BatchIdsRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
