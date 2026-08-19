"""add sales item shipped quantity

Revision ID: f1e2d3c4b5a6
Revises: c2d3e4f5a6b7, a7b8c9d0e1f2
Create Date: 2026-04-19 17:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f1e2d3c4b5a6"
down_revision = ("c2d3e4f5a6b7", "a7b8c9d0e1f2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_order_items",
        sa.Column("shipped_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.execute("UPDATE sales_order_items SET shipped_quantity = 0 WHERE shipped_quantity IS NULL")


def downgrade() -> None:
    op.drop_column("sales_order_items", "shipped_quantity")
