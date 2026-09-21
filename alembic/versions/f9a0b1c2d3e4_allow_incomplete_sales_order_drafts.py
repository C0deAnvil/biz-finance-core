"""Allow sales order drafts without a customer.

Revision ID: f9a0b1c2d3e4
Revises: f8a9b0c1d2e3
"""

from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("sales_orders")}
    if "customer_id" in columns and not columns["customer_id"].get("nullable"):
        with op.batch_alter_table("sales_orders") as batch:
            batch.alter_column("customer_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    # 已有不完整草稿时不能安全恢复 NOT NULL，保留可空列以避免迁移丢失草稿。
    pass
