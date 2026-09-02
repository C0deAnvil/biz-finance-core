"""add stock transfer

Revision ID: c1971156723f
Revises: eee82858d065
Create Date: 2026-09-02 16:30:04.044407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'c1971156723f'
down_revision: Union[str, Sequence[str], None] = 'eee82858d065'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'stock_transfers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('transfer_no', sa.String(length=60), nullable=False),
        sa.Column('source_warehouse_id', sa.Integer(), nullable=False),
        sa.Column('target_warehouse_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('material_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('outbound_by_id', sa.Integer(), nullable=True),
        sa.Column('outbound_at', sa.DateTime(), nullable=True),
        sa.Column('quality_by_id', sa.Integer(), nullable=True),
        sa.Column('quality_at', sa.DateTime(), nullable=True),
        sa.Column('quality_result', sa.String(length=30), nullable=True),
        sa.Column('completed_by_id', sa.Integer(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('reject_reason', sa.Text(), nullable=True),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['source_warehouse_id'], ['warehouses.id'], name='fk_stock_transfers_source_wh'),
        sa.ForeignKeyConstraint(['target_warehouse_id'], ['warehouses.id'], name='fk_stock_transfers_target_wh'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], name='fk_stock_transfers_product'),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], name='fk_stock_transfers_material'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_stock_transfers_created_by'),
        sa.ForeignKeyConstraint(['outbound_by_id'], ['users.id'], name='fk_stock_transfers_outbound_by'),
        sa.ForeignKeyConstraint(['quality_by_id'], ['users.id'], name='fk_stock_transfers_quality_by'),
        sa.ForeignKeyConstraint(['completed_by_id'], ['users.id'], name='fk_stock_transfers_completed_by'),
        sa.UniqueConstraint('transfer_no', name='uq_stock_transfers_transfer_no'),
    )
    op.create_index('ix_stock_transfers_status', 'stock_transfers', ['status'])



def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_stock_transfers_status', table_name='stock_transfers')
    op.drop_table('stock_transfers')
