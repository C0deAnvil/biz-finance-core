"""Add quality cost records table

Revision ID: c1a2b3d4e5f6
Revises: 8b9d3c1e7f2a
Create Date: 2026-03-11 17:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8b9d3c1e7f2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("quality_cost_records"):
        return
    op.create_table(
        "quality_cost_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quality_disposition_id", sa.Integer(), nullable=False),
        sa.Column("estimated_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("recognized_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("recognized_at", sa.DateTime(), nullable=True),
        sa.Column("recognized_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["quality_disposition_id"], ["quality_dispositions.id"]),
        sa.ForeignKeyConstraint(["recognized_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quality_disposition_id", name="uq_quality_cost_record_disposition"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("quality_cost_records"):
        op.drop_table("quality_cost_records")
