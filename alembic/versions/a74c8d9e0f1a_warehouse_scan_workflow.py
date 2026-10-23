"""Add warehouse-bound placement and shipment scan workflow.

Revision ID: a74c8d9e0f1a
Revises: f63b7c8d9e0f
"""

import secrets

import sqlalchemy as sa
from alembic import op


revision = "a74c8d9e0f1a"
down_revision = "f63b7c8d9e0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_dispatch_queues", sa.Column("warehouse_id", sa.Integer(), nullable=True))
    op.add_column("inventory_dispatch_queues", sa.Column("placement_scan_token", sa.String(32), nullable=True))
    op.add_column("inventory_dispatch_queues", sa.Column("shipment_scan_token", sa.String(32), nullable=True))
    op.add_column(
        "inventory_dispatch_queues",
        sa.Column("barcode_version", sa.Integer(), nullable=False, server_default="1"),
    )

    bind = op.get_bind()
    finished_id = bind.execute(
        sa.text("SELECT id FROM warehouses WHERE warehouse_type = 'finished' ORDER BY id LIMIT 1")
    ).scalar()
    if finished_id is None:
        finished_id = bind.execute(sa.text("SELECT id FROM warehouses ORDER BY id LIMIT 1")).scalar()
    queue_ids = bind.execute(sa.text("SELECT id FROM inventory_dispatch_queues ORDER BY id")).scalars().all()
    for queue_id in queue_ids:
        bind.execute(
            sa.text(
                "UPDATE inventory_dispatch_queues "
                "SET warehouse_id = :warehouse_id, placement_scan_token = :placement_token, "
                "shipment_scan_token = :shipment_token WHERE id = :queue_id"
            ),
            {
                "warehouse_id": finished_id,
                "placement_token": secrets.token_hex(12).upper(),
                "shipment_token": secrets.token_hex(12).upper(),
                "queue_id": queue_id,
            },
        )

    with op.batch_alter_table("inventory_dispatch_queues") as batch_op:
        batch_op.alter_column("placement_scan_token", existing_type=sa.String(32), nullable=False)
        batch_op.alter_column("shipment_scan_token", existing_type=sa.String(32), nullable=False)
        batch_op.create_foreign_key(
            "fk_dispatch_queue_warehouse", "warehouses", ["warehouse_id"], ["id"]
        )
        batch_op.create_index("ix_inventory_dispatch_queues_warehouse_id", ["warehouse_id"])
        batch_op.create_unique_constraint(
            "uq_inventory_dispatch_queues_placement_scan_token", ["placement_scan_token"]
        )
        batch_op.create_unique_constraint(
            "uq_inventory_dispatch_queues_shipment_scan_token", ["shipment_scan_token"]
        )

    op.create_table(
        "warehouse_scan_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("operator_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_warehouse_scan_devices_warehouse_id", "warehouse_scan_devices", ["warehouse_id"])

    op.create_table(
        "warehouse_scan_access_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("warehouse_scan_devices.id"), nullable=True),
        sa.Column("token_fingerprint", sa.String(12), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_warehouse_scan_access_logs_created_at", "warehouse_scan_access_logs", ["created_at"]
    )

    op.create_table(
        "warehouse_scan_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("warehouse_scan_devices.id"), nullable=False),
        sa.Column("queue_id", sa.Integer(), sa.ForeignKey("inventory_dispatch_queues.id"), nullable=True),
        sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id"), nullable=True),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("operator_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("barcode_fingerprint", sa.String(12), nullable=True),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("device_id", "idempotency_key", name="uq_scan_event_device_idempotency"),
    )
    for column in ("device_id", "queue_id", "sales_order_id", "warehouse_id"):
        op.create_index(f"ix_warehouse_scan_events_{column}", "warehouse_scan_events", [column])

    op.create_table(
        "shipment_proofs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queue_id", sa.Integer(), sa.ForeignKey("inventory_dispatch_queues.id"), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("warehouse_scan_devices.id"), nullable=False),
        sa.Column("attachment_id", sa.Integer(), sa.ForeignKey("attachments.id"), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_shipment_proofs_queue_id", "shipment_proofs", ["queue_id"])
    op.create_index("ix_shipment_proofs_sales_order_id", "shipment_proofs", ["sales_order_id"])

    op.create_table(
        "sales_shipment_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_no", sa.String(60), nullable=False, unique=True),
        sa.Column("queue_id", sa.Integer(), sa.ForeignKey("inventory_dispatch_queues.id"), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("warehouse_scan_devices.id"), nullable=True),
        sa.Column("operator_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("proof_id", sa.Integer(), sa.ForeignKey("shipment_proofs.id"), nullable=True, unique=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="completed"),
        sa.Column("total_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("shipped_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sales_shipment_batches_queue_id", "sales_shipment_batches", ["queue_id"])
    op.create_index("ix_sales_shipment_batches_sales_order_id", "sales_shipment_batches", ["sales_order_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_shipment_batches_sales_order_id", table_name="sales_shipment_batches")
    op.drop_index("ix_sales_shipment_batches_queue_id", table_name="sales_shipment_batches")
    op.drop_table("sales_shipment_batches")
    op.drop_index("ix_shipment_proofs_sales_order_id", table_name="shipment_proofs")
    op.drop_index("ix_shipment_proofs_queue_id", table_name="shipment_proofs")
    op.drop_table("shipment_proofs")
    for column in ("warehouse_id", "sales_order_id", "queue_id", "device_id"):
        op.drop_index(f"ix_warehouse_scan_events_{column}", table_name="warehouse_scan_events")
    op.drop_table("warehouse_scan_events")
    op.drop_index("ix_warehouse_scan_access_logs_created_at", table_name="warehouse_scan_access_logs")
    op.drop_table("warehouse_scan_access_logs")
    op.drop_index("ix_warehouse_scan_devices_warehouse_id", table_name="warehouse_scan_devices")
    op.drop_table("warehouse_scan_devices")

    with op.batch_alter_table("inventory_dispatch_queues") as batch_op:
        batch_op.drop_constraint("uq_inventory_dispatch_queues_shipment_scan_token", type_="unique")
        batch_op.drop_constraint("uq_inventory_dispatch_queues_placement_scan_token", type_="unique")
        batch_op.drop_index("ix_inventory_dispatch_queues_warehouse_id")
        batch_op.drop_constraint("fk_dispatch_queue_warehouse", type_="foreignkey")
        batch_op.drop_column("barcode_version")
        batch_op.drop_column("shipment_scan_token")
        batch_op.drop_column("placement_scan_token")
        batch_op.drop_column("warehouse_id")
