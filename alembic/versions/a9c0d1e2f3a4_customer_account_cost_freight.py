"""Separate customer new date, order account sets, packaging costs and freight review.

Revision ID: a9c0d1e2f3a4
Revises: 016c0110ebf1, d29a3b4c5d6e
"""

from alembic import op
import sqlalchemy as sa


revision = "a9c0d1e2f3a4"
down_revision = ("016c0110ebf1", "d29a3b4c5d6e")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("first_qualified_order_at", sa.DateTime(), nullable=True))
    op.create_index("ix_customers_first_qualified_order_at", "customers", ["first_qualified_order_at"])
    op.add_column("sales_orders", sa.Column("account_set", sa.String(30), nullable=False, server_default=""))
    op.add_column("packaging_configs", sa.Column("purchase_cost", sa.Numeric(20, 4), nullable=False, server_default="0"))
    op.add_column("packaging_configs", sa.Column("sales_cost", sa.Numeric(20, 4), nullable=False, server_default="0"))
    op.add_column("packaging_configs", sa.Column("is_virtual", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Preserve the former unit price as procurement cost; sales cost intentionally starts at zero.
    op.execute("UPDATE packaging_configs SET purchase_cost = unit_price, sales_cost = 0")
    op.execute("""
        INSERT INTO packaging_configs
            (entity_type, entity_id, packaging_name, kg_per_bag, unit_price, purchase_cost, sales_cost, is_virtual, is_default, is_active, created_at, updated_at)
        SELECT 'global', 0, '无包装', 1, 0, 0, 0, 1, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM packaging_configs WHERE entity_type = 'global' AND entity_id = 0 AND packaging_name = '无包装'
        )
    """)
    op.add_column("logistics_records", sa.Column("freight_status", sa.String(30), nullable=False, server_default="draft"))
    op.add_column("logistics_records", sa.Column("freight_reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("logistics_records", sa.Column("freight_reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("logistics_records", sa.Column("freight_review_remark", sa.Text(), nullable=True))
    op.add_column("logistics_records", sa.Column("freight_settlement_mode", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("logistics_records", "freight_settlement_mode")
    op.drop_column("logistics_records", "freight_review_remark")
    op.drop_column("logistics_records", "freight_reviewed_at")
    op.drop_column("logistics_records", "freight_reviewed_by_id")
    op.drop_column("logistics_records", "freight_status")
    op.drop_column("packaging_configs", "is_virtual")
    op.drop_column("packaging_configs", "sales_cost")
    op.drop_column("packaging_configs", "purchase_cost")
    op.drop_column("sales_orders", "account_set")
    op.drop_index("ix_customers_first_qualified_order_at", table_name="customers")
    op.drop_column("customers", "first_qualified_order_at")
