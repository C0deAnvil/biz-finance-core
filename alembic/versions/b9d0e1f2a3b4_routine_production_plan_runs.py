"""Add routine production plan daily run records.

Revision ID: b9d0e1f2a3b4
Revises: a9c0d1e2f3a4
"""

from alembic import op
import sqlalchemy as sa

revision = "b9d0e1f2a3b4"
down_revision = "a9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_plan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("production_plan_id", sa.Integer(), sa.ForeignKey("production_plans.id"), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("production_task_id", sa.Integer(), sa.ForeignKey("production_tasks.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("production_plan_id", "run_date", name="uq_production_plan_runs_plan_date"),
    )
    op.create_index("ix_production_plan_runs_run_date", "production_plan_runs", ["run_date"])


def downgrade() -> None:
    op.drop_index("ix_production_plan_runs_run_date", table_name="production_plan_runs")
    op.drop_table("production_plan_runs")
