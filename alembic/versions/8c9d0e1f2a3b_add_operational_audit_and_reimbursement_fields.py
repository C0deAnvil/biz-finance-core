"""add operational audit and reimbursement fields

Revision ID: 8c9d0e1f2a3b
Revises: 7a8b9c0d1e2f
Create Date: 2026-05-23 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8c9d0e1f2a3b"
down_revision = "7a8b9c0d1e2f"
branch_labels = None
depends_on = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_columns(bind, table_name: str, columns: dict[str, sa.Column]) -> None:
    existing = _existing_columns(bind, table_name)
    if not existing:
        return
    for name, column in columns.items():
        if name not in existing:
            op.add_column(table_name, column)


def _ensure_fk(bind, table_name: str, column_name: str, referred_table: str, fk_name: str) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing = inspector.get_foreign_keys(table_name)
    has_fk = any(fk.get("constrained_columns") == [column_name] and fk.get("referred_table") == referred_table for fk in existing)
    if has_fk:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.create_foreign_key(fk_name, referred_table, [column_name], ["id"])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _add_columns(
        bind,
        "customers",
        {
            "owner_user_id": sa.Column("owner_user_id", sa.Integer(), nullable=True),
            "unified_social_credit_code": sa.Column("unified_social_credit_code", sa.String(length=60), nullable=True),
            "source_type": sa.Column("source_type", sa.String(length=30), nullable=False, server_default="personal_development"),
            "archived_at": sa.Column("archived_at", sa.DateTime(), nullable=True),
        },
    )
    if inspector.has_table("customers"):
        op.execute("UPDATE customers SET owner_user_id = created_by_id WHERE owner_user_id IS NULL")
        _ensure_fk(bind, "customers", "owner_user_id", "users", "fk_customers_owner_user_id_users")
        indexes = {idx["name"] for idx in inspector.get_indexes("customers")}
        uniques = {uc["name"] for uc in inspector.get_unique_constraints("customers")}
        if "uq_customers_unified_social_credit_code" not in uniques and "uq_customers_unified_social_credit_code" not in indexes:
            with op.batch_alter_table("customers") as batch_op:
                batch_op.create_unique_constraint("uq_customers_unified_social_credit_code", ["unified_social_credit_code"])

    if not inspector.has_table("customer_transfer_records"):
        op.create_table(
            "customer_transfer_records",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("from_user_id", sa.Integer(), nullable=True),
            sa.Column("to_user_id", sa.Integer(), nullable=False),
            sa.Column("transferred_by_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("transferred_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["from_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["to_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["transferred_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    _add_columns(
        bind,
        "sales_orders",
        {
            "order_remark": sa.Column("order_remark", sa.Text(), nullable=True),
            "finance_edit_pending": sa.Column("finance_edit_pending", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            "price_approval_status": sa.Column("price_approval_status", sa.String(length=30), nullable=False, server_default="not_required"),
            "price_approved_by_id": sa.Column("price_approved_by_id", sa.Integer(), nullable=True),
            "price_approved_at": sa.Column("price_approved_at", sa.DateTime(), nullable=True),
        },
    )
    _ensure_fk(bind, "sales_orders", "price_approved_by_id", "users", "fk_sales_orders_price_approved_by_id_users")

    _add_columns(
        bind,
        "sales_order_price_change_logs",
        {
            "status": sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            "approved_by_id": sa.Column("approved_by_id", sa.Integer(), nullable=True),
            "approved_at": sa.Column("approved_at", sa.DateTime(), nullable=True),
        },
    )
    _ensure_fk(bind, "sales_order_price_change_logs", "approved_by_id", "users", "fk_sales_order_price_change_logs_approved_by_id_users")

    _add_columns(
        bind,
        "inventory_operation_requests",
        {
            "quantity_difference_remark": sa.Column("quantity_difference_remark", sa.Text(), nullable=True),
            "difference_notified_at": sa.Column("difference_notified_at", sa.DateTime(), nullable=True),
        },
    )

    _add_columns(
        bind,
        "purchase_receipts",
        {
            "preliminary_acceptance_note": sa.Column("preliminary_acceptance_note", sa.Text(), nullable=True),
            "recorded_at": sa.Column("recorded_at", sa.DateTime(), nullable=True),
        },
    )
    if inspector.has_table("purchase_receipts"):
        op.execute("UPDATE purchase_receipts SET recorded_at = created_at WHERE recorded_at IS NULL")

    _add_columns(
        bind,
        "purchase_receipt_items",
        {
            "expected_quantity": sa.Column("expected_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
            "difference_remark": sa.Column("difference_remark", sa.Text(), nullable=True),
        },
    )

    _add_columns(
        bind,
        "purchase_returns",
        {
            "requires_reinspection": sa.Column("requires_reinspection", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            "reinspection_status": sa.Column("reinspection_status", sa.String(length=30), nullable=False, server_default="not_required"),
        },
    )

    _add_columns(
        bind,
        "production_tasks",
        {
            "preliminary_acceptance_note": sa.Column("preliminary_acceptance_note", sa.Text(), nullable=True),
            "completion_recorded_by_id": sa.Column("completion_recorded_by_id", sa.Integer(), nullable=True),
        },
    )
    _ensure_fk(bind, "production_tasks", "completion_recorded_by_id", "users", "fk_production_tasks_completion_recorded_by_id_users")

    _add_columns(bind, "production_plans", {"locked_at": sa.Column("locked_at", sa.DateTime(), nullable=True)})

    _add_columns(
        bind,
        "sales_receipt_requests",
        {
            "finance_status": sa.Column("finance_status", sa.String(length=30), nullable=False, server_default="pending"),
            "finance_checked_by_id": sa.Column("finance_checked_by_id", sa.Integer(), nullable=True),
            "finance_checked_at": sa.Column("finance_checked_at", sa.DateTime(), nullable=True),
            "receipt_method": sa.Column("receipt_method", sa.String(length=80), nullable=True),
            "receipt_at": sa.Column("receipt_at", sa.DateTime(), nullable=True),
            "bank_receipt_attachment_id": sa.Column("bank_receipt_attachment_id", sa.Integer(), nullable=True),
        },
    )
    _ensure_fk(bind, "sales_receipt_requests", "finance_checked_by_id", "users", "fk_sales_receipt_requests_finance_checked_by_id_users")
    _ensure_fk(bind, "sales_receipt_requests", "bank_receipt_attachment_id", "attachments", "fk_sales_receipt_requests_bank_receipt_attachment_id")

    _add_columns(
        bind,
        "receivable_records",
        {
            "receipt_method": sa.Column("receipt_method", sa.String(length=80), nullable=True),
            "bank_receipt_attachment_id": sa.Column("bank_receipt_attachment_id", sa.Integer(), nullable=True),
        },
    )
    _ensure_fk(bind, "receivable_records", "bank_receipt_attachment_id", "attachments", "fk_receivable_records_bank_receipt_attachment_id")

    _add_columns(
        bind,
        "payable_records",
        {
            "payment_method": sa.Column("payment_method", sa.String(length=80), nullable=True),
            "bank_receipt_attachment_id": sa.Column("bank_receipt_attachment_id", sa.Integer(), nullable=True),
        },
    )
    _ensure_fk(bind, "payable_records", "bank_receipt_attachment_id", "attachments", "fk_payable_records_bank_receipt_attachment_id")

    if not inspector.has_table("payment_method_options"):
        op.create_table(
            "payment_method_options",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("method_type", sa.String(length=30), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("method_type", "name", name="uq_payment_method_options_type_name"),
        )

    if not inspector.has_table("reimbursements"):
        op.create_table(
            "reimbursements",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("reimbursement_no", sa.String(length=60), nullable=False),
            sa.Column("applicant_user_id", sa.Integer(), nullable=False),
            sa.Column("expense_category", sa.String(length=30), nullable=False),
            sa.Column("expense_subcategory", sa.String(length=80), nullable=False),
            sa.Column("amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("finance_user_id", sa.Integer(), nullable=True),
            sa.Column("finance_remark", sa.Text(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("attachment_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["applicant_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["finance_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("reimbursement_no"),
        )

    _add_columns(
        bind,
        "logistics_records",
        {
            "bill_date": sa.Column("bill_date", sa.Date(), nullable=True),
            "origin_station": sa.Column("origin_station", sa.String(length=120), nullable=True),
            "destination_station": sa.Column("destination_station", sa.String(length=120), nullable=True),
            "transfer_station": sa.Column("transfer_station", sa.String(length=120), nullable=True),
            "consignor_name": sa.Column("consignor_name", sa.String(length=100), nullable=True),
            "consignor_phone": sa.Column("consignor_phone", sa.String(length=30), nullable=True),
            "consignor_address": sa.Column("consignor_address", sa.String(length=255), nullable=True),
            "consignee_name": sa.Column("consignee_name", sa.String(length=100), nullable=True),
            "consignee_phone": sa.Column("consignee_phone", sa.String(length=30), nullable=True),
            "consignee_address": sa.Column("consignee_address", sa.String(length=255), nullable=True),
            "goods_info_json": sa.Column("goods_info_json", sa.JSON(), nullable=True),
            "delivery_fee": sa.Column("delivery_fee", sa.Numeric(18, 2), nullable=False, server_default="0"),
            "freight_payment_type": sa.Column("freight_payment_type", sa.String(length=30), nullable=True),
            "handover_type": sa.Column("handover_type", sa.String(length=30), nullable=True),
            "document_attachment_id": sa.Column("document_attachment_id", sa.Integer(), nullable=True),
        },
    )
    _ensure_fk(bind, "logistics_records", "document_attachment_id", "attachments", "fk_logistics_records_document_attachment_id")
    if inspector.has_table("logistics_records"):
        op.execute("UPDATE logistics_records SET bill_date = date(created_at) WHERE bill_date IS NULL")
        if "goods_info_json" in _existing_columns(bind, "logistics_records"):
            if bind.dialect.name == "mysql":
                op.execute("UPDATE logistics_records SET goods_info_json = JSON_ARRAY() WHERE goods_info_json IS NULL")
            else:
                op.execute("UPDATE logistics_records SET goods_info_json = '[]' WHERE goods_info_json IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("reimbursements"):
        op.drop_table("reimbursements")
    if inspector.has_table("payment_method_options"):
        op.drop_table("payment_method_options")
    if inspector.has_table("customer_transfer_records"):
        op.drop_table("customer_transfer_records")
