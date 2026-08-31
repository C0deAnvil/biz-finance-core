"""add flow status, customer tags/follow-ups, inventory & order fields

Revision ID: f6e7d8c9b0a1f2e3
Revises: 4f5a6b7c8d9e
Create Date: 2026-08-31

底层结构扩展（直接 SQL，目标 MySQL yecai_db）：
1. 新增 order_flow_status 全链订单状态表（多态 doc_type+doc_id，含 root 溯源）
2. 新增 customer_tags / customer_follow_ups 客户标签与跟进记录
3. inventories 增加在制/在途/在订/可用库存字段 + ext_json 备用
4. purchase_orders 增加 sales_order_id 外键串回客户订单 + ext_json
5. sales_orders / production_tasks / customers 增加 ext_json 备用字段
"""
from alembic import op
import sqlalchemy as sa

revision = "f6e7d8c9b0a1f2e3"
down_revision = "4f5a6b7c8d9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1. 全链订单状态表 ----------
    op.create_table(
        "order_flow_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_type", sa.String(length=30), nullable=False),
        sa.Column("doc_id", sa.Integer(), nullable=False),
        sa.Column("root_type", sa.String(length=30), nullable=False, server_default="sales_order"),
        sa.Column("root_id", sa.Integer(), nullable=True),
        # 各环节状态
        sa.Column("fin_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("placement_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("production_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("purchase_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("quality_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("inbound_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("shipment_status", sa.String(length=20), nullable=False, server_default="pending"),
        # 汇总态与当前环节
        sa.Column("overall_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(length=30), nullable=False, server_default="created"),
        # 各环节完成时间
        sa.Column("fin_at", sa.DateTime(), nullable=True),
        sa.Column("placement_at", sa.DateTime(), nullable=True),
        sa.Column("production_at", sa.DateTime(), nullable=True),
        sa.Column("purchase_at", sa.DateTime(), nullable=True),
        sa.Column("quality_at", sa.DateTime(), nullable=True),
        sa.Column("inbound_at", sa.DateTime(), nullable=True),
        sa.Column("shipment_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        # 审计与备用
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.Column("ext_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint("doc_type", "doc_id", name="uq_flow_doc"),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_flow_root", "order_flow_status", ["root_type", "root_id"])

    # ---------- 2. 客户标签 ----------
    op.create_table(
        "customer_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("tag", sa.String(length=50), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("customer_id", "tag", name="uq_customer_tag"),
        mysql_charset="utf8mb4",
    )

    # ---------- 3. 客户跟进记录 ----------
    op.create_table(
        "customer_follow_ups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("follow_type", sa.String(length=30), nullable=False, server_default="call"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("next_follow_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("followed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_customer_followup_next", "customer_follow_ups", ["next_follow_at"])

    # ---------- 4. inventories 增加库存多字段 ----------
    add_cols(inspect_cols(bind, "inventories"), "inventories", [
        ("in_production", sa.Numeric(18, 4), 0),   # 在制
        ("in_transit", sa.Numeric(18, 4), 0),      # 在途
        ("on_order", sa.Numeric(18, 4), 0),        # 在订（采购在途）
        ("available", sa.Numeric(18, 4), 0),       # 可用（冗余计算）
        ("ext_json", sa.JSON(), None),
    ])

    # ---------- 5. purchase_orders 增加 sales_order_id + ext_json ----------
    cols = inspect_cols(bind, "purchase_orders")
    if "sales_order_id" not in cols:
        op.add_column("purchase_orders", sa.Column("sales_order_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_po_sales_order", "purchase_orders", "sales_orders", ["sales_order_id"], ["id"])
    if "ext_json" not in cols:
        op.add_column("purchase_orders", sa.Column("ext_json", sa.JSON(), nullable=True))

    # ---------- 6. sales_orders / production_tasks / customers 增加 ext_json ----------
    add_cols(inspect_cols(bind, "sales_orders"), "sales_orders", [("ext_json", sa.JSON(), None)])
    add_cols(inspect_cols(bind, "production_tasks"), "production_tasks", [("ext_json", sa.JSON(), None)])
    add_cols(inspect_cols(bind, "customers"), "customers", [("ext_json", sa.JSON(), None)])


def downgrade() -> None:
    bind = op.get_bind()
    drop_cols(inspect_cols(bind, "customers"), "customers", ["ext_json"])
    drop_cols(inspect_cols(bind, "production_tasks"), "production_tasks", ["ext_json"])
    drop_cols(inspect_cols(bind, "sales_orders"), "sales_orders", ["ext_json"])
    if "ext_json" in inspect_cols(bind, "purchase_orders"):
        op.drop_column("purchase_orders", "ext_json")
    if "sales_order_id" in inspect_cols(bind, "purchase_orders"):
        op.drop_constraint("fk_po_sales_order", "purchase_orders", type_="foreignkey")
        op.drop_column("purchase_orders", "sales_order_id")
    for col in ["ext_json", "available", "on_order", "in_transit", "in_production"]:
        if col in inspect_cols(bind, "inventories"):
            op.drop_column("inventories", col)
    op.drop_index("ix_customer_followup_next", table_name="customer_follow_ups")
    op.drop_table("customer_follow_ups")
    op.drop_table("customer_tags")
    op.drop_index("ix_flow_root", table_name="order_flow_status")
    op.drop_table("order_flow_status")


def inspect_cols(bind, table):
    cols = [r[0] for r in bind.execute(sa.text(f"SHOW COLUMNS FROM {table}"))]
    return cols


def add_cols(cols, table, col_defs):
    for name, col_type, default in col_defs:
        if name not in cols:
            col = sa.Column(name, col_type, nullable=True)
            op.add_column(table, col)
