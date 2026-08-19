"""Add plan detail, merged purchase payment and user inheritance fields

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-04-21 19:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "2c3d4e5f6a7b"
down_revision = "1b2c3d4e5f6a"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    user_columns = _columns("users")
    if "inherited_from_user_id" not in user_columns:
        op.add_column("users", sa.Column("inherited_from_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_users_inherited_from_user_id", "users", "users", ["inherited_from_user_id"], ["id"])
    if "data_successor_user_id" not in user_columns:
        op.add_column("users", sa.Column("data_successor_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_users_data_successor_user_id", "users", "users", ["data_successor_user_id"], ["id"])

    request_columns = _columns("purchase_payment_requests")
    if "merge_no" not in request_columns:
        op.add_column("purchase_payment_requests", sa.Column("merge_no", sa.String(length=60), nullable=True))

    payable_columns = _columns("payable_records")
    if "merge_no" not in payable_columns:
        op.add_column("payable_records", sa.Column("merge_no", sa.String(length=60), nullable=True))

    plan_columns = _columns("production_plans")
    if "plan_name" not in plan_columns:
        op.add_column("production_plans", sa.Column("plan_name", sa.String(length=120), nullable=True))
    if "period_type" not in plan_columns:
        op.add_column("production_plans", sa.Column("period_type", sa.String(length=30), nullable=False, server_default="custom"))
    if "period_start" not in plan_columns:
        op.add_column("production_plans", sa.Column("period_start", sa.DateTime(), nullable=True))
    if "period_end" not in plan_columns:
        op.add_column("production_plans", sa.Column("period_end", sa.DateTime(), nullable=True))
    if "assigned_user_id" not in plan_columns:
        op.add_column("production_plans", sa.Column("assigned_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_production_plans_assigned_user_id", "production_plans", "users", ["assigned_user_id"], ["id"])
    if "detail_json" not in plan_columns:
        op.add_column("production_plans", sa.Column("detail_json", sa.JSON(), nullable=True))
    if "converted_task_id" not in plan_columns:
        op.add_column("production_plans", sa.Column("converted_task_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_production_plans_converted_task_id", "production_plans", "production_tasks", ["converted_task_id"], ["id"])


def downgrade() -> None:
    plan_columns = _columns("production_plans")
    if "converted_task_id" in plan_columns:
        op.drop_constraint("fk_production_plans_converted_task_id", "production_plans", type_="foreignkey")
        op.drop_column("production_plans", "converted_task_id")
    if "detail_json" in plan_columns:
        op.drop_column("production_plans", "detail_json")
    if "assigned_user_id" in plan_columns:
        op.drop_constraint("fk_production_plans_assigned_user_id", "production_plans", type_="foreignkey")
        op.drop_column("production_plans", "assigned_user_id")
    for column_name in ("period_end", "period_start", "period_type", "plan_name"):
        if column_name in _columns("production_plans"):
            op.drop_column("production_plans", column_name)

    if "merge_no" in _columns("payable_records"):
        op.drop_column("payable_records", "merge_no")
    if "merge_no" in _columns("purchase_payment_requests"):
        op.drop_column("purchase_payment_requests", "merge_no")

    user_columns = _columns("users")
    if "data_successor_user_id" in user_columns:
        op.drop_constraint("fk_users_data_successor_user_id", "users", type_="foreignkey")
        op.drop_column("users", "data_successor_user_id")
    if "inherited_from_user_id" in user_columns:
        op.drop_constraint("fk_users_inherited_from_user_id", "users", type_="foreignkey")
        op.drop_column("users", "inherited_from_user_id")
