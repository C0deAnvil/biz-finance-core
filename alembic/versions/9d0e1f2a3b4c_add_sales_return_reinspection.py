"""add sales return reinspection fields

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-05-23 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9d0e1f2a3b4c"
down_revision = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind, "sales_returns")
    if not existing:
        return
    if "requires_reinspection" not in existing:
        op.add_column(
            "sales_returns",
            sa.Column("requires_reinspection", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if "reinspection_status" not in existing:
        op.add_column(
            "sales_returns",
            sa.Column("reinspection_status", sa.String(length=30), nullable=False, server_default="not_required"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind, "sales_returns")
    if "reinspection_status" in existing:
        op.drop_column("sales_returns", "reinspection_status")
    if "requires_reinspection" in existing:
        op.drop_column("sales_returns", "requires_reinspection")
