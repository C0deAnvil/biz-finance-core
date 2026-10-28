"""Post actual stock movements into the sales-order cost cache.

The inventory transaction ledger is the source of truth; product master prices
are sales/reference prices and must never be used as inventory cost.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import InventoryTransaction, SalesOrder


def sync_sales_order_cost(db: Session, order: SalesOrder) -> None:
    db.flush()
    movements = db.execute(select(InventoryTransaction).where(
        InventoryTransaction.related_type == "sales_order",
        InventoryTransaction.related_id == order.id,
        InventoryTransaction.transaction_type.in_(("sales_outbound", "sales_packaging_outbound")),
    )).scalars().all()
    order.cost_amount = -sum((Decimal(row.total_cost or 0) for row in movements), Decimal("0"))
    if not movements or any(row.cost_status == "unpriced" for row in movements):
        order.cost_status = "unpriced"
    elif any(row.cost_status == "estimated" for row in movements):
        order.cost_status = "estimated"
    else:
        order.cost_status = "actual"

    # 只有订单全部发货、且每笔发货均有成本，才显示订单级利润。
    # 包装出库成本已包含在 sales_packaging_outbound 流水中，不能再次扣减。
    if order.status in {"shipped", "completed"} and order.cost_status != "unpriced":
        order.profit_amount = (
            Decimal(order.total_amount or 0) - Decimal(order.cost_amount or 0)
            - Decimal(order.transport_fee or 0)
        )
    else:
        order.profit_amount = Decimal("0")
