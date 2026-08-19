"""add line packaging master data

Revision ID: b0c1d2e3f4a5
Revises: a0b1c2d3e4f5
Create Date: 2026-06-05 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b0c1d2e3f4a5"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_columns(bind, table_name: str, columns: dict[str, sa.Column]) -> None:
    existing = _existing_columns(bind, table_name)
    if not existing:
        return
    for name, column in columns.items():
        if name not in existing:
            op.add_column(table_name, column)


def _ensure_fk(bind, table_name: str, column_name: str, referred_table: str, fk_name: str) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing = inspector.get_foreign_keys(table_name)
    has_fk = any(
        fk.get("constrained_columns") == [column_name] and fk.get("referred_table") == referred_table
        for fk in existing
    )
    if has_fk:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.create_foreign_key(fk_name, referred_table, [column_name], ["id"])


def upgrade() -> None:
    bind = op.get_bind()
    _add_columns(
        bind,
        "packaging_configs",
        {
            "unit_price": sa.Column("unit_price", sa.Numeric(18, 2), nullable=False, server_default="0"),
        },
    )
    _add_columns(
        bind,
        "sales_order_items",
        {
            "packaging_unit_price": sa.Column("packaging_unit_price", sa.Numeric(18, 2), nullable=False, server_default="0"),
            "packaging_fee": sa.Column("packaging_fee", sa.Numeric(18, 2), nullable=False, server_default="0"),
        },
    )
    _add_columns(
        bind,
        "boms",
        {
            "packaging_config_id": sa.Column("packaging_config_id", sa.Integer(), nullable=True),
        },
    )
    _ensure_fk(bind, "boms", "packaging_config_id", "packaging_configs", "fk_boms_packaging_config_id_packaging_configs")


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind, "boms")
    if "packaging_config_id" in existing:
        op.drop_column("boms", "packaging_config_id")
    existing = _existing_columns(bind, "sales_order_items")
    for name in ["packaging_fee", "packaging_unit_price"]:
        if name in existing:
            op.drop_column("sales_order_items", name)
    existing = _existing_columns(bind, "packaging_configs")
    if "unit_price" in existing:
        op.drop_column("packaging_configs", "unit_price")
