"""扩展客户与供应商档案字段，并新增供应商标签表

Revision ID: a3b4c5d6e7f8
Revises: f3a4b5c6d7e8
Create Date: 2026-09-15

变更内容：
1. suppliers 增加 统一社会信用代码 / 供应商类型 / 官网 / 开户行名称 / 银行账号 / 备注
2. customers 增加 客户类型（进口商/经销商/工厂/贸易公司/代理商）/ 区域类型（国内/海外）/ 备注
3. 新增 supplier_tags 表（与 customer_tags 结构对齐）
4. customer_tags 增加 dimension 维度列，承载《销帮帮标签表 KP-44-0818》六个标签维度
"""
from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1. 供应商档案扩展 ----------
    supplier_columns = [
        sa.Column("unified_social_credit_code", sa.String(length=60), nullable=True, comment="统一社会信用代码"),
        sa.Column("supplier_type", sa.String(length=30), nullable=True, comment="供应商类型：trader/manufacturer/manufacturer_trader/service/logistics/other"),
        sa.Column("website", sa.String(length=255), nullable=True, comment="官网"),
        sa.Column("bank_name", sa.String(length=150), nullable=True, comment="开户行名称"),
        sa.Column("bank_account", sa.String(length=60), nullable=True, comment="银行账号"),
        sa.Column("remark", sa.Text(), nullable=True, comment="备注"),
    ]
    for column in supplier_columns:
        if not _has_column(bind, "suppliers", column.name):
            op.add_column("suppliers", column)

    inspector = sa.inspect(bind)
    supplier_indexes = {idx["name"] for idx in inspector.get_indexes("suppliers")}
    supplier_uniques = {uq["name"] for uq in inspector.get_unique_constraints("suppliers")}
    if "uq_suppliers_credit_code" not in supplier_indexes | supplier_uniques:
        op.create_unique_constraint("uq_suppliers_credit_code", "suppliers", ["unified_social_credit_code"])
    if "ix_suppliers_supplier_type" not in supplier_indexes:
        op.create_index("ix_suppliers_supplier_type", "suppliers", ["supplier_type"])

    # ---------- 2. 客户档案扩展 ----------
    customer_columns = [
        sa.Column("customer_type", sa.String(length=30), nullable=True, comment="客户类型：importer/dealer/factory/trading/agent"),
        sa.Column("region_type", sa.String(length=20), nullable=True, comment="区域类型：domestic 国内客户 / overseas 海外客户"),
        sa.Column("remark", sa.Text(), nullable=True, comment="备注"),
    ]
    for column in customer_columns:
        if not _has_column(bind, "customers", column.name):
            op.add_column("customers", column)

    customer_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("customers")}
    if "ix_customers_customer_type" not in customer_indexes:
        op.create_index("ix_customers_customer_type", "customers", ["customer_type"])
    if "ix_customers_region_type" not in customer_indexes:
        op.create_index("ix_customers_region_type", "customers", ["region_type"])

    # ---------- 3. 供应商标签表 ----------
    if not _has_table(bind, "supplier_tags"):
        op.create_table(
            "supplier_tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
            sa.Column("tag", sa.String(length=50), nullable=False),
            sa.Column("dimension", sa.String(length=30), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("supplier_id", "tag", name="uq_supplier_tag"),
            mysql_charset="utf8mb4",
        )
        op.create_index("ix_supplier_tags_supplier_id", "supplier_tags", ["supplier_id"])

    # ---------- 4. 客户标签增加维度列 ----------
    if _has_table(bind, "customer_tags") and not _has_column(bind, "customer_tags", "dimension"):
        op.add_column("customer_tags", sa.Column("dimension", sa.String(length=30), nullable=True, comment="标签维度：value/industry/lifecycle/demand/status/source/custom"))
    if _has_table(bind, "customer_tags"):
        customer_tag_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("customer_tags")}
        if "ix_customer_tags_customer_id" not in customer_tag_indexes:
            op.create_index("ix_customer_tags_customer_id", "customer_tags", ["customer_id"])


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "customer_tags"):
        customer_tag_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("customer_tags")}
        if "ix_customer_tags_customer_id" in customer_tag_indexes:
            op.drop_index("ix_customer_tags_customer_id", table_name="customer_tags")
        if _has_column(bind, "customer_tags", "dimension"):
            op.drop_column("customer_tags", "dimension")

    if _has_table(bind, "supplier_tags"):
        op.drop_table("supplier_tags")

    customer_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("customers")}
    for index_name in ("ix_customers_customer_type", "ix_customers_region_type"):
        if index_name in customer_indexes:
            op.drop_index(index_name, table_name="customers")
    for column_name in ("customer_type", "region_type", "remark"):
        if _has_column(bind, "customers", column_name):
            op.drop_column("customers", column_name)

    inspector = sa.inspect(bind)
    supplier_indexes = {idx["name"] for idx in inspector.get_indexes("suppliers")}
    supplier_uniques = {uq["name"] for uq in inspector.get_unique_constraints("suppliers")}
    if "ix_suppliers_supplier_type" in supplier_indexes:
        op.drop_index("ix_suppliers_supplier_type", table_name="suppliers")
    if "uq_suppliers_credit_code" in supplier_uniques | supplier_indexes:
        op.drop_constraint("uq_suppliers_credit_code", "suppliers", type_="unique")
    for column_name in ("unified_social_credit_code", "supplier_type", "website", "bank_name", "bank_account", "remark"):
        if _has_column(bind, "suppliers", column_name):
            op.drop_column("suppliers", column_name)
