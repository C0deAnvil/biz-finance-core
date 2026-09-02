"""add sales order type and expected delivery date

Revision ID: 9a8b7c6d5e4f
Revises: 016c0110ebf1
Create Date: 2026-09-02 11:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9a8b7c6d5e4f"
down_revision = "016c0110ebf1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_orders",
        sa.Column("order_type", sa.String(30), nullable=False, server_default="normal"),
    )
    op.add_column(
        "sales_orders",
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales_orders", "expected_delivery_date")
    op.drop_column("sales_orders", "order_type")