"""统一 ORM 模型定义。

领域分区索引：
1. 系统与权限
2. 通知、待办、附件
3. 主数据
4. 库存与批次
5. 销售
6. 采购
7. 生产与质检
8. 财务
9. 盘点与报表
"""

from datetime import datetime
import secrets

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    text,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

from services.time_utils import now_local


Base = declarative_base()


class TimestampMixin:
    created_at = Column(DateTime, default=now_local, nullable=False)
    updated_at = Column(
        DateTime,
        default=now_local,
        onupdate=now_local,
        nullable=False,
    )


# ===== 系统与权限 =====
class Department(TimestampMixin, Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(255), nullable=True)
    parent_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    unit_type = Column(String(30), nullable=False, default="department")
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    warehouse_scope = Column(String(30), nullable=True)

    parent = relationship("Department", remote_side=[id], back_populates="children")
    children = relationship("Department", back_populates="parent")
    users = relationship("User", back_populates="department")


class Role(TimestampMixin, Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(255), nullable=True)
    home_route = Column(String(150), nullable=False)
    navigation_json = Column(JSON, nullable=False, default=list)
    scope_json = Column(JSON, nullable=False, default=list)
    is_admin = Column(Boolean, nullable=False, default=False)

    users = relationship("User", back_populates="role")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), nullable=False, unique=True)
    full_name = Column(String(100), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    phone = Column(String(30), nullable=True)
    email = Column(String(120), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    job_title = Column(String(100), nullable=True)
    data_scope = Column(String(30), nullable=False, default="self")
    inherited_from_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    data_successor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_login_at = Column(DateTime, nullable=True)

    department = relationship("Department", back_populates="users")
    role = relationship("Role", back_populates="users")
    inherited_from_user = relationship("User", foreign_keys=[inherited_from_user_id], remote_side=[id])
    data_successor_user = relationship("User", foreign_keys=[data_successor_user_id], remote_side=[id])
    sessions = relationship("UserSession", back_populates="user")


class UserModulePermission(TimestampMixin, Base):
    __tablename__ = "user_module_permissions"
    __table_args__ = (
        UniqueConstraint("user_id", "route_path", name="uq_user_module_permission"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    route_path = Column(String(150), nullable=False)
    assigned_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])


class UserSession(TimestampMixin, Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_token = Column(String(255), nullable=False, unique=True)
    refresh_token = Column(String(255), nullable=False, unique=True)
    device_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime, nullable=False)
    refresh_expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False, default=now_local)

    user = relationship("User", back_populates="sessions")


class SequenceCounter(TimestampMixin, Base):
    __tablename__ = "sequence_counters"
    __table_args__ = (UniqueConstraint("sequence_key", "business_date", name="uq_sequence_key_date"),)

    id = Column(Integer, primary_key=True)
    sequence_key = Column(String(50), nullable=False)
    business_date = Column(String(20), nullable=False)
    current_value = Column(Integer, nullable=False, default=0)


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    setting_key = Column(String(100), nullable=False, unique=True)
    setting_value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)


class TodoItem(TimestampMixin, Base):
    __tablename__ = "todo_items"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    module = Column(String(50), nullable=False)
    business_type = Column(String(50), nullable=True)
    business_id = Column(Integer, nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    priority = Column(String(30), nullable=False, default="normal")
    due_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    assigned_user = relationship("User")
    assigned_role = relationship("Role")


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(50), nullable=False, default="info")
    module = Column(String(50), nullable=False)
    is_popup = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class NotificationRecipient(TimestampMixin, Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("notification_id", "user_id", name="uq_notification_user"),
    )

    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey("notifications.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_read = Column(Boolean, nullable=False, default=False)
    read_at = Column(DateTime, nullable=True)

    notification = relationship("Notification")
    user = relationship("User")


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    module = Column(String(50), nullable=False)
    action = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    before_data = Column(JSON, nullable=True)
    after_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=now_local, nullable=False)

    user = relationship("User")


class DocumentStatusSnapshot(TimestampMixin, Base):
    """Current workflow status for any business document and status dimension."""

    __tablename__ = "document_status_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "document_type",
            "document_id",
            "status_dimension",
            name="uq_document_status_snapshot_dimension",
        ),
    )

    id = Column(Integer, primary_key=True)
    document_type = Column(String(60), nullable=False)
    document_id = Column(Integer, nullable=False)
    status_dimension = Column(String(50), nullable=False)
    current_stage = Column(String(60), nullable=False)
    status_code = Column(String(60), nullable=False)
    blocked_reason_code = Column(String(80), nullable=True)
    blocked_reason = Column(Text, nullable=True)
    status_version = Column(Integer, nullable=False, default=1)
    status_updated_at = Column(DateTime, default=now_local, nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    related_document_type = Column(String(60), nullable=True)
    related_document_id = Column(Integer, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    updated_by = relationship("User", foreign_keys=[updated_by_id])


class DocumentStatusHistory(Base):
    """Immutable audit trail for workflow status transitions."""

    __tablename__ = "document_status_history"

    id = Column(Integer, primary_key=True)
    document_type = Column(String(60), nullable=False)
    document_id = Column(Integer, nullable=False)
    status_dimension = Column(String(50), nullable=False)
    from_stage = Column(String(60), nullable=True)
    to_stage = Column(String(60), nullable=False)
    from_status_code = Column(String(60), nullable=True)
    to_status_code = Column(String(60), nullable=False)
    reason_code = Column(String(80), nullable=True)
    reason_text = Column(Text, nullable=True)
    operator_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    operation_id = Column(String(100), nullable=True)
    related_document_type = Column(String(60), nullable=True)
    related_document_id = Column(Integer, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=now_local, nullable=False)

    operator_user = relationship("User", foreign_keys=[operator_user_id])


class DocumentRelation(TimestampMixin, Base):
    """Polymorphic relation for one-to-many and many-to-many business documents."""

    __tablename__ = "document_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_document_type",
            "source_document_id",
            "target_document_type",
            "target_document_id",
            "relation_type",
            name="uq_document_relation_link",
        ),
    )

    id = Column(Integer, primary_key=True)
    source_document_type = Column(String(60), nullable=False)
    source_document_id = Column(Integer, nullable=False)
    target_document_type = Column(String(60), nullable=False)
    target_document_id = Column(Integer, nullable=False)
    relation_type = Column(String(60), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])


class Attachment(TimestampMixin, Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    business_type = Column(String(50), nullable=False)
    business_id = Column(Integer, nullable=False)
    attachment_type = Column(String(50), nullable=False)
    original_name = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False, unique=True)
    relative_path = Column(String(500), nullable=False)
    mime_type = Column(String(120), nullable=True)
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    verified_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime, nullable=True)

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
    verified_by = relationship("User", foreign_keys=[verified_by_id])


# ===== 主数据 =====
class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(150), nullable=False, unique=True)
    contact_name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=False)
    address = Column(String(255), nullable=False)
    star_level = Column(String(30), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    unified_social_credit_code = Column(String(60), nullable=True, unique=True)
    source_type = Column(String(30), nullable=False, default="personal_development")
    customer_type = Column(String(30), nullable=True)
    region_type = Column(String(20), nullable=True)
    region_province = Column(String(40), nullable=True, index=True)
    remark = Column(Text, nullable=True)
    last_transferred_at = Column(DateTime, nullable=True)
    churn_repurchase_at = Column(DateTime, nullable=True, index=True)
    last_order_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    ext_json = Column(JSON, nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])
    owner_user = relationship("User", foreign_keys=[owner_user_id])


class CustomerTag(TimestampMixin, Base):
    __tablename__ = "customer_tags"
    __table_args__ = (
        UniqueConstraint("customer_id", "tag", name="uq_customer_tag"),
    )

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    tag = Column(String(50), nullable=False)
    dimension = Column(String(30), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    customer = relationship("Customer")
    created_by = relationship("User", foreign_keys=[created_by_id])


class CustomerFollowUp(TimestampMixin, Base):
    __tablename__ = "customer_follow_ups"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    follow_type = Column(String(30), nullable=False, default="call")
    content = Column(Text, nullable=False)
    next_follow_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    followed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    customer = relationship("Customer")
    followed_by = relationship("User", foreign_keys=[followed_by_id])


class CustomerTransferRecord(TimestampMixin, Base):
    __tablename__ = "customer_transfer_records"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transferred_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=True)
    transferred_at = Column(DateTime, default=now_local, nullable=False)

    customer = relationship("Customer")
    from_user = relationship("User", foreign_keys=[from_user_id])
    to_user = relationship("User", foreign_keys=[to_user_id])
    transferred_by = relationship("User", foreign_keys=[transferred_by_id])


class CustomerComplaint(TimestampMixin, Base):
    __tablename__ = "customer_complaints"

    id = Column(Integer, primary_key=True)
    complaint_no = Column(String(60), nullable=False, unique=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    complainant_name = Column(String(100), nullable=False)
    complainant_phone = Column(String(30), nullable=False)
    complainant_address = Column(String(255), nullable=True)
    complaint_reason = Column(String(255), nullable=False)
    complaint_detail = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    handled_result = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    handled_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    handled_at = Column(DateTime, nullable=True)

    customer = relationship("Customer")
    created_by = relationship("User", foreign_keys=[created_by_id])
    handled_by = relationship("User", foreign_keys=[handled_by_id])


class CustomerInquiry(TimestampMixin, Base):
    __tablename__ = "customer_inquiries"

    id = Column(Integer, primary_key=True)
    inquiry_no = Column(String(60), nullable=False, unique=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    inquiry_text = Column(Text, nullable=False)
    source_channel = Column(String(50), nullable=True)
    product_hint = Column(String(150), nullable=True)
    intent_score = Column(Numeric(10, 4), nullable=False, default=0)
    intent_label = Column(String(30), nullable=False, default="unknown")
    model_version = Column(String(80), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    customer = relationship("Customer")
    created_by = relationship("User")


class CustomerAIResult(TimestampMixin, Base):
    __tablename__ = "customer_ai_results"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_customer_ai_result_customer"),
    )

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    segment_label = Column(String(50), nullable=False)
    segment_reason = Column(Text, nullable=True)
    lead_score = Column(Numeric(10, 4), nullable=False, default=0)
    lead_label = Column(String(30), nullable=False, default="待观察")
    rfm_json = Column(JSON, nullable=False, default=dict)
    recommendations_json = Column(JSON, nullable=False, default=list)
    model_version = Column(String(80), nullable=False, default="customer-ai-v1")
    generated_at = Column(DateTime, default=now_local, nullable=False)

    customer = relationship("Customer")


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(150), nullable=False, unique=True)
    contact_name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=False)
    address = Column(String(255), nullable=False)
    status = Column(String(30), nullable=False, default="enabled")
    unified_social_credit_code = Column(String(60), nullable=True, unique=True)
    supplier_type = Column(String(30), nullable=True, index=True)
    website = Column(String(255), nullable=True)
    bank_name = Column(String(150), nullable=True)
    bank_account = Column(String(60), nullable=True)
    remark = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])

    financial_accounts = relationship("FinancialAccount", foreign_keys="FinancialAccount.supplier_id", viewonly=True)


class SupplierTag(TimestampMixin, Base):
    __tablename__ = "supplier_tags"
    __table_args__ = (
        UniqueConstraint("supplier_id", "tag", name="uq_supplier_tag"),
    )

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    tag = Column(String(50), nullable=False)
    dimension = Column(String(30), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    supplier = relationship("Supplier")
    created_by = relationship("User", foreign_keys=[created_by_id])


class SupplierAIResult(TimestampMixin, Base):
    __tablename__ = "supplier_ai_results"
    __table_args__ = (
        UniqueConstraint("supplier_id", name="uq_supplier_ai_result_supplier"),
    )

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    predicted_grade = Column(String(30), nullable=False, default="unknown")
    risk_score = Column(Numeric(10, 4), nullable=False, default=0)
    risk_label = Column(String(30), nullable=False, default="unknown")
    confidence = Column(Numeric(10, 4), nullable=False, default=0)
    features_json = Column(JSON, nullable=False, default=dict)
    probabilities_json = Column(JSON, nullable=False, default=dict)
    model_name = Column(String(80), nullable=False, default="supplier-ai")
    model_version = Column(String(80), nullable=False, default="supplier-ai-v1")
    generated_at = Column(DateTime, default=now_local, nullable=False)

    supplier = relationship("Supplier")


class SupplierCatalogItem(TimestampMixin, Base):
    __tablename__ = "supplier_catalog_items"

    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    remark = Column(String(255), nullable=True)

    supplier = relationship("Supplier")
    product = relationship("Product")
    material = relationship("Material")
    packaging_config = relationship("PackagingConfig")


class SupplierSample(TimestampMixin, Base):
    """供应商送检样品；不参与库存数量、成本或出入库流水。"""
    __tablename__ = "supplier_samples"

    id = Column(Integer, primary_key=True)
    sample_no = Column(String(60), nullable=False, unique=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    quantity = Column(Numeric(18, 4), nullable=False)
    received_quantity = Column(Numeric(18, 4), nullable=True)
    unit = Column(String(30), nullable=False)
    batch_no = Column(String(80), nullable=False)
    storage_location = Column(String(150), nullable=True)
    receipt_remark = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="pending_receipt")
    remark = Column(Text, nullable=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    received_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    received_at = Column(DateTime, nullable=True)
    quality_task_id = Column(Integer, ForeignKey("quality_tasks.id"), nullable=True, unique=True)

    supplier = relationship("Supplier")
    product = relationship("Product")
    material = relationship("Material")
    packaging_config = relationship("PackagingConfig")
    warehouse = relationship("Warehouse")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    received_by = relationship("User", foreign_keys=[received_by_id])
    quality_task = relationship("QualityTask", foreign_keys=[quality_task_id])


class StandardPriceChangeLog(TimestampMixin, Base):
    __tablename__ = "standard_price_change_logs"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    old_price = Column(Numeric(20, 4), nullable=False, default=0)
    new_price = Column(Numeric(20, 4), nullable=False)
    remark = Column(String(255), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    product = relationship("Product")
    changed_by = relationship("User", foreign_keys=[changed_by_id])


class SalesOrderPriceChangeLog(TimestampMixin, Base):
    __tablename__ = "sales_order_price_change_logs"

    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    sales_order_item_id = Column(Integer, ForeignKey("sales_order_items.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    standard_price = Column(Numeric(20, 4), nullable=False, default=0)
    actual_price = Column(Numeric(20, 4), nullable=False)
    remark = Column(String(255), nullable=False)
    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    sales_order = relationship("SalesOrder")
    sales_order_item = relationship("SalesOrderItem")
    product = relationship("Product")
    changed_by = relationship("User", foreign_keys=[changed_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])


class Warehouse(TimestampMixin, Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False, unique=True)
    location = Column(String(255), nullable=True)
    warehouse_type = Column(String(30), nullable=False)
    qc_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class FinancialAccount(TimestampMixin, Base):
    __tablename__ = "financial_accounts"

    id = Column(Integer, primary_key=True)
    owner_type = Column(String(20), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    account_name = Column(String(150), nullable=False)
    bank_name = Column(String(150), nullable=False)
    account_number = Column(String(80), nullable=False)
    currency = Column(String(3), nullable=False, default="CNY")
    is_active = Column(Boolean, nullable=False, default=True)
    remark = Column(String(500), nullable=True)
    customer = relationship("Customer")
    supplier = relationship("Supplier")
    transactions = relationship("FinancialAccountTransaction", foreign_keys="FinancialAccountTransaction.account_id", viewonly=True)


class FinancialAccountTransaction(TimestampMixin, Base):
    """账户参考余额流水；amount 为该账户的增减额，可为负数。"""
    __tablename__ = "financial_account_transactions"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=False, index=True)
    related_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True, index=True)
    transaction_type = Column(String(30), nullable=False)  # adjustment / transfer_in / transfer_out
    amount = Column(Numeric(20, 4), nullable=False)
    reference_no = Column(String(60), nullable=False, index=True)
    remark = Column(String(500), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    account = relationship("FinancialAccount", foreign_keys=[account_id])
    related_account = relationship("FinancialAccount", foreign_keys=[related_account_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    product_code = Column(String(50), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    price = Column(Numeric(12, 4), nullable=False, default=0)
    specification = Column(String(255), nullable=True)
    unit = Column(String(20), nullable=False, default="kg")
    safety_stock_qty = Column(Numeric(18, 4), nullable=False, default=0)
    purchase_threshold_qty = Column(Numeric(18, 4), nullable=False, default=0)
    fulfillment_mode = Column(String(30), nullable=False, default="trade", server_default="trade")
    is_active = Column(Boolean, nullable=False, default=True)

    def __repr__(self):
        return f"<Product(name='{self.name}')>"


class Material(TimestampMixin, Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True)
    material_code = Column(String(50), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    price = Column(Numeric(12, 4), nullable=False, default=0)
    specification = Column(String(255), nullable=True)
    unit = Column(String(20), nullable=False, default="kg")
    safety_stock_qty = Column(Numeric(18, 4), nullable=False, default=0)
    purchase_threshold_qty = Column(Numeric(18, 4), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class PackagingConfig(TimestampMixin, Base):
    __tablename__ = "packaging_configs"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "packaging_name", name="uq_packaging_entity_name"),
    )

    id = Column(Integer, primary_key=True)
    entity_type = Column(String(30), nullable=False)
    entity_id = Column(Integer, nullable=False)
    packaging_name = Column(String(100), nullable=False)
    kg_per_bag = Column(Numeric(18, 4), nullable=False)
    unit_price = Column(Numeric(20, 4), nullable=False, default=0)
    is_default = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)


# ===== 库存与批次 =====
class Inventory(TimestampMixin, Base):
    __tablename__ = "inventories"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "product_id", "material_id", name="uq_inventory_item"),
    )

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=False, default=0)      # 实有
    reserved_quantity = Column(Numeric(18, 4), nullable=False, default=0)  # 占用
    in_transit = Column(Numeric(18, 4), nullable=False, default=0)     # 在途(退货在途;采购在途由采购单实时推导)
    # 已删除 in_production / on_order / available 三个缓存列:它们是第 4 套"可用量"算法,
    # 与其他算法口径互相矛盾且前端从未使用。可用量统一由 services/inventory_availability 实时计算。
    ext_json = Column(JSON, nullable=True)
    # cost_balance is authoritative; avg_unit_cost is its display/cache value.
    cost_balance = Column(Numeric(20, 4), nullable=False, default=0)
    avg_unit_cost = Column(Numeric(20, 8), nullable=False, default=0)
    cost_status = Column(String(20), nullable=False, default="unpriced")
    opening_cost_pending = Column(Boolean, nullable=False, default=False)

    warehouse = relationship("Warehouse")
    product = relationship("Product")
    material = relationship("Material")
    packaging_config = relationship("PackagingConfig")


class InventoryBatch(TimestampMixin, Base):
    __tablename__ = "inventory_batches"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "batch_no", "product_id", "material_id", name="uq_batch_item"),
    )

    id = Column(Integer, primary_key=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    batch_no = Column(String(80), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False, default=0)
    unit_cost = Column(Numeric(20, 8), nullable=False, default=0)
    quality_grade = Column(String(30), nullable=True)
    source_type = Column(String(50), nullable=True)
    source_id = Column(Integer, nullable=True)


class InventoryTransaction(TimestampMixin, Base):
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True)
    transaction_no = Column(String(60), nullable=False, unique=True)
    transaction_type = Column(String(50), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    batch_no = Column(String(80), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=False)
    unit_cost = Column(Numeric(20, 8), nullable=False, default=0)
    total_cost = Column(Numeric(20, 4), nullable=False, default=0)
    cost_status = Column(String(20), nullable=False, default="unpriced")
    related_type = Column(String(50), nullable=True)
    related_id = Column(Integer, nullable=True)
    sales_order_item_id = Column(Integer, ForeignKey("sales_order_items.id"), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class InventoryCostAdjustment(TimestampMixin, Base):
    __tablename__ = "inventory_cost_adjustments"

    id = Column(Integer, primary_key=True)
    inventory_id = Column(Integer, ForeignKey("inventories.id"), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    old_unit_cost = Column(Numeric(20, 8), nullable=False)
    new_unit_cost = Column(Numeric(20, 8), nullable=False)
    old_cost_balance = Column(Numeric(20, 4), nullable=False)
    new_cost_balance = Column(Numeric(20, 4), nullable=False)
    old_cost_status = Column(String(20), nullable=False)
    new_cost_status = Column(String(20), nullable=False)
    reason = Column(String(500), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    inventory = relationship("Inventory")
    created_by = relationship("User")


class InventoryOperationRequest(TimestampMixin, Base):
    __tablename__ = "inventory_operation_requests"

    id = Column(Integer, primary_key=True)
    request_no = Column(String(60), nullable=False, unique=True)
    operation_type = Column(String(20), nullable=False)
    request_category = Column(String(50), nullable=False)
    source_module = Column(String(50), nullable=False)
    source_type = Column(String(50), nullable=False)
    source_id = Column(Integer, nullable=False)
    source_no = Column(String(60), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    handled_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    remark = Column(Text, nullable=True)
    handled_remark = Column(Text, nullable=True)
    adjust_reason = Column(Text, nullable=True)
    quantity_difference_remark = Column(Text, nullable=True)
    difference_notified_at = Column(DateTime, nullable=True)
    original_items_json = Column(JSON, nullable=False, default=list)
    handled_items_json = Column(JSON, nullable=False, default=list)
    completed_at = Column(DateTime, nullable=True)

    warehouse = relationship("Warehouse")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    handled_by = relationship("User", foreign_keys=[handled_by_id])


class InventoryExceptionRecord(TimestampMixin, Base):
    """置货现场的成品/包装报损及包装追加，审批后才正式扣减库存。"""
    __tablename__ = "inventory_exception_records"

    id = Column(Integer, primary_key=True)
    exception_no = Column(String(60), nullable=False, unique=True)
    exception_type = Column(String(40), nullable=False)  # product_damage / packaging_damage / extra_packaging / packaging_adjustment
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True)
    status = Column(String(30), nullable=False, default="pending_approval")
    remark = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_note = Column(Text, nullable=True)
    rejected_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    warehouse = relationship("Warehouse")
    sales_order = relationship("SalesOrder")
    created_by = relationship("User", foreign_keys=[created_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    rejected_by = relationship("User", foreign_keys=[rejected_by_id])
    items = relationship("InventoryExceptionItem", cascade="all, delete-orphan")


class InventoryExceptionItem(TimestampMixin, Base):
    __tablename__ = "inventory_exception_items"

    id = Column(Integer, primary_key=True)
    exception_id = Column(Integer, ForeignKey("inventory_exception_records.id"), nullable=False, index=True)
    item_type = Column(String(20), nullable=False)  # product / packaging
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    sales_order_item_id = Column(Integer, ForeignKey("sales_order_items.id"), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=False)

    exception = relationship("InventoryExceptionRecord")
    product = relationship("Product")
    packaging_config = relationship("PackagingConfig")
    sales_order_item = relationship("SalesOrderItem")


# ===== 销售 =====
# ===== 销售单据类型配置(颜色便于修改,后续下游单据同色传播) =====
ORDER_TYPE_STYLES = {
    "normal":    {"label": "普通", "color": "#909399", "tag_type": "info",    "sort": 0},
    "urgent":    {"label": "加急", "color": "#F56C6C", "tag_type": "danger",  "sort": 1},
    "custom":    {"label": "定制", "color": "#E6A23C", "tag_type": "warning", "sort": 2},
    "important": {"label": "重要", "color": "#F56C6C", "tag_type": "danger",  "sort": 3},
}


def order_type_style(order_type: str | None) -> dict:
    """取单据类型样式;未知类型回退普通。"""
    return ORDER_TYPE_STYLES.get(order_type or "normal", ORDER_TYPE_STYLES["normal"])


class SalesOrder(TimestampMixin, Base):
    __tablename__ = "sales_orders"

    id = Column(Integer, primary_key=True)
    order_no = Column(String(60), nullable=False, unique=True)
    parent_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True)
    split_group_no = Column(String(60), nullable=True)
    split_phase = Column(Integer, nullable=False, default=1)
    # 草稿允许尚未选择客户；提交订单前由服务层统一校验为必填。
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    receipt_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True, index=True)
    sales_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operator_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    consignee = Column(String(100), nullable=False)
    consignee_phone = Column(String(30), nullable=False)
    delivery_address = Column(String(255), nullable=False)
    payment_type = Column(String(50), nullable=False)
    order_type = Column(String(30), nullable=False, default="normal")
    expected_delivery_date = Column(Date, nullable=True)
    status = Column(String(30), nullable=False, default="draft")
    total_amount = Column(Numeric(20, 4), nullable=False, default=0)
    deposit_amount = Column(Numeric(20, 4), nullable=False, default=0)
    transport_fee = Column(Numeric(20, 4), nullable=False, default=0)
    packaging_fee = Column(Numeric(20, 4), nullable=False, default=0)
    cost_amount = Column(Numeric(20, 4), nullable=False, default=0)
    cost_status = Column(String(20), nullable=False, default="unpriced")
    profit_amount = Column(Numeric(20, 4), nullable=False, default=0)
    order_remark = Column(Text, nullable=True)
    finance_edit_pending = Column(Boolean, nullable=False, default=False)
    price_approval_status = Column(String(30), nullable=False, default="not_required")
    price_approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    price_approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    # 提交审批时指定的财务人员(需求:所有财务事务都要指定一个财务负责人)。
    # 与 approved_by_id 的区别:这里是"派给谁办",后者是"最终谁办了"。
    assigned_finance_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    ext_json = Column(JSON, nullable=True)

    customer = relationship("Customer")
    receipt_account = relationship("FinancialAccount", foreign_keys=[receipt_account_id])
    sales_user = relationship("User", foreign_keys=[sales_user_id])
    operator_user = relationship("User", foreign_keys=[operator_user_id])
    price_approved_by = relationship("User", foreign_keys=[price_approved_by_id])
    parent_order = relationship("SalesOrder", remote_side=[id])
    items = relationship("SalesOrderItem", back_populates="sales_order", cascade="all, delete-orphan")


class SalesOrderItem(TimestampMixin, Base):
    __tablename__ = "sales_order_items"

    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    unit_price = Column(Numeric(20, 4), nullable=False)
    total_amount = Column(Numeric(20, 4), nullable=False)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    kg_per_bag = Column(Numeric(18, 4), nullable=True)
    bag_count = Column(Numeric(18, 4), nullable=True)
    packaging_unit_price = Column(Numeric(20, 4), nullable=False, default=0)
    packaging_fee = Column(Numeric(20, 4), nullable=False, default=0)
    display_specification = Column(String(120), nullable=True)
    remark = Column(String(255), nullable=True)
    shipped_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    placed_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    # 本明细当前占用了多少成品库存。有了这个"账",占用才能做到幂等(重复审批不叠加)
    # 且可释放(驳回/取消时知道该退还多少),否则只看仓库端的 reserved 无法区分是谁占的。
    reserved_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    packaging_reserved_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    packaging_shipped_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))

    sales_order = relationship("SalesOrder", back_populates="items")
    product = relationship("Product")
    packaging_config = relationship("PackagingConfig")


class SalesOrderApproval(TimestampMixin, Base):
    __tablename__ = "sales_order_approvals"

    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    approver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decision = Column(String(30), nullable=False)
    voucher_amount = Column(Numeric(20, 4), nullable=False, default=0)
    note = Column(Text, nullable=True)
    approved_at = Column(DateTime, default=now_local, nullable=False)


class InventoryDispatchQueue(TimestampMixin, Base):
    __tablename__ = "inventory_dispatch_queues"

    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False, unique=True)
    # 纸质作业单必须对应唯一的实际发货仓库。历史数据由迁移回填成品仓，
    # 新建队列也由销售流程显式写入；保留 nullable 仅兼容老测试夹具。
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True, index=True)
    placement_scan_token = Column(String(32), nullable=False, unique=True, default=lambda: secrets.token_hex(12).upper())
    shipment_scan_token = Column(String(32), nullable=False, unique=True, default=lambda: secrets.token_hex(12).upper())
    barcode_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    placement_status = Column(String(30), nullable=False, default="pending")
    shipment_status = Column(String(30), nullable=False, default="waiting_approval")
    placement_marked_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    placement_marked_at = Column(DateTime, nullable=True)
    shipment_marked_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    shipment_marked_at = Column(DateTime, nullable=True)
    placement_printed_at = Column(DateTime, nullable=True, index=True)
    placement_print_status = Column(String(20), nullable=False, default="unprinted", server_default="unprinted")
    placement_print_method = Column(String(20), nullable=True)
    placement_printed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    placement_print_count = Column(Integer, nullable=False, default=0, server_default=text("0"))

    sales_order = relationship("SalesOrder")
    warehouse = relationship("Warehouse")
    placement_printed_by = relationship("User", foreign_keys=[placement_printed_by_id])


class PlacementPrintAgent(TimestampMixin, Base):
    __tablename__ = "placement_print_agents"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class PlacementPrintJob(TimestampMixin, Base):
    __tablename__ = "placement_print_jobs"
    __table_args__ = (UniqueConstraint("queue_id", name="uq_placement_print_job_queue"),)

    id = Column(Integer, primary_key=True)
    queue_id = Column(Integer, ForeignKey("inventory_dispatch_queues.id"), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    claimed_by_agent_id = Column(Integer, ForeignKey("placement_print_agents.id"), nullable=True)
    lease_token_hash = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    printed_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)

    queue = relationship("InventoryDispatchQueue")


class PlacementPrintAccessLog(Base):
    __tablename__ = "placement_print_access_logs"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("placement_print_agents.id"), nullable=True)
    token_fingerprint = Column(String(12), nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    result = Column(String(30), nullable=False)
    created_at = Column(DateTime, default=now_local, nullable=False, index=True)


class WarehouseScanDevice(TimestampMixin, Base):
    """受信任的仓库扫码电脑；扫描枪本身不保存系统认证信息。"""

    __tablename__ = "warehouse_scan_devices"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    operator_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    last_seen_at = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    warehouse = relationship("Warehouse")
    operator_user = relationship("User", foreign_keys=[operator_user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class WarehouseScanAccessLog(Base):
    """设备接口安全访问日志；可按保留策略定期清理。"""

    __tablename__ = "warehouse_scan_access_logs"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("warehouse_scan_devices.id"), nullable=True)
    token_fingerprint = Column(String(12), nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    result = Column(String(30), nullable=False)
    created_at = Column(DateTime, default=now_local, nullable=False, index=True)

    device = relationship("WarehouseScanDevice")


class WarehouseScanEvent(TimestampMixin, Base):
    """永久业务审计事件；重复请求依靠 idempotency_key 返回同一结果。"""

    __tablename__ = "warehouse_scan_events"
    __table_args__ = (UniqueConstraint("device_id", "idempotency_key", name="uq_scan_event_device_idempotency"),)

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("warehouse_scan_devices.id"), nullable=False, index=True)
    queue_id = Column(Integer, ForeignKey("inventory_dispatch_queues.id"), nullable=True, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    operator_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(30), nullable=False)
    idempotency_key = Column(String(80), nullable=False)
    barcode_fingerprint = Column(String(12), nullable=True)
    result = Column(String(30), nullable=False)
    message = Column(String(500), nullable=True)
    ip_address = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)

    device = relationship("WarehouseScanDevice")
    queue = relationship("InventoryDispatchQueue")
    sales_order = relationship("SalesOrder")
    warehouse = relationship("Warehouse")
    operator_user = relationship("User", foreign_keys=[operator_user_id])


class ShipmentProof(TimestampMixin, Base):
    """高拍仪上传的发货凭证；只有一次成功发货可以消费该照片。"""

    __tablename__ = "shipment_proofs"

    id = Column(Integer, primary_key=True)
    queue_id = Column(Integer, ForeignKey("inventory_dispatch_queues.id"), nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("warehouse_scan_devices.id"), nullable=False)
    attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False, unique=True)
    captured_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

    attachment = relationship("Attachment")
    queue = relationship("InventoryDispatchQueue")
    device = relationship("WarehouseScanDevice")


class SalesShipmentBatch(TimestampMixin, Base):
    """一次实际销售发货。当前扫码方案固定为整单全量发货。"""

    __tablename__ = "sales_shipment_batches"

    id = Column(Integer, primary_key=True)
    batch_no = Column(String(60), nullable=False, unique=True)
    queue_id = Column(Integer, ForeignKey("inventory_dispatch_queues.id"), nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("warehouse_scan_devices.id"), nullable=True)
    operator_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    proof_id = Column(Integer, ForeignKey("shipment_proofs.id"), nullable=True, unique=True)
    status = Column(String(30), nullable=False, default="completed")
    total_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    shipped_at = Column(DateTime, default=now_local, nullable=False)

    queue = relationship("InventoryDispatchQueue")
    proof = relationship("ShipmentProof")
    device = relationship("WarehouseScanDevice")
    operator_user = relationship("User", foreign_keys=[operator_user_id])



class SalesReturn(TimestampMixin, Base):
    __tablename__ = "sales_returns"

    id = Column(Integer, primary_key=True)
    return_no = Column(String(60), nullable=False, unique=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    return_type = Column(String(30), nullable=False, default="return")
    reason = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    refund_amount = Column(Numeric(20, 4), nullable=False, default=0)
    review_note = Column(Text, nullable=True)
    requires_reinspection = Column(Boolean, nullable=False, default=False)
    reinspection_status = Column(String(30), nullable=False, default="not_required")
    approved_at = Column(DateTime, nullable=True)


class SalesReturnItem(TimestampMixin, Base):
    __tablename__ = "sales_return_items"

    id = Column(Integer, primary_key=True)
    sales_return_id = Column(Integer, ForeignKey("sales_returns.id"), nullable=False)
    sales_order_item_id = Column(Integer, ForeignKey("sales_order_items.id"), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    batch_suffix = Column(String(30), nullable=True)

#预计可用库存
class FulfillmentDemand(TimestampMixin, Base):
    __tablename__ = "fulfillment_demands"

    id = Column(Integer, primary_key=True)
    demand_no = Column(String(60), nullable=False, unique=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True)
    sales_order_item_id = Column(Integer, ForeignKey("sales_order_items.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    fulfillment_mode = Column(String(30), nullable=False)
    # required_quantity 为原始需求量(恒定不变),fulfilled_quantity 为已补货量(累加),
    # shortage_quantity 为当前待补量。改造前只有 shortage_quantity 且被原地递减,
    # 原始需求量丢失,导致分批生产/分批采购的"待补货/已补货"进度无法还原。
    required_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    fulfilled_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    shortage_quantity = Column(Numeric(18, 4), nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    processed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    process_note = Column(Text, nullable=True)
    source_order_no = Column(String(60), nullable=True)

    sales_order = relationship("SalesOrder")
    sales_order_item = relationship("SalesOrderItem")
    product = relationship("Product")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    processed_by = relationship("User", foreign_keys=[processed_by_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class MaterialReplenishmentDemand(TimestampMixin, Base):
    __tablename__ = "material_replenishment_demands"

    id = Column(Integer, primary_key=True)
    demand_no = Column(String(60), nullable=False, unique=True)
    fulfillment_demand_id = Column(Integer, ForeignKey("fulfillment_demands.id"), nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    items_json = Column(JSON, nullable=False)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True, index=True)


# ===== 移库 =====
class StockTransfer(TimestampMixin, Base):
    __tablename__ = "stock_transfers"

    id = Column(Integer, primary_key=True)
    transfer_no = Column(String(60), nullable=False, unique=True)
    source_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    target_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=False)
    requires_quality = Column(Boolean, nullable=False, default=False)
    status = Column(String(30), nullable=False, default="pending_outbound")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    outbound_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    outbound_at = Column(DateTime, nullable=True)
    quality_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    quality_at = Column(DateTime, nullable=True)
    quality_result = Column(String(30), nullable=True)
    completed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    reject_reason = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)

    source_warehouse = relationship("Warehouse", foreign_keys=[source_warehouse_id])
    target_warehouse = relationship("Warehouse", foreign_keys=[target_warehouse_id])
    product = relationship("Product")
    material = relationship("Material")
    packaging_config = relationship("PackagingConfig")
    created_by = relationship("User", foreign_keys=[created_by_id])


# ===== 采购 =====
class PurchaseRequest(TimestampMixin, Base):
    __tablename__ = "purchase_requests"

    id = Column(Integer, primary_key=True)
    request_no = Column(String(60), nullable=False, unique=True)
    department = Column(String(30), nullable=False)
    item_type = Column(String(20), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    item_name = Column(String(200), nullable=False)
    specification = Column(String(200), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=False)
    unit = Column(String(30), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=True)
    processed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    process_note = Column(Text, nullable=True)

    product = relationship("Product")
    material = relationship("Material")
    submitted_by = relationship("User", foreign_keys=[submitted_by_id])
    processed_by = relationship("User", foreign_keys=[processed_by_id])
    purchase_order = relationship("PurchaseOrder")


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint(
            "replenishment_source_type",
            "replenishment_source_id",
            name="uq_purchase_orders_replenishment_source",
        ),
    )

    id = Column(Integer, primary_key=True)
    order_no = Column(String(60), nullable=False, unique=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    purchaser_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operator_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    payment_type = Column(String(50), nullable=False)
    expected_arrival_date = Column(Date, nullable=True)
    target_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
    status = Column(String(30), nullable=False, default="draft")
    total_amount = Column(Numeric(20, 4), nullable=False, default=0)
    threshold_triggered = Column(Boolean, nullable=False, default=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    contract_received = Column(Boolean, nullable=False, default=False)
    replenishment_source_type = Column(String(50), nullable=True)
    replenishment_source_id = Column(Integer, nullable=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True)
    ext_json = Column(JSON, nullable=True)

    supplier = relationship("Supplier")
    target_warehouse = relationship("Warehouse")
    purchaser = relationship("User", foreign_keys=[purchaser_user_id])
    operator_user = relationship("User", foreign_keys=[operator_user_id])
    items = relationship("PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan")


class PurchaseOrderItem(TimestampMixin, Base):
    __tablename__ = "purchase_order_items"

    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=False)
    unit_price = Column(Numeric(20, 4), nullable=False)
    total_amount = Column(Numeric(20, 4), nullable=False)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    kg_per_bag = Column(Numeric(18, 4), nullable=True)
    bag_count = Column(Numeric(18, 4), nullable=True)
    display_specification = Column(String(120), nullable=True)
    received_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))
    returned_quantity = Column(Numeric(18, 4), nullable=False, default=0, server_default=text("0"))

    purchase_order = relationship("PurchaseOrder", back_populates="items")
    product = relationship("Product")
    material = relationship("Material")
    packaging_config = relationship("PackagingConfig")


class PurchaseOrderApproval(TimestampMixin, Base):
    __tablename__ = "purchase_order_approvals"

    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    approver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decision = Column(String(30), nullable=False)
    note = Column(Text, nullable=True)
    approved_at = Column(DateTime, default=now_local, nullable=False)


class PurchaseReceipt(TimestampMixin, Base):
    __tablename__ = "purchase_receipts"

    id = Column(Integer, primary_key=True)
    receipt_no = Column(String(60), nullable=False, unique=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    received_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    requires_quality = Column(Boolean, nullable=False, default=False)
    quality_status = Column(String(30), nullable=False, default="not_required")
    status = Column(String(30), nullable=False, default="received")
    remark = Column(String(255), nullable=True)
    preliminary_acceptance_note = Column(Text, nullable=True)
    recorded_at = Column(DateTime, nullable=True)


class PurchaseReceiptItem(TimestampMixin, Base):
    __tablename__ = "purchase_receipt_items"

    id = Column(Integer, primary_key=True)
    purchase_receipt_id = Column(Integer, ForeignKey("purchase_receipts.id"), nullable=False)
    purchase_order_item_id = Column(Integer, ForeignKey("purchase_order_items.id"), nullable=False)
    expected_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    received_quantity = Column(Numeric(18, 4), nullable=False)
    batch_no = Column(String(80), nullable=False)
    unit_cost = Column(Numeric(18, 4), nullable=False, default=0)
    difference_remark = Column(Text, nullable=True)


class PurchaseReturn(TimestampMixin, Base):
    __tablename__ = "purchase_returns"

    id = Column(Integer, primary_key=True)
    return_no = Column(String(60), nullable=False, unique=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    returned_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    return_type = Column(String(30), nullable=False, default="return")
    reason = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    refund_amount = Column(Numeric(20, 4), nullable=False, default=0)
    requires_reinspection = Column(Boolean, nullable=False, default=False)
    reinspection_status = Column(String(30), nullable=False, default="not_required")


class PurchasePaymentRequest(TimestampMixin, Base):
    __tablename__ = "purchase_payment_requests"

    company_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    counterparty_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    company_account = relationship("FinancialAccount", foreign_keys=[company_account_id])
    counterparty_account = relationship("FinancialAccount", foreign_keys=[counterparty_account_id])

    id = Column(Integer, primary_key=True)
    request_no = Column(String(60), nullable=False, unique=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    merge_no = Column(String(60), nullable=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    completed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_finance_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    requested_amount = Column(Numeric(20, 4), nullable=False, default=0)
    order_amount = Column(Numeric(20, 4), nullable=False, default=0)
    other_fee_amount = Column(Numeric(20, 4), nullable=False, default=0)
    actual_amount = Column(Numeric(20, 4), nullable=False, default=0)
    arrival_status = Column(String(30), nullable=False, default="not_arrived")
    status = Column(String(30), nullable=False, default="pending")
    payment_detail_remark = Column(Text, nullable=True)
    manager_remark = Column(Text, nullable=True)
    completion_remark = Column(Text, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    bank_receipt_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)
    payment_method = Column(String(80), nullable=True)
    paid_at = Column(DateTime, nullable=True)
    invoice_no = Column(String(80), nullable=True)
    invoice_amount = Column(Numeric(20, 4), nullable=True)
    invoice_date = Column(Date, nullable=True)
    invoice_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)

    purchase_order = relationship("PurchaseOrder")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    completed_by = relationship("User", foreign_keys=[completed_by_id])
    bank_receipt_attachment = relationship("Attachment", foreign_keys=[bank_receipt_attachment_id])


# ===== 生产与质检 =====
class BOM(TimestampMixin, Base):
    __tablename__ = "boms"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, unique=True)
    packaging_config_id = Column(Integer, ForeignKey("packaging_configs.id"), nullable=True)
    version_no = Column(String(30), nullable=False, default="v1")
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)

    product = relationship("Product")
    packaging_config = relationship("PackagingConfig")
    items = relationship("BOMItem", back_populates="bom", cascade="all, delete-orphan")


class BOMItem(TimestampMixin, Base):
    __tablename__ = "bom_items"

    id = Column(Integer, primary_key=True)
    bom_id = Column(Integer, ForeignKey("boms.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    standard_quantity = Column(Numeric(18, 4), nullable=False)

    bom = relationship("BOM", back_populates="items")
    material = relationship("Material")


class ProductionTask(TimestampMixin, Base):
    __tablename__ = "production_tasks"

    id = Column(Integer, primary_key=True)
    task_no = Column(String(60), nullable=False, unique=True)
    source_type = Column(String(30), nullable=False, default="manual")
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True)
    fulfillment_demand_id = Column(Integer, ForeignKey("fulfillment_demands.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    parent_task_id = Column(Integer, ForeignKey("production_tasks.id"), nullable=True)
    planned_quantity = Column(Numeric(18, 4), nullable=False)
    produced_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    scrap_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    direct_labor_cost = Column(Numeric(20, 4), nullable=False, default=0)
    manufacturing_overhead_cost = Column(Numeric(20, 4), nullable=False, default=0)
    fluctuation_ratio = Column(Numeric(8, 4), nullable=False, default=0)
    requires_quality = Column(Boolean, nullable=False, default=True)
    status = Column(String(30), nullable=False, default="pending_material")
    batch_no = Column(String(80), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    preliminary_acceptance_note = Column(Text, nullable=True)
    completion_recorded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
    ext_json = Column(JSON, nullable=True)

    product = relationship("Product")
    fulfillment_demand = relationship("FulfillmentDemand", foreign_keys=[fulfillment_demand_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    completion_recorded_by = relationship("User", foreign_keys=[completion_recorded_by_id])
    # 供接口展示"本任务归属哪个入库仓库",避免只拿到 id
    warehouse = relationship("Warehouse", foreign_keys=[warehouse_id])
    usages = relationship(
        "ProductionMaterialUsage",
        back_populates="production_task",
        cascade="all, delete-orphan",
    )


class ProductionMaterialUsage(TimestampMixin, Base):
    __tablename__ = "production_material_usages"

    id = Column(Integer, primary_key=True)
    production_task_id = Column(Integer, ForeignKey("production_tasks.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    planned_quantity = Column(Numeric(18, 4), nullable=False)
    requested_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    approved_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    issued_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    actual_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    reason = Column(Text, nullable=True)
    special_request_status = Column(String(30), nullable=False, default="not_required")
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    inventory_approval_status = Column(String(30), nullable=False, default="not_required")
    inventory_approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    inventory_approved_at = Column(DateTime, nullable=True)
    inventory_remark = Column(Text, nullable=True)
    manager_approval_status = Column(String(30), nullable=False, default="not_required")
    manager_approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime, nullable=True)
    manager_remark = Column(Text, nullable=True)

    production_task = relationship("ProductionTask", back_populates="usages")
    material = relationship("Material")


class ProductionPlan(TimestampMixin, Base):
    __tablename__ = "production_plans"

    id = Column(Integer, primary_key=True)
    plan_no = Column(String(60), nullable=False, unique=True)
    plan_name = Column(String(120), nullable=True)
    period_type = Column(String(30), nullable=False, default="custom")
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=True)
    fulfillment_demand_id = Column(Integer, ForeignKey("fulfillment_demands.id"), nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    planned_quantity = Column(Numeric(18, 4), nullable=False)
    scheduled_start_at = Column(DateTime, nullable=True)
    scheduled_end_at = Column(DateTime, nullable=True)
    priority = Column(String(30), nullable=False, default="normal")
    line_name = Column(String(100), nullable=True)
    scheduling_note = Column(Text, nullable=True)
    detail_json = Column(JSON, nullable=False, default=dict)
    converted_task_id = Column(Integer, ForeignKey("production_tasks.id"), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, default="planned")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    product = relationship("Product")
    sales_order = relationship("SalesOrder")
    fulfillment_demand = relationship("FulfillmentDemand", foreign_keys=[fulfillment_demand_id])
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    converted_task = relationship("ProductionTask", foreign_keys=[converted_task_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])


class QualityTask(TimestampMixin, Base):
    __tablename__ = "quality_tasks"

    id = Column(Integer, primary_key=True)
    quality_no = Column(String(60), nullable=False, unique=True)
    production_task_id = Column(Integer, ForeignKey("production_tasks.id"), nullable=True)
    stock_transfer_id = Column(Integer, ForeignKey("stock_transfers.id"), nullable=True, unique=True)
    batch_no = Column(String(80), nullable=False)
    source_category = Column(String(30), nullable=False, default="production")
    required_tests = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="pending")
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # 申请质检时指定的质检员(需求:由指定质检员或超级质检员处理)。
    # 与 QualityInspection.inspector_user_id 的区别:这里是"派给谁检",后者是"谁检了"。
    assigned_inspector_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # 该质检任务归属的仓库,用于普通质检员的可见范围过滤(超级质检员不受限)
    target_warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)

    # 供接口展示"派给谁检"与"归属哪个仓库"的名称,避免前端只拿到 id
    assigned_inspector = relationship("User", foreign_keys=[assigned_inspector_id])
    target_warehouse = relationship("Warehouse", foreign_keys=[target_warehouse_id])
    stock_transfer = relationship("StockTransfer", foreign_keys=[stock_transfer_id])

    inspections = relationship(
        "QualityInspection",
        back_populates="quality_task",
        cascade="all, delete-orphan",
    )


class QualityInspection(TimestampMixin, Base):
    __tablename__ = "quality_inspections"

    id = Column(Integer, primary_key=True)
    quality_task_id = Column(Integer, ForeignKey("quality_tasks.id"), nullable=False)
    inspector_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sequence_no = Column(Integer, nullable=False)
    result_text = Column(Text, nullable=False)
    grade = Column(String(30), nullable=False)
    is_passed = Column(Boolean, nullable=False, default=True)
    inspected_quantity = Column(Numeric(18, 4), nullable=False)
    sample_consumption_qty = Column(Numeric(18, 4), nullable=False, default=0)
    report_summary = Column(Text, nullable=True)
    inspected_at = Column(DateTime, default=now_local, nullable=False)

    quality_task = relationship("QualityTask", back_populates="inspections")


class QualityInboundApplication(TimestampMixin, Base):
    __tablename__ = "quality_inbound_applications"

    id = Column(Integer, primary_key=True)
    quality_task_id = Column(Integer, ForeignKey("quality_tasks.id"), nullable=False, unique=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    approved_at = Column(DateTime, nullable=True)
    remark = Column(Text, nullable=True)


class QualityDisposition(TimestampMixin, Base):
    __tablename__ = "quality_dispositions"
    __table_args__ = (UniqueConstraint("quality_task_id", name="uq_quality_disposition_task"),)

    id = Column(Integer, primary_key=True)
    quality_task_id = Column(Integer, ForeignKey("quality_tasks.id"), nullable=False)
    disposition_type = Column(String(30), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    reason = Column(Text, nullable=False)
    related_production_task_id = Column(Integer, ForeignKey("production_tasks.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    quality_task = relationship("QualityTask")
    related_production_task = relationship("ProductionTask", foreign_keys=[related_production_task_id])


class QualityCostRecord(TimestampMixin, Base):
    __tablename__ = "quality_cost_records"
    __table_args__ = (UniqueConstraint("quality_disposition_id", name="uq_quality_cost_record_disposition"),)

    id = Column(Integer, primary_key=True)
    quality_disposition_id = Column(Integer, ForeignKey("quality_dispositions.id"), nullable=False)
    estimated_amount = Column(Numeric(20, 4), nullable=False, default=0)
    recognized_amount = Column(Numeric(20, 4), nullable=False, default=0)
    status = Column(String(30), nullable=False, default="pending")
    remark = Column(String(255), nullable=True)
    recognized_at = Column(DateTime, nullable=True)
    recognized_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    disposition = relationship("QualityDisposition")
    recognized_by = relationship("User", foreign_keys=[recognized_by_id])


# ===== 财务 =====
class SalesReceiptRequest(TimestampMixin, Base):
    __tablename__ = "sales_receipt_requests"

    company_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    counterparty_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    company_account = relationship("FinancialAccount", foreign_keys=[company_account_id])
    counterparty_account = relationship("FinancialAccount", foreign_keys=[counterparty_account_id])

    id = Column(Integer, primary_key=True)
    request_no = Column(String(60), nullable=False, unique=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    requested_amount = Column(Numeric(20, 4), nullable=False, default=0)
    actual_amount = Column(Numeric(20, 4), nullable=False, default=0)
    payment_stage = Column(String(30), nullable=False)
    installment_total = Column(Integer, nullable=True)
    installment_index = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    is_abnormal = Column(Boolean, nullable=False, default=False)
    remark = Column(Text, nullable=True)
    approval_remark = Column(Text, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    finance_status = Column(String(30), nullable=False, default="pending")
    finance_checked_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    finance_checked_at = Column(DateTime, nullable=True)
    # 提交收款申请时指定的财务人员(派给谁办;finance_checked_by_id 记录最终谁办了)
    assigned_finance_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    receipt_method = Column(String(80), nullable=True)
    receipt_at = Column(DateTime, nullable=True)
    bank_receipt_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)

    sales_order = relationship("SalesOrder")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    finance_checked_by = relationship("User", foreign_keys=[finance_checked_by_id])
    bank_receipt_attachment = relationship("Attachment", foreign_keys=[bank_receipt_attachment_id])


class ReceivablePlan(TimestampMixin, Base):
    __tablename__ = "receivable_plans"

    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    due_date = Column(Date, nullable=False)
    planned_amount = Column(Numeric(20, 4), nullable=False)
    received_amount = Column(Numeric(20, 4), nullable=False, default=0)
    status = Column(String(30), nullable=False, default="pending")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class ReceivableRecord(TimestampMixin, Base):
    __tablename__ = "receivable_records"

    company_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    counterparty_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    company_account = relationship("FinancialAccount", foreign_keys=[company_account_id])
    counterparty_account = relationship("FinancialAccount", foreign_keys=[counterparty_account_id])

    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id"), nullable=False)
    receivable_plan_id = Column(Integer, ForeignKey("receivable_plans.id"), nullable=True)
    sales_receipt_request_id = Column(Integer, ForeignKey("sales_receipt_requests.id"), nullable=True)
    amount = Column(Numeric(20, 4), nullable=False)
    payment_stage = Column(String(30), nullable=True)
    installment_total = Column(Integer, nullable=True)
    installment_index = Column(Integer, nullable=True)
    received_at = Column(DateTime, nullable=False)
    received_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    remark = Column(String(255), nullable=True)
    receipt_method = Column(String(80), nullable=True)
    bank_receipt_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)


class PayableRecord(TimestampMixin, Base):
    __tablename__ = "payable_records"

    company_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    counterparty_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True)
    company_account = relationship("FinancialAccount", foreign_keys=[company_account_id])
    counterparty_account = relationship("FinancialAccount", foreign_keys=[counterparty_account_id])

    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    purchase_receipt_id = Column(Integer, ForeignKey("purchase_receipts.id"), nullable=True)
    purchase_payment_request_id = Column(Integer, ForeignKey("purchase_payment_requests.id"), nullable=True)
    merge_no = Column(String(60), nullable=True)
    amount = Column(Numeric(20, 4), nullable=False)
    order_amount = Column(Numeric(20, 4), nullable=False, default=0)
    other_fee_amount = Column(Numeric(20, 4), nullable=False, default=0)
    paid_at = Column(DateTime, nullable=False)
    paid_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(30), nullable=False, default="paid")
    remark = Column(String(255), nullable=True)
    payment_method = Column(String(80), nullable=True)
    bank_receipt_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)


class PaymentMethodOption(TimestampMixin, Base):
    __tablename__ = "payment_method_options"

    id = Column(Integer, primary_key=True)
    method_type = Column(String(30), nullable=False)
    name = Column(String(80), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_by = relationship("User")


class Reimbursement(TimestampMixin, Base):
    __tablename__ = "reimbursements"

    id = Column(Integer, primary_key=True)
    reimbursement_no = Column(String(60), nullable=False, unique=True)
    applicant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expense_category = Column(String(30), nullable=False)
    expense_subcategory = Column(String(80), nullable=False)
    amount = Column(Numeric(20, 4), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    # 申请时指定的财务人员(派给谁办)。
    # finance_user_id 保留原语义:实际执行付款的财务(谁办了),两者用途不同不可合并。
    assigned_finance_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    finance_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    finance_remark = Column(Text, nullable=True)
    chairman_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    chairman_remark = Column(Text, nullable=True)
    business_matter_id = Column(Integer, ForeignKey("business_matters.id"), nullable=True)
    bank_receipt_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)
    payment_method = Column(String(80), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)

    applicant = relationship("User", foreign_keys=[applicant_user_id])
    finance_user = relationship("User", foreign_keys=[finance_user_id])
    chairman_user = relationship("User", foreign_keys=[chairman_user_id])
    attachment = relationship("Attachment", foreign_keys=[attachment_id])
    bank_receipt_attachment = relationship("Attachment", foreign_keys=[bank_receipt_attachment_id])


class BusinessMatter(TimestampMixin, Base):
    __tablename__ = "business_matters"

    id = Column(Integer, primary_key=True)
    matter_no = Column(String(60), nullable=False, unique=True)
    applicant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expense_category = Column(String(30), nullable=False)
    expense_subcategory = Column(String(80), nullable=False)
    estimated_amount = Column(Numeric(20, 4), nullable=False, default=0)
    actual_amount = Column(Numeric(20, 4), nullable=False, default=0)
    occurred_at = Column(DateTime, nullable=False)
    detail = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="pending_manager_approval")
    manager_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manager_remark = Column(Text, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    converted_reimbursement_id = Column(Integer, ForeignKey("reimbursements.id"), nullable=True)
    attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)

    applicant = relationship("User", foreign_keys=[applicant_user_id])
    manager_user = relationship("User", foreign_keys=[manager_user_id])
    attachment = relationship("Attachment", foreign_keys=[attachment_id])
    converted_reimbursement = relationship("Reimbursement", foreign_keys=[converted_reimbursement_id])


class RefundRecord(TimestampMixin, Base):
    __tablename__ = "refund_records"

    id = Column(Integer, primary_key=True)
    business_type = Column(String(30), nullable=False)
    sales_return_id = Column(Integer, ForeignKey("sales_returns.id"), nullable=True)
    purchase_return_id = Column(Integer, ForeignKey("purchase_returns.id"), nullable=True)
    amount = Column(Numeric(20, 4), nullable=False)
    refunded_at = Column(DateTime, nullable=False)
    processed_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    remark = Column(String(255), nullable=True)


class CashFlowRecord(TimestampMixin, Base):
    __tablename__ = "cash_flow_records"

    id = Column(Integer, primary_key=True)
    record_no = Column(String(60), nullable=False, unique=True)
    flow_type = Column(String(30), nullable=False)
    business_type = Column(String(50), nullable=False)
    business_id = Column(Integer, nullable=False)
    related_no = Column(String(60), nullable=True)
    # 旧流水允许为空；新流水由服务层在落库时必须绑定企业账户。
    company_account_id = Column(Integer, ForeignKey("financial_accounts.id"), nullable=True, index=True)
    amount = Column(Numeric(20, 4), nullable=False)
    happened_at = Column(DateTime, nullable=False)
    remark = Column(String(255), nullable=True)
    company_account = relationship("FinancialAccount", foreign_keys=[company_account_id])


class LogisticsRecord(TimestampMixin, Base):
    __tablename__ = "logistics_records"

    id = Column(Integer, primary_key=True)
    logistics_no = Column(String(60), nullable=False, unique=True)
    module = Column(String(30), nullable=False)
    business_type = Column(String(30), nullable=False)
    business_id = Column(Integer, nullable=False)
    related_no = Column(String(60), nullable=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=True)
    logistics_mode = Column(String(30), nullable=False, default="road")
    carrier_name = Column(String(120), nullable=False)
    driver_name = Column(String(80), nullable=True)
    driver_phone = Column(String(30), nullable=True)
    vehicle_no = Column(String(60), nullable=True)
    tracking_no = Column(String(80), nullable=True)
    bill_date = Column(Date, nullable=True)
    origin_station = Column(String(120), nullable=True)
    destination_station = Column(String(120), nullable=True)
    transfer_station = Column(String(120), nullable=True)
    consignor_name = Column(String(100), nullable=True)
    consignor_phone = Column(String(30), nullable=True)
    consignor_address = Column(String(255), nullable=True)
    consignee_name = Column(String(100), nullable=True)
    consignee_phone = Column(String(30), nullable=True)
    consignee_address = Column(String(255), nullable=True)
    goods_info_json = Column(JSON, nullable=False, default=list)
    delivery_fee = Column(Numeric(20, 4), nullable=False, default=0)
    freight_payment_type = Column(String(30), nullable=True)
    handover_type = Column(String(30), nullable=True)
    document_attachment_id = Column(Integer, ForeignKey("attachments.id"), nullable=True)
    dispatched_at = Column(DateTime, nullable=True)
    arrived_at = Column(DateTime, nullable=True)
    remark = Column(String(255), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    warehouse = relationship("Warehouse")
    created_by = relationship("User")
    document_attachment = relationship("Attachment", foreign_keys=[document_attachment_id])


# ===== 盘点与报表 =====
class StockTakeTask(TimestampMixin, Base):
    __tablename__ = "stock_take_tasks"

    id = Column(Integer, primary_key=True)
    task_no = Column(String(60), nullable=False, unique=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    submitted_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    difference_note = Column(Text, nullable=True)
    approved_at = Column(DateTime, nullable=True)


class StockTakeItem(TimestampMixin, Base):
    __tablename__ = "stock_take_items"

    id = Column(Integer, primary_key=True)
    stock_take_task_id = Column(Integer, ForeignKey("stock_take_tasks.id"), nullable=False)
    inventory_id = Column(Integer, ForeignKey("inventories.id"), nullable=False)
    system_quantity = Column(Numeric(18, 4), nullable=False)
    counted_quantity = Column(Numeric(18, 4), nullable=False)
    difference_quantity = Column(Numeric(18, 4), nullable=False)
    reason = Column(String(255), nullable=True)

    inventory = relationship("Inventory")


class ReportSnapshot(TimestampMixin, Base):
    __tablename__ = "report_snapshots"

    id = Column(Integer, primary_key=True)
    report_type = Column(String(50), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    payload_json = Column(JSON, nullable=False, default=dict)
    generated_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
