"""add payment request assigned finance user

Revision ID: d85660848e5e
Revises: 5374478d052e
Create Date: 2026-09-02 15:41:03.135752

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'd85660848e5e'
down_revision: Union[str, Sequence[str], None] = '5374478d052e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('purchase_payment_requests', sa.Column('assigned_finance_user_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_purchase_payment_requests_assigned_finance', 'purchase_payment_requests', 'users', ['assigned_finance_user_id'], ['id'])



def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_purchase_payment_requests_assigned_finance', 'purchase_payment_requests', type_='foreignkey')
    op.drop_column('purchase_payment_requests', 'assigned_finance_user_id')
