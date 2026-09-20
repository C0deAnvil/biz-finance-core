"""Persist the company receipt account selected for a sales order.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
"""
from alembic import op
import sqlalchemy as sa

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("sales_orders")}
    if "receipt_account_id" not in columns:
        with op.batch_alter_table("sales_orders") as batch:
            batch.add_column(sa.Column("receipt_account_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_sales_orders_receipt_account_id",
                "financial_accounts",
                ["receipt_account_id"],
                ["id"],
            )
            batch.create_index("ix_sales_orders_receipt_account_id", ["receipt_account_id"])

    # 已有收款申请按最近一笔申请回填，保持历史业务可展示。
    bind.execute(sa.text("""
        UPDATE sales_orders
        SET receipt_account_id = (
            SELECT srr.company_account_id
            FROM sales_receipt_requests srr
            WHERE srr.sales_order_id = sales_orders.id
              AND srr.company_account_id IS NOT NULL
            ORDER BY srr.id DESC
            LIMIT 1
        )
        WHERE sales_orders.receipt_account_id IS NULL
          AND EXISTS (
            SELECT 1 FROM sales_receipt_requests srr2
            WHERE srr2.sales_order_id = sales_orders.id
              AND srr2.company_account_id IS NOT NULL
          )
    """))


def downgrade():
    with op.batch_alter_table("sales_orders") as batch:
        batch.drop_index("ix_sales_orders_receipt_account_id")
        batch.drop_constraint("fk_sales_orders_receipt_account_id", type_="foreignkey")
        batch.drop_column("receipt_account_id")
