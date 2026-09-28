"""Keep four decimal places for prices and monetary amounts.

Revision ID: fe5f6a7b8c9d
Revises: fd4e5f6a7b8c
"""

import sqlalchemy as sa
from alembic import op


revision = "fe5f6a7b8c9d"
down_revision = "fd4e5f6a7b8c"
branch_labels = None
depends_on = None


# These were the complete scale-2 monetary columns at this revision. Increasing
# precision by two preserves the existing number of integer digits.
MONEY_COLUMNS = {
    "standard_price_change_logs": ("old_price", "new_price"),
    "sales_order_price_change_logs": ("standard_price", "actual_price"),
    "financial_account_transactions": ("amount",),
    "products": ("price",),
    "materials": ("price",),
    "packaging_configs": ("unit_price",),
    "sales_orders": ("total_amount", "deposit_amount", "transport_fee", "packaging_fee", "cost_amount", "profit_amount"),
    "sales_order_items": ("unit_price", "total_amount", "packaging_unit_price", "packaging_fee"),
    "sales_order_approvals": ("voucher_amount",),
    "sales_returns": ("refund_amount",),
    "purchase_orders": ("total_amount",),
    "purchase_order_items": ("unit_price", "total_amount"),
    "purchase_returns": ("refund_amount",),
    "purchase_payment_requests": ("requested_amount", "order_amount", "other_fee_amount", "actual_amount", "invoice_amount"),
    "quality_cost_records": ("estimated_amount", "recognized_amount"),
    "sales_receipt_requests": ("requested_amount", "actual_amount"),
    "receivable_plans": ("planned_amount", "received_amount"),
    "receivable_records": ("amount",),
    "payable_records": ("amount", "order_amount", "other_fee_amount"),
    "reimbursements": ("amount",),
    "business_matters": ("estimated_amount", "actual_amount"),
    "refund_records": ("amount",),
    "cash_flow_records": ("amount",),
    "logistics_records": ("delivery_fee",),
}


def _types(table_name):
    return (sa.Numeric(10, 2), sa.Numeric(12, 4)) if table_name in {"products", "materials"} else (sa.Numeric(18, 2), sa.Numeric(20, 4))


def upgrade():
    for table_name, columns in MONEY_COLUMNS.items():
        old_type, new_type = _types(table_name)
        with op.batch_alter_table(table_name) as batch:
            for column_name in columns:
                batch.alter_column(
                    column_name, existing_type=old_type, type_=new_type,
                    existing_nullable=(table_name == "purchase_payment_requests" and column_name == "invoice_amount"),
                )
    op.execute("""
        INSERT INTO user_module_permissions (user_id, route_path, assigned_by_id, created_at, updated_at)
        SELECT u.id, '/sales/my-receivables', u.id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users u JOIN roles r ON r.id = u.role_id
        WHERE r.code = 'sales' AND u.is_active = 1
          AND NOT EXISTS (
              SELECT 1 FROM user_module_permissions p
              WHERE p.user_id = u.id AND p.route_path = '/sales/my-receivables'
          )
    """)


def downgrade():
    connection = op.get_bind()
    for table_name, columns in MONEY_COLUMNS.items():
        for column_name in columns:
            # Refuse to erase newly-entered third/fourth decimals on rollback.
            count = connection.scalar(sa.text(
                f"SELECT COUNT(*) FROM {table_name} WHERE {column_name} IS NOT NULL "
                f"AND {column_name} != ROUND({column_name}, 2)"
            ))
            if count:
                raise RuntimeError(f"Cannot downgrade {table_name}.{column_name}: values need more than two decimal places")
    for table_name, columns in MONEY_COLUMNS.items():
        old_type, new_type = _types(table_name)
        with op.batch_alter_table(table_name) as batch:
            for column_name in columns:
                batch.alter_column(
                    column_name, existing_type=new_type, type_=old_type,
                    existing_nullable=(table_name == "purchase_payment_requests" and column_name == "invoice_amount"),
                )
    op.execute("DELETE FROM user_module_permissions WHERE route_path = '/sales/my-receivables'")
