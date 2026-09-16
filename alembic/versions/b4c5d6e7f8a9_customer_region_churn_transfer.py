"""客户新增 地区(省份) / 转交日期 / 流失复购日期 / 末次下单时间，并补附件查询索引

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-16

1. customers 增加：
   - region_province     地区（按省级行政区划分）
   - last_transferred_at 最近一次转交日期（由客户转移接口自动写入）
   - churn_repurchase_at 流失复购日期（下单时自动判定：距上一笔订单 > 365 天且本单金额 > 100 元）
   - last_order_at       末次下单时间（流失复购判定的依据，系统维护）
2. attachments 增加 (business_type, business_id) 复合索引 —— 出入库照片会按这两列频繁查询
3. 回填：
   - last_transferred_at 取 customer_transfer_records 里最近一条 transferred_at
   - last_order_at / churn_repurchase_at 由 sales_orders 历史推算
"""
from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def _cols(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1. 客户新字段 ----------
    customer_columns = [
        sa.Column("region_province", sa.String(length=40), nullable=True, comment="地区：省/直辖市/自治区/特别行政区"),
        sa.Column("last_transferred_at", sa.DateTime(), nullable=True, comment="最近一次转交日期"),
        sa.Column("churn_repurchase_at", sa.DateTime(), nullable=True, comment="流失复购日期：距上笔订单>365天且本单>100元"),
        sa.Column("last_order_at", sa.DateTime(), nullable=True, comment="末次下单时间，系统维护"),
    ]
    existing = _cols(bind, "customers")
    for column in customer_columns:
        if column.name not in existing:
            op.add_column("customers", column)

    customer_indexes = _indexes(bind, "customers")
    if "ix_customers_region_province" not in customer_indexes:
        op.create_index("ix_customers_region_province", "customers", ["region_province"])
    if "ix_customers_churn_repurchase_at" not in customer_indexes:
        op.create_index("ix_customers_churn_repurchase_at", "customers", ["churn_repurchase_at"])

    # ---------- 2. 附件复合索引 ----------
    if "ix_attachments_business" not in _indexes(bind, "attachments"):
        op.create_index("ix_attachments_business", "attachments", ["business_type", "business_id"])

    # ---------- 3. 回填历史 ----------
    # 3.1 转交日期：取每个客户最近一条转交记录
    if sa.inspect(bind).has_table("customer_transfer_records"):
        bind.execute(
            sa.text(
                """
                UPDATE customers c
                LEFT JOIN (
                    SELECT customer_id, MAX(transferred_at) AS last_at
                      FROM customer_transfer_records
                     GROUP BY customer_id
                ) t ON t.customer_id = c.id
                   SET c.last_transferred_at = t.last_at
                 WHERE t.last_at IS NOT NULL
                """
            )
        )

    # 3.2 末次下单时间：取每个客户最近一笔未取消订单
    if sa.inspect(bind).has_table("sales_orders"):
        bind.execute(
            sa.text(
                """
                UPDATE customers c
                LEFT JOIN (
                    SELECT customer_id, MAX(created_at) AS last_at
                      FROM sales_orders
                     WHERE status <> 'cancelled'
                     GROUP BY customer_id
                ) o ON o.customer_id = c.id
                   SET c.last_order_at = o.last_at
                 WHERE o.last_at IS NOT NULL
                """
            )
        )

        # 3.3 流失复购日期：历史订单里，与上一笔间隔 > 365 天且本单 > 100 元的最近一笔
        #     用窗口函数取每笔订单的上一笔时间（MySQL 8 支持 LAG）
        bind.execute(
            sa.text(
                """
                UPDATE customers c
                LEFT JOIN (
                    SELECT customer_id, MAX(created_at) AS churn_at
                      FROM (
                        SELECT customer_id,
                               created_at,
                               total_amount,
                               LAG(created_at) OVER (
                                   PARTITION BY customer_id ORDER BY created_at
                               ) AS prev_at
                          FROM sales_orders
                         WHERE status <> 'cancelled'
                      ) x
                     WHERE prev_at IS NOT NULL
                       AND created_at > prev_at + INTERVAL 365 DAY
                       AND total_amount > 100
                     GROUP BY customer_id
                ) r ON r.customer_id = c.id
                   SET c.churn_repurchase_at = r.churn_at
                 WHERE r.churn_at IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    if "ix_attachments_business" in _indexes(bind, "attachments"):
        op.drop_index("ix_attachments_business", table_name="attachments")

    customer_indexes = _indexes(bind, "customers")
    for index_name in ("ix_customers_region_province", "ix_customers_churn_repurchase_at"):
        if index_name in customer_indexes:
            op.drop_index(index_name, table_name="customers")

    existing = _cols(bind, "customers")
    for column_name in ("region_province", "last_transferred_at", "churn_repurchase_at", "last_order_at"):
        if column_name in existing:
            op.drop_column("customers", column_name)
