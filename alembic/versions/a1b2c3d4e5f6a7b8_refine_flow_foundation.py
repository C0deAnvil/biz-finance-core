"""refine flow foundation: warehouse qc/manager, production target warehouse, invoice fields; drop redundant order_flow_status

Revision ID: a1b2c3d4e5f6a7b8
Revises: f6e7d8c9b0a1f2e3
Create Date: 2026-08-31

1. drop order_flow_status (redundant with document_status_snapshots)
2. warehouses: add qc_user_id / manager_user_id; insert 廊坊成品仓 (finished)
3. production_tasks: add warehouse_id (target inbound warehouse)
4. purchase_payment_requests: add invoice fields
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6a7b8"
down_revision = "f6e7d8c9b0a1f2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1. drop redundant order_flow_status ----------
    if _table_exists(bind, "order_flow_status"):
        op.drop_index("ix_flow_root", table_name="order_flow_status")
        op.drop_table("order_flow_status")

    # ---------- 2. warehouses: qc/manager + 廊坊成品仓 ----------
    cols = _cols(bind, "warehouses")
    if "qc_user_id" not in cols:
        op.add_column("warehouses", sa.Column("qc_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))
    if "manager_user_id" not in cols:
        op.add_column("warehouses", sa.Column("manager_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))

    # 插入廊坊成品仓（幂等：按 code 判断）
    exists = bind.execute(sa.text("SELECT COUNT(*) FROM warehouses WHERE code = 'LANGFANG_FINISHED'")).scalar()
    if not exists:
        bind.execute(
            sa.text(
                "INSERT INTO warehouses (code, name, location, warehouse_type, created_at, updated_at) "
                "VALUES ('LANGFANG_FINISHED', '廊坊成品仓', '廊坊工厂区', 'finished', NOW(), NOW())"
            )
        )

    # 绑定默认质检/库管（存在则赋值，便于后续主数据调整）
    bind.execute(
        sa.text(
            "UPDATE warehouses w "
            "LEFT JOIN users u ON u.username = 'quality01' "
            "SET w.qc_user_id = u.id WHERE w.code = 'WH-FG' AND u.id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE warehouses w "
            "LEFT JOIN users u ON u.username = 'inventory01' "
            "SET w.manager_user_id = u.id WHERE w.code = 'WH-FG' AND u.id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE warehouses w "
            "LEFT JOIN users u ON u.username = 'inventoryraw01' "
            "SET w.manager_user_id = u.id WHERE w.code = 'WH-RM' AND u.id IS NOT NULL"
        )
    )

    # ---------- 3. production_tasks: warehouse_id ----------
    cols = _cols(bind, "production_tasks")
    if "warehouse_id" not in cols:
        op.add_column("production_tasks", sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=True))

    # ---------- 4. purchase_payment_requests: invoice fields ----------
    cols = _cols(bind, "purchase_payment_requests")
    for name, col in (
        ("invoice_no", sa.String(length=80)),
        ("invoice_amount", sa.Numeric(18, 2)),
        ("invoice_date", sa.Date()),
    ):
        if name not in cols:
            op.add_column("purchase_payment_requests", sa.Column(name, col, nullable=True))
    if "invoice_attachment_id" not in cols:
        op.add_column(
            "purchase_payment_requests",
            sa.Column("invoice_attachment_id", sa.Integer(), sa.ForeignKey("attachments.id"), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = _cols(bind, "purchase_payment_requests")
    for name in ("invoice_attachment_id", "invoice_date", "invoice_amount", "invoice_no"):
        if name in cols:
            op.drop_column("purchase_payment_requests", name)
    cols = _cols(bind, "production_tasks")
    if "warehouse_id" in cols:
        op.drop_column("production_tasks", "warehouse_id")
    cols = _cols(bind, "warehouses")
    bind.execute(sa.text("DELETE FROM warehouses WHERE code = 'LANGFANG_FINISHED'"))
    for name in ("manager_user_id", "qc_user_id"):
        if name in cols:
            op.drop_column("warehouses", name)
    if not _table_exists(bind, "order_flow_status"):
        op.create_table(
            "order_flow_status",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("doc_type", sa.String(length=30), nullable=False),
            sa.Column("doc_id", sa.Integer(), nullable=False),
            sa.Column("root_type", sa.String(length=30), nullable=False, server_default="sales_order"),
            sa.Column("root_id", sa.Integer(), nullable=True),
            sa.Column("fin_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("placement_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("production_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("purchase_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("quality_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("inbound_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("shipment_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("overall_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("current_stage", sa.String(length=30), nullable=False, server_default="created"),
            sa.Column("fin_at", sa.DateTime(), nullable=True),
            sa.Column("placement_at", sa.DateTime(), nullable=True),
            sa.Column("production_at", sa.DateTime(), nullable=True),
            sa.Column("purchase_at", sa.DateTime(), nullable=True),
            sa.Column("quality_at", sa.DateTime(), nullable=True),
            sa.Column("inbound_at", sa.DateTime(), nullable=True),
            sa.Column("shipment_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            sa.Column("ext_json", sa.JSON(), nullable=True),
            sa.UniqueConstraint("doc_type", "doc_id", name="uq_flow_doc"),
            mysql_charset="utf8mb4",
        )
        op.create_index("ix_flow_root", "order_flow_status", ["root_type", "root_id"])


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(sa.text("SHOW TABLES LIKE :t"), {"t": table}).fetchone())


def _cols(bind, table: str) -> list[str]:
    return [r[0] for r in bind.execute(sa.text(f"SHOW COLUMNS FROM {table}"))]
