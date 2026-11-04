"""Split automatic placement and shipment print jobs.

Revision ID: d18f2b3c4d5e
Revises: d07f1a2b3c4d
"""
from alembic import op
import sqlalchemy as sa


revision = "d18f2b3c4d5e"
down_revision = "d07f1a2b3c4d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inventory_dispatch_queues") as batch:
        batch.add_column(sa.Column("shipment_printed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("shipment_print_status", sa.String(20), nullable=False, server_default="unprinted"))
        batch.add_column(sa.Column("shipment_print_method", sa.String(20), nullable=True))
        batch.add_column(sa.Column("shipment_printed_by_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("shipment_print_count", sa.Integer(), nullable=False, server_default="0"))
        batch.create_index("ix_inventory_dispatch_queues_shipment_printed_at", ["shipment_printed_at"])
        batch.create_foreign_key(
            "fk_dispatch_queue_shipment_printed_by",
            "users",
            ["shipment_printed_by_id"],
            ["id"],
        )
    with op.batch_alter_table("placement_print_jobs") as batch:
        batch.drop_constraint("uq_placement_print_job_queue", type_="unique")
        batch.add_column(sa.Column("document_kind", sa.String(20), nullable=False, server_default="placement"))
        batch.create_unique_constraint("uq_print_job_queue_kind", ["queue_id", "document_kind"])


def downgrade():
    op.execute("DELETE FROM placement_print_jobs WHERE document_kind = 'shipment'")
    with op.batch_alter_table("placement_print_jobs") as batch:
        batch.drop_constraint("uq_print_job_queue_kind", type_="unique")
        batch.drop_column("document_kind")
        batch.create_unique_constraint("uq_placement_print_job_queue", ["queue_id"])
    with op.batch_alter_table("inventory_dispatch_queues") as batch:
        batch.drop_constraint("fk_dispatch_queue_shipment_printed_by", type_="foreignkey")
        batch.drop_index("ix_inventory_dispatch_queues_shipment_printed_at")
        batch.drop_column("shipment_print_count")
        batch.drop_column("shipment_printed_by_id")
        batch.drop_column("shipment_print_method")
        batch.drop_column("shipment_print_status")
        batch.drop_column("shipment_printed_at")
