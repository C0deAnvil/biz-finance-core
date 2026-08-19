"""Bootstrap ERP schema

Revision ID: f7c5f2f7a9b1
Revises: 33fea41d6f15
Create Date: 2026-03-10 20:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from models import Base


# revision identifiers, used by Alembic.
revision: str = "f7c5f2f7a9b1"
down_revision: Union[str, Sequence[str], None] = "33fea41d6f15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _existing_columns(bind, "products")

    product_columns = {
        "specification": sa.Column("specification", sa.String(length=255), nullable=True),
        "unit": sa.Column("unit", sa.String(length=20), nullable=False, server_default="kg"),
        "safety_stock_qty": sa.Column("safety_stock_qty", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        "purchase_threshold_qty": sa.Column("purchase_threshold_qty", sa.Numeric(precision=18, scale=4), nullable=False, server_default="0"),
        "is_active": sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        "created_at": sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        "updated_at": sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    }
    for name, column in product_columns.items():
        if name not in existing_columns:
            op.add_column("products", column)

    other_tables = [table for name, table in Base.metadata.tables.items() if name != "products"]
    Base.metadata.create_all(bind=bind, tables=other_tables)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed([table for table in Base.metadata.sorted_tables if table.name != "products"]):
        if inspector.has_table(table.name):
            op.drop_table(table.name)

    existing_columns = _existing_columns(bind, "products")
    for column_name in [
        "updated_at",
        "created_at",
        "is_active",
        "purchase_threshold_qty",
        "safety_stock_qty",
        "unit",
        "specification",
    ]:
        if column_name in existing_columns:
            op.drop_column("products", column_name)
