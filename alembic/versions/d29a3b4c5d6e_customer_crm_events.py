"""Add persistent customer CRM lifecycle events.

Revision ID: d29a3b4c5d6e
Revises: d18f2b3c4d5e
"""
from alembic import op
import sqlalchemy as sa


revision = "d29a3b4c5d6e"
down_revision = "d18f2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "customer_crm_events" not in inspector.get_table_names():
        op.create_table(
            "customer_crm_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(30), nullable=False),
            sa.Column("event_at", sa.DateTime(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("operator_user_id", sa.Integer(), nullable=True),
            sa.Column("ext_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
            sa.ForeignKeyConstraint(["operator_user_id"], ["users.id"]),
        )
    inspector = sa.inspect(connection)
    indexes = {index["name"] for index in inspector.get_indexes("customer_crm_events")}
    if "ix_customer_crm_events_customer_id" not in indexes:
        op.create_index("ix_customer_crm_events_customer_id", "customer_crm_events", ["customer_id"])
    if "ix_customer_crm_events_event_type" not in indexes:
        op.create_index("ix_customer_crm_events_event_type", "customer_crm_events", ["event_type"])
    if "ix_customer_crm_events_event_at" not in indexes:
        op.create_index("ix_customer_crm_events_event_at", "customer_crm_events", ["event_at"])


def downgrade():
    op.drop_index("ix_customer_crm_events_event_at", table_name="customer_crm_events")
    op.drop_index("ix_customer_crm_events_event_type", table_name="customer_crm_events")
    op.drop_index("ix_customer_crm_events_customer_id", table_name="customer_crm_events")
    op.drop_table("customer_crm_events")
