"""Grant finance roles master-data and inventory-view permissions.

Revision ID: fb2c3d4e5f6a
Revises: fa1b2c3d4e5f
"""

from alembic import op


revision = "fb2c3d4e5f6a"
down_revision = "fa1b2c3d4e5f"
branch_labels = None
depends_on = None


FINANCE_ROUTE_PATHS = (
    "/customers",
    "/suppliers",
    "/products",
    "/materials",
    "/packaging-configs",
    "/standard-prices",
    "/inventory/stocks",
)


def upgrade():
    route_rows = " UNION ALL ".join(
        f"SELECT '{route_path}' AS route_path" for route_path in FINANCE_ROUTE_PATHS
    )
    op.execute(
        f"""
        INSERT INTO user_module_permissions
            (user_id, route_path, assigned_by_id, created_at, updated_at)
        SELECT users.id, finance_routes.route_path, users.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users
        JOIN roles ON roles.id = users.role_id
        CROSS JOIN ({route_rows}) AS finance_routes
        LEFT JOIN user_module_permissions existing_permission
            ON existing_permission.user_id = users.id
            AND existing_permission.route_path = finance_routes.route_path
        WHERE roles.code IN ('finance', 'finance_super')
          AND users.is_active = 1
          AND existing_permission.id IS NULL
        """
    )


def downgrade():
    # 这是权限数据补齐迁移。无法可靠区分迁移新增的授权与管理员后来手工
    # 配置的同名授权，因此回滚时保留权限，避免误删人工配置。
    pass
