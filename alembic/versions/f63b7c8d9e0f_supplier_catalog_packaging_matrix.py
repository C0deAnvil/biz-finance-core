"""Support packaging in supplier catalogs and supplier samples.

Revision ID: f63b7c8d9e0f
Revises: f52a6b7c8d9e
"""

from alembic import op
import sqlalchemy as sa


revision = "f63b7c8d9e0f"
down_revision = "f52a6b7c8d9e"
branch_labels = None
depends_on = None


def _check_names(table_name: str) -> set[str]:
    return {
        row.get("name")
        for row in sa.inspect(op.get_bind()).get_check_constraints(table_name)
        if row.get("name")
    }


def upgrade() -> None:
    with op.batch_alter_table("supplier_catalog_items") as batch_op:
        batch_op.add_column(sa.Column("packaging_config_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_supplier_catalog_item_packaging",
            "packaging_configs",
            ["packaging_config_id"],
            ["id"],
        )

    sample_checks = _check_names("supplier_samples")
    with op.batch_alter_table("supplier_samples") as batch_op:
        if "ck_supplier_sample_item" in sample_checks:
            batch_op.drop_constraint("ck_supplier_sample_item", type_="check")
        batch_op.add_column(sa.Column("packaging_config_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_supplier_sample_packaging",
            "packaging_configs",
            ["packaging_config_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "ck_supplier_sample_item",
            "(product_id IS NOT NULL AND material_id IS NULL AND packaging_config_id IS NULL) "
            "OR (product_id IS NULL AND material_id IS NOT NULL AND packaging_config_id IS NULL) "
            "OR (product_id IS NULL AND material_id IS NULL AND packaging_config_id IS NOT NULL)",
        )


def downgrade() -> None:
    sample_checks = _check_names("supplier_samples")
    with op.batch_alter_table("supplier_samples") as batch_op:
        if "ck_supplier_sample_item" in sample_checks:
            batch_op.drop_constraint("ck_supplier_sample_item", type_="check")
        batch_op.drop_constraint("fk_supplier_sample_packaging", type_="foreignkey")
        batch_op.drop_column("packaging_config_id")
        batch_op.create_check_constraint(
            "ck_supplier_sample_item",
            "(product_id IS NOT NULL AND material_id IS NULL) OR "
            "(product_id IS NULL AND material_id IS NOT NULL)",
        )

    with op.batch_alter_table("supplier_catalog_items") as batch_op:
        batch_op.drop_constraint("fk_supplier_catalog_item_packaging", type_="foreignkey")
        batch_op.drop_column("packaging_config_id")
