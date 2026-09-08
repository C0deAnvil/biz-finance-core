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

from api.routers.inventory._common import _transfer_status_label





router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.post("/transfers")
def create_stock_transfer(
    payload: StockTransferCreateRequest,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "system_admin")),
    db: Session = Depends(get_db),
):
    """发起移库:源仓库出库,目标仓库质检后入库。"""
    source_warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.source_warehouse_id)).scalar_one_or_none()
    target_warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.target_warehouse_id)).scalar_one_or_none()
    if source_warehouse is None or target_warehouse is None:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if source_warehouse.id == target_warehouse.id:
        raise HTTPException(status_code=409, detail="源仓库与目标仓库不能相同")
    if not warehouse_in_scope(db, current_user, source_warehouse.id):
        raise HTTPException(status_code=403, detail="当前账号无权操作源仓库")
    quantity = Decimal(str(payload.quantity))
    if quantity <= 0:
        raise HTTPException(status_code=409, detail="移库数量必须大于 0")
    if payload.product_id and payload.material_id:
        raise HTTPException(status_code=409, detail="商品与原料只能二选一")
    if not payload.product_id and not payload.material_id:
        raise HTTPException(status_code=409, detail="请选择商品或原料")

    inventory = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == source_warehouse.id,
            Inventory.product_id == payload.product_id,
            Inventory.material_id == payload.material_id,
        )
    ).with_for_update().scalar_one_or_none()
    if inventory is None:
        raise HTTPException(status_code=404, detail="源仓库无对应库存")
    available_quantity = max(Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0), Decimal("0"))
    if quantity > available_quantity:
        raise HTTPException(status_code=409, detail=f"源仓库可用库存不足(可用 {float(available_quantity)})")

    transfer = StockTransfer(
        transfer_no=next_business_no(db, "YK"),
        source_warehouse_id=source_warehouse.id,
        target_warehouse_id=target_warehouse.id,
        product_id=payload.product_id,
        material_id=payload.material_id,
        quantity=quantity,
        status="pending_outbound",
        created_by_id=current_user.id,
        remark=payload.remark,
    )
    db.add(transfer)
    db.commit()
    write_operation_log(
        db,
        user_id=current_user.id,
        module="inventory",
        action="create_stock_transfer",
        content=f"发起移库 {transfer.transfer_no}({source_warehouse.name}→{target_warehouse.name} {float(quantity)})",
        after_data={"transfer_no": transfer.transfer_no, "status": transfer.status},
    )
    return api_response(data={"id": transfer.id, "transfer_no": transfer.transfer_no}, msg=f"移库单 {transfer.transfer_no} 已创建")


@router.get("/transfers")
def list_stock_transfers(
    current_user: User = Depends(require_roles("inventory", "inventory_super", "quality", "quality_super", "system_admin")),
    db: Session = Depends(get_db),
):
    """移库列表:普通库管看自己发起或自己仓库的;超级/质检看全部。"""
    query = (
        select(StockTransfer)
        .options(
            joinedload(StockTransfer.source_warehouse),
            joinedload(StockTransfer.target_warehouse),
            joinedload(StockTransfer.product),
            joinedload(StockTransfer.material),
            joinedload(StockTransfer.created_by),
        )
        .order_by(StockTransfer.created_at.desc())
    )
    role_code = current_user.role.code if current_user.role else ""
    if role_code == "inventory":
        scoped_ids = get_inventory_scope_warehouse_ids(db, current_user)
        if scoped_ids is not None:
            query = query.where(
                or_(
                    StockTransfer.source_warehouse_id.in_(scoped_ids),
                    StockTransfer.target_warehouse_id.in_(scoped_ids),
                    StockTransfer.created_by_id == current_user.id,
                )
            )
    rows = db.execute(query).unique().scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "transfer_no": row.transfer_no,
                "source_warehouse_id": row.source_warehouse_id,
                "source_warehouse_name": row.source_warehouse.name if row.source_warehouse else None,
                "target_warehouse_id": row.target_warehouse_id,
                "target_warehouse_name": row.target_warehouse.name if row.target_warehouse else None,
                "product_id": row.product_id,
                "material_id": row.material_id,
                "item_name": (row.product.name if row.product else None) or (row.material.name if row.material else None),
                "quantity": float(row.quantity),
                "status": row.status,
                "status_label": _transfer_status_label(row.status),
                "created_by_name": row.created_by.full_name if row.created_by else None,
                "quality_result": row.quality_result,
                "reject_reason": row.reject_reason,
                "remark": row.remark,
                "outbound_at": row.outbound_at.isoformat() if row.outbound_at else None,
                "quality_at": row.quality_at.isoformat() if row.quality_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.post("/transfers/{transfer_id}/outbound")
def confirm_transfer_outbound(
    transfer_id: int,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "system_admin")),
    db: Session = Depends(get_db),
):
    """源仓库出库确认:扣减源仓库库存。"""
    transfer = db.execute(select(StockTransfer).where(StockTransfer.id == transfer_id)).scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status_code=404, detail="移库单不存在")
    if transfer.status != "pending_outbound":
        raise HTTPException(status_code=409, detail="当前移库单状态不允许出库")
    if not warehouse_in_scope(db, current_user, transfer.source_warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权操作源仓库")
    inventory = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == transfer.source_warehouse_id,
            Inventory.product_id == transfer.product_id,
            Inventory.material_id == transfer.material_id,
        )
    ).with_for_update().scalar_one_or_none()
    if inventory is None:
        raise HTTPException(status_code=404, detail="源仓库无对应库存")
    available_quantity = max(Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0), Decimal("0"))
    if Decimal(transfer.quantity) > available_quantity:
        raise HTTPException(status_code=409, detail=f"源仓库可用库存不足(可用 {float(available_quantity)})")
    apply_inventory_change(
        db,
        warehouse_id=transfer.source_warehouse_id,
        product_id=transfer.product_id,
        material_id=transfer.material_id,
        quantity=-Decimal(transfer.quantity),
        unit_cost=Decimal(inventory.avg_unit_cost or 0),
        transaction_type="stock_transfer_out",
        related_type="stock_transfer",
        related_id=transfer.id,
        created_by_id=current_user.id,
    )
    transfer.status = "pending_quality"
    transfer.outbound_by_id = current_user.id
    transfer.outbound_at = now_local()
    db.commit()
    return api_response(msg=f"移库单 {transfer.transfer_no} 已出库,等待目标仓库质检")


@router.post("/transfers/{transfer_id}/quality")
def submit_transfer_quality(
    transfer_id: int,
    payload: TransferQualityRequest,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "quality", "quality_super", "system_admin")),
    db: Session = Depends(get_db),
):
    """目标仓库质检:通过→待入库;不通过→退回源仓库。"""
    transfer = db.execute(select(StockTransfer).where(StockTransfer.id == transfer_id)).scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status_code=404, detail="移库单不存在")
    if transfer.status != "pending_quality":
        raise HTTPException(status_code=409, detail="当前移库单状态不允许质检")
    if not warehouse_in_scope(db, current_user, transfer.target_warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权操作目标仓库")
    transfer.quality_by_id = current_user.id
    transfer.quality_at = now_local()
    transfer.quality_result = payload.result
    if payload.result == "passed":
        transfer.status = "pending_inbound"
    else:
        # 质检不通过:回滚源仓库库存,退回发起方
        source_inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == transfer.source_warehouse_id,
                Inventory.product_id == transfer.product_id,
                Inventory.material_id == transfer.material_id,
            )
        ).with_for_update().scalar_one_or_none()
        apply_inventory_change(
            db,
            warehouse_id=transfer.source_warehouse_id,
            product_id=transfer.product_id,
            material_id=transfer.material_id,
            quantity=Decimal(transfer.quantity),
            unit_cost=Decimal(source_inventory.avg_unit_cost or 0) if source_inventory else Decimal("0"),
            transaction_type="stock_transfer_return",
            related_type="stock_transfer",
            related_id=transfer.id,
            created_by_id=current_user.id,
        )
        transfer.status = "returned"
        transfer.reject_reason = payload.reject_reason
        create_notification(
            db,
            title="移库质检不通过",
            content=f"移库单 {transfer.transfer_no} 质检不通过:{payload.reject_reason or '未填写原因'},库存已退回源仓库。",
            module="inventory",
            recipient_user_ids=[transfer.created_by_id],
            created_by_id=current_user.id,
            category="warning",
        )
    db.commit()
    return api_response(msg="质检通过,等待目标仓库入库" if payload.result == "passed" else "质检不通过,已退回源仓库")


@router.post("/transfers/{transfer_id}/inbound")
def confirm_transfer_inbound(
    transfer_id: int,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "system_admin")),
    db: Session = Depends(get_db),
):
    """目标仓库入库确认:增加目标仓库库存。"""
    transfer = db.execute(select(StockTransfer).where(StockTransfer.id == transfer_id)).scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status_code=404, detail="移库单不存在")
    if transfer.status != "pending_inbound":
        raise HTTPException(status_code=409, detail="当前移库单状态不允许入库")
    if not warehouse_in_scope(db, current_user, transfer.target_warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权操作目标仓库")
    source_inventory = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == transfer.source_warehouse_id,
            Inventory.product_id == transfer.product_id,
            Inventory.material_id == transfer.material_id,
        )
    ).with_for_update().scalar_one_or_none()
    unit_cost = Decimal(source_inventory.avg_unit_cost or 0) if source_inventory else Decimal("0")
    apply_inventory_change(
        db,
        warehouse_id=transfer.target_warehouse_id,
        product_id=transfer.product_id,
        material_id=transfer.material_id,
        quantity=Decimal(transfer.quantity),
        unit_cost=unit_cost,
        transaction_type="stock_transfer_in",
        related_type="stock_transfer",
        related_id=transfer.id,
        created_by_id=current_user.id,
    )
    transfer.status = "completed"
    transfer.completed_by_id = current_user.id
    transfer.completed_at = now_local()
    db.commit()
    return api_response(msg=f"移库单 {transfer.transfer_no} 已完成入库")


