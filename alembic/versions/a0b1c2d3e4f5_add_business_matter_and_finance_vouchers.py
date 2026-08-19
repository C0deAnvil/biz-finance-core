"""add business matter and finance voucher fields

Revision ID: a0b1c2d3e4f5
Revises: 9d0e1f2a3b4c
Create Date: 2026-05-24 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a0b1c2d3e4f5"
down_revision = "9d0e1f2a3b4c"
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

    if not inspector.has_table("business_matters"):
        op.create_table(
            "business_matters",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("matter_no", sa.String(length=60), nullable=False),
            sa.Column("applicant_user_id", sa.Integer(), nullable=False),
            sa.Column("expense_category", sa.String(length=30), nullable=False),
            sa.Column("expense_subcategory", sa.String(length=80), nullable=False),
            sa.Column("estimated_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
            sa.Column("actual_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending_manager_approval"),
            sa.Column("manager_user_id", sa.Integer(), nullable=True),
            sa.Column("manager_remark", sa.Text(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("converted_reimbursement_id", sa.Integer(), nullable=True),
            sa.Column("attachment_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["applicant_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["manager_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["converted_reimbursement_id"], ["reimbursements.id"]),
            sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("matter_no"),
        )

    _add_columns(
        bind,
        "reimbursements",
        {
            "chairman_user_id": sa.Column("chairman_user_id", sa.Integer(), nullable=True),
            "chairman_remark": sa.Column("chairman_remark", sa.Text(), nullable=True),
            "business_matter_id": sa.Column("business_matter_id", sa.Integer(), nullable=True),
            "bank_receipt_attachment_id": sa.Column("bank_receipt_attachment_id", sa.Integer(), nullable=True),
            "payment_method": sa.Column("payment_method", sa.String(length=80), nullable=True),
        },
    )
    _ensure_fk(bind, "reimbursements", "chairman_user_id", "users", "fk_reimbursements_chairman_user_id_users")
    _ensure_fk(bind, "reimbursements", "business_matter_id", "business_matters", "fk_reimbursements_business_matter_id_business_matters")
    _ensure_fk(bind, "reimbursements", "bank_receipt_attachment_id", "attachments", "fk_reimbursements_bank_receipt_attachment_id_attachments")

    _add_columns(
        bind,
        "purchase_payment_requests",
        {
            "bank_receipt_attachment_id": sa.Column("bank_receipt_attachment_id", sa.Integer(), nullable=True),
            "payment_method": sa.Column("payment_method", sa.String(length=80), nullable=True),
            "paid_at": sa.Column("paid_at", sa.DateTime(), nullable=True),
        },
    )
    _ensure_fk(bind, "purchase_payment_requests", "bank_receipt_attachment_id", "attachments", "fk_ppr_bank_receipt_attachment")


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind, "purchase_payment_requests")
    for name in ["paid_at", "payment_method", "bank_receipt_attachment_id"]:
        if name in existing:
            op.drop_column("purchase_payment_requests", name)

    existing = _existing_columns(bind, "reimbursements")
    for name in ["payment_method", "bank_receipt_attachment_id", "business_matter_id", "chairman_remark", "chairman_user_id"]:
        if name in existing:
            op.drop_column("reimbursements", name)

    inspector = sa.inspect(bind)
    if inspector.has_table("business_matters"):
        op.drop_table("business_matters")
