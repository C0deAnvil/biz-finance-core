"""Add warehouse preliminary screening fields for supplier samples.

Revision ID: b85d9e0f1a2b
Revises: a74c8d9e0f1a
"""

import sqlalchemy as sa
from alembic import op


revision = "b85d9e0f1a2b"
down_revision = "a74c8d9e0f1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("supplier_samples", sa.Column("label_code", sa.String(80), nullable=True))
    op.add_column(
        "supplier_samples",
        sa.Column("label_applied", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("supplier_samples", sa.Column("preliminary_result", sa.String(30), nullable=True))
    op.add_column("supplier_samples", sa.Column("preliminary_reason", sa.Text(), nullable=True))
    op.add_column("supplier_samples", sa.Column("screened_by_id", sa.Integer(), nullable=True))
    op.add_column("supplier_samples", sa.Column("screened_at", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_supplier_samples_screened_by",
        "supplier_samples",
        "users",
        ["screened_by_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_supplier_samples_screened_by", "supplier_samples", type_="foreignkey")
    op.drop_column("supplier_samples", "screened_at")
    op.drop_column("supplier_samples", "screened_by_id")
    op.drop_column("supplier_samples", "preliminary_reason")
    op.drop_column("supplier_samples", "preliminary_result")
    op.drop_column("supplier_samples", "label_applied")
    op.drop_column("supplier_samples", "label_code")
