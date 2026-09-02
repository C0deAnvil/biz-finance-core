"""add attachment verification

Revision ID: eee82858d065
Revises: d85660848e5e
Create Date: 2026-09-02 15:54:02.326112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'eee82858d065'
down_revision: Union[str, Sequence[str], None] = 'd85660848e5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('attachments', sa.Column('verified_by_id', sa.Integer(), nullable=True))
    op.add_column('attachments', sa.Column('verified_at', sa.DateTime(), nullable=True))
    op.create_foreign_key('fk_attachments_verified_by', 'attachments', 'users', ['verified_by_id'], ['id'])



def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_attachments_verified_by', 'attachments', type_='foreignkey')
    op.drop_column('attachments', 'verified_at')
    op.drop_column('attachments', 'verified_by_id')
