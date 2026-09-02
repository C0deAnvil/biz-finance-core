"""add sales order item placed quantity

Revision ID: 8f7e6d5c4b3a
Revises: 9a8b7c6d5e4f
Create Date: 2026-09-02 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8f7e6d5c4b3a"
down_revision = "9a8b7c6d5e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_order_items",
        sa.Column("placed_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.execute("UPDATE sales_order_items SET placed_quantity = 0 WHERE placed_quantity IS NULL")


def downgrade() -> None:
    op.drop_column("sales_order_items", "placed_quantity")