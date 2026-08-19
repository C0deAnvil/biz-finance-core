"""Add organization scope fields and logistics table

Revision ID: f0a1b2c3d4e5
Revises: e4f5a6b7c8d9
Create Date: 2026-03-18 12:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
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

    department_columns = {
        "parent_id": sa.Column("parent_id", sa.Integer(), nullable=True),
        "unit_type": sa.Column("unit_type", sa.String(length=30), nullable=False, server_default="department"),
        "sort_order": sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        "is_active": sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        "warehouse_scope": sa.Column("warehouse_scope", sa.String(length=30), nullable=True),
    }
    existing_department_columns = _existing_columns(bind, "departments")
    for column_name, column in department_columns.items():
        if column_name not in existing_department_columns:
            op.add_column("departments", column)
    if inspector.has_table("departments") and "parent_id" in _existing_columns(bind, "departments"):
        department_foreign_keys = {key["name"] for key in inspector.get_foreign_keys("departments")}
        if "fk_departments_parent_id" not in department_foreign_keys:
            op.create_foreign_key("fk_departments_parent_id", "departments", "departments", ["parent_id"], ["id"])

    user_columns = {
        "job_title": sa.Column("job_title", sa.String(length=100), nullable=True),
        "data_scope": sa.Column("data_scope", sa.String(length=30), nullable=False, server_default="self"),
    }
    existing_user_columns = _existing_columns(bind, "users")
    for column_name, column in user_columns.items():
        if column_name not in existing_user_columns:
            op.add_column("users", column)

    sales_order_columns = {
        "parent_order_id": sa.Column("parent_order_id", sa.Integer(), nullable=True),
        "split_group_no": sa.Column("split_group_no", sa.String(length=60), nullable=True),
        "split_phase": sa.Column("split_phase", sa.Integer(), nullable=False, server_default="1"),
    }
    existing_sales_order_columns = _existing_columns(bind, "sales_orders")
    for column_name, column in sales_order_columns.items():
        if column_name not in existing_sales_order_columns:
            op.add_column("sales_orders", column)
    if inspector.has_table("sales_orders") and "parent_order_id" in _existing_columns(bind, "sales_orders"):
        sales_order_foreign_keys = {key["name"] for key in inspector.get_foreign_keys("sales_orders")}
        if "fk_sales_orders_parent_order_id" not in sales_order_foreign_keys:
            op.create_foreign_key("fk_sales_orders_parent_order_id", "sales_orders", "sales_orders", ["parent_order_id"], ["id"])

    purchase_receipt_columns = {
        "requires_quality": sa.Column("requires_quality", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        "quality_status": sa.Column("quality_status", sa.String(length=30), nullable=False, server_default="not_required"),
    }
    existing_purchase_receipt_columns = _existing_columns(bind, "purchase_receipts")
    for column_name, column in purchase_receipt_columns.items():
        if column_name not in existing_purchase_receipt_columns:
            op.add_column("purchase_receipts", column)

    production_task_columns = {
        "requires_quality": sa.Column("requires_quality", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    }
    existing_production_task_columns = _existing_columns(bind, "production_tasks")
    for column_name, column in production_task_columns.items():
        if column_name not in existing_production_task_columns:
            op.add_column("production_tasks", column)

    quality_task_columns = {
        "source_category": sa.Column("source_category", sa.String(length=30), nullable=False, server_default="production"),
    }
    existing_quality_task_columns = _existing_columns(bind, "quality_tasks")
    for column_name, column in quality_task_columns.items():
        if column_name not in existing_quality_task_columns:
            op.add_column("quality_tasks", column)

    quality_inspection_columns = {
        "sample_consumption_qty": sa.Column("sample_consumption_qty", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        "report_summary": sa.Column("report_summary", sa.Text(), nullable=True),
    }
    existing_quality_inspection_columns = _existing_columns(bind, "quality_inspections")
    for column_name, column in quality_inspection_columns.items():
        if column_name not in existing_quality_inspection_columns:
            op.add_column("quality_inspections", column)

    if not inspector.has_table("logistics_records"):
        op.create_table(
            "logistics_records",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("logistics_no", sa.String(length=60), nullable=False),
            sa.Column("module", sa.String(length=30), nullable=False),
            sa.Column("business_type", sa.String(length=30), nullable=False),
            sa.Column("business_id", sa.Integer(), nullable=False),
            sa.Column("related_no", sa.String(length=60), nullable=True),
            sa.Column("warehouse_id", sa.Integer(), nullable=True),
            sa.Column("logistics_mode", sa.String(length=30), nullable=False, server_default="road"),
            sa.Column("carrier_name", sa.String(length=120), nullable=False),
            sa.Column("driver_name", sa.String(length=80), nullable=True),
            sa.Column("driver_phone", sa.String(length=30), nullable=True),
            sa.Column("vehicle_no", sa.String(length=60), nullable=True),
            sa.Column("tracking_no", sa.String(length=80), nullable=True),
            sa.Column("dispatched_at", sa.DateTime(), nullable=True),
            sa.Column("arrived_at", sa.DateTime(), nullable=True),
            sa.Column("remark", sa.String(length=255), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("logistics_no"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("logistics_records"):
        op.drop_table("logistics_records")

    if inspector.has_table("quality_inspections"):
        for column_name in ["sample_consumption_qty", "report_summary"]:
            if column_name in _existing_columns(bind, "quality_inspections"):
                op.drop_column("quality_inspections", column_name)

    if inspector.has_table("quality_tasks") and "source_category" in _existing_columns(bind, "quality_tasks"):
        op.drop_column("quality_tasks", "source_category")

    if inspector.has_table("production_tasks") and "requires_quality" in _existing_columns(bind, "production_tasks"):
        op.drop_column("production_tasks", "requires_quality")

    if inspector.has_table("purchase_receipts"):
        for column_name in ["requires_quality", "quality_status"]:
            if column_name in _existing_columns(bind, "purchase_receipts"):
                op.drop_column("purchase_receipts", column_name)

    if inspector.has_table("sales_orders"):
        sales_order_foreign_keys = {key["name"] for key in inspector.get_foreign_keys("sales_orders")}
        if "fk_sales_orders_parent_order_id" in sales_order_foreign_keys:
            op.drop_constraint("fk_sales_orders_parent_order_id", "sales_orders", type_="foreignkey")
        for column_name in ["parent_order_id", "split_group_no", "split_phase"]:
            if column_name in _existing_columns(bind, "sales_orders"):
                op.drop_column("sales_orders", column_name)

    if inspector.has_table("users"):
        for column_name in ["job_title", "data_scope"]:
            if column_name in _existing_columns(bind, "users"):
                op.drop_column("users", column_name)

    if inspector.has_table("departments"):
        department_foreign_keys = {key["name"] for key in inspector.get_foreign_keys("departments")}
        if "fk_departments_parent_id" in department_foreign_keys:
            op.drop_constraint("fk_departments_parent_id", "departments", type_="foreignkey")
        for column_name in ["parent_id", "unit_type", "sort_order", "is_active", "warehouse_scope"]:
            if column_name in _existing_columns(bind, "departments"):
                op.drop_column("departments", column_name)
