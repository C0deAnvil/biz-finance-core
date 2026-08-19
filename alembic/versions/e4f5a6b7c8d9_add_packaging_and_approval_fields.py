"""Add packaging config and approval extension fields

Revision ID: e4f5a6b7c8d9
Revises: d2e3f4a5b6c7
Create Date: 2026-03-14 11:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
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

    if not inspector.has_table("packaging_configs"):
        op.create_table(
            "packaging_configs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("entity_type", sa.String(length=30), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("packaging_name", sa.String(length=100), nullable=False),
            sa.Column("kg_per_bag", sa.Numeric(precision=18, scale=4), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("entity_type", "entity_id", "packaging_name", name="uq_packaging_entity_name"),
        )

    sales_order_item_columns = {
        "packaging_config_id": sa.Column("packaging_config_id", sa.Integer(), nullable=True),
        "kg_per_bag": sa.Column("kg_per_bag", sa.Numeric(precision=18, scale=4), nullable=True),
        "bag_count": sa.Column("bag_count", sa.Numeric(precision=18, scale=4), nullable=True),
        "display_specification": sa.Column("display_specification", sa.String(length=120), nullable=True),
    }
    existing_sales_item_columns = _existing_columns(bind, "sales_order_items")
    for column_name, column in sales_order_item_columns.items():
        if column_name not in existing_sales_item_columns:
            op.add_column("sales_order_items", column)
    if "packaging_config_id" in _existing_columns(bind, "sales_order_items"):
        foreign_keys = {key["name"] for key in inspector.get_foreign_keys("sales_order_items")}
        if "fk_sales_order_items_packaging_config_id" not in foreign_keys:
            op.create_foreign_key(
                "fk_sales_order_items_packaging_config_id",
                "sales_order_items",
                "packaging_configs",
                ["packaging_config_id"],
                ["id"],
            )

    purchase_order_item_columns = {
        "packaging_config_id": sa.Column("packaging_config_id", sa.Integer(), nullable=True),
        "kg_per_bag": sa.Column("kg_per_bag", sa.Numeric(precision=18, scale=4), nullable=True),
        "bag_count": sa.Column("bag_count", sa.Numeric(precision=18, scale=4), nullable=True),
        "display_specification": sa.Column("display_specification", sa.String(length=120), nullable=True),
    }
    existing_purchase_item_columns = _existing_columns(bind, "purchase_order_items")
    for column_name, column in purchase_order_item_columns.items():
        if column_name not in existing_purchase_item_columns:
            op.add_column("purchase_order_items", column)
    if "packaging_config_id" in _existing_columns(bind, "purchase_order_items"):
        foreign_keys = {key["name"] for key in inspector.get_foreign_keys("purchase_order_items")}
        if "fk_purchase_order_items_packaging_config_id" not in foreign_keys:
            op.create_foreign_key(
                "fk_purchase_order_items_packaging_config_id",
                "purchase_order_items",
                "packaging_configs",
                ["packaging_config_id"],
                ["id"],
            )

    sales_return_columns = _existing_columns(bind, "sales_returns")
    if "return_type" not in sales_return_columns:
        op.add_column(
            "sales_returns",
            sa.Column("return_type", sa.String(length=30), nullable=False, server_default="return"),
        )

    purchase_return_columns = _existing_columns(bind, "purchase_returns")
    if "return_type" not in purchase_return_columns:
        op.add_column(
            "purchase_returns",
            sa.Column("return_type", sa.String(length=30), nullable=False, server_default="return"),
        )

    usage_columns = {
        "inventory_approval_status": sa.Column("inventory_approval_status", sa.String(length=30), nullable=False, server_default="not_required"),
        "inventory_approved_by_id": sa.Column("inventory_approved_by_id", sa.Integer(), nullable=True),
        "inventory_approved_at": sa.Column("inventory_approved_at", sa.DateTime(), nullable=True),
        "inventory_remark": sa.Column("inventory_remark", sa.Text(), nullable=True),
        "manager_approval_status": sa.Column("manager_approval_status", sa.String(length=30), nullable=False, server_default="not_required"),
        "manager_approved_by_id": sa.Column("manager_approved_by_id", sa.Integer(), nullable=True),
        "manager_approved_at": sa.Column("manager_approved_at", sa.DateTime(), nullable=True),
        "manager_remark": sa.Column("manager_remark", sa.Text(), nullable=True),
    }
    existing_usage_columns = _existing_columns(bind, "production_material_usages")
    for column_name, column in usage_columns.items():
        if column_name not in existing_usage_columns:
            op.add_column("production_material_usages", column)

    usage_foreign_keys = {key["name"] for key in inspector.get_foreign_keys("production_material_usages")}
    if "inventory_approved_by_id" in _existing_columns(bind, "production_material_usages") and "fk_usage_inventory_approved_by" not in usage_foreign_keys:
        op.create_foreign_key(
            "fk_usage_inventory_approved_by",
            "production_material_usages",
            "users",
            ["inventory_approved_by_id"],
            ["id"],
        )
    if "manager_approved_by_id" in _existing_columns(bind, "production_material_usages") and "fk_usage_manager_approved_by" not in usage_foreign_keys:
        op.create_foreign_key(
            "fk_usage_manager_approved_by",
            "production_material_usages",
            "users",
            ["manager_approved_by_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("production_material_usages"):
        foreign_keys = {key["name"] for key in inspector.get_foreign_keys("production_material_usages")}
        if "fk_usage_inventory_approved_by" in foreign_keys:
            op.drop_constraint("fk_usage_inventory_approved_by", "production_material_usages", type_="foreignkey")
        if "fk_usage_manager_approved_by" in foreign_keys:
            op.drop_constraint("fk_usage_manager_approved_by", "production_material_usages", type_="foreignkey")
        for column_name in [
            "inventory_approval_status",
            "inventory_approved_by_id",
            "inventory_approved_at",
            "inventory_remark",
            "manager_approval_status",
            "manager_approved_by_id",
            "manager_approved_at",
            "manager_remark",
        ]:
            if column_name in _existing_columns(bind, "production_material_usages"):
                op.drop_column("production_material_usages", column_name)

    if inspector.has_table("purchase_returns") and "return_type" in _existing_columns(bind, "purchase_returns"):
        op.drop_column("purchase_returns", "return_type")
    if inspector.has_table("sales_returns") and "return_type" in _existing_columns(bind, "sales_returns"):
        op.drop_column("sales_returns", "return_type")

    if inspector.has_table("purchase_order_items"):
        foreign_keys = {key["name"] for key in inspector.get_foreign_keys("purchase_order_items")}
        if "fk_purchase_order_items_packaging_config_id" in foreign_keys:
            op.drop_constraint("fk_purchase_order_items_packaging_config_id", "purchase_order_items", type_="foreignkey")
        for column_name in ["packaging_config_id", "kg_per_bag", "bag_count", "display_specification"]:
            if column_name in _existing_columns(bind, "purchase_order_items"):
                op.drop_column("purchase_order_items", column_name)

    if inspector.has_table("sales_order_items"):
        foreign_keys = {key["name"] for key in inspector.get_foreign_keys("sales_order_items")}
        if "fk_sales_order_items_packaging_config_id" in foreign_keys:
            op.drop_constraint("fk_sales_order_items_packaging_config_id", "sales_order_items", type_="foreignkey")
        for column_name in ["packaging_config_id", "kg_per_bag", "bag_count", "display_specification"]:
            if column_name in _existing_columns(bind, "sales_order_items"):
                op.drop_column("sales_order_items", column_name)

    if inspector.has_table("packaging_configs"):
        op.drop_table("packaging_configs")
