"""质检任务:指定质检员 + 归属仓库

需求:申请质检时需选择一个质检员,由该质检员或超级质检员处理;
普通质检员只能看到自己所属仓库的质检任务。

- assigned_inspector_id:派给谁检(QualityInspection.inspector_user_id 记录谁检了,语义不同)
- target_warehouse_id:任务归属仓库,用于普通质检员的可见范围过滤

Revision ID: e2b3c4d5f6a7
Revises: d1a2b3c4e5f6
Create Date: 2026-09-05 02:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e2b3c4d5f6a7"
down_revision = "d1a2b3c4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quality_tasks", sa.Column("assigned_inspector_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_quality_tasks_assigned_inspector", "quality_tasks", "users",
        ["assigned_inspector_id"], ["id"],
    )
    op.add_column("quality_tasks", sa.Column("target_warehouse_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_quality_tasks_target_warehouse", "quality_tasks", "warehouses",
        ["target_warehouse_id"], ["id"],
    )
    # 回填历史任务的归属仓库:经影子生产任务关联到其仓库,
    # 否则历史任务对普通质检员将全部不可见(warehouse 为空时按"全部可见"兜底处理,
    # 但能回填的仍应回填,以保证权限过滤尽早生效)。
    op.execute(
        """
        UPDATE quality_tasks q
        JOIN production_tasks t ON t.id = q.production_task_id
        SET q.target_warehouse_id = t.warehouse_id
        WHERE t.warehouse_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_quality_tasks_target_warehouse", "quality_tasks", type_="foreignkey")
    op.drop_column("quality_tasks", "target_warehouse_id")
    op.drop_constraint("fk_quality_tasks_assigned_inspector", "quality_tasks", type_="foreignkey")
    op.drop_column("quality_tasks", "assigned_inspector_id")
