"""keep purchase item quantity defaults

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-04-19 19:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "1b2c3d4e5f6a"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("purchase_order_items")}
    for column_name in ("received_quantity", "returned_quantity"):
        if column_name not in columns:
            continue
        op.execute(f"UPDATE purchase_order_items SET {column_name} = 0 WHERE {column_name} IS NULL")
        op.alter_column(
            "purchase_order_items",
            column_name,
            existing_type=sa.Numeric(18, 4),
            nullable=False,
            server_default="0",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("purchase_order_items")}
    for column_name in ("received_quantity", "returned_quantity"):
        if column_name not in columns:
            continue
        op.alter_column(
            "purchase_order_items",
            column_name,
            existing_type=sa.Numeric(18, 4),
            nullable=False,
            server_default=None,
        )
