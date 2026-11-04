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
    # MySQL DDL is non-transactional. If deployment is interrupted, part of this
    # migration may already exist while alembic_version still points to d07.
    # Inspect every object before creating it so `alembic upgrade head` is safe
    # to rerun after a partial failure.
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    queue_columns = {column["name"] for column in inspector.get_columns("inventory_dispatch_queues")}
    queue_additions = (
        sa.Column("shipment_printed_at", sa.DateTime(), nullable=True),
        sa.Column("shipment_print_status", sa.String(20), nullable=False, server_default="unprinted"),
        sa.Column("shipment_print_method", sa.String(20), nullable=True),
        sa.Column("shipment_printed_by_id", sa.Integer(), nullable=True),
        sa.Column("shipment_print_count", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in queue_additions:
        if column.name not in queue_columns:
            op.add_column("inventory_dispatch_queues", column)

    inspector = sa.inspect(connection)
    queue_indexes = {index["name"] for index in inspector.get_indexes("inventory_dispatch_queues")}
    if "ix_inventory_dispatch_queues_shipment_printed_at" not in queue_indexes:
        op.create_index(
            "ix_inventory_dispatch_queues_shipment_printed_at",
            "inventory_dispatch_queues",
            ["shipment_printed_at"],
        )
    queue_foreign_keys = {
        foreign_key.get("name")
        for foreign_key in inspector.get_foreign_keys("inventory_dispatch_queues")
    }
    if "fk_dispatch_queue_shipment_printed_by" not in queue_foreign_keys:
        op.create_foreign_key(
            "fk_dispatch_queue_shipment_printed_by",
            "inventory_dispatch_queues",
            "users",
            ["shipment_printed_by_id"],
            ["id"],
        )

    inspector = sa.inspect(connection)
    job_columns = {column["name"] for column in inspector.get_columns("placement_print_jobs")}
    unique_constraints = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints("placement_print_jobs")
    }
    if "document_kind" not in job_columns:
        op.add_column(
            "placement_print_jobs",
            sa.Column("document_kind", sa.String(20), nullable=False, server_default="placement"),
        )
    inspector = sa.inspect(connection)
    unique_constraints = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints("placement_print_jobs")
    }
    if "uq_print_job_queue_kind" not in unique_constraints:
        op.create_unique_constraint(
            "uq_print_job_queue_kind",
            "placement_print_jobs",
            ["queue_id", "document_kind"],
        )
    # The old unique index is also the supporting index for queue_id's MySQL
    # foreign key. Create the replacement composite index first, then MySQL can
    # safely release the old one without error 1553.
    inspector = sa.inspect(connection)
    unique_constraints = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints("placement_print_jobs")
    }
    if "uq_placement_print_job_queue" in unique_constraints:
        op.drop_constraint("uq_placement_print_job_queue", "placement_print_jobs", type_="unique")


def downgrade():
    op.execute("DELETE FROM placement_print_jobs WHERE document_kind = 'shipment'")
    with op.batch_alter_table("placement_print_jobs") as batch:
        batch.create_unique_constraint("uq_placement_print_job_queue", ["queue_id"])
        batch.drop_constraint("uq_print_job_queue_kind", type_="unique")
        batch.drop_column("document_kind")
    with op.batch_alter_table("inventory_dispatch_queues") as batch:
        batch.drop_constraint("fk_dispatch_queue_shipment_printed_by", type_="foreignkey")
        batch.drop_index("ix_inventory_dispatch_queues_shipment_printed_at")
        batch.drop_column("shipment_print_count")
        batch.drop_column("shipment_printed_by_id")
        batch.drop_column("shipment_print_method")
        batch.drop_column("shipment_print_status")
        batch.drop_column("shipment_printed_at")
