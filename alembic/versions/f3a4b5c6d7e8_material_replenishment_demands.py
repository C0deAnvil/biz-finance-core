"""独立的人工原料补货需求，与生产补货需求和采购单分别关联。"""
from alembic import op
import sqlalchemy as sa

revision = "f3a4b5c6d7e8"
down_revision = "e2b3c4d5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "material_replenishment_demands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("demand_no", sa.String(60), nullable=False, unique=True),
        sa.Column("fulfillment_demand_id", sa.Integer(), sa.ForeignKey("fulfillment_demands.id"), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_material_replenishment_demands_fulfillment_demand_id", "material_replenishment_demands", ["fulfillment_demand_id"])
    op.create_index("ix_material_replenishment_demands_purchase_order_id", "material_replenishment_demands", ["purchase_order_id"])


def downgrade():
    op.drop_table("material_replenishment_demands")
