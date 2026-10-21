"""Track manual and automatic placement-order printing on the dispatch queue.

Revision ID: f52a6b7c8d9e
Revises: e41f5a6b7c8d
"""

import sqlalchemy as sa
from alembic import op


revision = "f52a6b7c8d9e"
down_revision = "e41f5a6b7c8d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inventory_dispatch_queues", sa.Column("placement_printed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "inventory_dispatch_queues",
        sa.Column("placement_print_status", sa.String(20), nullable=False, server_default="unprinted"),
    )
    op.add_column("inventory_dispatch_queues", sa.Column("placement_print_method", sa.String(20), nullable=True))
    op.add_column("inventory_dispatch_queues", sa.Column("placement_printed_by_id", sa.Integer(), nullable=True))
    op.add_column(
        "inventory_dispatch_queues",
        sa.Column("placement_print_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_dispatch_queue_placement_printed_by",
        "inventory_dispatch_queues",
        "users",
        ["placement_printed_by_id"],
        ["id"],
    )
    op.create_index(
        "ix_inventory_dispatch_queues_placement_printed_at",
        "inventory_dispatch_queues",
        ["placement_printed_at"],
    )
    # Preserve successful automatic prints recorded by the earlier job-only design.
    op.execute("""
        UPDATE inventory_dispatch_queues AS q
        SET placement_printed_at = (
                SELECT MAX(j.printed_at)
                FROM placement_print_jobs AS j
                WHERE j.queue_id = q.id
                  AND j.status = 'submitted'
                  AND j.printed_at IS NOT NULL
            ),
            placement_print_status = 'printed',
            placement_print_method = 'automatic',
            placement_print_count = 1
        WHERE EXISTS (
            SELECT 1
            FROM placement_print_jobs AS j
            WHERE j.queue_id = q.id
              AND j.status = 'submitted'
              AND j.printed_at IS NOT NULL
        )
    """)


def downgrade():
    op.drop_index("ix_inventory_dispatch_queues_placement_printed_at", table_name="inventory_dispatch_queues")
    op.drop_constraint(
        "fk_dispatch_queue_placement_printed_by",
        "inventory_dispatch_queues",
        type_="foreignkey",
    )
    op.drop_column("inventory_dispatch_queues", "placement_print_count")
    op.drop_column("inventory_dispatch_queues", "placement_printed_by_id")
    op.drop_column("inventory_dispatch_queues", "placement_print_method")
    op.drop_column("inventory_dispatch_queues", "placement_print_status")
    op.drop_column("inventory_dispatch_queues", "placement_printed_at")
