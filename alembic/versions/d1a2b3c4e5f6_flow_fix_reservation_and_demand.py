"""流程修正:明细级占用库存 + 补货需求量化 + 删除冗余库存缓存列

背景:
1. 占用库存原先只记在 inventories.reserved_quantity 上,无法区分"是哪张订单占的",
   导致占用不可幂等(重复审批叠加)也不可释放(驳回后永久泄漏)。
   新增 sales_order_items.reserved_quantity 记录每条明细占了多少。
2. fulfillment_demands 原先只有 shortage_quantity 且被原地递减,原始需求量丢失,
   无法表达"待补货/已补货"。新增 required_quantity / fulfilled_quantity。
3. inventories 的 in_production/on_order/available 是第 4 套"可用量"缓存算法,
   与其他算法口径矛盾且前端从未使用,可用量改为实时计算,故删除。

Revision ID: d1a2b3c4e5f6
Revises: c1971156723f
Create Date: 2026-09-05 01:10:00.000000
"""

from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = "d1a2b3c4e5f6"
down_revision = "c1971156723f"
branch_labels = None
depends_on = None


def _backfill_reservations() -> None:
    """回填明细级占用,并据此重算仓库端占用。

    必须与运行时 occupy_inventory_for_order 的口径一致,否则历史数据会带着
    "占用>实有"的脏值进入新逻辑,导致可置货量为负——正是本次要修的那个 bug。

    两条关键约束:
    1) 只占成品仓。运行时只在成品仓做占用(_lock_finished_inventory_map),
       若按产品无差别更新所有仓库的库存行,同一笔占用会在每个仓库各记一遍。
    2) 占用不能超过实有。按订单先后(id 升序)依次分配剩余可占量,
       分不到的部分留作缺口,由库管在置货环节提补货需求。
    """
    conn = op.get_bind()

    # 成品仓定位逻辑与 services/warehouse_lookup.get_finished_warehouse 保持一致
    finished_id = conn.execute(
        sa.text(
            "SELECT id FROM warehouses WHERE code IN ('CANGZHOU_FINISHED', 'WH-FG') "
            "ORDER BY id ASC LIMIT 1"
        )
    ).scalar()
    if finished_id is None:
        finished_id = conn.execute(
            sa.text("SELECT id FROM warehouses WHERE warehouse_type = 'finished' ORDER BY id ASC LIMIT 1")
        ).scalar()
    if finished_id is None:
        # 没有成品仓的库(如全新空库)无需回填
        return

    # 成品仓各产品实有量
    stock = {
        row[0]: Decimal(row[1] or 0)
        for row in conn.execute(
            sa.text(
                "SELECT product_id, quantity FROM inventories "
                "WHERE warehouse_id = :wid AND product_id IS NOT NULL AND material_id IS NULL"
            ),
            {"wid": finished_id},
        ).all()
    }

    # 待发货订单明细,按订单先后分配(老订单优先占用)
    rows = conn.execute(
        sa.text(
            "SELECT i.id, i.product_id, "
            "GREATEST(COALESCE(i.quantity,0) - COALESCE(i.shipped_quantity,0), 0) AS outstanding "
            "FROM sales_order_items i JOIN sales_orders o ON o.id = i.sales_order_id "
            "WHERE o.status = 'pending_shipment' "
            "ORDER BY i.sales_order_id ASC, i.id ASC"
        )
    ).all()

    remaining = dict(stock)
    allocated_per_product: dict[int, Decimal] = {}
    for item_id, product_id, outstanding in rows:
        outstanding = Decimal(outstanding or 0)
        free = remaining.get(product_id, Decimal("0"))
        occupy = min(outstanding, free) if free > 0 else Decimal("0")
        if occupy > 0:
            remaining[product_id] = free - occupy
            allocated_per_product[product_id] = allocated_per_product.get(product_id, Decimal("0")) + occupy
        conn.execute(
            sa.text("UPDATE sales_order_items SET reserved_quantity = :q WHERE id = :id"),
            {"q": str(occupy), "id": item_id},
        )

    # 先把所有产品行的占用清零(清掉历史脏值,含非成品仓被错误记账的行),
    # 再只给成品仓写入按明细账重算出的占用总量。
    conn.execute(sa.text("UPDATE inventories SET reserved_quantity = 0 WHERE product_id IS NOT NULL"))
    for product_id, total in allocated_per_product.items():
        conn.execute(
            sa.text(
                "UPDATE inventories SET reserved_quantity = :q "
                "WHERE warehouse_id = :wid AND product_id = :pid AND material_id IS NULL"
            ),
            {"q": str(total), "wid": finished_id, "pid": product_id},
        )


def upgrade() -> None:
    # ---- 1. 明细级占用 ----
    op.add_column(
        "sales_order_items",
        sa.Column("reserved_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    _backfill_reservations()

    # ---- 2. 补货需求量化 ----
    op.add_column(
        "fulfillment_demands",
        sa.Column("required_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "fulfillment_demands",
        sa.Column("fulfilled_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    # 历史数据:shortage_quantity 是被递减过的剩余量,已无法还原原始需求量,
    # 故以当前剩余量作为原始需求量,已补货量置 0(不影响后续增量记账)。
    op.execute("UPDATE fulfillment_demands SET required_quantity = COALESCE(shortage_quantity, 0)")

    # ---- 3. 指定财务人员(需求:所有财务事务提交时都要指定一个财务) ----
    # 采购付款申请已有该字段;这里补齐销售订单审批、销售收款申请、报销三处。
    # 报销另有 finance_user_id 记录"实际付款人",语义不同故不复用。
    for table_name in ("sales_orders", "sales_receipt_requests", "reimbursements"):
        op.add_column(
            table_name,
            sa.Column("assigned_finance_user_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table_name}_assigned_finance_user",
            table_name,
            "users",
            ["assigned_finance_user_id"],
            ["id"],
        )

    # ---- 4. 删除冗余库存缓存列 ----
    op.drop_column("inventories", "available")
    op.drop_column("inventories", "on_order")
    op.drop_column("inventories", "in_production")


def downgrade() -> None:
    op.add_column(
        "inventories",
        sa.Column("in_production", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "inventories",
        sa.Column("on_order", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "inventories",
        sa.Column("available", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    for table_name in ("reimbursements", "sales_receipt_requests", "sales_orders"):
        op.drop_constraint(
            f"fk_{table_name}_assigned_finance_user", table_name, type_="foreignkey"
        )
        op.drop_column(table_name, "assigned_finance_user_id")
    op.drop_column("fulfillment_demands", "fulfilled_quantity")
    op.drop_column("fulfillment_demands", "required_quantity")
    op.drop_column("sales_order_items", "reserved_quantity")
