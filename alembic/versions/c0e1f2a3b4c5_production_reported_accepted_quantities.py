"""Separate production reported and warehouse accepted quantities.

Revision ID: c0e1f2a3b4c5
Revises: b9d0e1f2a3b4
"""

from alembic import op
import sqlalchemy as sa

revision = "c0e1f2a3b4c5"
down_revision = "b9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("production_tasks", sa.Column("reported_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("production_tasks", sa.Column("accepted_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.execute("UPDATE production_tasks SET reported_quantity = produced_quantity, accepted_quantity = produced_quantity")


def downgrade() -> None:
    op.drop_column("production_tasks", "accepted_quantity")
    op.drop_column("production_tasks", "reported_quantity")
