"""Add unified document status tracking and document relations.

Revision ID: 6b7c8d9e0f1a
Revises: 5a6b7c8d9e0f
Create Date: 2026-08-27 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6b7c8d9e0f1a"
down_revision: Union[str, Sequence[str], None] = "5a6b7c8d9e0f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    if column.name not in _existing_columns(bind, table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(bind, index_name: str, table_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing_indexes = {index.get("name") for index in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()

    if sa.inspect(bind).has_table("production_tasks"):
        _add_column_if_missing(
            bind,
            "production_tasks",
            sa.Column("fulfillment_demand_id", sa.Integer(), nullable=True),
        )
        foreign_keys = {
            item.get("name")
            for item in sa.inspect(bind).get_foreign_keys("production_tasks")
        }
        if "fk_production_tasks_fulfillment_demand_id" not in foreign_keys:
            with op.batch_alter_table("production_tasks") as batch_op:
                batch_op.create_foreign_key(
                    "fk_production_tasks_fulfillment_demand_id",
                    "fulfillment_demands",
                    ["fulfillment_demand_id"],
                    ["id"],
                )

    if sa.inspect(bind).has_table("production_plans"):
        _add_column_if_missing(
            bind,
            "production_plans",
            sa.Column("fulfillment_demand_id", sa.Integer(), nullable=True),
        )
        foreign_keys = {
            item.get("name")
            for item in sa.inspect(bind).get_foreign_keys("production_plans")
        }
        if "fk_production_plans_fulfillment_demand_id" not in foreign_keys:
            with op.batch_alter_table("production_plans") as batch_op:
                batch_op.create_foreign_key(
                    "fk_production_plans_fulfillment_demand_id",
                    "fulfillment_demands",
                    ["fulfillment_demand_id"],
                    ["id"],
                )

    inspector = sa.inspect(bind)
    if not inspector.has_table("document_status_snapshots"):
        op.create_table(
            "document_status_snapshots",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("document_type", sa.String(length=60), nullable=False),
            sa.Column("document_id", sa.Integer(), nullable=False),
            sa.Column("status_dimension", sa.String(length=50), nullable=False),
            sa.Column("current_stage", sa.String(length=60), nullable=False),
            sa.Column("status_code", sa.String(length=60), nullable=False),
            sa.Column("blocked_reason_code", sa.String(length=80), nullable=True),
            sa.Column("blocked_reason", sa.Text(), nullable=True),
            sa.Column("status_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status_updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_by_id", sa.Integer(), nullable=True),
            sa.Column("related_document_type", sa.String(length=60), nullable=True),
            sa.Column("related_document_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "document_type",
                "document_id",
                "status_dimension",
                name="uq_document_status_snapshot_dimension",
            ),
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("document_status_history"):
        op.create_table(
            "document_status_history",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("document_type", sa.String(length=60), nullable=False),
            sa.Column("document_id", sa.Integer(), nullable=False),
            sa.Column("status_dimension", sa.String(length=50), nullable=False),
            sa.Column("from_stage", sa.String(length=60), nullable=True),
            sa.Column("to_stage", sa.String(length=60), nullable=False),
            sa.Column("from_status_code", sa.String(length=60), nullable=True),
            sa.Column("to_status_code", sa.String(length=60), nullable=False),
            sa.Column("reason_code", sa.String(length=80), nullable=True),
            sa.Column("reason_text", sa.Text(), nullable=True),
            sa.Column("operator_user_id", sa.Integer(), nullable=True),
            sa.Column("operation_id", sa.String(length=100), nullable=True),
            sa.Column("related_document_type", sa.String(length=60), nullable=True),
            sa.Column("related_document_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["operator_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("document_relations"):
        op.create_table(
            "document_relations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_document_type", sa.String(length=60), nullable=False),
            sa.Column("source_document_id", sa.Integer(), nullable=False),
            sa.Column("target_document_type", sa.String(length=60), nullable=False),
            sa.Column("target_document_id", sa.Integer(), nullable=False),
            sa.Column("relation_type", sa.String(length=60), nullable=False),
            sa.Column("quantity", sa.Numeric(precision=18, scale=4), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_document_type",
                "source_document_id",
                "target_document_type",
                "target_document_id",
                "relation_type",
                name="uq_document_relation_link",
            ),
        )

    _create_index_if_missing(
        bind,
        "ix_document_status_snapshot_document",
        "document_status_snapshots",
        ["document_type", "document_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_document_status_history_document",
        "document_status_history",
        ["document_type", "document_id", "created_at"],
    )
    _create_index_if_missing(
        bind,
        "ix_document_relation_source",
        "document_relations",
        ["source_document_type", "source_document_id"],
    )
    _create_index_if_missing(
        bind,
        "ix_document_relation_target",
        "document_relations",
        ["target_document_type", "target_document_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for index_name, table_name in [
        ("ix_document_relation_target", "document_relations"),
        ("ix_document_relation_source", "document_relations"),
        ("ix_document_status_history_document", "document_status_history"),
        ("ix_document_status_snapshot_document", "document_status_snapshots"),
    ]:
        if inspector.has_table(table_name) and index_name in {item.get("name") for item in inspector.get_indexes(table_name)}:
            op.drop_index(index_name, table_name=table_name)

    for table_name in ["document_relations", "document_status_history", "document_status_snapshots"]:
        if sa.inspect(bind).has_table(table_name):
            op.drop_table(table_name)

    for table_name, column_name, constraint_name in [
        ("production_plans", "fulfillment_demand_id", "fk_production_plans_fulfillment_demand_id"),
        ("production_tasks", "fulfillment_demand_id", "fk_production_tasks_fulfillment_demand_id"),
    ]:
        if sa.inspect(bind).has_table(table_name) and column_name in _existing_columns(bind, table_name):
            with op.batch_alter_table(table_name) as batch_op:
                try:
                    batch_op.drop_constraint(constraint_name, type_="foreignkey")
                except Exception:
                    pass
                batch_op.drop_column(column_name)
