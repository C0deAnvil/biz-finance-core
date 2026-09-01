"""auto_check_diff - 占位迁移（已中和）

此迁移由一次误触发的 ``alembic revision --autogenerate`` 生成，内容为
删除业务索引与修改列可空性，属于无意义的 schema 漂移同步，不应执行。
现将其中和为空操作，仅保留 revision 链以保持 alembic 版本连续性。

Revision ID: 016c0110ebf1
Revises: a2b3c4d5e6f7a8b9
Create Date: 2026-09-01 15:29:32.745198

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '016c0110ebf1'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 空操作：保留数据库现有索引与列定义
    pass


def downgrade() -> None:
    """Downgrade schema."""
    # 空操作
    pass