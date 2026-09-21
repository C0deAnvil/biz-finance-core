"""Track packaging as an inventory item and persist sales packaging reservations.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""
from alembic import op
import sqlalchemy as sa


revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def _add_packaging_reference(table_name: str) -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
    if "packaging_config_id" in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.add_column(sa.Column("packaging_config_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            f"fk_{table_name}_packaging_config_id",
            "packaging_configs",
            ["packaging_config_id"],
            ["id"],
        )
        batch.create_index(f"ix_{table_name}_packaging_config_id", ["packaging_config_id"])


def upgrade():
    for table_name in ("inventories", "inventory_batches", "inventory_transactions", "stock_transfers"):
        _add_packaging_reference(table_name)

    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("sales_order_items")}
    with op.batch_alter_table("sales_order_items") as batch:
        if "packaging_reserved_quantity" not in columns:
            batch.add_column(
                sa.Column(
                    "packaging_reserved_quantity",
                    sa.Numeric(18, 4),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "packaging_shipped_quantity" not in columns:
            batch.add_column(
                sa.Column(
                    "packaging_shipped_quantity",
                    sa.Numeric(18, 4),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )


def downgrade():
    with op.batch_alter_table("sales_order_items") as batch:
        batch.drop_column("packaging_shipped_quantity")
        batch.drop_column("packaging_reserved_quantity")
    for table_name in ("stock_transfers", "inventory_transactions", "inventory_batches", "inventories"):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_index(f"ix_{table_name}_packaging_config_id")
            batch.drop_constraint(f"fk_{table_name}_packaging_config_id", type_="foreignkey")
            batch.drop_column("packaging_config_id")
