"""Track inventory value, cost provenance, and audited cost estimates.

Revision ID: d30e4f5a6b7c
Revises: c29d3e4f5a6b
"""

from alembic import op
import sqlalchemy as sa


revision = "d30e4f5a6b7c"
down_revision = "c29d3e4f5a6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventories", sa.Column("cost_balance", sa.Numeric(20, 4), nullable=False, server_default="0"))
    op.add_column("inventories", sa.Column("cost_status", sa.String(20), nullable=False, server_default="unpriced"))
    op.add_column("inventories", sa.Column("opening_cost_pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("inventories", "avg_unit_cost", existing_type=sa.Numeric(18, 4), type_=sa.Numeric(20, 8), existing_nullable=False)
    op.alter_column("inventory_batches", "unit_cost", existing_type=sa.Numeric(18, 4), type_=sa.Numeric(20, 8), existing_nullable=False)
    op.execute("""
        UPDATE inventories
        SET cost_balance = ROUND(quantity * avg_unit_cost, 4),
            cost_status = CASE
                WHEN quantity <= 0 THEN 'unpriced'
                WHEN avg_unit_cost > 0 THEN 'estimated'
                ELSE 'unpriced'
            END
    """)
    op.execute("UPDATE inventories SET opening_cost_pending = 1 WHERE quantity > 0")

    op.alter_column("inventory_transactions", "unit_cost", existing_type=sa.Numeric(18, 4), type_=sa.Numeric(20, 8), existing_nullable=False)
    op.alter_column("inventory_transactions", "total_cost", existing_type=sa.Numeric(18, 4), type_=sa.Numeric(20, 4), existing_nullable=False)
    op.add_column("inventory_transactions", sa.Column("cost_status", sa.String(20), nullable=False, server_default="unpriced"))
    op.add_column("inventory_transactions", sa.Column("sales_order_item_id", sa.Integer(), sa.ForeignKey("sales_order_items.id"), nullable=True))
    op.create_index("ix_inventory_transactions_sales_order_item_id", "inventory_transactions", ["sales_order_item_id"])
    # Historical movements cannot be certified from the current balance alone.
    op.execute("""
        UPDATE inventory_transactions
        SET cost_status = CASE WHEN unit_cost > 0 THEN 'estimated' ELSE 'unpriced' END
    """)

    op.create_table(
        "inventory_cost_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("inventories.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("old_unit_cost", sa.Numeric(20, 8), nullable=False),
        sa.Column("new_unit_cost", sa.Numeric(20, 8), nullable=False),
        sa.Column("old_cost_balance", sa.Numeric(20, 4), nullable=False),
        sa.Column("new_cost_balance", sa.Numeric(20, 4), nullable=False),
        sa.Column("old_cost_status", sa.String(20), nullable=False),
        sa.Column("new_cost_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_inventory_cost_adjustments_inventory_id", "inventory_cost_adjustments", ["inventory_id"])
    op.add_column("sales_orders", sa.Column("cost_status", sa.String(20), nullable=False, server_default="unpriced"))
    op.add_column("production_tasks", sa.Column("direct_labor_cost", sa.Numeric(20, 4), nullable=False, server_default="0"))
    op.add_column("production_tasks", sa.Column("manufacturing_overhead_cost", sa.Numeric(20, 4), nullable=False, server_default="0"))


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM inventory_cost_adjustments")).scalar_one():
        raise RuntimeError("已有库存成本调整记录，不能无损回退成本台账迁移")
    op.drop_column("production_tasks", "manufacturing_overhead_cost")
    op.drop_column("production_tasks", "direct_labor_cost")
    op.drop_column("sales_orders", "cost_status")
    op.drop_index("ix_inventory_cost_adjustments_inventory_id", table_name="inventory_cost_adjustments")
    op.drop_table("inventory_cost_adjustments")
    op.drop_column("inventory_transactions", "cost_status")
    op.drop_index("ix_inventory_transactions_sales_order_item_id", table_name="inventory_transactions")
    op.drop_column("inventory_transactions", "sales_order_item_id")
    op.alter_column("inventory_transactions", "total_cost", existing_type=sa.Numeric(20, 4), type_=sa.Numeric(18, 4), existing_nullable=False)
    op.alter_column("inventory_transactions", "unit_cost", existing_type=sa.Numeric(20, 8), type_=sa.Numeric(18, 4), existing_nullable=False)
    op.drop_column("inventories", "cost_status")
    op.drop_column("inventories", "opening_cost_pending")
    op.drop_column("inventories", "cost_balance")
    op.alter_column("inventories", "avg_unit_cost", existing_type=sa.Numeric(20, 8), type_=sa.Numeric(18, 4), existing_nullable=False)
    op.alter_column("inventory_batches", "unit_cost", existing_type=sa.Numeric(20, 8), type_=sa.Numeric(18, 4), existing_nullable=False)
