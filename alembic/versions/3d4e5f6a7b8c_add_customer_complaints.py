"""Add customer complaint records

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
Create Date: 2026-04-28 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "3d4e5f6a7b8c"
down_revision = "2c3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("customer_complaints"):
        return
    op.create_table(
        "customer_complaints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("complaint_no", sa.String(length=60), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("complainant_name", sa.String(length=100), nullable=False),
        sa.Column("complainant_phone", sa.String(length=30), nullable=False),
        sa.Column("complainant_address", sa.String(length=255), nullable=True),
        sa.Column("complaint_reason", sa.String(length=255), nullable=False),
        sa.Column("complaint_detail", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("handled_result", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("handled_by_id", sa.Integer(), nullable=True),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["handled_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("complaint_no"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("customer_complaints"):
        op.drop_table("customer_complaints")
