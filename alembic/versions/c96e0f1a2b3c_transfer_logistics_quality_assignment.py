"""Add stock transfer logistics and quality assignment audit fields.

Revision ID: c96e0f1a2b3c
Revises: b85d9e0f1a2b
"""

import sqlalchemy as sa
from alembic import op


revision = "c96e0f1a2b3c"
down_revision = "b85d9e0f1a2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_transfers", sa.Column("carrier_name", sa.String(100), nullable=True))
    op.add_column("stock_transfers", sa.Column("carrier_phone", sa.String(50), nullable=True))
    op.add_column("stock_transfers", sa.Column("vehicle_plate_no", sa.String(50), nullable=True))
    op.add_column("quality_tasks", sa.Column("assigned_by_id", sa.Integer(), nullable=True))
    op.add_column("quality_tasks", sa.Column("assigned_at", sa.DateTime(), nullable=True))
    op.create_foreign_key("fk_quality_tasks_assigned_by", "quality_tasks", "users", ["assigned_by_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_quality_tasks_assigned_by", "quality_tasks", type_="foreignkey")
    op.drop_column("quality_tasks", "assigned_at")
    op.drop_column("quality_tasks", "assigned_by_id")
    op.drop_column("stock_transfers", "vehicle_plate_no")
    op.drop_column("stock_transfers", "carrier_phone")
    op.drop_column("stock_transfers", "carrier_name")
