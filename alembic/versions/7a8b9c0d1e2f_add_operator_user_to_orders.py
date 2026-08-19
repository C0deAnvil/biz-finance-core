"""add operator user to purchase and sales orders

Revision ID: 7a8b9c0d1e2f
Revises: 6a7b8c9d0e1f
Create Date: 2026-05-18 22:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "7a8b9c0d1e2f"
down_revision = "6a7b8c9d0e1f"
branch_labels = None
depends_on = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name, owner_column in (
        ("purchase_orders", "purchaser_user_id"),
        ("sales_orders", "sales_user_id"),
    ):
        columns = _existing_columns(bind, table_name)
        if "operator_user_id" not in columns:
            op.add_column(
                table_name,
                sa.Column("operator_user_id", sa.Integer(), nullable=True),
            )
        op.execute(f"UPDATE {table_name} SET operator_user_id = {owner_column} WHERE operator_user_id IS NULL")
        op.alter_column(
            table_name,
            "operator_user_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        existing_foreign_keys = inspector.get_foreign_keys(table_name)
        has_operator_fk = any(fk.get("referred_table") == "users" and fk.get("constrained_columns") == ["operator_user_id"] for fk in existing_foreign_keys)
        foreign_keys = {fk["name"] for fk in existing_foreign_keys}
        fk_name = f"fk_{table_name}_operator_user_id_users"
        if not has_operator_fk and fk_name not in foreign_keys:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.create_foreign_key(
                    fk_name,
                    "users",
                    ["operator_user_id"],
                    ["id"],
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in ("sales_orders", "purchase_orders"):
        columns = _existing_columns(bind, table_name)
        if "operator_user_id" in columns:
            foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys(table_name)}
            with op.batch_alter_table(table_name) as batch_op:
                fk_name = f"fk_{table_name}_operator_user_id_users"
                if fk_name in foreign_keys:
                    batch_op.drop_constraint(fk_name, type_="foreignkey")
            op.drop_column(table_name, "operator_user_id")
