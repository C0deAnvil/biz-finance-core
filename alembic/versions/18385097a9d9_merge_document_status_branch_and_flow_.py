"""merge document status branch and flow foundation branch

Revision ID: 18385097a9d9
Revises: 6b7c8d9e0f1a, a1b2c3d4e5f6a7b8
Create Date: 2026-08-31 18:03:29.799192

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18385097a9d9'
down_revision: Union[str, Sequence[str], None] = ('6b7c8d9e0f1a', 'a1b2c3d4e5f6a7b8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
