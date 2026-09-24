"""Add planned arrival date to purchase orders.

Revision ID: fc3d4e5f6a7b
Revises: fb2c3d4e5f6a
"""

import sqlalchemy as sa
from alembic import op


revision = "fc3d4e5f6a7b"
down_revision = "fb2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchase_orders", sa.Column("expected_arrival_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("purchase_orders", "expected_arrival_date")
