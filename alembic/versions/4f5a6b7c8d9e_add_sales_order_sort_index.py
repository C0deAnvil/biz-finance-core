"""Add order sorting index for finance receivables.

Revision ID: 4f5a6b7c8d9e
Revises: 3e4f5a6b7c8d
Create Date: 2026-08-20 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f5a6b7c8d9e"
down_revision: Union[str, Sequence[str], None] = "3e4f5a6b7c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_sales_orders_created_at_id"
TABLE_NAME = "sales_orders"
COLUMNS = ["created_at", "id"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_equivalent_index = any(
        index.get("name") == INDEX_NAME or index.get("column_names") == COLUMNS
        for index in inspector.get_indexes(TABLE_NAME)
    )
    if not has_equivalent_index:
        op.create_index(INDEX_NAME, TABLE_NAME, COLUMNS, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_names = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
    if INDEX_NAME in existing_names:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
