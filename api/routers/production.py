import json
from datetime import datetime
from decimal import Decimal
from math import ceil
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import (
    BOM,
    BOMItem,
    FulfillmentDemand,
    Inventory,
    InventoryOperationRequest,
    Material,
    ProductionPlan,
    ProductionMaterialUsage,
    ProductionTask,
    PackagingConfig,
    Product,
    QualityTask,
    Role,
    SalesOrder,
    SalesOrderItem,
    PurchaseOrder,
    PurchaseOrderItem,
    TodoItem,
    User,
    Warehouse,
    order_type_style,
)
from responses import api_response
from schemas.production import (
    BOMCreate,
    DemandRawMaterialDemandCreateRequest,
    FulfillmentDemandCancelRequest,
    FulfillmentDemandUpdateRequest,
    MaterialRequestCreate,
    MaterialUsageInput,
    ProductionPlanCreate,
    ProductionPlanUpdate,
    ProductionInboundRequest,
    ProductionCompleteRequest,
    ProductionTaskCancelRequest,
    ProductionTaskCreate,
    SpecialMaterialApproval,
    SpecialMaterialRequestCreate,
)
from services.core import _role_user_ids, apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, sync_low_stock_alerts, write_operation_log
from services.display import status_label
from services.production_service import (
    approve_special_request as approve_special_request_service,
    cancel_task as cancel_task_service,
    complete_task as complete_task_service,
    convert_plan_to_task as convert_plan_to_task_service,
    create_plan as create_plan_service,
    create_task as create_task_service,
    _inventory_user_ids_for_warehouse,
    can_review_factory_material,
    _create_raw_material_demands_for_task,
    raw_material_shortage_detail,
    submit_material_request as submit_material_request_service,
    submit_special_requests as submit_special_requests_service,
    update_plan as update_plan_service,
)
from services.quality_workflow import quality_sample_consumption
from services.time_utils import now_local
from services.workflow import create_inventory_operation_request, production_flow_steps
from services.warehouse_lookup import get_factory_warehouse, get_finished_warehouse, get_raw_warehouse
from services.inventory_availability import purchase_in_transit_map, stock_key


router = APIRouter(prefix="/production", tags=["production"])
BOM_MANAGE_ROLES = ("production", "system_admin", "master_data_admin")


def _raw_warehouse(db: Session) -> Warehouse:
    return get_raw_warehouse(db)


def _finished_warehouse(db: Session) -> Warehouse:
    return get_finished_warehouse(db)




_PROD_TASK_NON_RESERVING = {"completed", "cancelled", "scrapped", "partially_scrapped", "reworked", "rework_pending"}


def _batch_max_producible_map(db: Session, product_ids: list[int]) -> dict[int, Decimal]:
    """批量计算多个产品的原料可支持最大产量(等价于逐产品调用 _max_producible_quantity_from_raw_stock)。"""
    product_ids = [pid for pid in (product_ids or []) if pid]
    if not product_ids:
        return {}
    boms = db.execute(
        select(BOM)
        .options(joinedload(BOM.items))
        .where(BOM.product_id.in_(product_ids))
    ).unique().scalars().all()
    bom_map = {bom.product_id: bom for bom in boms}
    material_ids = {}
    for bom in boms:
        for item in (bom.items or []):
            if item.standard_quantity and item.standard_quantity > 0:
                material_ids[item.material_id] = True
    material_ids = list(material_ids)

    raw_warehouse = _raw_warehouse(db)
    inventory_map = {}
    if material_ids:
        inventory_map = {
            row.material_id: row
            for row in db.execute(
                select(Inventory).where(
                    Inventory.warehouse_id == raw_warehouse.id,
                    Inventory.material_id.in_(material_ids),
                    Inventory.product_id.is_(None),
                )
            ).scalars().all()
        }

    planned_by_material: dict[int, Decimal] = {}
    if material_ids:
        for material_id, planned_quantity, actual_quantity in db.execute(
            select(
                ProductionMaterialUsage.material_id,
                ProductionMaterialUsage.planned_quantity,
                ProductionMaterialUsage.actual_quantity,
            )
            .join(ProductionTask, ProductionTask.id == ProductionMaterialUsage.production_task_id)
            .where(
                ProductionTask.status.not_in(_PROD_TASK_NON_RESERVING),
                ProductionMaterialUsage.material_id.in_(material_ids),
            )
        ).all():
            outstanding = max(Decimal(planned_quantity or 0) - Decimal(actual_quantity or 0), Decimal("0"))
            planned_by_material[material_id] = planned_by_material.get(material_id, Decimal("0")) + outstanding

    result: dict[int, Decimal] = {}
    for product_id in product_ids:
        bom = bom_map.get(product_id)
        if bom is None or not bom.items:
            result[product_id] = Decimal("0")
            continue
        max_quantity: Decimal | None = None
        for bom_item in bom.items:
            standard_quantity = Decimal(bom_item.standard_quantity or 0)
            if standard_quantity <= 0:
                continue
            inventory = inventory_map.get(bom_item.material_id)
            stock_available = max(Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0) if inventory else Decimal("0"), Decimal("0"))
            available = max(stock_available - planned_by_material.get(bom_item.material_id, Decimal("0")), Decimal("0"))
            item_max = available / standard_quantity
            max_quantity = item_max if max_quantity is None else min(max_quantity, item_max)
        result[product_id] = max_quantity if max_quantity is not None else Decimal("0")
    return result


def _batch_demand_progress_map(db: Session, demand_ids: list[int]) -> dict[int, dict]:
    """批量推导补货需求进度,避免逐行查询(等价于逐行 resolve_demand_progress)。"""
    demand_ids = [did for did in (demand_ids or []) if did]
    result: dict[int, dict] = {}
    if not demand_ids:
        return result
    po_map = {
        po.replenishment_source_id: po
        for po in db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.replenishment_source_type == "fulfillment_demand",
                PurchaseOrder.replenishment_source_id.in_(demand_ids),
            )
        ).scalars().all()
    }
    task_map = {
        task.fulfillment_demand_id: task
        for task in db.execute(
            select(ProductionTask).where(ProductionTask.fulfillment_demand_id.in_(demand_ids))
        ).scalars().all()
    }
    po_ids = [po.id for po in po_map.values()]
    paid_map: dict[int, Decimal] = {}
    if po_ids:
        for po_id, amount, pay_status in db.execute(
            select(PayableRecord.purchase_order_id, PayableRecord.order_amount, PayableRecord.status)
            .where(PayableRecord.purchase_order_id.in_(po_ids))
        ).all():
            if pay_status in {"paid", "partial_settled", "settled"}:
                paid_map[po_id] = paid_map.get(po_id, Decimal("0")) + Decimal(amount or 0)

    def _progress(demand: FulfillmentDemand) -> dict:
        if demand.status == "cancelled":
            return {"progress_status": "cancelled", "progress_label": "已取消", "related_no": None, "related_status_label": None}
        if demand.status == "pending_material":
            return {"progress_status": "pending_material", "progress_label": "待补料", "related_no": None, "related_status_label": None}
        if demand.status != "converted":
            return {"progress_status": "pending", "progress_label": "待处理", "related_no": None, "related_status_label": None}
        if demand.fulfillment_mode == "trade":
            po = po_map.get(demand.id)
            if po is None:
                return {"progress_status": "pending", "progress_label": "待处理", "related_no": None, "related_status_label": None}
            po_label = status_label(po.status)
            if po.status in {"draft", "pending_approval", "rejected"}:
                return {"progress_status": "purchasing", "progress_label": "采购中", "related_no": po.order_no, "related_status_label": po_label}
            if po.status == "purchasing":
                return {"progress_status": "ordered", "progress_label": "已下单", "related_no": po.order_no, "related_status_label": po_label}
            if po.status in {"partial_received", "pending_purchase_confirm"}:
                return {"progress_status": "receiving", "progress_label": "待收货", "related_no": po.order_no, "related_status_label": po_label}
            if po.status == "fully_received":
                if paid_map.get(po.id, Decimal("0")) >= Decimal(po.total_amount or 0):
                    return {"progress_status": "completed", "progress_label": "已完成", "related_no": po.order_no, "related_status_label": po_label}
                return {"progress_status": "pending_payment", "progress_label": "待财务付款", "related_no": po.order_no, "related_status_label": po_label}
            if po.status == "completed":
                return {"progress_status": "completed", "progress_label": "已完成", "related_no": po.order_no, "related_status_label": po_label}
            if po.status == "cancelled":
                return {"progress_status": "pending", "progress_label": "待处理", "related_no": po.order_no, "related_status_label": po_label}
            return {"progress_status": "purchasing", "progress_label": "采购中", "related_no": po.order_no, "related_status_label": po_label}
        task = task_map.get(demand.id)
        if task is None:
            return {"progress_status": "pending", "progress_label": "待处理", "related_no": None, "related_status_label": None}
        task_label = status_label(task.status)
        if task.status in {"pending_material", "pending_material_approval", "pending_material_outbound"}:
            return {"progress_status": "raw_purchasing", "progress_label": "原料采购中", "related_no": task.task_no, "related_status_label": task_label}
        if task.status in {"material_issued", "in_production", "reworked"}:
            return {"progress_status": "producing", "progress_label": "生产中", "related_no": task.task_no, "related_status_label": task_label}
        if task.status in {"waiting_quality", "ready_for_inbound"}:
            return {"progress_status": "pending_inbound", "progress_label": "待入库", "related_no": task.task_no, "related_status_label": task_label}
        if task.status == "completed":
            return {"progress_status": "completed", "progress_label": "已完成", "related_no": task.task_no, "related_status_label": task_label}
        if task.status in {"scrapped", "partially_scrapped", "cancelled"}:
            return {"progress_status": "pending", "progress_label": "待处理", "related_no": task.task_no, "related_status_label": task_label}
        return {"progress_status": "producing", "progress_label": "生产中", "related_no": task.task_no, "related_status_label": task_label}

    for demand_id in demand_ids:
        result[demand_id] = {}
    demands = db.execute(
        select(FulfillmentDemand).where(FulfillmentDemand.id.in_(demand_ids))
    ).scalars().all()
    for demand in demands:
        result[demand.id] = _progress(demand)
    return result


def _allowed_quantity(task: ProductionTask, usage: ProductionMaterialUsage) -> Decimal:
    return Decimal(usage.planned_quantity or 0) * (Decimal("1") + Decimal(task.fluctuation_ratio or 0))


def _serialize_usage(
    task: ProductionTask,
    usage: ProductionMaterialUsage,
    available_quantity: Decimal = Decimal("0"),
    approver_map: dict[int, User] | None = None,
) -> dict:
    allowed_quantity = _allowed_quantity(task, usage)
    requested_quantity = Decimal(usage.requested_quantity or 0)
    return {
        "id": usage.id,
        "material_id": usage.material_id,
        "material_name": usage.material.name if usage.material else None,
        "planned_quantity": float(usage.planned_quantity),
        "allowed_quantity": float(allowed_quantity),
        "requested_quantity": float(requested_quantity),
        "approved_quantity": float(usage.approved_quantity),
        "actual_quantity": float(usage.actual_quantity),
        "available_quantity": float(available_quantity),
        "reason": usage.reason,
        "special_request_status": usage.special_request_status,
        "special_request_status_label": status_label(usage.special_request_status),
        "inventory_approval_status": usage.inventory_approval_status,
        "inventory_approval_status_label": status_label(usage.inventory_approval_status),
        "inventory_approved_at": usage.inventory_approved_at.isoformat() if usage.inventory_approved_at else None,
        "inventory_approved_by_name": approver_map[usage.inventory_approved_by_id].full_name if approver_map and usage.inventory_approved_by_id in approver_map else None,
        "inventory_remark": usage.inventory_remark,
        "manager_approval_status": usage.manager_approval_status,
        "manager_approval_status_label": status_label(usage.manager_approval_status),
        "manager_approved_at": usage.manager_approved_at.isoformat() if usage.manager_approved_at else None,
        "manager_approved_by_name": approver_map[usage.manager_approved_by_id].full_name if approver_map and usage.manager_approved_by_id in approver_map else None,
        "manager_remark": usage.manager_remark,
        "needs_special_approval": requested_quantity > allowed_quantity,
    }


def _serialize_special_request(usage: ProductionMaterialUsage, approver_map: dict[int, User] | None = None) -> dict:
    task = usage.production_task
    allowed_quantity = _allowed_quantity(task, usage)
    request_type = "special" if Decimal(usage.requested_quantity or 0) > allowed_quantity or usage.manager_approval_status != "not_required" else "material"
    combined_status = usage.special_request_status if request_type == "special" else usage.inventory_approval_status
    return {
        "id": usage.id,
        "production_task_id": task.id,
        "task_no": task.task_no,
        "product_name": task.product.name if task.product else None,
        "material_name": usage.material.name if usage.material else None,
        "request_type": request_type,
        "request_type_label": "特殊领料" if request_type == "special" else "领料",
        "planned_quantity": float(usage.planned_quantity),
        "allowed_quantity": float(allowed_quantity),
        "requested_quantity": float(usage.requested_quantity),
        "approved_quantity": float(usage.approved_quantity),
        "issued_quantity": float(usage.actual_quantity or 0),
        "over_limit_quantity": float(Decimal(usage.requested_quantity or 0) - allowed_quantity),
        "status": combined_status,
        "status_label": status_label(combined_status),
        "reason": usage.reason,
        "inventory_approval_status": usage.inventory_approval_status,
        "inventory_approval_status_label": status_label(usage.inventory_approval_status),
        "inventory_approved_by_id": usage.inventory_approved_by_id,
        "inventory_approved_by_name": approver_map[usage.inventory_approved_by_id].full_name if approver_map and usage.inventory_approved_by_id in approver_map else None,
        "inventory_approved_at": usage.inventory_approved_at.isoformat() if usage.inventory_approved_at else None,
        "inventory_remark": usage.inventory_remark,
        "manager_approval_status": usage.manager_approval_status,
        "manager_approval_status_label": status_label(usage.manager_approval_status),
        "manager_approved_by_id": usage.manager_approved_by_id,
        "manager_approved_by_name": approver_map[usage.manager_approved_by_id].full_name if approver_map and usage.manager_approved_by_id in approver_map else None,
        "manager_approved_at": usage.manager_approved_at.isoformat() if usage.manager_approved_at else None,
        "manager_remark": usage.manager_remark,
        "task_status": task.status,
        "task_status_label": status_label(task.status),
        "created_by_id": task.created_by_id,
        "created_by_name": approver_map[task.created_by_id].full_name if approver_map and task.created_by_id in approver_map else None,
        "created_at": usage.created_at.isoformat() if usage.created_at else None,
        "updated_at": usage.updated_at.isoformat() if usage.updated_at else None,
    }


def _build_batch_no(db: Session, task: ProductionTask) -> str:
    if task.parent_task_id:
        parent = db.execute(select(ProductionTask).where(ProductionTask.id == task.parent_task_id)).scalar_one_or_none()
        base_batch = (parent.batch_no or "").split("-")[0] if parent and parent.batch_no else now_local().strftime("%Y%m%d")
        sibling_count = db.execute(
            select(func.count(ProductionTask.id)).where(
                ProductionTask.parent_task_id == task.parent_task_id,
                ProductionTask.batch_no.is_not(None),
                ProductionTask.id != task.id,
            )
        ).scalar_one()
        suffix = "RW" if task.source_type == "rework" else "FG"
        return f"{base_batch}-{suffix}{sibling_count + 1:02d}"
    return f"{now_local().strftime('%Y%m%d')}{task.id:04d}-A"


@router.get("/boms")
def list_boms(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(BOM)
        .options(joinedload(BOM.product), joinedload(BOM.packaging_config), joinedload(BOM.items).joinedload(BOMItem.material))
        .order_by(BOM.created_at.desc())
    ).unique().scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "product_id": row.product_id,
                "product_name": row.product.name,
                "packaging_config_id": row.packaging_config_id,
                "packaging_name": row.packaging_config.packaging_name if row.packaging_config else None,
                "kg_per_bag": float(row.packaging_config.kg_per_bag) if row.packaging_config else None,
                "packaging_unit_price": float(row.packaging_config.unit_price or 0) if row.packaging_config else 0,
                "version_no": row.version_no,
                "notes": row.notes,
                "items": [
                    {
                        "material_id": item.material_id,
                        "material_name": item.material.name,
                        "standard_quantity": float(item.standard_quantity),
                    }
                    for item in row.items
                ],
            }
            for row in rows
        ]
    )


@router.post("/boms")
def create_bom(
    payload: BOMCreate,
    current_user: User = Depends(require_roles(*BOM_MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    if not payload.product_id:
        raise HTTPException(status_code=400, detail="请选择成品")
    if not payload.items:
        raise HTTPException(status_code=400, detail="BOM 至少需要一条原料明细")
    product = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    packaging = db.execute(
        select(PackagingConfig).where(
            PackagingConfig.id == payload.packaging_config_id,
            PackagingConfig.entity_type == "global",
            PackagingConfig.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if packaging is None:
        raise HTTPException(status_code=404, detail="请选择有效的包装配置")
    existing = db.execute(select(BOM).where(BOM.product_id == payload.product_id)).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()
    bom = BOM(product_id=payload.product_id, packaging_config_id=payload.packaging_config_id, version_no=payload.version_no, notes=payload.notes, is_active=True)
    db.add(bom)
    db.flush()
    for item_payload in payload.items:
        material = db.execute(select(Material).where(Material.id == item_payload.material_id)).scalar_one_or_none()
        if material is None:
            raise HTTPException(status_code=404, detail="原料不存在")
        db.add(BOMItem(bom_id=bom.id, material_id=item_payload.material_id, standard_quantity=item_payload.standard_quantity))
    db.commit()
    return api_response(data={"id": bom.id})


@router.put("/boms/{bom_id}")
def update_bom(
    bom_id: int,
    payload: BOMCreate,
    current_user: User = Depends(require_roles(*BOM_MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    bom = db.execute(select(BOM).options(joinedload(BOM.items)).where(BOM.id == bom_id)).unique().scalar_one_or_none()
    if bom is None:
        raise HTTPException(status_code=404, detail="BOM 不存在")
    product = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    packaging = db.execute(
        select(PackagingConfig).where(
            PackagingConfig.id == payload.packaging_config_id,
            PackagingConfig.entity_type == "global",
            PackagingConfig.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if packaging is None:
        raise HTTPException(status_code=404, detail="请选择有效的包装配置")
    if not payload.items:
        raise HTTPException(status_code=400, detail="BOM 至少需要一条原料明细")

    duplicate_bom = db.execute(select(BOM).where(BOM.product_id == payload.product_id, BOM.id != bom_id)).scalar_one_or_none()
    if duplicate_bom is not None:
        raise HTTPException(status_code=409, detail="该产品已存在其他 BOM，请先删除或修改原 BOM")

    bom.product_id = payload.product_id
    bom.packaging_config_id = payload.packaging_config_id
    bom.version_no = payload.version_no
    bom.notes = payload.notes
    bom.is_active = True
    for item in list(bom.items):
        db.delete(item)
    db.flush()
    for item_payload in payload.items:
        material = db.execute(select(Material).where(Material.id == item_payload.material_id)).scalar_one_or_none()
        if material is None:
            raise HTTPException(status_code=404, detail="原料不存在")
        db.add(BOMItem(bom_id=bom.id, material_id=item_payload.material_id, standard_quantity=item_payload.standard_quantity))
    db.commit()
    return api_response(data={"id": bom.id})


@router.delete("/boms/{bom_id}")
def delete_bom(
    bom_id: int,
    current_user: User = Depends(require_roles(*BOM_MANAGE_ROLES)),
    db: Session = Depends(get_db),
):
    bom = db.execute(select(BOM).where(BOM.id == bom_id)).scalar_one_or_none()
    if bom is None:
        raise HTTPException(status_code=404, detail="BOM 不存在")
    db.delete(bom)
    db.commit()
    return api_response(msg="BOM 已删除")


@router.get("/tasks")
def list_tasks(
    task_status: str = Query(default="ongoing", pattern="^(ongoing|closed|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    completed_statuses = ["completed", "cancelled", "scrapped", "partially_scrapped", "reworked"]
    task_filters = []
    if task_status == "closed":
        task_filters.append(ProductionTask.status.in_(completed_statuses))
    elif task_status == "ongoing":
        task_filters.append(~ProductionTask.status.in_(completed_statuses))
    if current_user.role.code == "quality":
        task_filters.extend([~ProductionTask.status.in_(completed_statuses), ProductionTask.source_type != "purchase_quality"])
    total = db.execute(select(func.count(ProductionTask.id)).where(*task_filters)).scalar_one()
    task_ids_page = db.execute(
        select(ProductionTask.id)
        .where(*task_filters)
        .order_by(ProductionTask.created_at.desc(), ProductionTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    rows = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.product))
        .where(ProductionTask.id.in_(task_ids_page or [-1]))
        .order_by(ProductionTask.created_at.desc(), ProductionTask.id.desc())
    ).scalars().all()
    parent_ids = [row.parent_task_id for row in rows if row.parent_task_id]
    task_ids = [row.id for row in rows]
    sales_order_ids = [row.sales_order_id for row in rows if row.sales_order_id]
    parent_map = {
        row.id: row
        for row in db.execute(select(ProductionTask).where(ProductionTask.id.in_(parent_ids))).scalars().all()
    } if parent_ids else {}
    sales_order_map = {
        row.id: row
        for row in db.execute(select(SalesOrder).where(SalesOrder.id.in_(sales_order_ids))).scalars().all()
    } if sales_order_ids else {}
    quality_task_map = {
        row.production_task_id: row
        for row in db.execute(select(QualityTask).where(QualityTask.production_task_id.in_(task_ids))).scalars().all()
    } if task_ids else {}
    inventory_request_map = {
        row.source_id: row
        for row in db.execute(
            select(InventoryOperationRequest)
            .where(
                InventoryOperationRequest.source_type == "production_task",
                InventoryOperationRequest.source_id.in_(task_ids or [-1]),
                InventoryOperationRequest.request_category.in_(["production_finished_inbound", "production_quality_inbound"]),
                InventoryOperationRequest.status.in_(["pending", "completed"]),
            )
            .order_by(InventoryOperationRequest.id.desc())
        ).scalars().all()
    } if task_ids else {}
    return api_response(
        data={
            "items": [
            {
                "id": row.id,
                "task_no": row.task_no,
                "source_type": row.source_type,
                "sales_order_id": row.sales_order_id,
                "sales_order_no": sales_order_map[row.sales_order_id].order_no if row.sales_order_id in sales_order_map else None,
                "order_type": sales_order_map[row.sales_order_id].order_type if row.sales_order_id in sales_order_map else None,
                "order_type_color": order_type_style(sales_order_map[row.sales_order_id].order_type).get("color") if row.sales_order_id in sales_order_map else None,
                "order_type_label": order_type_style(sales_order_map[row.sales_order_id].order_type).get("label") if row.sales_order_id in sales_order_map else None,
                "product_id": row.product_id,
                "product_name": row.product.name,
                "parent_task_id": row.parent_task_id,
                "parent_task_no": parent_map[row.parent_task_id].task_no if row.parent_task_id in parent_map else None,
                "planned_quantity": float(row.planned_quantity),
                "produced_quantity": float(row.produced_quantity),
                "scrap_quantity": float(row.scrap_quantity),
                "direct_labor_cost": float(row.direct_labor_cost or 0),
                "manufacturing_overhead_cost": float(row.manufacturing_overhead_cost or 0),
                "status": row.status,
                "status_label": status_label(row.status),
                "batch_no": row.batch_no,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "preliminary_acceptance_note": row.preliminary_acceptance_note,
                "requires_quality": row.requires_quality,
                "quality_task_id": quality_task_map[row.id].id if row.id in quality_task_map else None,
                "quality_task_no": quality_task_map[row.id].quality_no if row.id in quality_task_map else None,
                "quality_task_status": quality_task_map[row.id].status if row.id in quality_task_map else None,
                "quality_task_status_label": status_label(quality_task_map[row.id].status) if row.id in quality_task_map else None,
                "inbound_request_id": inventory_request_map[row.id].id if row.id in inventory_request_map else None,
                "inbound_request_status": inventory_request_map[row.id].status if row.id in inventory_request_map else None,
                "can_finish_inbound": (
                    ((row.status == "ready_for_inbound") or (row.status == "waiting_quality" and row.id in quality_task_map and quality_task_map[row.id].status == "completed"))
                    and row.id not in inventory_request_map
                ),
                "flow_steps": production_flow_steps(row),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
            ],
            "pagination": {"page": page, "page_size": page_size, "total": total},
        }
    )


@router.get("/tasks/{task_id}")
def get_task_detail(task_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.product), joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == task_id)
    ).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    parent = db.execute(select(ProductionTask).where(ProductionTask.id == task.parent_task_id)).scalar_one_or_none() if task.parent_task_id else None
    raw_warehouse = _raw_warehouse(db)
    material_ids = [usage.material_id for usage in task.usages]
    inventory_rows = db.execute(
        select(Inventory).where(Inventory.warehouse_id == raw_warehouse.id, Inventory.material_id.in_(material_ids))
    ).scalars().all() if material_ids else []
    inventory_map = {row.material_id: row for row in inventory_rows}
    approver_ids = {
        user_id
        for usage in task.usages
        for user_id in {usage.inventory_approved_by_id, usage.manager_approved_by_id}
        if user_id
    }
    approver_map = {
        row.id: row for row in db.execute(select(User).where(User.id.in_(approver_ids))).scalars().all()
    } if approver_ids else {}
    quality_task = db.execute(select(QualityTask).where(QualityTask.production_task_id == task.id)).scalar_one_or_none()
    inventory_request = db.execute(
        select(InventoryOperationRequest)
        .where(
            InventoryOperationRequest.source_type == "production_task",
            InventoryOperationRequest.source_id == task.id,
            InventoryOperationRequest.request_category.in_(["production_finished_inbound", "production_quality_inbound"]),
        )
        .order_by(InventoryOperationRequest.id.desc())
    ).scalars().first()
    finished_warehouse = task.warehouse or get_factory_warehouse(db)
    sales_order = db.execute(
        select(SalesOrder).options(joinedload(SalesOrder.customer)).where(SalesOrder.id == task.sales_order_id)
    ).scalar_one_or_none() if task.sales_order_id else None
    return api_response(
        data={
            "id": task.id,
            "task_no": task.task_no,
            "source_type": task.source_type,
            "sales_order_id": task.sales_order_id,
            "sales_order": {
                "id": sales_order.id,
                "order_no": sales_order.order_no,
                "customer_name": sales_order.customer.name if sales_order.customer else None,
                "status": sales_order.status,
                "status_label": status_label(sales_order.status),
            } if sales_order else None,
            "product_id": task.product_id,
            "product_name": task.product.name if task.product else None,
            "parent_task_id": task.parent_task_id,
            "parent_task_no": parent.task_no if parent else None,
            "planned_quantity": float(task.planned_quantity),
            "produced_quantity": float(task.produced_quantity),
            "scrap_quantity": float(task.scrap_quantity),
            "direct_labor_cost": float(task.direct_labor_cost or 0),
            "manufacturing_overhead_cost": float(task.manufacturing_overhead_cost or 0),
            "fluctuation_ratio": float(task.fluctuation_ratio),
            "status": task.status,
            "status_label": status_label(task.status),
            "requires_quality": task.requires_quality,
            "batch_no": task.batch_no,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "preliminary_acceptance_note": task.preliminary_acceptance_note,
            "completion_recorded_by_id": task.completion_recorded_by_id,
            "completion_recorded_by_name": task.completion_recorded_by.full_name if task.completion_recorded_by else None,
            "notes": task.notes,
            "quality_task": {
                "id": quality_task.id,
                "quality_no": quality_task.quality_no,
                "status": quality_task.status,
                "status_label": status_label(quality_task.status),
                "can_finish_inbound": ((task.status == "ready_for_inbound") or (task.status == "waiting_quality" and quality_task.status == "completed")) and inventory_request is None,
            } if quality_task else None,
            "inbound_request": {
                "id": inventory_request.id,
                "request_no": inventory_request.request_no,
                "status": inventory_request.status,
                "status_label": status_label(inventory_request.status),
            } if inventory_request else None,
            "finished_warehouse": {
                "id": finished_warehouse.id,
                "code": finished_warehouse.code,
                "name": finished_warehouse.name,
            },
            # 本任务实际归属的入库仓库；历史未指定任务默认廊坊工厂仓。
            # 完工录入要据此告知"质检与入库由哪个仓库接手",
            # 后端建质检任务时用的也正是 task.warehouse_id
            # (services/quality_workflow.py:114)。
            "warehouse": (
                {
                    "id": task.warehouse.id,
                    "code": task.warehouse.code,
                    "name": task.warehouse.name,
                }
                if task.warehouse
                else {
                    "id": finished_warehouse.id,
                    "code": finished_warehouse.code,
                    "name": finished_warehouse.name,
                }
            ),
            "raw_warehouse": {
                "id": raw_warehouse.id,
                "code": raw_warehouse.code,
                "name": raw_warehouse.name,
            },
            "usages": [
                _serialize_usage(
                    task,
                    usage,
                    Decimal(inventory_map.get(usage.material_id).quantity or 0) if inventory_map.get(usage.material_id) else Decimal("0"),
                    approver_map,
                )
                for usage in task.usages
            ],
            "flow_steps": production_flow_steps(task),
        }
    )


@router.post("/fulfillment-demands/{demand_id}/raw-material-demands")
def create_demand_raw_material_demands(
    demand_id: int,
    payload: DemandRawMaterialDemandCreateRequest,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    """为未覆盖的原料缺口追加独立补货需求。"""
    demand = db.execute(
        select(FulfillmentDemand)
        .options(joinedload(FulfillmentDemand.product), joinedload(FulfillmentDemand.sales_order), joinedload(FulfillmentDemand.created_by))
        .where(FulfillmentDemand.id == demand_id, FulfillmentDemand.fulfillment_mode == "production")
        .with_for_update()
    ).unique().scalar_one_or_none()
    if demand is None:
        raise HTTPException(status_code=404, detail="生产补货需求不存在")
    if demand.status not in {"pending", "pending_material"}:
        raise HTTPException(status_code=409, detail="当前生产补货需求已处理")
    raw_demand_nos = _create_raw_material_demands_for_task(
        db,
        task=None,
        demand=demand,
        raw_material_demands=payload.items,
        current_user=current_user,
    )
    if not raw_demand_nos:
        raise HTTPException(status_code=409, detail="请至少提交一条原料补货明细")
    demand.status = "pending_material"
    demand.processed_by_id = current_user.id
    demand.process_note = (
        f"已提交原料补货需求: {', '.join(raw_demand_nos)}。"
        f"原料到货质检合格后,可继续安排生产。"
    )
    db.commit()
    return api_response(
        msg=f"已提交原料补货需求 {', '.join(raw_demand_nos)},生产补货需求已标记待补料",
        data={"demand_id": demand.id, "demand_no": demand.demand_no, "raw_demand_nos": raw_demand_nos, "status": demand.status},
    )


@router.get("/raw-material-shortage")
def get_raw_material_shortage(
    product_id: int,
    planned_quantity: float = Query(default=1, gt=0),
    current_user: User = Depends(require_roles("production", "inventory", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    """转生产任务前,按计划数量计算每种原料的缺口明细(供前端询问是否提原料补货)。"""
    product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    rows = raw_material_shortage_detail(
        db,
        product_id=product_id,
        planned_quantity=Decimal(str(planned_quantity)),
    )
    return api_response(data=rows)


@router.post("/tasks")
def create_task(
    payload: ProductionTaskCreate,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return create_task_service(payload, current_user, db)


@router.get("/special-requests")
def list_special_requests(
    current_user: User = Depends(require_roles("production", "inventory", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    if current_user.role.code == "inventory" and not can_review_factory_material(db, current_user):
        return api_response(data=[])
    query = (
        select(ProductionMaterialUsage)
        .join(ProductionTask, ProductionMaterialUsage.production_task_id == ProductionTask.id)
        .options(
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
            joinedload(ProductionMaterialUsage.material),
        )
        .where(
            ProductionMaterialUsage.special_request_status.in_(["pending_approval", "approved", "rejected"])
            | ProductionMaterialUsage.inventory_approval_status.in_(["pending_approval", "approved", "rejected"])
        )
        .order_by(ProductionMaterialUsage.updated_at.desc())
    )
    if current_user.role.code == "production":
        query = query.where(ProductionTask.created_by_id == current_user.id)
    rows = db.execute(query).unique().scalars().all()
    approver_ids = {
        user_id
        for row in rows
        for user_id in {row.production_task.created_by_id, row.inventory_approved_by_id, row.manager_approved_by_id}
        if user_id
    }
    approver_map = {
        row.id: row for row in db.execute(select(User).where(User.id.in_(approver_ids))).scalars().all()
    } if approver_ids else {}
    return api_response(data=[_serialize_special_request(row, approver_map) for row in rows])


@router.post("/tasks/{task_id}/material-request")
def submit_material_request(
    task_id: int,
    payload: MaterialRequestCreate,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return submit_material_request_service(task_id, payload, current_user, db)


@router.post("/tasks/{task_id}/special-requests")
def submit_special_requests(
    task_id: int,
    payload: SpecialMaterialRequestCreate,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return submit_special_requests_service(task_id, payload, current_user, db)


@router.post("/special-requests/{usage_id}/approve")
def approve_special_request(
    usage_id: int,
    payload: SpecialMaterialApproval,
    current_user: User = Depends(require_roles("inventory", "manager", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_special_request_service(usage_id, payload, current_user, db)


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: int,
    payload: ProductionCompleteRequest,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return complete_task_service(task_id, payload, current_user, db)


@router.post("/tasks/{task_id}/inbound")
def finish_inbound(
    task_id: int,
    payload: ProductionInboundRequest,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.product), joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == task_id)
    ).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    if task.status not in {"waiting_quality", "ready_for_inbound"}:
        raise HTTPException(status_code=409, detail="当前生产任务未处于待入库状态")
    if current_user.role.code == "production" and task.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可处理本人创建的生产任务")

    quality_task = db.execute(select(QualityTask).where(QualityTask.production_task_id == task.id)).scalar_one_or_none()
    if task.requires_quality:
        if quality_task is None:
            raise HTTPException(status_code=404, detail="未找到对应质检任务")
        if quality_task.status != "completed":
            raise HTTPException(status_code=409, detail="质检尚未准许入库")

    warehouse = db.get(Warehouse, task.warehouse_id) if task.warehouse_id else get_factory_warehouse(db)
    if payload.warehouse_id and payload.warehouse_id != warehouse.id:
        override_warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.warehouse_id)).scalar_one_or_none()
        if override_warehouse is None:
            raise HTTPException(status_code=404, detail="入库仓库不存在")
        if override_warehouse.warehouse_type not in {"raw", "finished"}:
            raise HTTPException(status_code=409, detail="生产成品只能入廊坊工厂仓或成品仓")
        warehouse = override_warehouse

    produced_quantity = Decimal(task.produced_quantity or 0)
    scrap_quantity = Decimal(task.scrap_quantity or 0)
    sample_quantity = quality_sample_consumption(db, quality_task.id) if quality_task is not None else Decimal("0")
    inbound_quantity = produced_quantity - sample_quantity
    if inbound_quantity <= 0:
        raise HTTPException(status_code=409, detail="当前生产任务产出数量无效，不能入库")
    create_inventory_operation_request(
        db,
        operation_type="inbound",
        request_category="production_quality_inbound" if quality_task is not None else "production_finished_inbound",
        source_module="production",
        source_type="production_task",
        source_id=task.id,
        source_no=task.task_no,
        warehouse_id=warehouse.id,
        requested_by_id=current_user.id,
        items=[
            {
                "product_id": task.product_id,
                "material_id": None,
                "batch_no": quality_task.batch_no if quality_task else task.batch_no,
                "quantity": float(inbound_quantity),
                "unit_cost": 0,
            }
        ],
        remark=payload.remark,
        recipient_user_ids=_inventory_user_ids_for_warehouse(db, warehouse.id),
        title=f"待执行生产入库 {task.task_no}",
        description=f"生产任务 {task.task_no} 已提交入库申请，请库管执行成品入库。",
    )

    task.status = "ready_for_inbound"
    if payload.preliminary_acceptance_note:
        task.preliminary_acceptance_note = payload.preliminary_acceptance_note
        task.completion_recorded_by_id = current_user.id
    task.notes = f"{task.notes or ''}\n[入库申请] 仓库：{warehouse.name}；备注：{payload.remark or '无'}；初验：{payload.preliminary_acceptance_note or task.preliminary_acceptance_note or '无'}".strip()
    create_notification(
        db,
        title="生产入库申请已提交",
        content=f"生产任务 {task.task_no} 已向库管提交入库申请，数量 {float(inbound_quantity)}。",
        module="production",
        recipient_user_ids=list(dict.fromkeys([task.created_by_id, *_inventory_user_ids_for_warehouse(db, warehouse.id)])),
        created_by_id=current_user.id,
        category="info",
    )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="complete_production_inbound",
        content=f"提交生产任务 {task.task_no} 的成品入库申请",
        after_data={
            "task_id": task.id,
            "task_no": task.task_no,
            "quality_no": quality_task.quality_no if quality_task else None,
            "warehouse_name": warehouse.name,
            "quantity": float(inbound_quantity),
            "sample_quantity": float(sample_quantity),
            "scrap_quantity": float(scrap_quantity),
            "status": task.status,
        },
    )
    db.commit()
    return api_response(data={"id": task.id, "status": task.status, "warehouse_name": warehouse.name})


@router.get("/plans")
def list_plans(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ProductionPlan)
        .options(
            joinedload(ProductionPlan.product),
            joinedload(ProductionPlan.sales_order),
            joinedload(ProductionPlan.assigned_user),
            joinedload(ProductionPlan.converted_task),
        )
        .order_by(ProductionPlan.created_at.desc())
    ).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "plan_no": row.plan_no,
                "plan_name": row.plan_name,
                "period_type": row.period_type,
                "period_start": row.period_start.isoformat() if row.period_start else None,
                "period_end": row.period_end.isoformat() if row.period_end else None,
                "product_id": row.product_id,
                "product_name": row.product.name if row.product else None,
                "sales_order_id": row.sales_order_id,
                "sales_order_no": row.sales_order.order_no if row.sales_order else None,
                "assigned_user_id": row.assigned_user_id,
                "assigned_user_name": row.assigned_user.full_name if row.assigned_user else None,
                "planned_quantity": float(row.planned_quantity or 0),
                "scheduled_start_at": row.scheduled_start_at.isoformat() if row.scheduled_start_at else None,
                "scheduled_end_at": row.scheduled_end_at.isoformat() if row.scheduled_end_at else None,
                "priority": row.priority,
                "line_name": row.line_name,
                "scheduling_note": row.scheduling_note,
                "detail_json": row.detail_json or {},
                "converted_task_id": row.converted_task_id,
                "converted_task_no": row.converted_task.task_no if row.converted_task else None,
                "status": row.status,
                "status_label": status_label(row.status),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/plans/{plan_id}/export")
def export_plan(plan_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = db.execute(
        select(ProductionPlan)
        .options(
            joinedload(ProductionPlan.product),
            joinedload(ProductionPlan.sales_order),
            joinedload(ProductionPlan.assigned_user),
            joinedload(ProductionPlan.created_by),
            joinedload(ProductionPlan.updated_by),
        )
        .where(ProductionPlan.id == plan_id)
    ).unique().scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="生产计划不存在")
    detail = plan.detail_json or {}

    def fmt(value):
        return value.strftime("%Y-%m-%d %H:%M") if value else "-"

    lines = [
        "生产计划详情",
        "=" * 36,
        f"计划号：{plan.plan_no}",
        f"计划名称：{plan.plan_name or '-'}",
        f"周期类型：{ {'annual': '年度计划', 'quarterly': '季度计划', 'monthly': '月度计划', 'weekly': '周度计划', 'custom': '自定义计划'}.get(plan.period_type, plan.period_type) }",
        f"计划周期：{fmt(plan.period_start)} 至 {fmt(plan.period_end)}",
        f"计划开工：{fmt(plan.scheduled_start_at)}",
        f"计划完工：{fmt(plan.scheduled_end_at)}",
        f"产品：{plan.product.name if plan.product else plan.product_id}",
        f"计划数量：{float(plan.planned_quantity or 0)}",
        f"关联客户订单：{plan.sales_order.order_no if plan.sales_order else '-'}",
        f"责任人：{plan.assigned_user.full_name if plan.assigned_user else '-'}",
        f"优先级：{ {'low': '低', 'normal': '普通', 'high': '高', 'urgent': '紧急'}.get(plan.priority, plan.priority) }",
        f"产线/班组：{plan.line_name or '-'}",
        "",
        "排产与执行说明",
        "-" * 36,
        f"排产说明：{plan.scheduling_note or '-'}",
        f"排产节奏：{detail.get('arrangement') or '-'}",
        f"班次安排：{detail.get('shift') or '-'}",
        f"计划细节说明：{detail.get('target_note') or '-'}",
        "",
        "调整记录",
        "-" * 36,
        f"当前状态：{status_label(plan.status)}",
        f"创建人：{plan.created_by.full_name if plan.created_by else '-'}",
        f"最近调整人：{plan.updated_by.full_name if plan.updated_by else '-'}",
        f"创建时间：{fmt(plan.created_at)}",
        f"更新时间：{fmt(plan.updated_at)}",
    ]
    content = "\n".join(lines)
    filename = quote(f"{plan.plan_no}-生产计划.txt")
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/plan-users")
def list_plan_users(
    current_user: User = Depends(require_roles("production", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(Role.code.in_(["production", "system_admin"]), User.is_active.is_(True))
        .order_by(User.full_name.asc(), User.id.asc())
    ).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "full_name": row.full_name,
                "role_code": row.role.code if row.role else None,
            }
            for row in rows
        ]
    )


@router.get("/fulfillment-demands")
def list_fulfillment_demands(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_group: str = Query(default="pending", pattern="^(pending|completed)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(FulfillmentDemand)
        .options(joinedload(FulfillmentDemand.product), joinedload(FulfillmentDemand.sales_order))
        .where(
            FulfillmentDemand.fulfillment_mode == "production",
            FulfillmentDemand.status != "cancelled",
        )
        .order_by(FulfillmentDemand.created_at.desc(), FulfillmentDemand.id.desc())
    )
    if current_user.role.code == "production":
        query = query.where(
            or_(
                FulfillmentDemand.assigned_user_id == current_user.id,
                FulfillmentDemand.assigned_user_id.is_(None),
                select(TodoItem.id)
                .where(
                    TodoItem.business_type == "fulfillment_demand",
                    TodoItem.business_id == FulfillmentDemand.id,
                    TodoItem.assigned_user_id == current_user.id,
                    TodoItem.status != "completed",
                )
                .exists(),
            )
        )
    if status_group == "completed":
        query = query.where(FulfillmentDemand.status == "converted")
    else:
        query = query.where(FulfillmentDemand.status != "converted")
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(
        query.offset((page - 1) * page_size).limit(page_size)
    ).unique().scalars().all()

    low_stock_rows = db.execute(
        select(TodoItem)
        .where(TodoItem.business_type == "low_stock_replenishment", TodoItem.status != "completed", TodoItem.module == "production")
        .order_by(TodoItem.created_at.desc(), TodoItem.id.desc())
    ).scalars().all() if status_group == "pending" else []
    finished_warehouse = _finished_warehouse(db)
    product_ids = [row.product_id for row in rows if row.product_id]
    inventory_map = {
        row.product_id: row
        for row in db.execute(
            select(Inventory).where(Inventory.warehouse_id == finished_warehouse.id, Inventory.product_id.in_(product_ids or [-1]))
        ).scalars().all()
    } if product_ids else {}
    purchase_in_transit = purchase_in_transit_map(db)

    # 批量计算最大可产:一次预取 BOM/原料库存/生产占用,内存推导
    max_producible_map = _batch_max_producible_map(db, product_ids)
    # 批量推导补货进度:一次预取关联采购单/生产任务/付款金额
    progress_map = _batch_demand_progress_map(db, [row.id for row in rows])

    def _shortage_level(shortage: float, available: float) -> str:
        """红=完全不足 / 黄=部分不足 / 绿=充足"""
        if shortage <= 0:
            return "green"
        if available <= 0:
            return "red"
        if available < shortage:
            return "yellow"
        return "green"

    data = [
            {
                "id": row.id,
                "demand_no": row.demand_no,
                "demand_type": "sales_shortage",
                "source_type": "sales_shortage",
                "sales_order_id": row.sales_order_id,
                "sales_order_no": row.sales_order.order_no if row.sales_order else None,
                "order_type": row.sales_order.order_type if row.sales_order else None,
                "order_type_color": order_type_style(row.sales_order.order_type).get("color") if row.sales_order else None,
                "order_type_label": order_type_style(row.sales_order.order_type).get("label") if row.sales_order else None,
                "product_id": row.product_id,
                "product_name": row.product.name if row.product else None,
                "shortage_quantity": float(row.shortage_quantity or 0),
                # 分批补货进度:shortage_quantity 是"剩余待补量",单看它看不出
                # "原需求多少、已补多少"。required_quantity 缺省时回退为剩余量,
                # 保证老数据也能算出合理进度。
                "required_quantity": float(row.required_quantity or row.shortage_quantity or 0),
                "fulfilled_quantity": float(row.fulfilled_quantity or 0),
                "pending_quantity": float(
                    max(
                        Decimal(row.required_quantity or row.shortage_quantity or 0)
                        - Decimal(row.fulfilled_quantity or 0),
                        Decimal("0"),
                    )
                ),
                # max_producible_map 的键是 product_id(见 _batch_max_producible_map 入参),
                # 原先用 row.id(补货需求ID)取值 —— 两套ID几乎永不相等,于是恒取到默认 0,
                # 导致"最大可产"永远显示 0、"原料状况"永远是红,
                # 而转任务弹窗走 raw_material_shortage_detail 另一条路径算得出真实缺口,
                # 于是出现"显示原料不足却能正常转任务"的矛盾。
                "max_producible_quantity": float(max_producible_map.get(row.product_id, 0)),
                "shortage_level": _shortage_level(
                    float(row.shortage_quantity or 0), float(max_producible_map.get(row.product_id, 0))
                ),
                "safety_stock_qty": float(row.product.safety_stock_qty or 0) if row.product else 0,
                "expected_available_quantity": float(
                    (
                        Decimal((inventory_map.get(row.product_id).quantity if inventory_map.get(row.product_id) else 0) or 0)
                        - Decimal((inventory_map.get(row.product_id).reserved_quantity if inventory_map.get(row.product_id) else 0) or 0)
                        + Decimal(purchase_in_transit.get(stock_key(product_id=row.product_id), 0))
                    )
                ),
                "status": row.status,
                "status_label": status_label(row.status),
                "progress_status": progress_map.get(row.id, {}).get("progress_status"),
                "progress_label": progress_map.get(row.id, {}).get("progress_label"),
                "related_no": progress_map.get(row.id, {}).get("related_no"),
                "related_status_label": progress_map.get(row.id, {}).get("related_status_label"),
                "assigned_user_id": row.assigned_user_id,
                "submitted_by_name": row.created_by.full_name if row.created_by else None,
                "process_note": row.process_note,
                "cancel_reason": row.cancel_reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
    ]
    for todo in low_stock_rows:
        raw_description = todo.description or ""
        payload = {}
        if "|replenishment:" in raw_description:
            try:
                payload = json.loads(raw_description.split("|replenishment:", 1)[1])
            except Exception:
                payload = {}
        data.append(
            {
                "id": f"low-{todo.id}",
                "low_stock_todo_id": todo.id,
                "demand_no": f"AQ-{todo.id}",
                "demand_type": "low_stock_product",
                "source_type": "low_stock_replenishment",
                "sales_order_id": None,
                "sales_order_no": None,
                "product_id": payload.get("product_id"),
                "product_name": payload.get("product_name") or todo.title,
                "shortage_quantity": float(payload.get("shortage_quantity") or 0),
                "safety_stock_qty": float(payload.get("safety_stock_qty") or 0),
                "expected_available_quantity": float(payload.get("expected_available_quantity") or 0),
                "status": "pending",
                "status_label": "待处理",
                "assigned_user_id": todo.assigned_user_id,
                "process_note": raw_description.split("|replenishment:", 1)[0],
                "cancel_reason": None,
                "created_at": todo.created_at.isoformat() if todo.created_at else None,
            }
        )
    return api_response(
        data={
            "items": data,
            "pagination": {"page": page, "page_size": page_size, "total": total + len(low_stock_rows)},
        }
    )


@router.get("/fulfillment-demands/{demand_id}/material-progress")
def get_fulfillment_demand_material_progress(
    demand_id: int,
    current_user: User = Depends(require_roles("production", "inventory", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    """生产补货需求转建任务时展示原料采购处理情况。

    每种原料:需求数量、已提交补货数量、对应采购订单处理状态
    (待处理 / 已下单 / 已到货 / 已完成=已入库)。
    """
    demand = db.execute(
        select(FulfillmentDemand)
        .options(joinedload(FulfillmentDemand.product))
        .where(FulfillmentDemand.id == demand_id, FulfillmentDemand.fulfillment_mode == "production")
    ).unique().scalar_one_or_none()
    if demand is None:
        raise HTTPException(status_code=404, detail="生产补货需求不存在")

    from services.material_replenishment_service import material_demand_progress
    return api_response(data=material_demand_progress(db, demand))


@router.put("/fulfillment-demands/{demand_id}")
def update_fulfillment_demand(
    demand_id: int,
    payload: FulfillmentDemandUpdateRequest,
    current_user: User = Depends(require_roles("production", "inventory", "system_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id == demand_id)).scalar_one_or_none()
    if row is None or row.fulfillment_mode != "production":
        raise HTTPException(status_code=404, detail="补货需求不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="仅待处理的补货需求可修改数量")
    old_qty = float(row.shortage_quantity or 0)
    row.shortage_quantity = Decimal(str(payload.shortage_quantity))
    if payload.note:
        row.process_note = f"数量调整: {old_qty} → {float(row.shortage_quantity)}。{payload.note}"
    else:
        row.process_note = f"数量调整: {old_qty} → {float(row.shortage_quantity)}。"
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="update_fulfillment_demand",
        content=f"调整生产补货需求 {row.demand_no} 数量 {old_qty} → {float(row.shortage_quantity)}",
        after_data={"demand_id": row.id, "shortage_quantity": float(row.shortage_quantity)},
    )
    db.commit()
    return api_response(data={"id": row.id, "demand_no": row.demand_no, "shortage_quantity": float(row.shortage_quantity)})


@router.post("/fulfillment-demands/{demand_id}/cancel")
def cancel_fulfillment_demand(
    demand_id: int,
    payload: FulfillmentDemandCancelRequest,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id == demand_id)).scalar_one_or_none()
    if row is None or row.fulfillment_mode != "production":
        raise HTTPException(status_code=404, detail="补货需求不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="当前补货需求已处理")
    if row.assigned_user_id and row.assigned_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可处理分配给本人的补货需求")
    row.status = "cancelled"
    row.processed_by_id = current_user.id
    row.cancel_reason = payload.reason.strip()
    complete_todos(db, business_type="fulfillment_demand", business_id=row.id)
    recipient_user_ids = [current_user.id]
    if row.sales_order and row.sales_order.sales_user_id:
        recipient_user_ids.append(row.sales_order.sales_user_id)
    create_notification(
        db,
        title="生产补货需求已取消",
        content=f"补货需求 {row.demand_no} 已取消，原因：{row.cancel_reason}",
        module="production",
        recipient_user_ids=list(dict.fromkeys(recipient_user_ids)),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="cancel_fulfillment_demand",
        content=f"取消生产补货需求 {row.demand_no}",
        after_data={"demand_id": row.id, "reason": row.cancel_reason, "status": row.status},
    )
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})


@router.post("/fulfillment-demands/{demand_id}/link-task/{task_id}")
def link_fulfillment_demand_task(
    demand_id: int,
    task_id: int,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id == demand_id)).scalar_one_or_none()
    if row is None or row.fulfillment_mode != "production":
        raise HTTPException(status_code=404, detail="补货需求不存在")
    task = db.execute(select(ProductionTask).where(ProductionTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="生产任务不存在")
    if task.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可关联本人创建的生产任务")
    if row.process_note and task.task_no in row.process_note:
        return api_response(data={"id": row.id, "status": row.status})
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="当前补货需求已处理")
    if task.product_id != row.product_id:
        raise HTTPException(status_code=409, detail="生产任务产品与补货需求不一致")
    if task.sales_order_id is None:
        task.sales_order_id = row.sales_order_id
        task.source_type = "sales_order"
    if task.sales_order_id != row.sales_order_id:
        raise HTTPException(status_code=409, detail="生产任务来源客户订单与补货需求不一致")

    row.processed_by_id = current_user.id
    # 关联既有生产任务时同样按"原始需求量 / 已补货量"记账,保持与 create_task 一致
    required_quantity = Decimal(row.required_quantity or 0) or Decimal(row.shortage_quantity or 0)
    row.required_quantity = required_quantity
    row.fulfilled_quantity = Decimal(row.fulfilled_quantity or 0) + Decimal(task.planned_quantity or 0)
    remaining_quantity = max(required_quantity - Decimal(row.fulfilled_quantity or 0), Decimal("0"))
    row.shortage_quantity = remaining_quantity
    # 回填来源需求,使补货需求进度可从任务实时推导
    if task.fulfillment_demand_id is None:
        task.fulfillment_demand_id = row.id
    row.process_note = (
        f"已转生产任务 {task.task_no}，本批 {float(task.planned_quantity or 0)}，"
        f"累计已补 {float(row.fulfilled_quantity)}/{float(required_quantity)}，剩余 {float(remaining_quantity)}。"
    )
    if remaining_quantity <= 0:
        row.status = "converted"
        complete_todos(db, business_type="fulfillment_demand", business_id=row.id)
    else:
        row.status = "pending"
    recipient_user_ids = [current_user.id]
    if row.sales_order and row.sales_order.sales_user_id:
        recipient_user_ids.append(row.sales_order.sales_user_id)
    create_notification(
        db,
        title="生产补货需求已转生产任务",
        content=f"补货需求 {row.demand_no} 已转为生产任务 {task.task_no}。",
        module="production",
        recipient_user_ids=list(dict.fromkeys(recipient_user_ids)),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="production",
        action="convert_fulfillment_demand",
        content=f"补货需求 {row.demand_no} 关联生产任务 {task.task_no}",
        after_data={"demand_id": row.id, "production_task_id": task.id, "status": row.status},
    )
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})


@router.post("/plans")
def create_plan(
    payload: ProductionPlanCreate,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return create_plan_service(payload, current_user, db)


@router.put("/plans/{plan_id}")
def update_plan(
    plan_id: int,
    payload: ProductionPlanUpdate,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return update_plan_service(plan_id, payload, current_user, db)


@router.post("/plans/{plan_id}/convert-task")
def convert_plan_to_task(
    plan_id: int,
    current_user: User = Depends(require_roles("production")),
    db: Session = Depends(get_db),
):
    return convert_plan_to_task_service(plan_id, current_user, db)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    payload: ProductionTaskCancelRequest,
    current_user: User = Depends(require_roles("production", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    return cancel_task_service(task_id, payload, current_user, db)
