"""Add indexes for finance receivable queries.

Revision ID: 3e4f5a6b7c8d
Revises: 2c3d4e5f6a7b, b0c1d2e3f4a5
Create Date: 2026-08-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3e4f5a6b7c8d"
down_revision: Union[str, Sequence[str], None] = ("2c3d4e5f6a7b", "b0c1d2e3f4a5")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = (
    ("ix_sales_orders_status_created_at", "sales_orders", ["status", "created_at", "id"]),
    ("ix_sales_order_items_order_id", "sales_order_items", ["sales_order_id"]),
    ("ix_receivable_records_order_received_at", "receivable_records", ["sales_order_id", "received_at", "id"]),
    ("ix_receivable_records_received_at", "receivable_records", ["received_at", "id"]),
    ("ix_sales_receipt_requests_order_created_at", "sales_receipt_requests", ["sales_order_id", "created_at", "id"]),
    ("ix_sales_receipt_requests_status_created_at", "sales_receipt_requests", ["status", "created_at", "id"]),
    ("ix_attachments_business_created_at", "attachments", ["business_type", "business_id", "created_at", "id"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {
        table_name: inspector.get_indexes(table_name)
        for _, table_name, _ in INDEXES
    }
    for index_name, table_name, columns in INDEXES:
        has_equivalent_index = any(
            index.get("name") == index_name or index.get("column_names") == columns
            for index in existing_indexes[table_name]
        )
        if not has_equivalent_index:
            op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for index_name, table_name, _ in reversed(INDEXES):
        existing_names = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name in existing_names:
            op.drop_index(index_name, table_name=table_name)
