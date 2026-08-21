"""Add replenishment source to purchase orders

Revision ID: 5a6b7c8d9e0f
Revises: 4f5a6b7c8d9e
Create Date: 2026-08-21 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "5a6b7c8d9e0f"
down_revision = "4f5a6b7c8d9e"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _has_unique_constraint(table_name: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(item.get("name") == name for item in inspector.get_unique_constraints(table_name))


def upgrade() -> None:
    columns = _columns("purchase_orders")
    if "replenishment_source_type" not in columns:
        op.add_column("purchase_orders", sa.Column("replenishment_source_type", sa.String(length=50), nullable=True))
    if "replenishment_source_id" not in columns:
        op.add_column("purchase_orders", sa.Column("replenishment_source_id", sa.Integer(), nullable=True))
    if not _has_unique_constraint("purchase_orders", "uq_purchase_orders_replenishment_source"):
        with op.batch_alter_table("purchase_orders") as batch_op:
            batch_op.create_unique_constraint(
                "uq_purchase_orders_replenishment_source",
                ["replenishment_source_type", "replenishment_source_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("purchase_orders"):
        if _has_unique_constraint("purchase_orders", "uq_purchase_orders_replenishment_source"):
            with op.batch_alter_table("purchase_orders") as batch_op:
                batch_op.drop_constraint("uq_purchase_orders_replenishment_source", type_="unique")
        columns = _columns("purchase_orders")
        if "replenishment_source_id" in columns:
            op.drop_column("purchase_orders", "replenishment_source_id")
        if "replenishment_source_type" in columns:
            op.drop_column("purchase_orders", "replenishment_source_type")
