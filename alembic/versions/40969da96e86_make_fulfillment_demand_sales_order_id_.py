"""make fulfillment demand sales_order_id nullable

Revision ID: 40969da96e86
Revises: 8f7e6d5c4b3a
Create Date: 2026-09-02 12:33:46.603659

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '40969da96e86'
down_revision: Union[str, Sequence[str], None] = '8f7e6d5c4b3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('fulfillment_demands', 'sales_order_id',
               existing_type=mysql.INTEGER(),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('fulfillment_demands', 'sales_order_id',
               existing_type=mysql.INTEGER(),
               nullable=False)