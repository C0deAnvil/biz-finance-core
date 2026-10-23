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
    PackagingConfig,
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
from services.inventory_cost import sync_sales_order_cost
from services.replenishment_service import create_manual_fulfillment_demand, ensure_fulfillment_demand_routing, resolve_demand_progress
from services.display import calculate_bag_count, clean_display_text, get_inventory_packaging_config, inventory_request_category_label, status_label, transaction_type_label
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
from services.sales_service import _create_fulfillment_demands, _sales_received_amount
from services.document_status import sync_sales_order_process_status

from api.routers.inventory._common import _finance_edit_confirm_state, _finance_edit_snapshot, _finished_warehouse_id, _has_open_demand, _order_item_name, _placement_blocked_reason, _role_user_ids, _scoped_warehouse_ids, _serialize_inventory_order_detail, _serialize_inventory_sales_order_summary, _serialize_placement_queue, _serialize_shipment_queue
from services.inventory_availability import placeable_of





router = APIRouter(prefix="/inventory", tags=["inventory"])


def _queue_search_filter(keyword: str | None):
    """待置货/待发货列表的统一搜索条件。"""
    text = (keyword or "").strip()
    if not text:
        return None
    pattern = f"%{text}%"
    return or_(
        SalesOrder.order_no.like(pattern),
        SalesOrder.sales_user.has(or_(User.full_name.like(pattern), User.username.like(pattern))),
        SalesOrder.items.any(
            SalesOrderItem.product.has(or_(Product.name.like(pattern), Product.product_code.like(pattern)))
        ),
    )


def _sales_item_packaging_units(item: SalesOrderItem, quantity: Decimal) -> Decimal:
    if not item.packaging_config_id or not item.kg_per_bag or quantity <= 0:
        return Decimal("0")
    return Decimal(calculate_bag_count(quantity, item.kg_per_bag) or 0)


def _lock_sales_packaging_inventory(db: Session, *, warehouse_id: int, item: SalesOrderItem) -> tuple[PackagingConfig | None, Inventory | None]:
    packaging = get_inventory_packaging_config(db, item.packaging_config)
    if packaging is None:
        return None, None
    inventory = db.execute(
        select(Inventory)
        .where(
            Inventory.warehouse_id == warehouse_id,
            Inventory.product_id.is_(None),
            Inventory.material_id.is_(None),
            Inventory.packaging_config_id == packaging.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    return packaging, inventory


def _lock_product_inventories(db: Session, product_id: int, warehouse_id: int) -> list[Inventory]:
    """锁定作业单指定仓库中的成品库存。"""
    return db.execute(
        select(Inventory)
        .where(
            Inventory.warehouse_id == warehouse_id,
            Inventory.product_id == product_id,
            Inventory.material_id.is_(None),
        )
        .order_by(Inventory.warehouse_id, Inventory.id)
        .with_for_update()
    ).scalars().all()


def _lock_packaging_inventories(db: Session, packaging_id: int, warehouse_id: int) -> list[Inventory]:
    """锁定作业单指定仓库中的包装库存。"""
    return db.execute(
        select(Inventory)
        .where(
            Inventory.warehouse_id == warehouse_id,
            Inventory.packaging_config_id == packaging_id,
            Inventory.product_id.is_(None),
            Inventory.material_id.is_(None),
        )
        .order_by(Inventory.warehouse_id, Inventory.id)
        .with_for_update()
    ).scalars().all()


def _placeable_total(rows: list[Inventory]) -> Decimal:
    return sum((max(placeable_of(row), Decimal("0")) for row in rows), Decimal("0"))


def _reserve_from_rows(rows: list[Inventory], quantity: Decimal) -> None:
    """将新增占用按仓库顺序分摊到仍有可用量的库存行。"""
    remaining = quantity
    for row in rows:
        if remaining <= 0:
            break
        take = min(max(placeable_of(row), Decimal("0")), remaining)
        if take > 0:
            row.reserved_quantity = Decimal(row.reserved_quantity or 0) + take
            remaining -= take


@router.get("/queues")
def list_dispatch_queues(
    placement_page: int = Query(default=1, ge=1),
    placement_page_size: int = Query(default=10, ge=1, le=100),
    shipment_page: int = Query(default=1, ge=1),
    shipment_page_size: int = Query(default=10, ge=1, le=100),
    sales_order_page: int = Query(default=1, ge=1),
    sales_order_page_size: int = Query(default=10, ge=1, le=100),
    placement_keyword: str | None = Query(default=None, max_length=100),
    shipment_keyword: str | None = Query(default=None, max_length=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    placement_filter = (
        SalesOrder.status.in_(["pending_confirmation", "pending_shipment"]),
        InventoryDispatchQueue.placement_status == "pending",
    )
    placement_search = _queue_search_filter(placement_keyword)
    if placement_search is not None:
        placement_filter = (*placement_filter, placement_search)
    placement_count = db.execute(
        select(func.count(InventoryDispatchQueue.id))
        .join(SalesOrder, SalesOrder.id == InventoryDispatchQueue.sales_order_id)
        .where(*placement_filter)
    ).scalar_one()
    placement_ids = db.execute(
        select(InventoryDispatchQueue.id)
        .join(SalesOrder, SalesOrder.id == InventoryDispatchQueue.sales_order_id)
        .where(*placement_filter)
        .order_by(InventoryDispatchQueue.created_at.desc(), InventoryDispatchQueue.id.desc())
        .offset((placement_page - 1) * placement_page_size)
        .limit(placement_page_size)
    ).scalars().all()
    rows = db.execute(
        select(InventoryDispatchQueue)
        .options(
            joinedload(InventoryDispatchQueue.sales_order).joinedload(SalesOrder.sales_user),
        )
        .where(InventoryDispatchQueue.id.in_(placement_ids or [-1]))
        .order_by(InventoryDispatchQueue.created_at.desc(), InventoryDispatchQueue.id.desc())
    ).unique().scalars().all()

    shipment_filter = and_(
        InventoryDispatchQueue.id.is_not(None),
        SalesOrder.status == "pending_shipment",
        InventoryDispatchQueue.placement_status == "hidden",
        InventoryDispatchQueue.shipment_status.in_(["pending", "waiting_approval"]),
    )
    shipment_base = (
        select(SalesOrder.id)
        .outerjoin(InventoryDispatchQueue, InventoryDispatchQueue.sales_order_id == SalesOrder.id)
        .where(shipment_filter)
    )
    shipment_search = _queue_search_filter(shipment_keyword)
    if shipment_search is not None:
        shipment_base = shipment_base.where(shipment_search)
    if current_user.role.code == "sales":
        scoped_user_ids = [
            user.id for user in db.execute(select(User).where(User.is_active.is_(True))).scalars().all()
            if user_in_scope(db, current_user, business_role_code="sales", owner_user_id=user.id)
        ]
        shipment_base = shipment_base.where(SalesOrder.sales_user_id.in_(scoped_user_ids or [-1]))
    shipment_count = db.execute(select(func.count()).select_from(shipment_base.subquery())).scalar_one()
    shipment_ids = db.execute(
        shipment_base.order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        .offset((shipment_page - 1) * shipment_page_size)
        .limit(shipment_page_size)
    ).scalars().all()
    shipment_orders = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.sales_user),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.product),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        )
        .where(SalesOrder.id.in_(shipment_ids or [-1]))
    ).unique().scalars().all()
    shipment_queue_map = {
        row.sales_order_id: row
        for row in db.execute(
            select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id.in_(shipment_ids or [-1]))
        ).scalars().all()
    }

    sales_order_filter = SalesOrder.status.in_(["pending_shipment", "shipped", "completed"])
    sales_order_count = db.execute(select(func.count(SalesOrder.id)).where(sales_order_filter)).scalar_one()
    sales_order_ids = db.execute(
        select(SalesOrder.id)
        .where(sales_order_filter)
        .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        .offset((sales_order_page - 1) * sales_order_page_size)
        .limit(sales_order_page_size)
    ).scalars().all()
    shipment_rows = [_serialize_shipment_queue(shipment_queue_map[order.id], db, current_user) for order in shipment_orders]
    sales_order_rows = (
        db.execute(
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.sales_user),
                selectinload(SalesOrder.items).joinedload(SalesOrderItem.product),
                selectinload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            )
            .where(SalesOrder.id.in_(sales_order_ids or [-1]))
            .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        )
        .unique()
        .scalars()
        .all()
    )
    return api_response(
        data={
            "placement": [_serialize_placement_queue(row, db, current_user) for row in rows],
            "shipment": shipment_rows,
            "sales_orders": [_serialize_inventory_sales_order_summary(order, db, current_user) for order in sales_order_rows],
            "pagination": {
                "placement": {"page": placement_page, "page_size": placement_page_size, "total": placement_count},
                "shipment": {"page": shipment_page, "page_size": shipment_page_size, "total": shipment_count},
                "sales_orders": {"page": sales_order_page, "page_size": sales_order_page_size, "total": sales_order_count},
            },
        }
    )


@router.get("/sales-orders/{order_id}")
def get_inventory_sales_order_detail(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.sales_user),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        )
        .where(SalesOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    process_status = sync_sales_order_process_status(db, order)
    detail = _serialize_inventory_order_detail(order, process_status)
    detail["finance_edit_snapshot"] = _finance_edit_snapshot(db, order)
    detail["finance_edit_confirm_state"] = _finance_edit_confirm_state(db, order.id, current_user)
    return api_response(data=detail)


@router.post("/queues/{queue_id}/placement")
def mark_placement(
    queue_id: int,
    payload: PlacementActionRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.id == queue_id)).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="置货队列不存在")
    warehouse_id = queue.warehouse_id or _finished_warehouse_id(db)
    if queue.warehouse_id is None:
        queue.warehouse_id = warehouse_id
    if not warehouse_in_scope(db, current_user, warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权处理该仓库的置货任务")
    if payload.action != "placed":
        raise HTTPException(status_code=409, detail="置货操作只允许提交 placed")
    order = db.execute(
        select(SalesOrder)
        .options(selectinload(SalesOrder.items).joinedload(SalesOrderItem.product))
        .where(SalesOrder.id == queue.sales_order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="置货对应的客户订单不存在")

    # 1. 定金校验:先付定金后付尾款且定金未足额 -> 禁止置货
    blocked_reason = _placement_blocked_reason(db, order, "green")
    if blocked_reason:
        return api_response(code=409, msg=blocked_reason, data={"code": "placement_blocked", "message": blocked_reason})

    # 2. 分批次置货:按明细提交置货数量
    item_map = {item.id: item for item in order.items}
    shortage_rows = []
    placed_any = False
    for p in payload.items:
        item = item_map.get(p.sales_order_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"订单明细 {p.sales_order_item_id} 不存在")
        remaining_quantity = max(
            Decimal(item.quantity or 0) - Decimal(item.placed_quantity or 0) - Decimal(item.shipped_quantity or 0),
            Decimal("0"),
        )
        place_qty = Decimal(p.placed_quantity or 0)
        if place_qty <= 0:
            continue
        if place_qty > remaining_quantity:
            return api_response(
                code=409,
                msg=f"商品 {_order_item_name(item)} 置货数量超过待置货量(剩余 {float(remaining_quantity)})",
            )
        inventories = _lock_product_inventories(db, item.product_id, warehouse_id)
        # 财务审批时已为本明细占用了库存(item.reserved_quantity),置货不再重复占用,
        # 否则同一订单会被占两次(库存 200/订单 100 → 置货后占用 200)。
        # 只有当累计置货量超过已占量时(例如审批时库存不足、后来补货到货),才按当前可置货量补足差额。
        already_reserved = Decimal(item.reserved_quantity or 0)
        placed_after = Decimal(item.placed_quantity or 0) + place_qty
        extra_needed = max(placed_after - already_reserved, Decimal("0"))
        placeable = _placeable_total(inventories)
        if extra_needed > placeable:
            # 可支撑的置货量 = 已占量 + 现在还能占到的量
            can_place_total = already_reserved + placeable
            can_place_now = max(can_place_total - Decimal(item.placed_quantity or 0), Decimal("0"))
            shortage_rows.append(
                {
                    "product_id": item.product_id,
                    "product_name": _order_item_name(item),
                    "required_quantity": float(place_qty),
                    "available_quantity": float(can_place_now),
                    "shortage_quantity": float(place_qty - can_place_now),
                }
            )
            continue
        packaging = get_inventory_packaging_config(db, item.packaging_config)
        packaging_inventories = _lock_packaging_inventories(db, packaging.id, warehouse_id) if packaging else []
        target_packaging_units = _sales_item_packaging_units(item, placed_after)
        allocated_packaging_units = Decimal(item.packaging_reserved_quantity or 0) + Decimal(item.packaging_shipped_quantity or 0)
        packaging_extra_needed = max(target_packaging_units - allocated_packaging_units, Decimal("0"))
        if item.packaging_config_id and packaging is None:
            raise HTTPException(status_code=409, detail=f"{_order_item_name(item)} 的包装未关联全局包装主数据，无法核算包装库存")
        packaging_placeable = _placeable_total(packaging_inventories)
        if packaging_extra_needed > packaging_placeable:
            shortage_rows.append(
                {
                    "product_id": item.product_id,
                    "product_name": _order_item_name(item),
                    "packaging_config_id": packaging.id if packaging else None,
                    "packaging_name": packaging.packaging_name if packaging else None,
                    "required_quantity": float(packaging_extra_needed),
                    "available_quantity": float(packaging_placeable),
                    "shortage_quantity": float(packaging_extra_needed - packaging_placeable),
                    "shortage_type": "packaging",
                }
            )
            continue
        item.placed_quantity = placed_after
        if extra_needed > 0:
            _reserve_from_rows(inventories, extra_needed)
            item.reserved_quantity = already_reserved + extra_needed
        if packaging_extra_needed > 0:
            _reserve_from_rows(packaging_inventories, packaging_extra_needed)
            item.packaging_reserved_quantity = Decimal(item.packaging_reserved_quantity or 0) + packaging_extra_needed
        placed_any = True

    if shortage_rows:
        if payload.atomic:
            db.rollback()
            has_packaging_shortage = any(row.get("shortage_type") == "packaging" for row in shortage_rows)
            shortage_message = "包装库存不足，整单未置货" if has_packaging_shortage else "成品库存不足，整单未置货"
            return api_response(
                code=409,
                msg=shortage_message,
                data={"code": "atomic_stock_shortage", "shortages": shortage_rows},
            )
        db.commit()
        has_product_shortage = any(row.get("shortage_type") != "packaging" for row in shortage_rows)
        has_packaging_shortage = any(row.get("shortage_type") == "packaging" for row in shortage_rows)
        shortage_message = (
            "部分商品或包装库存不足，已完成其余可置货明细；请先采购或入库包装"
            if has_packaging_shortage
            else "部分商品库存不足,已置入可置货部分;是否提补货需求?"
        )
        # 需求:置货后库存不足→询问是否提补货需求(不自动生成),缺口明细交给前端弹窗确认
        return api_response(
            code=409,
            msg=shortage_message,
            data={
                "code": "finished_product_stock_shortage" if has_product_shortage else "packaging_stock_shortage",
                "message": shortage_message,
                "shortages": shortage_rows,
                "demand_required": has_product_shortage,
            },
        )

    # 3. 置货后仍有缺口 → 询问是否提补货需求(不自动生成)
    remaining_shortages = []
    for it in order.items:
        remaining = max(Decimal(it.quantity or 0) - Decimal(it.placed_quantity or 0) - Decimal(it.shipped_quantity or 0), Decimal("0"))
        if remaining <= 0:
            continue
        # 已提过且未完成的补货需求不再重复询问(需求已与本单据关联,进度可在补货需求里跟踪)。
        # 这也顺带解决了"在途货重复补"的问题:有在途必然已存在对应的补货需求/采购单。
        if _has_open_demand(db, sales_order_item_id=it.id):
            continue
        # 审批时已占用但尚未置货的那部分,已有实物库存支撑,不算缺口。
        reserved_for_unplaced = max(
            Decimal(it.reserved_quantity or 0) - Decimal(it.placed_quantity or 0), Decimal("0")
        )
        shortage = max(remaining - reserved_for_unplaced, Decimal("0"))
        if shortage > 0:
            remaining_shortages.append({
                "product_id": it.product_id,
                "product_name": _order_item_name(it),
                "required_quantity": float(remaining),
                "available_quantity": float(reserved_for_unplaced),
                "shortage_quantity": float(shortage),
            })
    if remaining_shortages:
        if payload.atomic:
            db.rollback()
            return api_response(
                code=409,
                msg="订单仍有库存缺口，整单未置货",
                data={"code": "atomic_stock_shortage", "shortages": remaining_shortages},
            )
        db.commit()
        # 需求:置货后仍有缺口→询问是否提补货需求(不自动生成)
        return api_response(
            code=409,
            msg="本次置货已完成,订单仍有库存缺口;是否提补货需求?",
            data={
                "code": "finished_product_stock_shortage",
                "message": "本次置货已完成,订单仍有库存缺口;是否提补货需求?",
                "shortages": remaining_shortages,
                "demand_required": True,
            },
        )

    # 4. 判断是否全部置货完成
    all_placed = all(
        max(Decimal(it.quantity or 0) - Decimal(it.placed_quantity or 0) - Decimal(it.shipped_quantity or 0), Decimal("0")) <= 0
        for it in order.items
    )
    if all_placed:
        queue.placement_status = "hidden"
        queue.shipment_status = "pending"
        queue.placement_marked_by_id = current_user.id
        queue.placement_marked_at = now_local()
        complete_todos(db, business_type="sales_order_placement", business_id=queue.sales_order_id)
        # 置货完成即推进订单至待发货状态（取消/驳回/已完成等终态不强制变更）
        if order.status in {"pending_confirmation", "pending_shipment"}:
            order.status = "pending_shipment"
        inventory_user_ids = _role_user_ids(db, "inventory")
        existing_todo_user_ids = {
            todo.assigned_user_id
            for todo in db.execute(
                select(TodoItem).where(
                    TodoItem.business_type == "sales_order",
                    TodoItem.business_id == order.id,
                    TodoItem.status != "completed",
                )
            ).scalars().all()
        }
        for user_id in inventory_user_ids:
            if user_id in existing_todo_user_ids:
                continue
            create_todo(
                db,
                title=f"待发货客户订单 {order.order_no}",
                description="订单已完成置货，请库管安排出库发货。",
                module="inventory",
                assigned_user_id=user_id,
                business_type="sales_order",
                business_id=order.id,
                priority="high",
            )
    db.commit()
    sync_sales_order_process_status(db, order, updated_by_id=current_user.id)
    return api_response(msg="置货状态已更新")


@router.post("/queues/{queue_id}/replenishment-demands")
def create_queue_replenishment_demands(
    queue_id: int,
    payload: SalesOrderReplenishmentCreateRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    """置货后库存不足时,由库管确认后批量提补货需求(数量默认缺口,可修改)。"""
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.id == queue_id)).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="置货队列不存在")
    order = db.execute(
        select(SalesOrder)
        .options(selectinload(SalesOrder.items).joinedload(SalesOrderItem.product))
        .where(SalesOrder.id == queue.sales_order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="置货对应的客户订单不存在")
    item_map = {item.id: item for item in order.items}
    product_item_map = {item.product_id: item for item in order.items}
    shortage_override: dict[int, Decimal] = {}
    for p in payload.items:
        item = item_map.get(p.sales_order_item_id) if p.sales_order_item_id else product_item_map.get(p.product_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"订单明细/产品 {p.sales_order_item_id or p.product_id} 不存在")
        # 同一产品多行明细时按产品聚合,取最大缺口
        shortage_override[item.product_id] = max(
            shortage_override.get(item.product_id, Decimal("0")),
            Decimal(str(p.shortage_quantity)),
        )
    created = _create_fulfillment_demands(
        db,
        order=order,
        current_user=current_user,
        shortage_override=shortage_override,
    )
    db.commit()
    sync_sales_order_process_status(db, order, updated_by_id=current_user.id)
    # 区分"新建"与"复用已有未完成需求(去重)":
    # 原先一律报"已提 N 条",库管重复点击时看到同样的成功提示,
    # 分不清究竟提没提、是不是提重了。
    new_rows = [row for row in created if not row.get("reused")]
    reused_rows = [row for row in created if row.get("reused")]
    if new_rows and reused_rows:
        msg = f"已提 {len(new_rows)} 条补货需求；另有 {len(reused_rows)} 条已存在未完成需求，未重复提交"
    elif new_rows:
        msg = f"已提 {len(new_rows)} 条补货需求"
    elif reused_rows:
        reused_nos = "、".join(row["demand_no"] for row in reused_rows)
        msg = f"该订单已存在未完成的补货需求（{reused_nos}），未重复提交"
    else:
        msg = "当前没有需要补货的缺口"
    return api_response(
        msg=msg,
        data={"demands": created, "new_count": len(new_rows), "reused_count": len(reused_rows)},
    )


@router.post("/queues/{queue_id}/shipment")
def mark_shipment(
    queue_id: int,
    payload: SalesShipmentRequest | QueueActionRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.id == queue_id)).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="发货队列不存在")
    warehouse_id = queue.warehouse_id or _finished_warehouse_id(db)
    if queue.warehouse_id is None:
        queue.warehouse_id = warehouse_id
    if not warehouse_in_scope(db, current_user, warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权处理该仓库的发货任务")
    if queue.placement_status != "hidden":
        raise HTTPException(status_code=409, detail="当前客户订单尚未完成置货，不能发货")
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            joinedload(SalesOrder.customer),
        )
        .where(SalesOrder.id == queue.sales_order_id)
    ).unique().scalar_one()
    if order.status != "pending_shipment":
        raise HTTPException(status_code=409, detail="当前客户订单不处于待发货状态")
    if order.finance_edit_pending:
        raise HTTPException(status_code=409, detail="财务修改待销售和库存确认，暂不能发货")

    # ---- 发货条件检查：财务收款 / 质检 / 库存 / 置货 ----
    process_status = sync_sales_order_process_status(db, order)
    dimensions = {d.get("dimension") or d.get("status_dimension"): d for d in process_status.get("dimensions", [])}

    def _dim_stage(name: str) -> str:
        return (dimensions.get(name) or {}).get("current_stage") or ""

    finance_stage = _dim_stage("finance")
    quality_stage = _dim_stage("quality")
    inbound_stage = _dim_stage("inbound")
    blocked_conditions: list[str] = []

    # 财务收款条件按付款方式差异化:
    # 全款发货 → 需收齐全款;先付定金后付尾款 → 需收齐定金;货到付款/分期 → 不阻止
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    received_amount = _sales_received_amount(db, order.id)
    if payment_type == "全款发货":
        if received_amount < Decimal(order.total_amount or 0):
            blocked_conditions.append(
                f"全款发货需收齐全款 ¥{float(order.total_amount or 0):g}，已收 ¥{float(received_amount):g}"
            )
    elif payment_type == "先付定金后付尾款":
        # 需求:定金单需尾款付清才能发货(收齐定金+尾款=全款)
        if received_amount < Decimal(order.total_amount or 0):
            blocked_conditions.append(
                f"先付定金后付尾款需尾款付清(全款 ¥{float(order.total_amount or 0):g}，已收 ¥{float(received_amount):g})"
            )
    # 货到付款/分期付款:发货前不校验收款
    if finance_stage in ("rejected", "pending_finance_check") and not blocked_conditions:
        blocked_conditions.append("财务收款条件未满足")
    if quality_stage not in ("passed", "qualified", "completed", "not_required", "done", "complete", "approved"):
        blocked_conditions.append("质检条件未满足")
    if inbound_stage not in ("completed", "done", "complete", "approved", "not_required", "passed"):
        blocked_conditions.append("入库条件未满足")

    force_shipment = bool(getattr(payload, "force", False)) if not isinstance(payload, QueueActionRequest) else False
    if blocked_conditions and not force_shipment:
        return api_response(
            code=409,
            msg="存在未满足的发货条件，是否继续发货？",
            data={
                "blocked_conditions": blocked_conditions,
                "finance_stage": finance_stage,
                "quality_stage": quality_stage,
                "inbound_stage": inbound_stage,
                "require_force": True,
            },
        )

    if isinstance(payload, QueueActionRequest):
        shipment_items = [
            {
                "sales_order_item_id": item.id,
                "shipped_quantity": max(Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0), Decimal("0")),
            }
            for item in order.items
        ]
    else:
        shipment_items = [item.model_dump() for item in payload.items]

    if not shipment_items:
        raise HTTPException(status_code=409, detail="请至少提交一条发货明细")

    order_item_map = {item.id: item for item in order.items}
    total_shipped_this_time = Decimal("0")
    for item_payload in shipment_items:
        order_item = order_item_map.get(item_payload["sales_order_item_id"])
        if order_item is None:
            raise HTTPException(status_code=404, detail="客户订单明细不存在")
        ship_quantity = Decimal(item_payload.get("shipped_quantity") or 0)
        if ship_quantity <= 0:
            continue
        remaining_quantity = Decimal(order_item.quantity or 0) - Decimal(order_item.shipped_quantity or 0)
        if ship_quantity > remaining_quantity:
            raise HTTPException(status_code=409, detail=f"{_order_item_name(order_item)} 本次发货数量不能大于剩余待发数量")
        inventory = db.execute(
            select(Inventory)
            .where(
                Inventory.warehouse_id == warehouse_id,
                Inventory.product_id == order_item.product_id,
                Inventory.material_id.is_(None),
            )
            .with_for_update()
        ).scalar_one_or_none()
        # 要发的货正是审批时为本单锁定的,因此本明细自己的占用额度算可发量;
        # 若按"实有-总占用"判断,自己锁的货反而会被当成别人的,导致发不出去。
        reserved_for_me = Decimal(order_item.reserved_quantity or 0)
        unreserved = max(placeable_of(inventory), Decimal("0"))
        shippable_quantity = reserved_for_me + unreserved
        if ship_quantity > shippable_quantity:
            raise HTTPException(status_code=409, detail=f"{_order_item_name(order_item)} 当前可用库存不足")
        packaging, packaging_inventory = _lock_sales_packaging_inventory(
            db,
            warehouse_id=warehouse_id,
            item=order_item,
        )
        target_packaging_shipped = _sales_item_packaging_units(
            order_item,
            Decimal(order_item.shipped_quantity or 0) + ship_quantity,
        )
        packaging_ship_quantity = max(
            target_packaging_shipped - Decimal(order_item.packaging_shipped_quantity or 0),
            Decimal("0"),
        )
        if order_item.packaging_config_id and packaging is None:
            raise HTTPException(status_code=409, detail=f"{_order_item_name(order_item)} 的包装未关联全局包装主数据，无法核算包装库存")
        packaging_reserved_for_me = Decimal(order_item.packaging_reserved_quantity or 0)
        packaging_shippable = packaging_reserved_for_me + max(placeable_of(packaging_inventory), Decimal("0"))
        if packaging_ship_quantity > packaging_shippable:
            name = packaging.packaging_name if packaging else "包装"
            raise HTTPException(status_code=409, detail=f"{_order_item_name(order_item)} 所需{name}库存不足")
        apply_inventory_change(
            db,
            warehouse_id=warehouse_id,
            product_id=order_item.product_id,
            material_id=None,
            quantity=-ship_quantity,
            unit_cost=Decimal(inventory.avg_unit_cost or 0) if inventory else Decimal("0"),
            batch_no=None,
            transaction_type="sales_outbound",
            related_type="sales_order",
            related_id=order.id,
            sales_order_item_id=order_item.id,
            created_by_id=current_user.id,
        )
        if packaging_ship_quantity > 0 and packaging is not None:
            apply_inventory_change(
                db,
                warehouse_id=warehouse_id,
                packaging_config_id=packaging.id,
                quantity=-packaging_ship_quantity,
                unit_cost=Decimal(packaging_inventory.avg_unit_cost or 0) if packaging_inventory else Decimal("0"),
                batch_no=None,
                transaction_type="sales_packaging_outbound",
                related_type="sales_order",
                related_id=order.id,
                sales_order_item_id=order_item.id,
                created_by_id=current_user.id,
            )
            packaging_release = min(packaging_ship_quantity, packaging_reserved_for_me)
            if packaging_release > 0:
                order_item.packaging_reserved_quantity = packaging_reserved_for_me - packaging_release
                packaging_inventory.reserved_quantity = max(
                    Decimal(packaging_inventory.reserved_quantity or 0) - packaging_release,
                    Decimal("0"),
                )
            order_item.packaging_shipped_quantity = Decimal(order_item.packaging_shipped_quantity or 0) + packaging_ship_quantity
        # 发货即"占用兑现":实有已扣,占用同步释放(只释放本明细实际占的那部分)
        release_qty = min(ship_quantity, reserved_for_me)
        if release_qty > 0:
            order_item.reserved_quantity = reserved_for_me - release_qty
            if inventory is not None:
                inventory.reserved_quantity = max(
                    Decimal(inventory.reserved_quantity or 0) - release_qty,
                    Decimal("0"),
                )
        order_item.shipped_quantity = Decimal(order_item.shipped_quantity or 0) + ship_quantity
        total_shipped_this_time += ship_quantity

    if total_shipped_this_time <= 0:
        raise HTTPException(status_code=409, detail="本次发货数量必须大于 0")

    all_shipped = all(Decimal(item.shipped_quantity or 0) >= Decimal(item.quantity or 0) for item in order.items)
    order.status = "shipped" if all_shipped else "pending_shipment"
    sync_sales_order_cost(db, order)
    if all_shipped:
        order.shipped_at = now_local()
        queue.shipment_status = "completed"
        queue.placement_status = "hidden"
        complete_todos(db, business_type="sales_order", business_id=order.id)
    else:
        queue.shipment_status = "pending"
    queue.shipment_marked_by_id = current_user.id
    queue.shipment_marked_at = now_local()
    create_notification(
        db,
        title="客户订单已全部发货" if all_shipped else "客户订单已部分发货",
        content=f"客户订单 {order.order_no} 本次发货 {float(total_shipped_this_time)}，{'已全部发货' if all_shipped else '仍有剩余待发数量'}。",
        module="sales",
        recipient_user_ids=[user_id for user_id in [order.sales_user_id] if user_id],
        created_by_id=current_user.id,
        category="success" if all_shipped else "info",
    )
    sync_sales_order_process_status(db, order, updated_by_id=current_user.id)
    db.commit()
    return api_response(msg="发货出库已完成", data={"order_id": order.id, "status": order.status, "all_shipped": all_shipped})


@router.post("/sales-orders/{order_id}/shipment")
def mark_sales_order_shipment(
    order_id: int,
    payload: SalesShipmentRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    warehouse_id = _finished_warehouse_id(db)
    if not warehouse_in_scope(db, current_user, warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权处理成品仓发货任务")
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order_id)).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=409, detail="当前客户订单尚未完成置货，不能发货")
    return mark_shipment(queue.id, payload, current_user, db)

