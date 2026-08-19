"""Add standard and sales price change logs

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
Create Date: 2026-05-15 22:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "6a7b8c9d0e1f"
down_revision = "5f6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("standard_price_change_logs"):
        op.create_table(
            "standard_price_change_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("old_price", sa.Numeric(18, 2), nullable=False, server_default="0"),
            sa.Column("new_price", sa.Numeric(18, 2), nullable=False),
            sa.Column("remark", sa.String(length=255), nullable=False),
            sa.Column("changed_by_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not inspector.has_table("sales_order_price_change_logs"):
        op.create_table(
            "sales_order_price_change_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), nullable=False),
            sa.Column("sales_order_item_id", sa.Integer(), nullable=True),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("standard_price", sa.Numeric(18, 2), nullable=False, server_default="0"),
            sa.Column("actual_price", sa.Numeric(18, 2), nullable=False),
            sa.Column("remark", sa.String(length=255), nullable=False),
            sa.Column("changed_by_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
            sa.ForeignKeyConstraint(["sales_order_item_id"], ["sales_order_items.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("sales_order_price_change_logs"):
        op.drop_table("sales_order_price_change_logs")
    if inspector.has_table("standard_price_change_logs"):
        op.drop_table("standard_price_change_logs")
