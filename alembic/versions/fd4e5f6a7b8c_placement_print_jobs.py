"""Print agent credentials and durable placement print queue.

Revision ID: fd4e5f6a7b8c
Revises: fc3d4e5f6a7b
"""

import sqlalchemy as sa
from alembic import op


revision = "fd4e5f6a7b8c"
down_revision = "fc3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "placement_print_agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "placement_print_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queue_id", sa.Integer(), sa.ForeignKey("inventory_dispatch_queues.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by_agent_id", sa.Integer(), sa.ForeignKey("placement_print_agents.id"), nullable=True),
        sa.Column("lease_token_hash", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("printed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("queue_id", name="uq_placement_print_job_queue"),
    )
    op.create_table(
        "placement_print_access_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("placement_print_agents.id"), nullable=True),
        sa.Column("token_fingerprint", sa.String(12), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_placement_print_access_logs_created_at", "placement_print_access_logs", ["created_at"])
    op.execute("""
        INSERT INTO user_module_permissions (user_id, route_path, assigned_by_id, created_at, updated_at)
        SELECT u.id, '/admin/print-agents', u.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users u JOIN roles r ON r.id = u.role_id
        WHERE r.code = 'system_admin' AND u.is_active = 1
          AND NOT EXISTS (
              SELECT 1 FROM user_module_permissions p
              WHERE p.user_id = u.id AND p.route_path = '/admin/print-agents'
          )
    """)


def downgrade():
    op.drop_index("ix_placement_print_access_logs_created_at", table_name="placement_print_access_logs")
    op.drop_table("placement_print_access_logs")
    op.drop_table("placement_print_jobs")
    op.drop_table("placement_print_agents")
