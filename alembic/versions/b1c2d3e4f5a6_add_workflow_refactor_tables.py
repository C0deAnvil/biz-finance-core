"""Add settlement workflow, inventory operations, production plans

Revision ID: b1c2d3e4f5a6
Revises: f0a1b2c3d4e5
Create Date: 2026-04-15 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("products"):
        existing_columns = _existing_columns(bind, "products")
        if "fulfillment_mode" not in existing_columns:
            op.add_column(
                "products",
                sa.Column("fulfillment_mode", sa.String(length=30), nullable=False, server_default="production"),
            )

    if inspector.has_table("production_material_usages"):
        existing_columns = _existing_columns(bind, "production_material_usages")
        if "issued_quantity" not in existing_columns:
            op.add_column(
                "production_material_usages",
                sa.Column("issued_quantity", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
            )

    if inspector.has_table("receivable_records"):
        existing_columns = _existing_columns(bind, "receivable_records")
        for column_name, column in {
            "sales_receipt_request_id": sa.Column("sales_receipt_request_id", sa.Integer(), nullable=True),
            "payment_stage": sa.Column("payment_stage", sa.String(length=30), nullable=True),
            "installment_total": sa.Column("installment_total", sa.Integer(), nullable=True),
            "installment_index": sa.Column("installment_index", sa.Integer(), nullable=True),
        }.items():
            if column_name not in existing_columns:
                op.add_column("receivable_records", column)

    if inspector.has_table("payable_records"):
        existing_columns = _existing_columns(bind, "payable_records")
        for column_name, column in {
            "purchase_payment_request_id": sa.Column("purchase_payment_request_id", sa.Integer(), nullable=True),
            "order_amount": sa.Column("order_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            "other_fee_amount": sa.Column("other_fee_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        }.items():
            if column_name not in existing_columns:
                op.add_column("payable_records", column)

    if not inspector.has_table("inventory_operation_requests"):
        op.create_table(
            "inventory_operation_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_no", sa.String(length=60), nullable=False),
            sa.Column("operation_type", sa.String(length=20), nullable=False),
            sa.Column("request_category", sa.String(length=50), nullable=False),
            sa.Column("source_module", sa.String(length=50), nullable=False),
            sa.Column("source_type", sa.String(length=50), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("source_no", sa.String(length=60), nullable=True),
            sa.Column("warehouse_id", sa.Integer(), nullable=False),
            sa.Column("requested_by_id", sa.Integer(), nullable=False),
            sa.Column("handled_by_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("handled_remark", sa.Text(), nullable=True),
            sa.Column("adjust_reason", sa.Text(), nullable=True),
            sa.Column("original_items_json", sa.JSON(), nullable=False),
            sa.Column("handled_items_json", sa.JSON(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["handled_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_no"),
        )

    if not inspector.has_table("fulfillment_demands"):
        op.create_table(
            "fulfillment_demands",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("demand_no", sa.String(length=60), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), nullable=False),
            sa.Column("sales_order_item_id", sa.Integer(), nullable=True),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("fulfillment_mode", sa.String(length=30), nullable=False),
            sa.Column("shortage_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("assigned_user_id", sa.Integer(), nullable=True),
            sa.Column("processed_by_id", sa.Integer(), nullable=True),
            sa.Column("cancel_reason", sa.Text(), nullable=True),
            sa.Column("process_note", sa.Text(), nullable=True),
            sa.Column("source_order_no", sa.String(length=60), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
            sa.ForeignKeyConstraint(["sales_order_item_id"], ["sales_order_items.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["processed_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("demand_no"),
        )

    if not inspector.has_table("purchase_payment_requests"):
        op.create_table(
            "purchase_payment_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_no", sa.String(length=60), nullable=False),
            sa.Column("purchase_order_id", sa.Integer(), nullable=False),
            sa.Column("requested_by_id", sa.Integer(), nullable=False),
            sa.Column("approved_by_id", sa.Integer(), nullable=True),
            sa.Column("completed_by_id", sa.Integer(), nullable=True),
            sa.Column("requested_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            sa.Column("order_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            sa.Column("other_fee_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            sa.Column("actual_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            sa.Column("arrival_status", sa.String(length=30), nullable=False, server_default="not_arrived"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("payment_detail_remark", sa.Text(), nullable=True),
            sa.Column("manager_remark", sa.Text(), nullable=True),
            sa.Column("completion_remark", sa.Text(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
            sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["completed_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_no"),
        )

    if not inspector.has_table("sales_receipt_requests"):
        op.create_table(
            "sales_receipt_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_no", sa.String(length=60), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), nullable=False),
            sa.Column("requested_by_id", sa.Integer(), nullable=False),
            sa.Column("approved_by_id", sa.Integer(), nullable=True),
            sa.Column("requested_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            sa.Column("actual_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
            sa.Column("payment_stage", sa.String(length=30), nullable=False),
            sa.Column("installment_total", sa.Integer(), nullable=True),
            sa.Column("installment_index", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("is_abnormal", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("approval_remark", sa.Text(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
            sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_no"),
        )

    if not inspector.has_table("production_plans"):
        op.create_table(
            "production_plans",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("plan_no", sa.String(length=60), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), nullable=True),
            sa.Column("planned_quantity", sa.Numeric(precision=18, scale=4), nullable=False),
            sa.Column("scheduled_start_at", sa.DateTime(), nullable=True),
            sa.Column("scheduled_end_at", sa.DateTime(), nullable=True),
            sa.Column("priority", sa.String(length=30), nullable=False, server_default="normal"),
            sa.Column("line_name", sa.String(length=100), nullable=True),
            sa.Column("scheduling_note", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="planned"),
            sa.Column("created_by_id", sa.Integer(), nullable=False),
            sa.Column("updated_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["sales_order_id"], ["sales_orders.id"]),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("plan_no"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name in [
        "production_plans",
        "sales_receipt_requests",
        "purchase_payment_requests",
        "fulfillment_demands",
        "inventory_operation_requests",
    ]:
        if inspector.has_table(table_name):
            op.drop_table(table_name)

    if inspector.has_table("payable_records"):
        existing_columns = _existing_columns(bind, "payable_records")
        for column_name in ["purchase_payment_request_id", "order_amount", "other_fee_amount"]:
            if column_name in existing_columns:
                op.drop_column("payable_records", column_name)

    if inspector.has_table("receivable_records"):
        existing_columns = _existing_columns(bind, "receivable_records")
        for column_name in ["sales_receipt_request_id", "payment_stage", "installment_total", "installment_index"]:
            if column_name in existing_columns:
                op.drop_column("receivable_records", column_name)

    if inspector.has_table("production_material_usages") and "issued_quantity" in _existing_columns(bind, "production_material_usages"):
        op.drop_column("production_material_usages", "issued_quantity")

    if inspector.has_table("products") and "fulfillment_mode" in _existing_columns(bind, "products"):
        op.drop_column("products", "fulfillment_mode")
