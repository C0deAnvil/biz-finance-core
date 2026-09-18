"""Unified settlement accounts and default trade products.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
"""
from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

SETTLEMENT_TABLES = ("sales_receipt_requests", "purchase_payment_requests", "receivable_records", "payable_records")


def upgrade():
    bind = op.get_bind()
    if "financial_accounts" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "financial_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_type", sa.String(20), nullable=False),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id")),
            sa.Column("account_name", sa.String(150), nullable=False),
            sa.Column("bank_name", sa.String(150), nullable=False),
            sa.Column("account_number", sa.String(80), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("remark", sa.String(500)),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        )
        for col in ("owner_type", "customer_id", "supplier_id"):
            op.create_index(f"ix_financial_accounts_{col}", "financial_accounts", [col])
    # 保留存量单据，账户由后续财务登记时补齐；禁止猜测历史收付款账户。
    for table in SETTLEMENT_TABLES:
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in ("company_account_id", "counterparty_account_id"):
                if column not in existing:
                    batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
                    batch.create_foreign_key(f"fk_{table}_{column}", "financial_accounts", [column], ["id"])
    with op.batch_alter_table("products") as batch:
        batch.alter_column("fulfillment_mode", existing_type=sa.String(30), existing_nullable=False, server_default="trade")
    # 迁移原供应商账户；不臆造企业/客户账号。
    bind.execute(sa.text("""
        INSERT INTO financial_accounts
            (owner_type, supplier_id, account_name, bank_name, account_number, currency, is_active, remark)
        SELECT 'supplier', s.id, s.name, TRIM(s.bank_name), TRIM(s.bank_account), 'CNY', 1, '迁移自供应商档案'
        FROM suppliers s
        WHERE s.bank_account IS NOT NULL AND TRIM(s.bank_account) <> ''
          AND s.bank_name IS NOT NULL AND TRIM(s.bank_name) <> ''
          AND NOT EXISTS (SELECT 1 FROM financial_accounts a
              WHERE a.supplier_id = s.id AND a.account_number = TRIM(s.bank_account) AND a.bank_name = TRIM(s.bank_name))
    """))


def downgrade():
    for table in reversed(SETTLEMENT_TABLES):
        with op.batch_alter_table(table) as batch:
            for column in ("counterparty_account_id", "company_account_id"):
                batch.drop_constraint(f"fk_{table}_{column}", type_="foreignkey")
                batch.drop_column(column)
    with op.batch_alter_table("products") as batch:
        batch.alter_column("fulfillment_mode", existing_type=sa.String(30), existing_nullable=False, server_default="production")
    op.drop_table("financial_accounts")
