from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from database import get_db
from dependencies import get_current_user, require_roles
from models import (
    Customer,
    FulfillmentDemand,
    Inventory,
    InventoryDispatchQueue,
    InventoryOperationRequest,
    InventoryTransaction,
    Material,
    Product,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Role,
    OperationLog,
    ReceivableRecord,
    order_type_style,
    SalesOrderItem,
    SalesOrder,
    StockTransfer,
    StockTakeItem,
    StockTakeTask,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.inventory import InventoryManualOperationCreateRequest, InventoryOperationApprovalRequest, InventoryOperationHandleRequest, InventoryOperationRejectRequest, StockTakeCreate, StockTakeSubmit, StockTransferCreateRequest, TransferQualityRequest
from schemas.production import FulfillmentDemandManualCreateRequest, SalesOrderReplenishmentCreateRequest
from schemas.sales import PlacementActionRequest, QueueActionRequest, SalesShipmentRequest
from services.core import apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, sync_low_stock_alerts, write_operation_log
from services.replenishment_service import create_manual_fulfillment_demand, ensure_fulfillment_demand_routing, resolve_demand_progress
from services.display import clean_display_text, inventory_request_category_label, status_label, transaction_type_label
from services.display import payment_type_label
from services.organization import get_inventory_scope_warehouse_ids, user_in_scope, warehouse_in_scope
from services.workflow import (
    approve_inventory_operation_request,
    complete_inventory_operation_request,
    create_inventory_operation_request,
    normalize_inventory_operation_request_approval,
)
from services.time_utils import now_local
from services.warehouse_lookup import get_finished_warehouse
from services.sales_service import _create_fulfillment_demands, _sales_received_amount, ensure_sales_order_fulfillment_demands
from services.document_status import sync_sales_order_process_status
from services.inventory_dimensions import sync_inventory_dimensions

from api.routers.inventory._common import _apply_stock_take_submission, _assigned_operation_request_ids, _can_access_operation_request, _expected_available_maps, _operation_source_detail, _scoped_warehouse_ids, _serialize_stock, _serialize_stock_take_item, _serialize_stock_take_task, _serialize_stock_take_template_item, _serialize_transaction, _stock_key





router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/stocks")
def list_stocks(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    warehouse_id: int | None = Query(default=None),
    item_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    alert_only: bool = Query(default=False),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    sync_low_stock_alerts(db)
    sync_inventory_dimensions(db)
    db.commit()
    query = (
        select(Inventory)
        .options(joinedload(Inventory.product), joinedload(Inventory.material), joinedload(Inventory.warehouse))
        .outerjoin(Product, Inventory.product_id == Product.id)
        .outerjoin(Material, Inventory.material_id == Material.id)
        .join(Warehouse, Inventory.warehouse_id == Warehouse.id)
        .order_by(Inventory.updated_at.desc())
    )
    warehouse_ids = _scoped_warehouse_ids(db, current_user)
    if warehouse_ids is not None:
        query = query.where(Inventory.warehouse_id.in_(warehouse_ids or [-1]))
    if warehouse_id:
        query = query.where(Inventory.warehouse_id == warehouse_id)
    if item_type == "product":
        query = query.where(Inventory.product_id.is_not(None))
    elif item_type == "material":
        query = query.where(Inventory.material_id.is_not(None))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.where(or_(Product.name.ilike(pattern), Product.product_code.ilike(pattern), Material.name.ilike(pattern), Material.material_code.ilike(pattern), Warehouse.name.ilike(pattern), Warehouse.code.ilike(pattern)))
    if alert_only:
        query = query.where(
            or_(
                (Inventory.product_id.is_not(None)) & ((Inventory.quantity - Inventory.reserved_quantity) < Product.safety_stock_qty),
                (Inventory.material_id.is_not(None)) & ((Inventory.quantity - Inventory.reserved_quantity) < Material.safety_stock_qty),
            )
        )
    total = None
    if page is not None:
        total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(query).scalars().all()
    pending_sales, purchase_in_transit = _expected_available_maps(db)
    synthetic_rows = []
    if page is None:
        scoped_ids = warehouse_ids
        if scoped_ids is None:
            scoped_ids = [row[0] for row in db.execute(select(Warehouse.id)).all()]
        existing_keys = {
            (row.warehouse_id, "product", row.product_id) if row.product_id else (row.warehouse_id, "material", row.material_id)
            for row in rows
        }
        warehouses = db.execute(select(Warehouse).where(Warehouse.id.in_(scoped_ids or [-1]))).scalars().all()
        products = db.execute(select(Product)).scalars().all()
        materials = db.execute(select(Material)).scalars().all()
        for warehouse in warehouses:
            for product in products:
                if (warehouse.id, "product", product.id) in existing_keys:
                    continue
                synthetic_rows.append(
                    {
                        "id": -(warehouse.id * 100000000 + product.id * 10 + 1),
                        "warehouse_id": warehouse.id,
                        "warehouse_code": warehouse.code,
                        "warehouse_name": warehouse.name,
                        "item_id": product.id,
                        "item_type": "product",
                        "item_code": product.product_code,
                        "item_name": product.name,
                        "quantity": 0,
                        "reserved_quantity": 0,
                        "available_quantity": 0,
                        "expected_available_quantity": float(Decimal(purchase_in_transit.get(_stock_key(product_id=product.id), 0)) - Decimal(pending_sales.get(_stock_key(product_id=product.id), 0))),
                        "avg_unit_cost": float(product.price or 0), "inventory_amount": 0,
                        "safety_stock_qty": float(product.safety_stock_qty or 0), "below_safety_stock": Decimal(product.safety_stock_qty or 0) > 0, "updated_at": None,
                    }
                )
            for material in materials:
                if (warehouse.id, "material", material.id) in existing_keys:
                    continue
                synthetic_rows.append(
                    {
                    "id": -(warehouse.id * 100000000 + material.id * 10 + 2),
                    "warehouse_id": warehouse.id,
                    "warehouse_code": warehouse.code,
                    "warehouse_name": warehouse.name,
                    "item_id": material.id,
                    "item_type": "material",
                    "item_code": material.material_code,
                    "item_name": material.name,
                    "quantity": 0,
                    "reserved_quantity": 0,
                    "available_quantity": 0,
                    "expected_available_quantity": float(
                        Decimal(purchase_in_transit.get(_stock_key(material_id=material.id), 0))
                        - Decimal(pending_sales.get(_stock_key(material_id=material.id), 0))
                    ),
                    "avg_unit_cost": float(material.price or 0),
                    "inventory_amount": 0,
                    "safety_stock_qty": float(material.safety_stock_qty or 0),
                    "below_safety_stock": Decimal(material.safety_stock_qty or 0) > 0,
                    "updated_at": None,
                    }
                )
    items = [
            _serialize_stock(row, pending_sales=pending_sales, purchase_in_transit=purchase_in_transit)
            for row in rows
        ] + synthetic_rows
    if page is not None:
        items = items[:page_size]
        return api_response(data={"items": items, "pagination": {"page": page, "page_size": page_size, "total": total or len(items)}})
    return api_response(data=items)


@router.get("/transactions")
def list_transactions(
    warehouse_id: int | None = Query(default=None), product_id: int | None = Query(default=None), material_id: int | None = Query(default=None),
    page: int | None = Query(default=None, ge=1), page_size: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    query = select(InventoryTransaction).order_by(InventoryTransaction.created_at.desc())
    warehouse_ids = _scoped_warehouse_ids(db, current_user)
    if warehouse_ids is not None:
        query = query.where(InventoryTransaction.warehouse_id.in_(warehouse_ids or [-1]))
    if warehouse_id:
        query = query.where(InventoryTransaction.warehouse_id == warehouse_id)
    if product_id:
        query = query.where(InventoryTransaction.product_id == product_id)
    if material_id:
        query = query.where(InventoryTransaction.material_id == material_id)
    total = None
    if page is not None:
        total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(query).scalars().all()
    warehouse_ids = {row.warehouse_id for row in rows if row.warehouse_id}
    product_ids = {row.product_id for row in rows if row.product_id}
    material_ids = {row.material_id for row in rows if row.material_id}
    warehouse_map = {
        row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids))).scalars().all()
    } if warehouse_ids else {}
    product_map = {
        row.id: row for row in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
    } if product_ids else {}
    material_map = {
        row.id: row for row in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars().all()
    } if material_ids else {}
    items = [_serialize_transaction(row, warehouse_map, product_map, material_map) for row in rows]
    if page is not None:
        return api_response(data={"items": items, "pagination": {"page": page, "page_size": page_size, "total": total or 0}})
    return api_response(data=items)


@router.get("/operation-requests")
def list_operation_requests(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(InventoryOperationRequest).order_by(InventoryOperationRequest.created_at.desc(), InventoryOperationRequest.id.desc())
    warehouse_ids = _scoped_warehouse_ids(db, current_user)
    if warehouse_ids is not None:
        assigned_ids = _assigned_operation_request_ids(db, current_user)
        query = query.where(
            or_(
                InventoryOperationRequest.warehouse_id.in_(warehouse_ids or [-1]),
                InventoryOperationRequest.id.in_(assigned_ids or [-1]),
            )
        )
    rows = db.execute(query).scalars().all()
    normalized_any = False
    for row in rows:
        normalized_any = normalize_inventory_operation_request_approval(row, db) or normalized_any
    if normalized_any:
        db.commit()
    return api_response(
        data=[
            {
                "id": row.id,
                "request_no": row.request_no,
                "operation_type": row.operation_type,
                "request_category": row.request_category,
                "request_category_label": inventory_request_category_label(row.request_category),
                "source_module": row.source_module,
                "source_type": row.source_type,
                "source_id": row.source_id,
                "source_no": row.source_no,
                "warehouse_id": row.warehouse_id,
                "status": row.status,
                "status_label": status_label(row.status),
                "remark": row.remark,
                "handled_remark": row.handled_remark,
                "adjust_reason": row.adjust_reason,
                "quantity_difference_remark": row.quantity_difference_remark,
                "original_items": row.original_items_json or [],
                "handled_items": row.handled_items_json or [],
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
            for row in rows
        ]
    )


@router.get("/operation-requests/{request_id}")
def get_operation_request_detail(request_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(select(InventoryOperationRequest).where(InventoryOperationRequest.id == request_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="库存作业请求不存在")
    if not _can_access_operation_request(db, current_user, row):
        raise HTTPException(status_code=403, detail="仅可查看本仓库范围内的库存作业")
    if normalize_inventory_operation_request_approval(row, db):
        db.commit()
    source_detail = _operation_source_detail(db, row)
    return api_response(
        data={
            "id": row.id,
            "request_no": row.request_no,
            "operation_type": row.operation_type,
            "request_category": row.request_category,
            "request_category_label": inventory_request_category_label(row.request_category),
            "source_module": row.source_module,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_no": row.source_no,
            "warehouse_id": row.warehouse_id,
            "status": row.status,
            "status_label": status_label(row.status),
            "remark": row.remark,
            "handled_remark": row.handled_remark,
            "adjust_reason": row.adjust_reason,
            "quantity_difference_remark": row.quantity_difference_remark,
            "original_items": row.original_items_json or [],
            "handled_items": row.handled_items_json or [],
            "source_detail": source_detail,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    )


@router.post('/operation-requests/{request_id}/complete')
def complete_operation_request(
    request_id: int,
    payload: InventoryOperationHandleRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(InventoryOperationRequest).where(InventoryOperationRequest.id == request_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="库存作业请求不存在")
    if not _can_access_operation_request(db, current_user, row):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的库存作业")
    return complete_inventory_operation_request(request_id, payload, current_user, db)


@router.post("/operation-requests/{request_id}/reject")
def reject_operation_request(
    request_id: int,
    payload: InventoryOperationRejectRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(InventoryOperationRequest).where(InventoryOperationRequest.id == request_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="库存作业请求不存在")
    if not _can_access_operation_request(db, current_user, row):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的库存作业")
    if row.status not in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="当前库存作业状态不允许驳回")
    row.status = "rejected"
    row.handled_by_id = current_user.id
    row.completed_at = now_local()
    row.handled_remark = payload.reject_reason
    if row.request_category == "production_material_issue":
        if row.source_type == "production_task":
            task = db.execute(
                select(ProductionTask)
                .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
                .where(ProductionTask.id == row.source_id)
            ).unique().scalar_one_or_none()
            if task is not None:
                for usage in task.usages:
                    if Decimal(usage.actual_quantity or 0) > 0:
                        continue
                    usage.inventory_approval_status = "rejected"
                    usage.inventory_approved_by_id = current_user.id
                    usage.inventory_approved_at = now_local()
                    usage.inventory_remark = payload.reject_reason
                    if usage.special_request_status == "pending_approval":
                        usage.special_request_status = "rejected"
                task.status = "pending_material"
                create_notification(
                    db,
                    title="领料申请已拒绝",
                    content=f"生产任务 {task.task_no} 的领料出库申请已拒绝，原因：{payload.reject_reason}",
                    module="production",
                    recipient_user_ids=[task.created_by_id],
                    created_by_id=current_user.id,
                    category="warning",
                )
        elif row.source_type == "production_material_usage":
            usage = db.execute(
                select(ProductionMaterialUsage)
                .options(joinedload(ProductionMaterialUsage.production_task), joinedload(ProductionMaterialUsage.material))
                .where(ProductionMaterialUsage.id == row.source_id)
            ).unique().scalar_one_or_none()
            if usage is not None:
                usage.inventory_approval_status = "rejected"
                usage.inventory_approved_by_id = current_user.id
                usage.inventory_approved_at = now_local()
                usage.inventory_remark = payload.reject_reason
                if usage.special_request_status == "pending_approval":
                    usage.special_request_status = "rejected"
                if usage.production_task is not None:
                    usage.production_task.status = "pending_material"
                    create_notification(
                        db,
                        title="领料申请已拒绝",
                        content=(
                            f"生产任务 {usage.production_task.task_no} 的原料 "
                            f"{usage.material.name if usage.material else usage.material_id} 出库申请已拒绝，原因：{payload.reject_reason}"
                        ),
                        module="production",
                        recipient_user_ids=[usage.production_task.created_by_id],
                        created_by_id=current_user.id,
                        category="warning",
                    )
    complete_todos(db, business_type="inventory_operation_request", business_id=row.id)
    if row.requested_by_id:
        create_notification(
            db,
            title="库存作业请求已驳回",
            content=f"库存作业 {row.request_no} 已驳回，原因：{payload.reject_reason}",
            module=row.source_module or "inventory",
            recipient_user_ids=[row.requested_by_id],
            created_by_id=current_user.id,
            category="warning",
        )
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})


@router.post("/manual-operations")
def create_manual_operation(
    payload: InventoryManualOperationCreateRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    if not warehouse_in_scope(db, current_user, payload.warehouse_id):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的手工出入库")
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少填写一条库存明细")
    for item in payload.items:
        if bool(item.product_id) == bool(item.material_id):
            raise HTTPException(status_code=409, detail="每条手工出入库明细必须且只能选择一种库存对象")
        if item.product_id:
            exists = db.execute(select(Product.id).where(Product.id == item.product_id)).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=404, detail="手工出入库产品不存在")
        if item.material_id:
            exists = db.execute(select(Material.id).where(Material.id == item.material_id)).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=404, detail="手工出入库原料不存在")

    request = InventoryOperationRequest(
        request_no=next_business_no(db, "RK" if payload.operation_type == "inbound" else "CK"),
        operation_type=payload.operation_type,
        request_category="manual_inbound_request" if payload.operation_type == "inbound" else "manual_outbound_request",
        source_module="inventory",
        source_type="manual_operation",
        source_id=0,
        source_no=(payload.source_no or "").strip() or None,
        warehouse_id=payload.warehouse_id,
        requested_by_id=current_user.id,
        status="pending",
        remark=payload.remark,
        original_items_json=[
            {
                "product_id": item.product_id,
                "material_id": item.material_id,
                "batch_no": item.batch_no,
                "quantity": float(item.quantity),
                "unit_cost": float(item.unit_cost or 0),
            }
            for item in payload.items
        ],
        handled_items_json=[],
    )
    db.add(request)
    db.flush()
    request.source_id = request.id
    request.source_no = request.source_no or request.request_no

    return complete_inventory_operation_request(
        request.id,
        InventoryOperationHandleRequest(
            items=payload.items,
            handled_remark=(payload.remark or "").strip() or None,
            adjust_reason=None,
        ),
        current_user,
        db,
    )


@router.post("/stock-takes")
def create_stock_take(
    payload: StockTakeCreate | None = Body(default=None),
    warehouse_id: int | None = None,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    target_warehouse_id = payload.warehouse_id if payload else warehouse_id
    if not warehouse_in_scope(db, current_user, target_warehouse_id):
        raise HTTPException(status_code=403, detail="仅可创建本仓库范围内的盘点任务")
    if target_warehouse_id is None:
        raise HTTPException(status_code=400, detail="请选择仓库")
    task = StockTakeTask(
        task_no=next_business_no(db, "PD"),
        warehouse_id=target_warehouse_id,
        submitted_by_id=current_user.id,
        status="completed" if payload and payload.items else "pending",
    )
    db.add(task)
    db.flush()
    inventories = db.execute(select(Inventory).where(Inventory.warehouse_id == target_warehouse_id)).scalars().all()
    for inventory in inventories:
        db.add(
            StockTakeItem(
                stock_take_task_id=task.id,
                inventory_id=inventory.id,
                system_quantity=inventory.quantity,
                counted_quantity=inventory.quantity,
                difference_quantity=0,
            )
        )
    db.flush()

    if payload and payload.items:
        result = _apply_stock_take_submission(task, payload, current_user, db)
        db.commit()
        return api_response(data={"id": task.id, "task_no": task.task_no, **result})
    db.commit()
    return api_response(data={"id": task.id, "task_no": task.task_no})


@router.get("/stock-takes/template")
def get_stock_take_template(
    warehouse_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == warehouse_id)).scalar_one_or_none()
    if warehouse is None:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if not warehouse_in_scope(db, current_user, warehouse_id):
        raise HTTPException(status_code=403, detail="仅可查看本仓库范围内的盘点模板")
    inventories = db.execute(
        select(Inventory)
        .options(joinedload(Inventory.product), joinedload(Inventory.material))
        .where(Inventory.warehouse_id == warehouse_id)
        .order_by(Inventory.id.asc())
    ).scalars().all()
    return api_response(
        data={
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.name,
            "items": [_serialize_stock_take_template_item(item) for item in inventories],
        }
    )


@router.get("/stock-takes")
def list_stock_takes(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(StockTakeTask).order_by(StockTakeTask.created_at.desc())
    warehouse_ids = _scoped_warehouse_ids(db, current_user)
    if warehouse_ids is not None:
        query = query.where(StockTakeTask.warehouse_id.in_(warehouse_ids or [-1]))
    rows = db.execute(query).scalars().all()
    warehouse_ids = {row.warehouse_id for row in rows}
    warehouses = {
        row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids))).scalars().all()
    } if warehouse_ids else {}
    items = db.execute(select(StockTakeItem).where(StockTakeItem.stock_take_task_id.in_([row.id for row in rows]))).scalars().all() if rows else []
    items_map: dict[int, list[StockTakeItem]] = {}
    for item in items:
        items_map.setdefault(item.stock_take_task_id, []).append(item)
    return api_response(data=[_serialize_stock_take_task(row, warehouses, items_map) for row in rows])


@router.get("/stock-takes/{task_id}")
def get_stock_take_detail(task_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.execute(select(StockTakeTask).where(StockTakeTask.id == task_id)).scalar_one_or_none()
    if task is not None and not warehouse_in_scope(db, current_user, task.warehouse_id):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的盘点任务")
    if task is None:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    if not warehouse_in_scope(db, current_user, task.warehouse_id):
        raise HTTPException(status_code=403, detail="仅可查看本仓库范围内的盘点任务")
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == task.warehouse_id)).scalar_one_or_none()
    items = db.execute(
        select(StockTakeItem)
        .options(
            joinedload(StockTakeItem.inventory).joinedload(Inventory.product),
            joinedload(StockTakeItem.inventory).joinedload(Inventory.material),
        )
        .where(StockTakeItem.stock_take_task_id == task.id)
        .order_by(StockTakeItem.id.asc())
    ).scalars().all()
    return api_response(
        data={
            "id": task.id,
            "task_no": task.task_no,
            "warehouse_id": task.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else None,
            "status": task.status,
            "status_label": status_label(task.status),
            "difference_note": clean_display_text(task.difference_note, "库存盘点差异调整") if task.difference_note else "",
            "approved_at": task.approved_at.isoformat() if task.approved_at else None,
            "items": [_serialize_stock_take_item(item, item.inventory) for item in items],
        }
    )


@router.put("/stock-takes/{task_id}/items")
def save_stock_take_items(
    task_id: int,
    payload: StockTakeSubmit,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    task = db.execute(select(StockTakeTask).where(StockTakeTask.id == task_id)).scalar_one_or_none()
    if task is not None and not warehouse_in_scope(db, current_user, task.warehouse_id):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的盘点任务")
    if task is None:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    if task.status == "completed":
        raise HTTPException(status_code=409, detail="盘点任务已完成，不能重复修改")
    result = _apply_stock_take_submission(task, payload, current_user, db)
    db.commit()
    return api_response(data=result, msg="盘点任务已完成并完成差异入账")


@router.post("/stock-takes/{task_id}/approve")
def approve_stock_take(task_id: int, current_user: User = Depends(require_roles("inventory", "manager", "chairman")), db: Session = Depends(get_db)):
    task = db.execute(select(StockTakeTask).where(StockTakeTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    if task.status == "completed":
        raise HTTPException(status_code=409, detail="盘点任务已完成")
    items = db.execute(select(StockTakeItem).where(StockTakeItem.stock_take_task_id == task.id)).scalars().all()
    payload = StockTakeSubmit(
        difference_note=task.difference_note,
        items=[
            {
                "stock_take_item_id": item.id,
                "inventory_id": item.inventory_id,
                "counted_quantity": item.counted_quantity,
                "reason": item.reason,
            }
            for item in items
        ],
    )
    result = _apply_stock_take_submission(task, payload, current_user, db)
    db.commit()
    return api_response(msg="盘点任务已完成并完成差异入账", data=result)
@router.post('/operation-requests/{request_id}/approve')
def approve_operation_request(
    request_id: int,
    payload: InventoryOperationApprovalRequest,
    current_user: User = Depends(require_roles('manager', 'system_admin')),
    db: Session = Depends(get_db),
):
    row = db.execute(select(InventoryOperationRequest).where(InventoryOperationRequest.id == request_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail='\u5e93\u5b58\u4f5c\u4e1a\u8bf7\u6c42\u4e0d\u5b58\u5728')
    if not _can_access_operation_request(db, current_user, row):
        raise HTTPException(status_code=403, detail='\u4ec5\u53ef\u5ba1\u6279\u5f53\u524d\u6743\u9650\u8303\u56f4\u5185\u7684\u5e93\u5b58\u4f5c\u4e1a\u8bf7\u6c42')
    return approve_inventory_operation_request(request_id, payload, current_user, db)
