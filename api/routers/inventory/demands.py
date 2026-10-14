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
from services.document_status import sync_sales_order_process_status





router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.post("/fulfillment-demands")
def create_manual_demand(
    payload: FulfillmentDemandManualCreateRequest,
    current_user: User = Depends(require_roles("inventory", "production", "system_admin")),
    db: Session = Depends(get_db),
):
    """库管/生产手动提补货需求(自动按产品类型路由到采购/生产)。"""
    product = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    if not product.is_active:
        raise HTTPException(status_code=409, detail="产品已停用,不能提补货需求")
    try:
        demand = create_manual_fulfillment_demand(
            db,
            product=product,
            shortage_quantity=Decimal(str(payload.shortage_quantity)),
            remark=payload.remark,
            current_user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return api_response(
        data={
            "id": demand.id,
            "demand_no": demand.demand_no,
            "product_id": product.id,
            "product_name": product.name,
            "shortage_quantity": float(demand.shortage_quantity),
            "required_quantity": float(demand.required_quantity or 0),
            "fulfilled_quantity": float(demand.fulfilled_quantity or 0),
            "fulfillment_mode": demand.fulfillment_mode,
            "status": demand.status,
        },
        msg=f"补货需求 {demand.demand_no} 已提交,已通知{'采购' if demand.fulfillment_mode == 'trade' else '生产'}处理",
    )


@router.get("/fulfillment-demands")
def list_my_fulfillment_demands(
    current_user: User = Depends(require_roles("inventory", "production", "system_admin")),
    db: Session = Depends(get_db),
):
    """库管/生产查看自己提交的补货需求(含进度状态)。"""
    rows = db.execute(
        select(FulfillmentDemand)
        .options(joinedload(FulfillmentDemand.product), joinedload(FulfillmentDemand.sales_order))
        .where(FulfillmentDemand.created_by_id == current_user.id)
        .order_by(FulfillmentDemand.created_at.desc(), FulfillmentDemand.id.desc())
    ).unique().scalars().all()
    items = []
    for row in rows:
        progress = resolve_demand_progress(db, row)
        items.append(
            {
                "id": row.id,
                "demand_no": row.demand_no,
                "product_id": row.product_id,
                "product_name": row.product.name if row.product else None,
                "shortage_quantity": float(row.shortage_quantity or 0),
                # 分批补货进度:原始需求量 / 已补货量 / 待补货量
                "required_quantity": progress.get("required_quantity"),
                "fulfilled_quantity": progress.get("fulfilled_quantity"),
                "pending_quantity": progress.get("pending_quantity"),
                "fulfillment_mode": row.fulfillment_mode,
                "fulfillment_mode_label": "采购" if row.fulfillment_mode == "trade" else "生产",
                "sales_order_id": row.sales_order_id,
                "sales_order_no": row.sales_order.order_no if row.sales_order else None,
                "status": row.status,
                "status_label": "待补料" if row.status == "pending_material" else status_label(row.status),
                "progress_status": progress.get("progress_status"),
                "progress_label": progress.get("progress_label"),
                "related_no": progress.get("related_no"),
                "related_status_label": progress.get("related_status_label"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return api_response(data=items)


