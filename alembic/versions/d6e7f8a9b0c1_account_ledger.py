"""Add auditable account adjustment and transfer ledger.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
"""
from alembic import op
import sqlalchemy as sa

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "financial_account_transactions" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "financial_account_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("financial_accounts.id"), nullable=False),
        sa.Column("related_account_id", sa.Integer(), sa.ForeignKey("financial_accounts.id"), nullable=True),
        sa.Column("transaction_type", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("reference_no", sa.String(60), nullable=False),
        sa.Column("remark", sa.String(500), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_financial_account_transactions_account_id", "financial_account_transactions", ["account_id"])
    op.create_index("ix_financial_account_transactions_related_account_id", "financial_account_transactions", ["related_account_id"])
    op.create_index("ix_financial_account_transactions_reference_no", "financial_account_transactions", ["reference_no"])


def downgrade():
    op.drop_table("financial_account_transactions")
