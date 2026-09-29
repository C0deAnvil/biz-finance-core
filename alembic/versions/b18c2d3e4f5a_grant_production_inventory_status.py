"""Grant existing production users access to inventory status.

Revision ID: b18c2d3e4f5a
Revises: a07b8c9d0e1f
"""

from alembic import op


revision = "b18c2d3e4f5a"
down_revision = "a07b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        INSERT INTO user_module_permissions
            (user_id, route_path, assigned_by_id, created_at, updated_at)
        SELECT users.id, '/inventory/stocks', users.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users
        JOIN roles ON roles.id = users.role_id
        LEFT JOIN user_module_permissions existing_permission
            ON existing_permission.user_id = users.id
            AND existing_permission.route_path = '/inventory/stocks'
        WHERE roles.code = 'production'
          AND users.is_active = 1
          AND existing_permission.id IS NULL
        """
    )


def downgrade():
    # 无法可靠区分迁移新增授权与管理员之后手动配置的授权，回滚时保留。
    pass
