"""Add customer AI analysis tables

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
Create Date: 2026-05-05 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "4e5f6a7b8c9d"
down_revision = "3d4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("customer_inquiries"):
        op.create_table(
            "customer_inquiries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("inquiry_no", sa.String(length=60), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("inquiry_text", sa.Text(), nullable=False),
            sa.Column("source_channel", sa.String(length=50), nullable=True),
            sa.Column("product_hint", sa.String(length=150), nullable=True),
            sa.Column("intent_score", sa.Numeric(10, 4), nullable=False, server_default="0"),
            sa.Column("intent_label", sa.String(length=30), nullable=False, server_default="unknown"),
            sa.Column("model_version", sa.String(length=80), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("inquiry_no"),
        )

    if not inspector.has_table("customer_ai_results"):
        op.create_table(
            "customer_ai_results",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("segment_label", sa.String(length=50), nullable=False),
            sa.Column("segment_reason", sa.Text(), nullable=True),
            sa.Column("lead_score", sa.Numeric(10, 4), nullable=False, server_default="0"),
            sa.Column("lead_label", sa.String(length=30), nullable=False, server_default="待观察"),
            sa.Column("rfm_json", sa.JSON(), nullable=False),
            sa.Column("recommendations_json", sa.JSON(), nullable=False),
            sa.Column("model_version", sa.String(length=80), nullable=False, server_default="customer-ai-v1"),
            sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("customer_id", name="uq_customer_ai_result_customer"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("customer_ai_results"):
        op.drop_table("customer_ai_results")
    if inspector.has_table("customer_inquiries"):
        op.drop_table("customer_inquiries")
