from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from database import get_db
from models import FinancialAccount
from dependencies import get_current_user, require_roles
from models import (
    BOM,
    BOMItem,
    AppSetting,
    Customer,
    CustomerComplaint,
    CustomerTag,
    CustomerTransferRecord,
    Department,
    Inventory,
    InventoryBatch,
    InventoryTransaction,
    Material,
    PackagingConfig,
    Product,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    Role,
    SalesOrder,
    SalesOrderItem,
    StandardPriceChangeLog,
    Supplier,
    SupplierCatalogItem,
    SupplierTag,
    User,
    Warehouse,
)
from responses import api_response
from schemas.master_data import CustomerComplaintCreate, CustomerComplaintUpdate, CustomerCreate, CustomerTransferCreate, MaterialCreate, PackagingConfigCreate, ProductCreate, StandardPriceUpdate, SupplierCatalogItemInput, SupplierCreate
from services.core import _role_user_ids, create_notification, create_todo, next_business_no, write_operation_log
from services.inventory_availability import placeable_of, replenishment_available_of, purchase_in_transit_map, stock_key
from services.display import get_global_packaging_name, packaging_name_needs_repair, packaging_spec_text, upsert_default_packaging_config
from services.customer_activity import register_customer_transfer
from services.organization import get_department_path_names
from services.tag_catalog import (
    CUSTOMER_REGION_OPTIONS,
    CUSTOMER_TYPE_OPTIONS,
    SUPPLIER_TYPE_OPTIONS,
    COMPANY_MANAGED_CUSTOMER_TAGS,
    customer_region_label,
    customer_tag_catalog,
    customer_tag_dimension,
    customer_type_label,
    normalize_tags,
    options_payload,
    province_catalog,
    province_region,
    resolve_customer_tag,
    star_level_from_tags,
    supplier_type_label,
)
from services.time_utils import now_local


router = APIRouter(prefix="/master-data", tags=["master-data"])


def _next_available_master_data_code(db: Session, *, model, column, prefix: str, sequence_key: str) -> str:
    """生成档案编号，并跳过历史人工录入造成的同名编号。"""
    for _ in range(100):
        code = next_business_no(db, prefix, sequence_key=sequence_key)
        existing = db.execute(select(model.id).where(column == code)).scalar_one_or_none()
        if existing is None:
            return code
    raise HTTPException(status_code=500, detail="自动生成编号失败，请联系管理员")


@router.get("/settings")
def list_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(select(AppSetting).where(AppSetting.setting_key == "purchase_approval_amount_threshold")).scalar_one_or_none()
    if row is None:
        row = AppSetting(
            setting_key="purchase_approval_amount_threshold",
            setting_value="50000",
            description="采购订单审批金额阈值，订单总金额达到或超过该值时需要经理审批",
        )
        db.add(row)
        db.commit()
    return api_response(
        data={
            "purchase_approval_amount_threshold": float(Decimal(str(row.setting_value or "0"))),
            "purchase_approval_amount_threshold_description": row.description,
        }
    )


@router.put("/settings/purchase-approval-threshold")
def update_purchase_approval_threshold(
    payload: dict,
    current_user: User = Depends(require_roles("purchase", "manager", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    amount = Decimal(str(payload.get("amount", 0)))
    if amount < 0:
        raise HTTPException(status_code=409, detail="采购审批金额阈值不能小于 0")
    row = db.execute(select(AppSetting).where(AppSetting.setting_key == "purchase_approval_amount_threshold")).scalar_one_or_none()
    if row is None:
        row = AppSetting(
            setting_key="purchase_approval_amount_threshold",
            setting_value=str(amount),
            description="采购订单审批金额阈值，订单总金额达到或超过该值时需要经理审批",
        )
        db.add(row)
    else:
        row.setting_value = str(amount)
    db.commit()
    return api_response(data={"purchase_approval_amount_threshold": float(amount)})


def _load_customer_tag_map(db: Session, customer_ids: list[int]) -> dict[int, list[str]]:
    """批量取客户标签，避免列表页 N+1 查询。"""
    if not customer_ids:
        return {}
    rows = db.execute(
        select(CustomerTag.customer_id, CustomerTag.tag)
        .where(CustomerTag.customer_id.in_(customer_ids))
        .order_by(CustomerTag.id.asc())
    ).all()
    tag_map: dict[int, list[str]] = {}
    for customer_id, tag in rows:
        tag_map.setdefault(customer_id, []).append(tag)
    return tag_map


def _load_supplier_tag_map(db: Session, supplier_ids: list[int]) -> dict[int, list[str]]:
    if not supplier_ids:
        return {}
    rows = db.execute(
        select(SupplierTag.supplier_id, SupplierTag.tag)
        .where(SupplierTag.supplier_id.in_(supplier_ids))
        .order_by(SupplierTag.id.asc())
    ).all()
    tag_map: dict[int, list[str]] = {}
    for supplier_id, tag in rows:
        tag_map.setdefault(supplier_id, []).append(tag)
    return tag_map


def _sync_customer_tags(db: Session, customer: Customer, tags: list[str] | None, current_user: User) -> None:
    """按提交的标签集合做增量同步。销售端不可自行增删公司统一维护的价值标签。"""
    if tags is None:
        return
    desired = normalize_tags(tags)
    existing_rows = db.execute(select(CustomerTag).where(CustomerTag.customer_id == customer.id)).scalars().all()
    existing = {row.tag: row for row in existing_rows}
    role_code = current_user.role.code if current_user.role else ""
    company_managed_locked = role_code == "sales"

    if company_managed_locked:
        # 销售提交的集合里，公司标以库中现状为准，防止误删/误加
        desired = [tag for tag in desired if tag not in COMPANY_MANAGED_CUSTOMER_TAGS]
        desired += [tag for tag in existing if tag in COMPANY_MANAGED_CUSTOMER_TAGS]

    for tag in desired:
        if tag not in existing:
            db.add(
                CustomerTag(
                    customer_id=customer.id,
                    tag=tag,
                    dimension=customer_tag_dimension(tag),
                    created_by_id=current_user.id,
                )
            )
    for tag, row in existing.items():
        if tag not in desired:
            db.delete(row)
        elif row.dimension != customer_tag_dimension(tag):
            row.dimension = customer_tag_dimension(tag)

    # 客户价值标签同步回 star_level，保持旧字段与既有筛选可用
    star_level = star_level_from_tags(desired)
    if star_level:
        customer.star_level = star_level


def _sync_supplier_tags(db: Session, supplier: Supplier, tags: list[str] | None, current_user: User) -> None:
    if tags is None:
        return
    desired = normalize_tags(tags)
    existing_rows = db.execute(select(SupplierTag).where(SupplierTag.supplier_id == supplier.id)).scalars().all()
    existing = {row.tag: row for row in existing_rows}
    for tag in desired:
        if tag not in existing:
            db.add(SupplierTag(supplier_id=supplier.id, tag=tag, created_by_id=current_user.id))
    for tag, row in existing.items():
        if tag not in desired:
            db.delete(row)


def _serialize_customer(row: Customer, order_count: int = 0, tags: list[str] | None = None) -> dict:
    tag_codes = tags if tags is not None else []
    tag_details = [resolve_customer_tag(code) for code in tag_codes]
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "contact_name": row.contact_name,
        "phone": row.phone,
        "address": row.address,
        "star_level": row.star_level,
        "unified_social_credit_code": row.unified_social_credit_code,
        "source_type": row.source_type,
        "source_type_label": "公司转交" if row.source_type == "company_transfer" else "个人开发",
        "customer_type": row.customer_type,
        "customer_type_label": customer_type_label(row.customer_type),
        "region_type": row.region_type,
        "region_type_label": customer_region_label(row.region_type),
        "region_province": row.region_province,
        "region_group": province_region(row.region_province),
        "remark": row.remark,
        "last_transferred_at": row.last_transferred_at.isoformat() if row.last_transferred_at else None,
        "churn_repurchase_at": row.churn_repurchase_at.isoformat() if row.churn_repurchase_at else None,
        "last_order_at": row.last_order_at.isoformat() if row.last_order_at else None,
        "tags": tag_codes,
        "tag_details": tag_details,
        "tag_labels": [item["label"] for item in tag_details],
        "owner_user_id": row.owner_user_id,
        "owner_user_name": row.owner_user.full_name if row.owner_user else None,
        "created_by_id": row.created_by_id,
        "created_by_name": row.created_by.full_name if row.created_by else None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "order_count": order_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_customer_complaint(row: CustomerComplaint) -> dict:
    return {
        "id": row.id,
        "complaint_no": row.complaint_no,
        "customer_id": row.customer_id,
        "customer_name": row.customer.name if row.customer else None,
        "complainant_name": row.complainant_name,
        "complainant_phone": row.complainant_phone,
        "complainant_address": row.complainant_address,
        "complaint_reason": row.complaint_reason,
        "complaint_detail": row.complaint_detail,
        "status": row.status,
        "status_label": {"pending": "待处理", "processing": "处理中", "resolved": "已解决", "closed": "已关闭"}.get(row.status, row.status),
        "handled_result": row.handled_result,
        "handled_by_id": row.handled_by_id,
        "handled_at": row.handled_at.isoformat() if row.handled_at else None,
        "created_by_id": row.created_by_id,
        "created_by_name": row.created_by.full_name if row.created_by else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_supplier(row: Supplier, order_count: int = 0, tags: list[str] | None = None) -> dict:
    accounts = sorted((a for a in row.financial_accounts if a.is_active), key=lambda a: a.id, reverse=True)
    account = accounts[0] if accounts else None
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "contact_name": row.contact_name,
        "phone": row.phone,
        "address": row.address,
        "status": row.status,
        "status_label": "启用" if row.status == "enabled" else "停用",
        "unified_social_credit_code": row.unified_social_credit_code,
        "supplier_type": row.supplier_type,
        "supplier_type_label": supplier_type_label(row.supplier_type),
        "website": row.website,
        "bank_name": account.bank_name if account else None,
        "bank_account": account.account_number if account else None,
        "remark": row.remark,
        "tags": tags if tags is not None else [],
        "purchase_order_count": order_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _latest_supplier_catalog_price_map(db: Session, supplier_id: int) -> dict[tuple[str, int], dict]:
    rows = db.execute(
        select(PurchaseOrderItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.supplier_id == supplier_id, PurchaseOrder.status != "cancelled")
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrderItem.id.desc())
    ).all()
    result: dict[tuple[str, int], dict] = {}
    for item, order in rows:
        if item.product_id:
            key = ("product", item.product_id)
        elif item.material_id:
            key = ("material", item.material_id)
        else:
            continue
        if key in result:
            continue
        result[key] = {
            "latest_price": float(item.unit_price or 0),
            "latest_price_at": order.created_at.isoformat() if order.created_at else None,
            "latest_order_no": order.order_no,
        }
    return result


def _serialize_supplier_catalog_item(row: SupplierCatalogItem, latest_price_map: dict[tuple[str, int], dict] | None = None) -> dict:
    item_type = "product" if row.product_id else "material"
    item_id = row.product_id or row.material_id
    latest = (latest_price_map or {}).get((item_type, item_id), {})
    return {
        "id": row.id,
        "supplier_id": row.supplier_id,
        "product_id": row.product_id,
        "product_name": row.product.name if row.product else None,
        "product_code": row.product.product_code if row.product else None,
        "material_id": row.material_id,
        "material_name": row.material.name if row.material else None,
        "material_code": row.material.material_code if row.material else None,
        "item_type": item_type,
        "item_name": row.product.name if row.product else (row.material.name if row.material else None),
        "item_code": row.product.product_code if row.product else (row.material.material_code if row.material else None),
        "latest_price": latest.get("latest_price"),
        "latest_price_at": latest.get("latest_price_at"),
        "latest_order_no": latest.get("latest_order_no"),
        "remark": row.remark,
    }


def _serialize_packaging(row: PackagingConfig | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "packaging_name": row.packaging_name,
        "kg_per_bag": float(row.kg_per_bag),
        "unit_price": float(row.unit_price or 0),
        "specification_text": packaging_spec_text(row.kg_per_bag),
        "is_default": row.is_default,
        "is_active": row.is_active,
    }


def _load_packaging_map(db: Session, *, entity_type: str, entity_ids: list[int] | None = None) -> dict[int, PackagingConfig]:
    filters = [PackagingConfig.entity_type == entity_type, PackagingConfig.is_active.is_(True)]
    if entity_ids is not None:
        filters.append(PackagingConfig.entity_id.in_(entity_ids or [-1]))
    rows = db.execute(
        select(PackagingConfig)
        .where(*filters)
        .order_by(PackagingConfig.is_default.desc(), PackagingConfig.id.asc())
    ).scalars().all()
    packaging_map: dict[int, PackagingConfig] = {}
    for row in rows:
        packaging_map.setdefault(row.entity_id, row)
    return packaging_map


def _load_stock_map(db: Session, *, item_type: str, item_ids: list[int] | None = None) -> dict[int, dict]:
    group_field = Inventory.product_id if item_type == "product" else Inventory.material_id
    warehouse_type = "finished" if item_type == "product" else "raw"
    filters = [group_field.is_not(None), Warehouse.warehouse_type == warehouse_type]
    if item_ids is not None:
        filters.append(group_field.in_(item_ids or [-1]))
    rows = db.scalars(select(Inventory).join(Warehouse, Warehouse.id == Inventory.warehouse_id).where(*filters)).all()
    stock_map = {}
    transit_map = purchase_in_transit_map(db) if item_type == "product" else {}
    for row in rows:
        item_id = row.product_id if item_type == "product" else row.material_id
        stock = stock_map.setdefault(item_id, {"quantity": 0, "reserved_quantity": 0,
                                               "available_quantity": 0, "expected_available_quantity": 0})
        stock["quantity"] += Decimal(row.quantity or 0)
        stock["reserved_quantity"] += Decimal(row.reserved_quantity or 0)
        stock["available_quantity"] += placeable_of(row)
        stock["expected_available_quantity"] += replenishment_available_of(row)
    for item_id, stock in stock_map.items():
        if item_type == "product":
            stock["expected_available_quantity"] += transit_map.get(stock_key(product_id=item_id), Decimal("0"))
        for field, value in stock.items():
            stock[field] = float(value)
    return stock_map


@router.get("/departments")
def list_departments(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Department).order_by(Department.sort_order.asc(), Department.id.asc())).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "parent_id": row.parent_id,
                "unit_type": row.unit_type,
                "is_active": row.is_active,
                "warehouse_scope": row.warehouse_scope,
                "path_name": " / ".join(get_department_path_names(db, row.id)),
            }
            for row in rows
        ]
    )


@router.get("/roles")
def list_roles(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Role).order_by(Role.id)).scalars().all()
    return api_response(data=[{"id": row.id, "code": row.code, "name": row.name, "is_admin": row.is_admin} for row in rows])


@router.get("/warehouses")
def list_warehouses(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Warehouse).order_by(Warehouse.id)).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "type": row.warehouse_type,
                "location": row.location,
            }
            for row in rows
        ]
    )


@router.get("/customer-tag-catalog")
def get_customer_tag_catalog(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """客户标签字典（销帮帮标签表 KP-44-0818）+ 客户档案枚举，供前端下拉渲染。

    同时返回库内已出现的自定义标签，方便销售复用同事已经用过的说法。
    """
    known_codes = set()
    for group in customer_tag_catalog():
        known_codes.update(option["code"] for option in group["options"])
    used_tags = db.execute(select(CustomerTag.tag).distinct()).scalars().all()
    custom_tags = sorted({tag for tag in used_tags if tag not in known_codes})
    return api_response(
        data={
            "dimensions": customer_tag_catalog(),
            "custom_tags": custom_tags,
            "customer_types": options_payload(CUSTOMER_TYPE_OPTIONS),
            "region_types": options_payload(CUSTOMER_REGION_OPTIONS),
            "provinces": province_catalog(),
            "company_managed_tags": sorted(COMPANY_MANAGED_CUSTOMER_TAGS),
        }
    )


@router.get("/supplier-tag-catalog")
def get_supplier_tag_catalog(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """供应商类型枚举 + 库内已用过的供应商标签（自由文本标签，便于复用）。"""
    used_tags = sorted(set(db.execute(select(SupplierTag.tag).distinct()).scalars().all()))
    return api_response(
        data={
            "supplier_types": options_payload(SUPPLIER_TYPE_OPTIONS),
            "used_tags": used_tags,
        }
    )


@router.get("/customers")
def list_customers(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = None,
    source_type: str | None = None,
    owner_user_id: int | None = None,
    star_level: str | None = None,
    customer_type: str | None = None,
    region_type: str | None = None,
    region_province: str | None = None,
    churn_repurchase_only: bool = False,
    tags: str | None = Query(default=None, description="标签 code，多个用英文逗号分隔，命中任意一个即返回"),
    start_date: date | None = None,
    end_date: date | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(Customer)
        .options(joinedload(Customer.owner_user), joinedload(Customer.created_by))
        .where(Customer.archived_at.is_(None))
        .order_by(Customer.created_at.desc())
    )
    role_code = current_user.role.code if current_user.role else ""
    if role_code == "sales":
        query = query.where(Customer.owner_user_id == current_user.id)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.where(or_(Customer.name.ilike(pattern), Customer.contact_name.ilike(pattern), Customer.phone.ilike(pattern), Customer.code.ilike(pattern), Customer.unified_social_credit_code.ilike(pattern), Customer.address.ilike(pattern)))
    if source_type:
        query = query.where(Customer.source_type == source_type)
    if owner_user_id:
        query = query.where(Customer.owner_user_id == owner_user_id)
    if star_level:
        query = query.where(Customer.star_level.ilike(f"%{star_level}%"))
    if customer_type:
        query = query.where(Customer.customer_type == customer_type)
    if region_type:
        query = query.where(Customer.region_type == region_type)
    if region_province:
        query = query.where(Customer.region_province == region_province)
    if churn_repurchase_only:
        query = query.where(Customer.churn_repurchase_at.is_not(None))
    tag_filters = [item.strip() for item in (tags or "").split(",") if item.strip()]
    if tag_filters:
        query = query.where(
            Customer.id.in_(select(CustomerTag.customer_id).where(CustomerTag.tag.in_(tag_filters)))
        )
    if start_date:
        query = query.where(Customer.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.where(Customer.created_at < datetime.combine(end_date, datetime.max.time()))
    total = None
    if page is not None:
        total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(query).scalars().all()
    customer_ids = [row.id for row in rows]
    order_counts = {
        customer_id: count
        for customer_id, count in db.execute(
            select(SalesOrder.customer_id, func.count(SalesOrder.id)).where(SalesOrder.customer_id.in_(customer_ids or [-1])).group_by(SalesOrder.customer_id)
        ).all()
    }
    tag_map = _load_customer_tag_map(db, customer_ids)
    items = [_serialize_customer(row, order_counts.get(row.id, 0), tag_map.get(row.id, [])) for row in rows]
    if page is None:
        return api_response(data=items)
    return api_response(data={"items": items, "pagination": {"page": page, "page_size": page_size, "total": total}})


@router.post("/customers")
def create_customer(
    payload: CustomerCreate,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    existing = db.execute(select(Customer).where(Customer.name == payload.name)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="客户名称已存在")
    credit_code = (payload.unified_social_credit_code or "").strip() or None
    if credit_code:
        duplicate_code = db.execute(select(Customer).where(Customer.unified_social_credit_code == credit_code)).scalar_one_or_none()
        if duplicate_code:
            raise HTTPException(status_code=409, detail="该统一社会信用代码对应客户已存在，不能重复建档")
    role_code = current_user.role.code if current_user.role else ""
    owner_user_id = payload.owner_user_id
    source_type = payload.source_type
    if role_code == "sales":
        owner_user_id = current_user.id
        source_type = "personal_development"
    elif owner_user_id is None:
        owner_user_id = current_user.id
    customer = Customer(
        code=next_business_no(db, "KH"),
        name=payload.name,
        contact_name=payload.contact_name,
        phone=payload.phone,
        address=payload.address,
        star_level=payload.star_level,
        unified_social_credit_code=credit_code,
        source_type=source_type,
        customer_type=payload.customer_type,
        region_type=payload.region_type,
        region_province=payload.region_province,
        remark=(payload.remark or "").strip() or None,
        owner_user_id=owner_user_id,
        created_by_id=current_user.id,
    )
    db.add(customer)
    db.flush()
    _sync_customer_tags(db, customer, payload.tags, current_user)
    db.commit()
    db.refresh(customer)
    return api_response(data=_serialize_customer(customer, 0, _load_customer_tag_map(db, [customer.id]).get(customer.id, [])))


@router.put("/customers/{customer_id}")
def update_customer(
    customer_id: int,
    payload: CustomerCreate,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    duplicate = db.execute(select(Customer).where(Customer.name == payload.name, Customer.id != customer_id)).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="客户名称已存在")
    credit_code = (payload.unified_social_credit_code or "").strip() or None
    if credit_code:
        duplicate_code = db.execute(
            select(Customer).where(Customer.unified_social_credit_code == credit_code, Customer.id != customer_id)
        ).scalar_one_or_none()
        if duplicate_code:
            raise HTTPException(status_code=409, detail="该统一社会信用代码对应客户已存在，不能重复建档")
    role_code = current_user.role.code if current_user.role else ""
    if role_code == "sales" and customer.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="销售只能维护本人负责的客户")
    system_managed_fields = {"last_transferred_at", "churn_repurchase_at", "last_order_at"}
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "tags" or field in system_managed_fields:
            continue
        if field == "unified_social_credit_code":
            value = credit_code
        if field == "remark":
            value = (value or "").strip() or None
        if role_code == "sales" and field in {"owner_user_id", "source_type"}:
            continue
        setattr(customer, field, value)
    _sync_customer_tags(db, customer, payload.tags, current_user)
    db.commit()
    db.refresh(customer)
    order_count = db.execute(select(func.count(SalesOrder.id)).where(SalesOrder.customer_id == customer.id)).scalar_one()
    return api_response(
        msg="客户已更新",
        data=_serialize_customer(customer, order_count, _load_customer_tag_map(db, [customer.id]).get(customer.id, [])),
    )


@router.post("/customers/{customer_id}/transfer")
def transfer_customer(
    customer_id: int,
    payload: CustomerTransferCreate,
    current_user: User = Depends(require_roles("manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    target_user = db.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(User.id == payload.to_user_id, Role.code == "sales", User.is_active.is_(True))
    ).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=404, detail="转交目标销售不存在")
    record = CustomerTransferRecord(
        customer_id=customer.id,
        from_user_id=customer.owner_user_id,
        to_user_id=target_user.id,
        transferred_by_id=current_user.id,
        reason=payload.reason,
    )
    customer.owner_user_id = target_user.id
    customer.source_type = "company_transfer"
    register_customer_transfer(db, customer=customer, transferred_at=record.transferred_at)
    db.add(record)
    create_notification(
        db,
        title="客户已转移",
        content=f"客户 {customer.name} 已转交给 {target_user.full_name}。",
        module="master_data",
        recipient_user_ids=[target_user.id],
        created_by_id=current_user.id,
        category="info",
    )
    db.commit()
    db.refresh(customer)
    return api_response(data=_serialize_customer(customer, 0, _load_customer_tag_map(db, [customer.id]).get(customer.id, [])))


def _customer_has_relations(db: Session, customer_id: int) -> bool:
    return (
        db.execute(select(SalesOrder.id).where(SalesOrder.customer_id == customer_id)).first() is not None
        or db.execute(select(FinancialAccount.id).where(FinancialAccount.customer_id == customer_id)).first() is not None
    )


@router.delete("/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "master_data_admin")),
    db: Session = Depends(get_db),
):
    customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if _customer_has_relations(db, customer_id):
        raise HTTPException(status_code=409, detail="客户已关联销售订单或收付款账户，不可删除")
    db.execute(delete(CustomerTag).where(CustomerTag.customer_id == customer_id))
    db.delete(customer)
    db.commit()
    return api_response(msg="客户已删除")


@router.get("/customer-complaints")
def list_customer_complaints(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(CustomerComplaint)
        .options(joinedload(CustomerComplaint.customer), joinedload(CustomerComplaint.created_by), joinedload(CustomerComplaint.handled_by))
        .order_by(CustomerComplaint.created_at.desc(), CustomerComplaint.id.desc())
    ).unique().scalars().all()
    return api_response(data=[_serialize_customer_complaint(row) for row in rows])


@router.post("/customer-complaints")
def create_customer_complaint(
    payload: CustomerComplaintCreate,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    complaint = CustomerComplaint(
        complaint_no=next_business_no(db, "TS"),
        customer_id=payload.customer_id,
        complainant_name=payload.complainant_name.strip(),
        complainant_phone=payload.complainant_phone.strip(),
        complainant_address=(payload.complainant_address or "").strip() or None,
        complaint_reason=payload.complaint_reason.strip(),
        complaint_detail=(payload.complaint_detail or "").strip() or None,
        status="pending",
        created_by_id=current_user.id,
    )
    db.add(complaint)
    db.flush()
    manager_user_ids = _role_user_ids(db, "manager")
    for user_id in manager_user_ids:
        create_todo(
            db,
            title=f"待处理客户投诉 {complaint.complaint_no}",
            description=f"客户 {customer.name} 已录入投诉：{complaint.complaint_reason}",
            module="master_data",
            assigned_user_id=user_id,
            business_type="customer_complaint",
            business_id=complaint.id,
            priority="high",
        )
    create_notification(
        db,
        title="客户投诉已录入",
        content=f"客户 {customer.name} 已录入投诉 {complaint.complaint_no}：{complaint.complaint_reason}",
        module="master_data",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *manager_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="master_data",
        action="create_customer_complaint",
        content=f"录入客户投诉 {complaint.complaint_no}",
        after_data={"complaint_no": complaint.complaint_no, "customer_id": customer.id, "reason": complaint.complaint_reason},
    )
    db.commit()
    db.refresh(complaint)
    return api_response(data=_serialize_customer_complaint(complaint))


@router.put("/customer-complaints/{complaint_id}")
def update_customer_complaint(
    complaint_id: int,
    payload: CustomerComplaintUpdate,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    complaint = db.execute(select(CustomerComplaint).where(CustomerComplaint.id == complaint_id)).scalar_one_or_none()
    if complaint is None:
        raise HTTPException(status_code=404, detail="客户投诉不存在")
    complaint.status = payload.status
    complaint.handled_result = (payload.handled_result or "").strip() or None
    complaint.handled_by_id = current_user.id
    complaint.handled_at = now_local()
    write_operation_log(
        db,
        user_id=current_user.id,
        module="master_data",
        action="update_customer_complaint",
        content=f"更新客户投诉 {complaint.complaint_no}",
        after_data={"complaint_id": complaint.id, "status": complaint.status},
    )
    db.commit()
    db.refresh(complaint)
    return api_response(data=_serialize_customer_complaint(complaint))


@router.get("/suppliers")
def list_suppliers(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Supplier).options(selectinload(Supplier.financial_accounts)).order_by(Supplier.created_at.desc())).scalars().all()
    order_counts = {
        supplier_id: count
        for supplier_id, count in db.execute(
            select(PurchaseOrder.supplier_id, func.count(PurchaseOrder.id)).group_by(PurchaseOrder.supplier_id)
        ).all()
    }
    catalog_counts = {
        supplier_id: count
        for supplier_id, count in db.execute(
            select(SupplierCatalogItem.supplier_id, func.count(SupplierCatalogItem.id)).group_by(SupplierCatalogItem.supplier_id)
        ).all()
    }
    tag_map = _load_supplier_tag_map(db, [row.id for row in rows])
    return api_response(
        data=[
            {
                **_serialize_supplier(row, order_counts.get(row.id, 0), tag_map.get(row.id, [])),
                "catalog_item_count": catalog_counts.get(row.id, 0),
            }
            for row in rows
        ]
    )


@router.post("/suppliers")
def create_supplier(
    payload: SupplierCreate,
    current_user: User = Depends(require_roles("purchase", "finance", "finance_super", "master_data_admin")),
    db: Session = Depends(get_db),
):
    existing = db.execute(select(Supplier).where(Supplier.name == payload.name)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="供应商已存在")
    credit_code = (payload.unified_social_credit_code or "").strip() or None
    if credit_code:
        duplicate_code = db.execute(
            select(Supplier).where(Supplier.unified_social_credit_code == credit_code)
        ).scalar_one_or_none()
        if duplicate_code:
            raise HTTPException(status_code=409, detail="该统一社会信用代码对应供应商已存在，不能重复建档")
    supplier = Supplier(
        code=next_business_no(db, "GY"),
        name=payload.name,
        contact_name=payload.contact_name,
        phone=payload.phone,
        address=payload.address,
        status=payload.status,
        unified_social_credit_code=credit_code,
        supplier_type=payload.supplier_type,
        website=(payload.website or "").strip() or None,

        remark=(payload.remark or "").strip() or None,
        created_by_id=current_user.id,
    )
    db.add(supplier)
    db.flush()
    if (payload.bank_name or "").strip() and (payload.bank_account or "").strip():
        existing_account = db.scalar(select(FinancialAccount.id).where(
            FinancialAccount.supplier_id == supplier.id,
            FinancialAccount.bank_name == payload.bank_name.strip(),
            FinancialAccount.account_number == payload.bank_account.strip()))
        if not existing_account:
            db.add(FinancialAccount(owner_type="supplier", supplier_id=supplier.id,
                                    account_name=supplier.name, bank_name=payload.bank_name.strip(),
                                    account_number=payload.bank_account.strip(), currency="CNY"))
    _sync_supplier_tags(db, supplier, payload.tags, current_user)
    db.commit()
    db.refresh(supplier)
    return api_response(data=_serialize_supplier(supplier, 0, _load_supplier_tag_map(db, [supplier.id]).get(supplier.id, [])))


@router.put("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: int,
    payload: SupplierCreate,
    current_user: User = Depends(require_roles("purchase", "finance", "finance_super", "master_data_admin")),
    db: Session = Depends(get_db),
):
    supplier = db.execute(select(Supplier).where(Supplier.id == supplier_id)).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    duplicate = db.execute(select(Supplier).where(Supplier.name == payload.name, Supplier.id != supplier_id)).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="供应商名称已存在")
    credit_code = (payload.unified_social_credit_code or "").strip() or None
    if credit_code:
        duplicate_code = db.execute(
            select(Supplier).where(Supplier.unified_social_credit_code == credit_code, Supplier.id != supplier_id)
        ).scalar_one_or_none()
        if duplicate_code:
            raise HTTPException(status_code=409, detail="该统一社会信用代码对应供应商已存在，不能重复建档")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field in {"tags", "bank_name", "bank_account"}:
            continue
        if field == "unified_social_credit_code":
            value = credit_code
        elif field in {"website", "bank_name", "bank_account", "remark"}:
            value = (value or "").strip() or None
        setattr(supplier, field, value)
    if (payload.bank_name or "").strip() and (payload.bank_account or "").strip():
        existing_account = db.scalar(select(FinancialAccount.id).where(
            FinancialAccount.supplier_id == supplier.id,
            FinancialAccount.bank_name == payload.bank_name.strip(),
            FinancialAccount.account_number == payload.bank_account.strip()))
        if not existing_account:
            db.add(FinancialAccount(owner_type="supplier", supplier_id=supplier.id,
                                    account_name=supplier.name, bank_name=payload.bank_name.strip(),
                                    account_number=payload.bank_account.strip(), currency="CNY"))
    _sync_supplier_tags(db, supplier, payload.tags, current_user)
    db.commit()
    db.refresh(supplier)
    order_count = db.execute(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.supplier_id == supplier.id)).scalar_one()
    return api_response(
        msg="供应商已更新",
        data=_serialize_supplier(supplier, order_count, _load_supplier_tag_map(db, [supplier.id]).get(supplier.id, [])),
    )


@router.get("/suppliers/{supplier_id}/catalog")
def get_supplier_catalog(
    supplier_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    supplier = db.execute(select(Supplier).where(Supplier.id == supplier_id)).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    rows = db.execute(
        select(SupplierCatalogItem)
        .options(joinedload(SupplierCatalogItem.product), joinedload(SupplierCatalogItem.material))
        .where(SupplierCatalogItem.supplier_id == supplier_id)
        .order_by(SupplierCatalogItem.id.asc())
    ).scalars().all()
    latest_price_map = _latest_supplier_catalog_price_map(db, supplier_id)
    purchase_order_count = db.execute(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.supplier_id == supplier.id)).scalar_one()
    return api_response(
        data={
            "supplier": _serialize_supplier(supplier, purchase_order_count, _load_supplier_tag_map(db, [supplier.id]).get(supplier.id, [])),
            "items": [_serialize_supplier_catalog_item(row, latest_price_map) for row in rows],
        }
    )


@router.get("/suppliers/{supplier_id}/catalog-price-history")
def get_supplier_catalog_price_history(
    supplier_id: int,
    item_type: str,
    item_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if item_type not in {"product", "material"}:
        raise HTTPException(status_code=409, detail="价格历史类型必须是产品或原料")
    supplier = db.execute(select(Supplier).where(Supplier.id == supplier_id)).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    item_filter = PurchaseOrderItem.product_id == item_id if item_type == "product" else PurchaseOrderItem.material_id == item_id
    rows = db.execute(
        select(PurchaseOrderItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.supplier_id == supplier_id, PurchaseOrder.status != "cancelled", item_filter)
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrderItem.id.desc())
    ).all()
    return api_response(
        data={
            "supplier": _serialize_supplier(supplier),
            "item_type": item_type,
            "item_id": item_id,
            "history": [
                {
                    "purchase_order_id": order.id,
                    "purchase_order_no": order.order_no,
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "total_amount": float(item.total_amount or 0),
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                }
                for item, order in rows
            ],
        }
    )


@router.put("/suppliers/{supplier_id}/catalog")
def replace_supplier_catalog(
    supplier_id: int,
    payload: list[SupplierCatalogItemInput],
    current_user: User = Depends(require_roles("purchase", "finance", "finance_super", "master_data_admin")),
    db: Session = Depends(get_db),
):
    supplier = db.execute(select(Supplier).where(Supplier.id == supplier_id)).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")

    seen_keys: set[tuple[str, int]] = set()
    normalized_rows: list[dict] = []
    for item in payload:
        if bool(item.product_id) == bool(item.material_id):
            raise HTTPException(status_code=409, detail="名录项必须且只能选择一个产品或原料")
        if item.product_id:
            if db.execute(select(Product.id).where(Product.id == item.product_id)).scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="名录中的产品不存在")
            dedupe_key = ("product", item.product_id)
        else:
            if db.execute(select(Material.id).where(Material.id == item.material_id)).scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="名录中的原料不存在")
            dedupe_key = ("material", item.material_id)
        if dedupe_key in seen_keys:
            raise HTTPException(status_code=409, detail="供应商名录中存在重复条目")
        seen_keys.add(dedupe_key)
        normalized_rows.append(item.model_dump())

    for row in db.execute(select(SupplierCatalogItem).where(SupplierCatalogItem.supplier_id == supplier_id)).scalars().all():
        db.delete(row)
    db.flush()

    for row in normalized_rows:
        db.add(SupplierCatalogItem(supplier_id=supplier_id, **row))

    db.commit()
    rows = db.execute(
        select(SupplierCatalogItem)
        .options(joinedload(SupplierCatalogItem.product), joinedload(SupplierCatalogItem.material))
        .where(SupplierCatalogItem.supplier_id == supplier_id)
        .order_by(SupplierCatalogItem.id.asc())
    ).scalars().all()
    latest_price_map = _latest_supplier_catalog_price_map(db, supplier_id)
    return api_response(msg="供应商可供货名录已更新", data=[_serialize_supplier_catalog_item(row, latest_price_map) for row in rows])


def _supplier_has_relations(db: Session, supplier_id: int) -> bool:
    return (
        db.execute(select(PurchaseOrder.id).where(PurchaseOrder.supplier_id == supplier_id)).first() is not None
        or db.execute(select(FinancialAccount.id).where(FinancialAccount.supplier_id == supplier_id)).first() is not None
    )


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(
    supplier_id: int,
    current_user: User = Depends(require_roles("purchase", "finance", "finance_super", "master_data_admin")),
    db: Session = Depends(get_db),
):
    supplier = db.execute(select(Supplier).where(Supplier.id == supplier_id)).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    if _supplier_has_relations(db, supplier_id):
        supplier.status = "disabled"
        db.commit()
        return api_response(msg="供应商已停用，因存在业务引用未执行物理删除", data={"id": supplier.id, "disabled": True})
    db.execute(delete(SupplierTag).where(SupplierTag.supplier_id == supplier_id))
    db.delete(supplier)
    db.commit()
    return api_response(msg="供应商已删除")


@router.get("/products")
def list_products(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = None,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Product).order_by(Product.created_at.desc(), Product.id.desc())
    if active_only:
        query = query.where(Product.is_active.is_(True))
    if keyword and keyword.strip():
        term = keyword.strip()
        query = query.where(or_(Product.product_code.contains(term, autoescape=True),
                                Product.name.contains(term, autoescape=True),
                                Product.specification.contains(term, autoescape=True)))
    total = None
    if page is not None:
        total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(query).scalars().all()
    product_ids = [row.id for row in rows]
    packaging_map = _load_packaging_map(db, entity_type="product", entity_ids=product_ids)
    stock_map = _load_stock_map(db, item_type="product", item_ids=product_ids)
    items = [
            {
                "id": row.id,
                "product_code": row.product_code,
                "name": row.name,
                "price": float(row.price),
                "fulfillment_mode": row.fulfillment_mode,
                "fulfillment_mode_label": "生产型" if row.fulfillment_mode == "production" else "贸易型",
                "specification": packaging_spec_text(packaging_map[row.id].kg_per_bag) if row.id in packaging_map else row.specification,
                "unit": row.unit,
                "safety_stock_qty": float(row.safety_stock_qty),
                "purchase_threshold_qty": float(row.purchase_threshold_qty),
                "is_active": row.is_active,
                "packaging": (
                    lambda packaging: (
                        {**packaging, "packaging_name": get_global_packaging_name(db, packaging["kg_per_bag"]) or packaging["packaging_name"]}
                        if packaging and packaging_name_needs_repair(packaging.get("packaging_name"))
                        else packaging
                    )
                )(_serialize_packaging(packaging_map.get(row.id))),
                "stock": stock_map.get(row.id, {"quantity": 0, "reserved_quantity": 0, "available_quantity": 0}),
            }
            for row in rows
        ]
    if page is None:
        return api_response(data=items)
    return api_response(data={"items": items, "pagination": {"page": page, "page_size": page_size, "total": total}})


@router.get("/standard-prices")
def list_standard_prices(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Product).where(Product.is_active.is_(True)).order_by(Product.product_code.asc(), Product.id.asc())).scalars().all()
    latest_logs = db.execute(
        select(StandardPriceChangeLog)
        .order_by(StandardPriceChangeLog.created_at.desc(), StandardPriceChangeLog.id.desc())
    ).scalars().all()
    log_map: dict[int, StandardPriceChangeLog] = {}
    for row in latest_logs:
        log_map.setdefault(row.product_id, row)
    return api_response(
        data=[
            {
                "product_id": row.id,
                "product_code": row.product_code,
                "product_name": row.name,
                "specification": row.specification,
                "unit": row.unit,
                "standard_price": float(row.price or 0),
                "is_active": row.is_active,
                "latest_change_remark": log_map[row.id].remark if row.id in log_map else None,
                "latest_changed_at": log_map[row.id].created_at.isoformat() if row.id in log_map and log_map[row.id].created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/standard-prices/{product_id}/history")
def get_standard_price_history(product_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    rows = db.execute(
        select(StandardPriceChangeLog)
        .options(joinedload(StandardPriceChangeLog.changed_by))
        .where(StandardPriceChangeLog.product_id == product_id)
        .order_by(StandardPriceChangeLog.created_at.desc(), StandardPriceChangeLog.id.desc())
    ).scalars().all()
    return api_response(
        data={
            "product": {
                "id": product.id,
                "product_code": product.product_code,
                "name": product.name,
                "price": float(product.price or 0),
            },
            "history": [
                {
                    "id": row.id,
                    "old_price": float(row.old_price or 0),
                    "new_price": float(row.new_price or 0),
                    "remark": row.remark,
                    "changed_by_name": row.changed_by.full_name if row.changed_by else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    )


@router.put("/standard-prices/{product_id}")
def update_standard_price(
    product_id: int,
    payload: StandardPriceUpdate,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    old_price = Decimal(product.price or 0)
    new_price = Decimal(payload.price)
    if old_price == new_price:
        return api_response(msg="标准价格未变化", data={"product_id": product.id, "standard_price": float(product.price or 0)})
    product.price = new_price
    db.add(
        StandardPriceChangeLog(
            product_id=product.id,
            old_price=old_price,
            new_price=new_price,
            remark=payload.remark.strip(),
            changed_by_id=current_user.id,
        )
    )
    recipient_user_ids = list(dict.fromkeys([*_role_user_ids(db, "sales", "manager", "chairman")]))
    create_notification(
        db,
        title="标准价格表已更新",
        content=f"产品 {product.name} 标准价格由 {old_price} 调整为 {new_price}。原因：{payload.remark.strip()}",
        module="sales",
        recipient_user_ids=recipient_user_ids,
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="update_standard_price",
        content=f"更新标准价格 {product.name}",
        before_data={"product_id": product.id, "price": float(old_price)},
        after_data={"product_id": product.id, "price": float(new_price), "remark": payload.remark.strip()},
    )
    db.commit()
    return api_response(msg="标准价格已更新", data={"product_id": product.id, "standard_price": float(new_price)})


@router.post("/products")
def create_product(
    payload: ProductCreate,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "finance", "finance_super", "manager", "master_data_admin")),
    db: Session = Depends(get_db),
):
    product_code = (payload.product_code or "").strip() or _next_available_master_data_code(
        db, model=Product, column=Product.product_code, prefix="CP", sequence_key="product_code"
    )
    existing = db.execute(select(Product).where(Product.product_code == product_code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="产品编码已存在")
    product = Product(**(payload.model_dump() | {"product_code": product_code}))
    product.unit = "kg"
    db.add(product)
    db.commit()
    db.refresh(product)
    return api_response(data={"id": product.id, "product_code": product.product_code})


@router.put("/products/{product_id}")
def update_product(
    product_id: int,
    payload: ProductCreate,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "finance", "finance_super", "manager", "master_data_admin")),
    db: Session = Depends(get_db),
):
    product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    product_code = (payload.product_code or "").strip()
    if not product_code:
        raise HTTPException(status_code=422, detail="产品编码不能为空")
    duplicate = db.execute(select(Product).where(Product.product_code == product_code, Product.id != product_id)).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="产品编码已存在")
    for field, value in (payload.model_dump() | {"product_code": product_code}).items():
        setattr(product, field, value)
    product.unit = "kg"
    db.commit()
    return api_response(msg="产品已更新", data={"id": product.id})


def _product_has_relations(db: Session, product_id: int) -> bool:
    checks = [
        select(SalesOrderItem.id).where(SalesOrderItem.product_id == product_id),
        select(PurchaseOrderItem.id).where(PurchaseOrderItem.product_id == product_id),
        select(BOM.id).where(BOM.product_id == product_id),
        select(ProductionTask.id).where(ProductionTask.product_id == product_id),
        select(Inventory.id).where(Inventory.product_id == product_id),
        select(InventoryBatch.id).where(InventoryBatch.product_id == product_id),
        select(InventoryTransaction.id).where(InventoryTransaction.product_id == product_id),
    ]
    return any(db.execute(query).first() for query in checks)


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "finance", "finance_super", "manager", "master_data_admin")),
    db: Session = Depends(get_db),
):
    product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    if _product_has_relations(db, product_id):
        product.is_active = False
        db.commit()
        return api_response(msg="产品已停用，因存在业务引用未执行物理删除", data={"id": product.id, "disabled": True})
    db.delete(product)
    db.commit()
    return api_response(msg="产品已删除")


@router.get("/materials")
def list_materials(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = None,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Material).order_by(Material.created_at.desc(), Material.id.desc())
    if active_only:
        query = query.where(Material.is_active.is_(True))
    if keyword and keyword.strip():
        term = keyword.strip()
        query = query.where(or_(Material.material_code.contains(term, autoescape=True),
                                Material.name.contains(term, autoescape=True),
                                Material.specification.contains(term, autoescape=True)))
    total = None
    if page is not None:
        total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(query).scalars().all()
    material_ids = [row.id for row in rows]
    packaging_map = _load_packaging_map(db, entity_type="material", entity_ids=material_ids)
    stock_map = _load_stock_map(db, item_type="material", item_ids=material_ids)
    items = [
            {
                "id": row.id,
                "material_code": row.material_code,
                "name": row.name,
                "price": float(row.price),
                "specification": packaging_spec_text(packaging_map[row.id].kg_per_bag) if row.id in packaging_map else row.specification,
                "unit": row.unit,
                "safety_stock_qty": float(row.safety_stock_qty),
                "purchase_threshold_qty": float(row.purchase_threshold_qty),
                "is_active": row.is_active,
                "packaging": (
                    lambda packaging: (
                        {**packaging, "packaging_name": get_global_packaging_name(db, packaging["kg_per_bag"]) or packaging["packaging_name"]}
                        if packaging and packaging_name_needs_repair(packaging.get("packaging_name"))
                        else packaging
                    )
                )(_serialize_packaging(packaging_map.get(row.id))),
                "stock": stock_map.get(row.id, {"quantity": 0, "reserved_quantity": 0, "available_quantity": 0}),
            }
            for row in rows
        ]
    if page is None:
        return api_response(data=items)
    return api_response(data={"items": items, "pagination": {"page": page, "page_size": page_size, "total": total}})


@router.post("/materials")
def create_material(
    payload: MaterialCreate,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "finance", "finance_super", "manager", "master_data_admin")),
    db: Session = Depends(get_db),
):
    material_code = (payload.material_code or "").strip() or _next_available_master_data_code(
        db, model=Material, column=Material.material_code, prefix="YL", sequence_key="material_code"
    )
    existing = db.execute(select(Material).where(Material.material_code == material_code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="原料编码已存在")
    material = Material(**(payload.model_dump() | {"material_code": material_code}))
    material.unit = "kg"
    db.add(material)
    db.commit()
    db.refresh(material)
    return api_response(data={"id": material.id, "material_code": material.material_code})


@router.put("/materials/{material_id}")
def update_material(
    material_id: int,
    payload: MaterialCreate,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "finance", "finance_super", "manager", "master_data_admin")),
    db: Session = Depends(get_db),
):
    material = db.execute(select(Material).where(Material.id == material_id)).scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=404, detail="原料不存在")
    material_code = (payload.material_code or "").strip()
    if not material_code:
        raise HTTPException(status_code=422, detail="原料编码不能为空")
    duplicate = db.execute(select(Material).where(Material.material_code == material_code, Material.id != material_id)).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="原料编码已存在")
    for field, value in (payload.model_dump() | {"material_code": material_code}).items():
        setattr(material, field, value)
    material.unit = "kg"
    db.commit()
    return api_response(msg="原料已更新", data={"id": material.id})


def _material_has_relations(db: Session, material_id: int) -> bool:
    checks = [
        select(PurchaseOrderItem.id).where(PurchaseOrderItem.material_id == material_id),
        select(BOMItem.id).where(BOMItem.material_id == material_id),
        select(ProductionMaterialUsage.id).where(ProductionMaterialUsage.material_id == material_id),
        select(Inventory.id).where(Inventory.material_id == material_id),
        select(InventoryBatch.id).where(InventoryBatch.material_id == material_id),
        select(InventoryTransaction.id).where(InventoryTransaction.material_id == material_id),
    ]
    return any(db.execute(query).first() for query in checks)


@router.delete("/materials/{material_id}")
def delete_material(
    material_id: int,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "finance", "finance_super", "manager", "master_data_admin")),
    db: Session = Depends(get_db),
):
    material = db.execute(select(Material).where(Material.id == material_id)).scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=404, detail="原料不存在")
    if _material_has_relations(db, material_id):
        material.is_active = False
        db.commit()
        return api_response(msg="原料已停用，因存在业务引用未执行物理删除", data={"id": material.id, "disabled": True})
    db.delete(material)
    db.commit()
    return api_response(msg="原料已删除")


@router.get("/packaging-configs")
def list_packaging_configs(
    entity_type: str | None = None,
    entity_id: int | None = None,
    active_only: bool = False,
    keyword: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(PackagingConfig).order_by(PackagingConfig.entity_type.asc(), PackagingConfig.entity_id.asc(), PackagingConfig.id.asc())
    if entity_type:
        query = query.where(PackagingConfig.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(PackagingConfig.entity_id == entity_id)
    if active_only:
        query = query.where(PackagingConfig.is_active.is_(True))
    if keyword and keyword.strip():
        query = query.where(PackagingConfig.packaging_name.contains(keyword.strip(), autoescape=True))
    rows = db.execute(query).scalars().all()
    return api_response(data=[_serialize_packaging(row) for row in rows])


def _ensure_packaging_entity(payload: PackagingConfigCreate, db: Session):
    if payload.entity_type == "global":
        return None
    if payload.entity_type == "product":
        entity = db.execute(select(Product).where(Product.id == payload.entity_id)).scalar_one_or_none()
        if entity is None:
            raise HTTPException(status_code=404, detail="产品不存在")
        return entity
    entity = db.execute(select(Material).where(Material.id == payload.entity_id)).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="原料不存在")
    return entity


@router.post("/packaging-configs")
def upsert_packaging_config(
    payload: PackagingConfigCreate,
    current_user: User = Depends(require_roles("sales", "purchase", "production", "inventory", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    entity = _ensure_packaging_entity(payload, db)

    if payload.entity_type == "global":
        row = db.execute(
            select(PackagingConfig).where(
                PackagingConfig.entity_type == "global",
                PackagingConfig.entity_id == 0,
                PackagingConfig.packaging_name == payload.packaging_name,
            )
        ).scalar_one_or_none()
        if row is None:
            row = PackagingConfig(
                entity_type="global",
                entity_id=0,
                packaging_name=payload.packaging_name,
                kg_per_bag=payload.kg_per_bag,
                unit_price=payload.unit_price or 0,
                is_default=payload.is_default,
                is_active=payload.is_active,
            )
            db.add(row)
            db.flush()
        else:
            row.kg_per_bag = payload.kg_per_bag
            row.unit_price = payload.unit_price if payload.unit_price is not None else row.unit_price
            row.is_default = payload.is_default
            row.is_active = payload.is_active
        db.commit()
        return api_response(data=_serialize_packaging(row))

    row = upsert_default_packaging_config(
        db,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        packaging_name=payload.packaging_name,
        kg_per_bag=payload.kg_per_bag,
        unit_price=payload.unit_price,
        is_active=payload.is_active,
    )
    entity.specification = packaging_spec_text(row.kg_per_bag)
    entity.unit = "kg"
    db.commit()
    return api_response(data=_serialize_packaging(row))


@router.put("/packaging-configs/{packaging_id}")
def update_packaging_config(
    packaging_id: int,
    payload: PackagingConfigCreate,
    current_user: User = Depends(require_roles("sales", "purchase", "production", "inventory", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(PackagingConfig).where(PackagingConfig.id == packaging_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="包装配置不存在")
    if payload.entity_type == "global":
        duplicate = db.execute(
            select(PackagingConfig).where(
                PackagingConfig.entity_type == "global",
                PackagingConfig.entity_id == 0,
                PackagingConfig.packaging_name == payload.packaging_name,
                PackagingConfig.id != packaging_id,
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="已存在同名包装")
    entity = _ensure_packaging_entity(payload, db)
    row.entity_type = payload.entity_type
    row.entity_id = 0 if payload.entity_type == "global" else payload.entity_id
    row.packaging_name = payload.packaging_name
    row.kg_per_bag = payload.kg_per_bag
    if payload.unit_price is not None:
        row.unit_price = payload.unit_price
    row.is_default = payload.is_default
    row.is_active = payload.is_active
    if entity is not None:
        entity.specification = packaging_spec_text(row.kg_per_bag)
        entity.unit = "kg"
    db.commit()
    return api_response(data=_serialize_packaging(row))


@router.delete("/packaging-configs/{packaging_id}")
def delete_packaging_config(
    packaging_id: int,
    current_user: User = Depends(require_roles("sales", "purchase", "production", "inventory", "finance", "finance_super", "manager", "chairman", "master_data_admin", "system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(PackagingConfig).where(PackagingConfig.id == packaging_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="包装配置不存在")
    row.is_active = False
    db.commit()
    return api_response(msg="包装配置已停用", data=_serialize_packaging(row))
