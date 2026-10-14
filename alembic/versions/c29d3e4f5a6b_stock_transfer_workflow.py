"""Route stock transfers through inventory operations and optional quality tasks.

Revision ID: c29d3e4f5a6b
Revises: b18c2d3e4f5a
"""

from alembic import op
import sqlalchemy as sa

revision = "c29d3e4f5a6b"
down_revision = "b18c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stock_transfers", sa.Column("requires_quality", sa.Boolean(), nullable=False, server_default=sa.false()))
    # 历史移库全部走质检；新单才采用默认免检。
    op.execute("UPDATE stock_transfers SET requires_quality = 1")
    op.alter_column("quality_tasks", "production_task_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("quality_tasks", sa.Column("stock_transfer_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_quality_tasks_stock_transfer_id", "quality_tasks", "stock_transfers", ["stock_transfer_id"], ["id"])
    op.create_unique_constraint("uq_quality_tasks_stock_transfer_id", "quality_tasks", ["stock_transfer_id"])


def downgrade() -> None:
    linked_tasks = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM quality_tasks WHERE stock_transfer_id IS NOT NULL")).scalar_one()
    if linked_tasks:
        raise RuntimeError("先处理移库质检任务数据，才能回退本次迁移")
    op.drop_constraint("uq_quality_tasks_stock_transfer_id", "quality_tasks", type_="unique")
    op.drop_constraint("fk_quality_tasks_stock_transfer_id", "quality_tasks", type_="foreignkey")
    op.drop_column("quality_tasks", "stock_transfer_id")
    op.alter_column("quality_tasks", "production_task_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("stock_transfers", "requires_quality")
