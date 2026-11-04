"""Add customer CRM state and allow simplified financial accounts.

Revision ID: d07f1a2b3c4d
Revises: c96e0f1a2b3c
"""
from alembic import op
import sqlalchemy as sa


revision = "d07f1a2b3c4d"
down_revision = "c96e0f1a2b3c"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("customers") as batch:
        batch.add_column(sa.Column("crm_status", sa.String(20), nullable=False, server_default="active"))
        batch.add_column(sa.Column("lost_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("lost_reason", sa.Text(), nullable=True))
        batch.create_index("ix_customers_crm_status", ["crm_status"])
        batch.create_index("ix_customers_lost_at", ["lost_at"])
    with op.batch_alter_table("financial_accounts") as batch:
        batch.alter_column("bank_name", existing_type=sa.String(150), nullable=True)
        batch.alter_column("account_number", existing_type=sa.String(80), nullable=True)


def downgrade():
    op.execute("UPDATE financial_accounts SET bank_name = '' WHERE bank_name IS NULL")
    op.execute("UPDATE financial_accounts SET account_number = '' WHERE account_number IS NULL")
    with op.batch_alter_table("financial_accounts") as batch:
        batch.alter_column("account_number", existing_type=sa.String(80), nullable=False)
        batch.alter_column("bank_name", existing_type=sa.String(150), nullable=False)
    with op.batch_alter_table("customers") as batch:
        batch.drop_index("ix_customers_lost_at")
        batch.drop_index("ix_customers_crm_status")
        batch.drop_column("lost_reason")
        batch.drop_column("lost_at")
        batch.drop_column("crm_status")
