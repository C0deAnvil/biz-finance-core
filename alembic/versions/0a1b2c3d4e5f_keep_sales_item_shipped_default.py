"""keep sales item shipped quantity default

Revision ID: 0a1b2c3d4e5f
Revises: f1e2d3c4b5a6
Create Date: 2026-04-19 18:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0a1b2c3d4e5f"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("sales_order_items")}
    if "shipped_quantity" not in columns:
        op.add_column(
            "sales_order_items",
            sa.Column("shipped_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        )
    else:
        op.execute("UPDATE sales_order_items SET shipped_quantity = 0 WHERE shipped_quantity IS NULL")
        op.alter_column(
            "sales_order_items",
            "shipped_quantity",
            existing_type=sa.Numeric(18, 4),
            nullable=False,
            server_default="0",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("sales_order_items")}
    if "shipped_quantity" in columns:
        op.alter_column(
            "sales_order_items",
            "shipped_quantity",
            existing_type=sa.Numeric(18, 4),
            nullable=False,
            server_default=None,
        )
