"""Add quality disposition table

Revision ID: 8b9d3c1e7f2a
Revises: f7c5f2f7a9b1
Create Date: 2026-03-11 14:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8b9d3c1e7f2a"
down_revision: Union[str, Sequence[str], None] = "f7c5f2f7a9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("quality_dispositions"):
        return
    op.create_table(
        "quality_dispositions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quality_task_id", sa.Integer(), nullable=False),
        sa.Column("disposition_type", sa.String(length=30), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("related_production_task_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["quality_task_id"], ["quality_tasks.id"]),
        sa.ForeignKeyConstraint(["related_production_task_id"], ["production_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quality_task_id", name="uq_quality_disposition_task"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("quality_dispositions"):
        op.drop_table("quality_dispositions")
