"""Store the target warehouse selected on purchase orders.

Revision ID: a07b8c9d0e1f
Revises: ff6a7b8c9d0e
"""

import sqlalchemy as sa
from alembic import op


revision = "a07b8c9d0e1f"
down_revision = "ff6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_orders") as batch:
        batch.add_column(sa.Column("target_warehouse_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_purchase_orders_target_warehouse_id", "warehouses", ["target_warehouse_id"], ["id"],
        )


def downgrade():
    with op.batch_alter_table("purchase_orders") as batch:
        batch.drop_constraint("fk_purchase_orders_target_warehouse_id", type_="foreignkey")
        batch.drop_column("target_warehouse_id")
