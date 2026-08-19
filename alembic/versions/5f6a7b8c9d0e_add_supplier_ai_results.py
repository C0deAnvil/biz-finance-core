"""Add supplier AI analysis table

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
Create Date: 2026-05-05 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "5f6a7b8c9d0e"
down_revision = "4e5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("supplier_ai_results"):
        return
    op.create_table(
        "supplier_ai_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("predicted_grade", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("risk_score", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("risk_label", sa.String(length=30), nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("features_json", sa.JSON(), nullable=False),
        sa.Column("probabilities_json", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(length=80), nullable=False, server_default="supplier-ai"),
        sa.Column("model_version", sa.String(length=80), nullable=False, server_default="supplier-ai-v1"),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supplier_id", name="uq_supplier_ai_result_supplier"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("supplier_ai_results"):
        op.drop_table("supplier_ai_results")
