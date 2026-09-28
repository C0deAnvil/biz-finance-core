"""Shared four-place input contract for persisted monetary values."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field


Money = Annotated[Decimal, Field(max_digits=20, decimal_places=4)]
