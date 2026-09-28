"""Decimal-only monetary arithmetic at the database's four-place scale."""

from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.0001")


def money(value: Decimal | int | str) -> Decimal:
    """Round once, explicitly, when an amount becomes a persisted money value."""
    return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
