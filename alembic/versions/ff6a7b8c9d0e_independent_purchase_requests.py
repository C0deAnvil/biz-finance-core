"""Independent production and quality purchase requests.

Revision ID: ff6a7b8c9d0e
Revises: fe5f6a7b8c9d
"""

import sqlalchemy as sa
from alembic import op


revision = "ff6a7b8c9d0e"
down_revision = "fe5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchase_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_no", sa.String(60), nullable=False, unique=True),
        sa.Column("department", sa.String(30), nullable=False),
        sa.Column("item_type", sa.String(20), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("item_name", sa.String(200), nullable=False),
        sa.Column("specification", sa.String(200), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("submitted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("processed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("process_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_purchase_requests_status", "purchase_requests", ["status"])


def downgrade():
    op.drop_index("ix_purchase_requests_status", table_name="purchase_requests")
    op.drop_table("purchase_requests")
