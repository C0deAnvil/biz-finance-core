"""Add sales return review note

Revision ID: d2e3f4a5b6c7
Revises: c1a2b3d4e5f6
Create Date: 2026-03-12 11:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _existing_columns(bind, "sales_returns")
    if "review_note" not in existing_columns:
        op.add_column("sales_returns", sa.Column("review_note", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = _existing_columns(bind, "sales_returns")
    if "review_note" in existing_columns:
        op.drop_column("sales_returns", "review_note")
