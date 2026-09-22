"""Add inventory exception approval records and cash-flow account reference.

Revision ID: fa1b2c3d4e5f
Revises: f9a0b1c2d3e4
"""
from alembic import op
import sqlalchemy as sa


revision = "fa1b2c3d4e5f"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "cash_flow_records" in tables:
        columns = {column["name"] for column in inspector.get_columns("cash_flow_records")}
        if "company_account_id" not in columns:
            with op.batch_alter_table("cash_flow_records") as batch:
                batch.add_column(sa.Column("company_account_id", sa.Integer(), nullable=True))
                batch.create_foreign_key("fk_cash_flow_records_company_account_id", "financial_accounts", ["company_account_id"], ["id"])
                batch.create_index("ix_cash_flow_records_company_account_id", ["company_account_id"])

    if "inventory_exception_records" not in tables:
        op.create_table(
            "inventory_exception_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("exception_no", sa.String(60), nullable=False, unique=True),
            sa.Column("exception_type", sa.String(40), nullable=False),
            sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id"), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending_approval"),
            sa.Column("remark", sa.Text(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("approval_note", sa.Text(), nullable=True),
            sa.Column("rejected_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("rejected_at", sa.DateTime(), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        )
    if "inventory_exception_items" not in tables:
        op.create_table(
            "inventory_exception_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("exception_id", sa.Integer(), sa.ForeignKey("inventory_exception_records.id"), nullable=False),
            sa.Column("item_type", sa.String(20), nullable=False),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=True),
            sa.Column("packaging_config_id", sa.Integer(), sa.ForeignKey("packaging_configs.id"), nullable=True),
            sa.Column("sales_order_item_id", sa.Integer(), sa.ForeignKey("sales_order_items.id"), nullable=True),
            sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        )
        op.create_index("ix_inventory_exception_items_exception_id", "inventory_exception_items", ["exception_id"])


def downgrade():
    op.drop_table("inventory_exception_items")
    op.drop_table("inventory_exception_records")
    with op.batch_alter_table("cash_flow_records") as batch:
        batch.drop_index("ix_cash_flow_records_company_account_id")
        batch.drop_constraint("fk_cash_flow_records_company_account_id", type_="foreignkey")
        batch.drop_column("company_account_id")
