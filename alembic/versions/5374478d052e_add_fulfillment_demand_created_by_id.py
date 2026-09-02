"""add fulfillment_demand created_by_id

Revision ID: 5374478d052e
Revises: 40969da96e86
Create Date: 2026-09-02 15:17:38.020896

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5374478d052e'
down_revision: Union[str, Sequence[str], None] = '40969da96e86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('fulfillment_demands', sa.Column('created_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_fulfillment_demands_created_by', 'fulfillment_demands', 'users', ['created_by_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_fulfillment_demands_created_by', 'fulfillment_demands', type_='foreignkey')
    op.drop_column('fulfillment_demands', 'created_by_id')