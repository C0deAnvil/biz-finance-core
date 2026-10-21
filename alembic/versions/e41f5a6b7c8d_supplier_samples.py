"""Track supplier samples separately from saleable inventory.

Revision ID: e41f5a6b7c8d
Revises: d30e4f5a6b7c
"""

from alembic import op
import sqlalchemy as sa


revision = "e41f5a6b7c8d"
down_revision = "d30e4f5a6b7c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sample_no", sa.String(60), nullable=False, unique=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("received_quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("batch_no", sa.String(80), nullable=False),
        sa.Column("storage_location", sa.String(150), nullable=True),
        sa.Column("receipt_remark", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("received_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("quality_task_id", sa.Integer(), sa.ForeignKey("quality_tasks.id"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("(product_id IS NOT NULL AND material_id IS NULL) OR (product_id IS NULL AND material_id IS NOT NULL)", name="ck_supplier_sample_item"),
    )
    op.create_index("ix_supplier_samples_supplier_id", "supplier_samples", ["supplier_id"])
    op.create_index("ix_supplier_samples_warehouse_id", "supplier_samples", ["warehouse_id"])


def downgrade() -> None:
    op.drop_index("ix_supplier_samples_warehouse_id", table_name="supplier_samples")
    op.drop_index("ix_supplier_samples_supplier_id", table_name="supplier_samples")
    op.drop_table("supplier_samples")
