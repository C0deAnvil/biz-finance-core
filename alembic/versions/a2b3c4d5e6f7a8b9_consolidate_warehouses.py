"""consolidate warehouses to two: 廊坊仓库 (raw) + 沧州仓库 (finished)

Revision ID: a2b3c4d5e6f7a8b9
Revises: 18385097a9d9
Create Date: 2026-08-31

- WH-FG 成品中心仓 -> code=CANGZHOU_FINISHED, name=沧州仓库
- WH-RM 原料中心仓 -> code=LANGFANG_RAW, name=廊坊仓库
- drop WH-QC 待检暂存仓 (quality) and LANGFANG_FINISHED 廊坊成品仓 (no references)
"""
from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7a8b9"
down_revision = "18385097a9d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE warehouses SET code = 'CANGZHOU_FINISHED', name = '沧州仓库' WHERE code = 'WH-FG'")
    )
    bind.execute(
        sa.text("UPDATE warehouses SET code = 'LANGFANG_RAW', name = '廊坊仓库' WHERE code = 'WH-RM'")
    )
    bind.execute(sa.text("DELETE FROM warehouses WHERE code IN ('WH-QC', 'LANGFANG_FINISHED')"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE warehouses SET code = 'WH-FG', name = '成品中心仓' WHERE code = 'CANGZHOU_FINISHED'")
    )
    bind.execute(
        sa.text("UPDATE warehouses SET code = 'WH-RM', name = '原料中心仓' WHERE code = 'LANGFANG_RAW'")
    )
    bind.execute(
        sa.text(
            "INSERT INTO warehouses (code, name, location, warehouse_type, created_at, updated_at) "
            "VALUES ('WH-QC', '待检暂存仓', 'A厂区质检暂存区', 'quality', NOW(), NOW()), "
            "('LANGFANG_FINISHED', '廊坊成品仓', '廊坊工厂区', 'finished', NOW(), NOW())"
        )
    )
