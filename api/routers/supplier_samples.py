"""Supplier sample receipt and inspection workflow. Samples never change inventory."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import require_roles
from models import Material, PackagingConfig, Product, QualityInspection, QualityTask, Role, Supplier, SupplierCatalogItem, SupplierSample, User, Warehouse
from responses import api_response
from services.core import complete_todos, create_notification, create_todo, next_business_no, write_operation_log
from services.organization import get_inventory_scope_warehouse_ids
from services.quality_workflow import resolve_quality_recipients_for_warehouse
from services.stock_transfer_workflow import _warehouse_workers
from services.time_utils import now_local


router = APIRouter(prefix="/supplier-samples", tags=["supplier-samples"])


class SampleCreate(BaseModel):
    supplier_id: int
    product_id: int | None = None
    material_id: int | None = None
    packaging_config_id: int | None = None
    warehouse_id: int
    quantity: Decimal = Field(..., gt=0, max_digits=18, decimal_places=4)
    batch_no: str | None = Field(default=None, max_length=80)
    remark: str | None = Field(default=None, max_length=1000)


class SampleReceive(BaseModel):
    received_quantity: Decimal = Field(..., gt=0, max_digits=18, decimal_places=4)
    batch_no: str | None = Field(default=None, max_length=80)
    storage_location: str | None = Field(default=None, max_length=150)
    receipt_remark: str | None = Field(default=None, max_length=1000)


def _can_receive(db: Session, user: User, warehouse: Warehouse) -> bool:
    role_code = user.role.code if user.role else None
    if role_code in {"inventory_super", "system_admin"}:
        return True
    return role_code == "inventory" and user.id in _warehouse_workers(db, warehouse.id, "inventory", warehouse.manager_user_id)


def _sample_data(sample: SupplierSample, inspection: QualityInspection | None = None) -> dict:
    task = sample.quality_task
    item_type = "product" if sample.product_id else "material" if sample.material_id else "packaging"
    item = sample.product or sample.material or sample.packaging_config
    return {
        "id": sample.id,
        "sample_no": sample.sample_no,
        "supplier_id": sample.supplier_id,
        "supplier_name": sample.supplier.name if sample.supplier else None,
        "product_id": sample.product_id,
        "material_id": sample.material_id,
        "packaging_config_id": sample.packaging_config_id,
        "item_type": item_type,
        "item_name": item.name if item and item_type != "packaging" else item.packaging_name if item else None,
        "item_code": sample.product.product_code if sample.product else sample.material.material_code if sample.material else f"BZ-{sample.packaging_config_id}" if sample.packaging_config_id else None,
        "warehouse_id": sample.warehouse_id,
        "warehouse_name": sample.warehouse.name if sample.warehouse else None,
        "quantity": float(sample.quantity),
        "received_quantity": float(sample.received_quantity) if sample.received_quantity is not None else None,
        "unit": sample.unit,
        "batch_no": sample.batch_no,
        "storage_location": sample.storage_location,
        "receipt_remark": sample.receipt_remark,
        "status": sample.status,
        "remark": sample.remark,
        "requested_by_name": sample.requested_by.full_name if sample.requested_by else None,
        "received_by_name": sample.received_by.full_name if sample.received_by else None,
        "received_at": sample.received_at.isoformat() if sample.received_at else None,
        "quality_task_id": sample.quality_task_id,
        "quality_no": task.quality_no if task else None,
        "quality_status": task.status if task else None,
        "grade": inspection.grade if inspection else None,
        "is_passed": inspection.is_passed if inspection else None,
        "result_text": inspection.result_text if inspection else None,
        "report_summary": inspection.report_summary if inspection else None,
        "inspected_at": inspection.inspected_at.isoformat() if inspection and inspection.inspected_at else None,
        "inspection_id": inspection.id if inspection else None,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
    }


def _latest_inspections(db: Session, task_ids: list[int]) -> dict[int, QualityInspection]:
    if not task_ids:
        return {}
    rows = db.scalars(
        select(QualityInspection)
        .where(QualityInspection.quality_task_id.in_(task_ids))
        .order_by(QualityInspection.quality_task_id, QualityInspection.sequence_no.desc(), QualityInspection.id.desc())
    ).all()
    result: dict[int, QualityInspection] = {}
    for row in rows:
        result.setdefault(row.quality_task_id, row)
    return result


def _load_samples(db: Session, supplier_id: int | None = None, status: str | None = None) -> list[SupplierSample]:
    query = select(SupplierSample).options(
        joinedload(SupplierSample.supplier), joinedload(SupplierSample.product), joinedload(SupplierSample.material),
        joinedload(SupplierSample.packaging_config),
        joinedload(SupplierSample.warehouse), joinedload(SupplierSample.requested_by),
        joinedload(SupplierSample.received_by), joinedload(SupplierSample.quality_task),
    )
    if supplier_id is not None:
        query = query.where(SupplierSample.supplier_id == supplier_id)
    if status is not None:
        query = query.where(SupplierSample.status == status)
    return db.scalars(query.order_by(SupplierSample.created_at.desc(), SupplierSample.id.desc())).all()


@router.get("")
def list_samples(
    supplier_id: int | None = None,
    status: str | None = None,
    current_user: User = Depends(require_roles("purchase", "finance", "finance_super", "master_data_admin", "inventory", "inventory_super", "quality", "quality_super", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    if status and status not in {"pending_receipt", "pending_quality", "completed"}:
        raise HTTPException(status_code=422, detail="无效的样品状态")
    rows = _load_samples(db, supplier_id, status)
    role_code = current_user.role.code if current_user.role else None
    if role_code == "inventory":
        warehouse_map = {row.warehouse_id: row.warehouse for row in rows}
        allowed = {warehouse_id for warehouse_id, warehouse in warehouse_map.items() if warehouse and _can_receive(db, current_user, warehouse)}
        rows = [row for row in rows if row.warehouse_id in allowed]
    elif role_code == "quality":
        scope = get_inventory_scope_warehouse_ids(db, current_user)
        rows = [row for row in rows if row.warehouse_id in (scope or []) or row.warehouse.qc_user_id == current_user.id]
    inspection_map = _latest_inspections(db, [row.quality_task_id for row in rows if row.quality_task_id])
    return api_response(data=[_sample_data(row, inspection_map.get(row.quality_task_id)) for row in rows])


@router.post("")
def create_sample(
    payload: SampleCreate,
    current_user: User = Depends(require_roles("purchase", "finance", "finance_super", "master_data_admin", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    selected_ids = (payload.product_id, payload.material_id, payload.packaging_config_id)
    if sum(value is not None for value in selected_ids) != 1:
        raise HTTPException(status_code=422, detail="样品必须且只能选择一种产品、原料或包装")
    supplier = db.get(Supplier, payload.supplier_id)
    if not supplier or supplier.status != "enabled":
        raise HTTPException(status_code=404, detail="供应商不存在或已停用")
    item_filter = (
        SupplierCatalogItem.product_id == payload.product_id
        if payload.product_id is not None
        else SupplierCatalogItem.material_id == payload.material_id
        if payload.material_id is not None
        else SupplierCatalogItem.packaging_config_id == payload.packaging_config_id
    )
    catalog = db.scalar(select(SupplierCatalogItem.id).where(
        SupplierCatalogItem.supplier_id == payload.supplier_id,
        item_filter,
    ))
    if not catalog:
        raise HTTPException(status_code=409, detail="该供应商的可供货名录中不存在此产品、原料或包装")
    item = (
        db.get(Product, payload.product_id)
        if payload.product_id is not None
        else db.get(Material, payload.material_id)
        if payload.material_id is not None
        else db.get(PackagingConfig, payload.packaging_config_id)
    )
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not item or not warehouse:
        raise HTTPException(status_code=404, detail="产品、原料、包装或仓库不存在")
    recipients = _warehouse_workers(db, warehouse.id, "inventory", warehouse.manager_user_id)
    if not recipients:
        raise HTTPException(status_code=409, detail=f"{warehouse.name}未配置在岗库管，无法登记待收样任务")
    sample_no = next_business_no(db, "YP")
    sample = SupplierSample(
        sample_no=sample_no, supplier_id=supplier.id,
        product_id=payload.product_id, material_id=payload.material_id,
        packaging_config_id=payload.packaging_config_id,
        warehouse_id=warehouse.id, quantity=payload.quantity,
        unit=item.unit if payload.packaging_config_id is None else "件", batch_no=(payload.batch_no or "").strip() or sample_no,
        status="pending_receipt", remark=(payload.remark or "").strip() or None,
        requested_by_id=current_user.id,
    )
    db.add(sample)
    db.flush()
    for user_id in recipients:
        create_todo(db, title=f"待接收供应商样品 {sample.sample_no}",
                    description=f"{supplier.name} / {item.packaging_name if payload.packaging_config_id else item.name} / {warehouse.name}，请核对并确认收样。",
                    module="inventory", assigned_user_id=user_id,
                    business_type="supplier_sample", business_id=sample.id)
    write_operation_log(db, user_id=current_user.id, module="master_data", action="create_supplier_sample",
                        content=f"登记供应商样品 {sample.sample_no}", after_data={"sample_id": sample.id})
    db.commit()
    return api_response(data={"id": sample.id, "sample_no": sample.sample_no})


@router.post("/{sample_id}/receive")
def receive_sample(
    sample_id: int,
    payload: SampleReceive,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "system_admin")),
    db: Session = Depends(get_db),
):
    sample = db.scalar(select(SupplierSample).where(SupplierSample.id == sample_id).with_for_update())
    if sample is None:
        raise HTTPException(status_code=404, detail="样品记录不存在")
    if sample.status != "pending_receipt":
        raise HTTPException(status_code=409, detail="该样品已收样或已处理，不能重复确认")
    warehouse = db.get(Warehouse, sample.warehouse_id)
    if warehouse is None or not _can_receive(db, current_user, warehouse):
        raise HTTPException(status_code=403, detail="仅目标仓库库管可以确认收样")
    recipients = resolve_quality_recipients_for_warehouse(db, warehouse_id=warehouse.id, fallback_user_ids=[])
    if not recipients:
        # 供应商收样是新增流程，旧仓库通常还没有维护 qc_user_id 或质检部门仓库范围。
        # 只对该流程兼容回退到在岗质检人员，避免影响采购/生产现有的仓库质检分配规则。
        recipients = list(db.scalars(
            select(User.id).join(Role, User.role_id == Role.id).where(
                Role.code.in_(["quality", "quality_super"]),
                User.is_active.is_(True),
            )
        ))
    if not recipients:
        raise HTTPException(status_code=409, detail="系统没有在岗质检人员，不能确认收样；请先启用质检账号")
    if warehouse.qc_user_id in recipients:
        recipients = [warehouse.qc_user_id]
    assigned_inspector_id = recipients[0] if len(recipients) == 1 else None
    task = QualityTask(
        quality_no=next_business_no(db, "ZJY"), batch_no=sample.batch_no,
        source_category="supplier_sample", required_tests=1, status="pending",
        submitted_by_id=current_user.id, target_warehouse_id=warehouse.id,
        assigned_inspector_id=assigned_inspector_id,
    )
    db.add(task)
    db.flush()
    sample.quality_task_id = task.id
    sample.status = "pending_quality"
    sample.received_by_id = current_user.id
    sample.received_at = now_local()
    sample.received_quantity = payload.received_quantity
    sample.batch_no = (payload.batch_no or "").strip() or sample.batch_no
    task.batch_no = sample.batch_no
    sample.storage_location = (payload.storage_location or "").strip() or None
    sample.receipt_remark = (payload.receipt_remark or "").strip() or None
    complete_todos(db, business_type="supplier_sample", business_id=sample.id)
    for user_id in recipients:
        create_todo(db, title=f"待检供应商样品 {sample.sample_no}",
                    description=f"{sample.supplier.name} / {sample.product.name if sample.product else sample.material.name if sample.material else sample.packaging_config.packaging_name} / {warehouse.name}。",
                    module="quality", assigned_user_id=user_id,
                    business_type="quality_task", business_id=task.id)
    create_notification(db, title="供应商样品已收样", content=f"样品 {sample.sample_no} 已由 {current_user.full_name} 接收，待质检。",
                        module="quality", recipient_user_ids=recipients, created_by_id=current_user.id, category="info")
    write_operation_log(db, user_id=current_user.id, module="inventory", action="receive_supplier_sample",
                        content=f"确认收样 {sample.sample_no}", after_data={"quality_task_id": task.id})
    db.commit()
    return api_response(data={"id": sample.id, "quality_task_id": task.id, "quality_no": task.quality_no})
