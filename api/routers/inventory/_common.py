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
from services.core import _role_user_ids, apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, sync_low_stock_alerts, write_operation_log
from services.replenishment_service import create_manual_fulfillment_demand, ensure_fulfillment_demand_routing, resolve_demand_progress
from services.display import calculate_bag_count, clean_display_text, get_inventory_packaging_config, inventory_request_category_label, product_specification_text, status_label, transaction_type_label
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
from services.inventory_availability import (
    placeable_of,
    purchase_in_transit_map,
    replenishment_available_of,
    stock_key,
)



def _scoped_warehouse_ids(db: Session, current_user: User) -> list[int] | None:
    return get_inventory_scope_warehouse_ids(db, current_user)


def _assigned_operation_request_ids(db: Session, current_user: User) -> list[int]:
    return [
        row.business_id
        for row in db.execute(
            select(TodoItem).where(
                TodoItem.assigned_user_id == current_user.id,
                TodoItem.business_type == "inventory_operation_request",
                TodoItem.status != "completed",
                TodoItem.business_id.is_not(None),
            )
        ).scalars().all()
    ]


def _can_access_operation_request(db: Session, current_user: User, row: InventoryOperationRequest) -> bool:
    if row.source_type == "stock_transfer" and current_user.role.code == "inventory":
        warehouse = db.get(Warehouse, row.warehouse_id)
        if warehouse and warehouse.manager_user_id:
            return warehouse.manager_user_id == current_user.id
        scope = get_inventory_scope_warehouse_ids(db, current_user)
        return bool((scope is not None and row.warehouse_id in scope) or row.id in _assigned_operation_request_ids(db, current_user))
    if row.request_category == "production_material_issue" and current_user.role.code == "inventory":
        warehouse = db.get(Warehouse, row.warehouse_id)
        scope = get_inventory_scope_warehouse_ids(db, current_user)
        return bool((warehouse and warehouse.manager_user_id == current_user.id) or (scope is not None and row.warehouse_id in scope))
    if warehouse_in_scope(db, current_user, row.warehouse_id):
        return True
    return row.id in set(_assigned_operation_request_ids(db, current_user))


def _finished_warehouse_id(db: Session) -> int:
    return get_finished_warehouse(db).id


def _serialize_stock(
    row: Inventory,
    *,
    purchase_in_transit: dict[tuple[str, int | None], Decimal] | None = None,
) -> dict:
    """序列化库存行。两个可用量口径见 services/inventory_availability 模块说明。

    available_quantity = 可置货量(实有-占用),决定能否交货;
    expected_available_quantity = 补货可用量(实有+在途-占用),决定是否还要补货。
    """
    key = stock_key(
        product_id=row.product_id,
        material_id=row.material_id,
        packaging_config_id=row.packaging_config_id,
    )
    in_transit_qty = Decimal((purchase_in_transit or {}).get(key, 0))
    available_quantity = placeable_of(row)
    expected_available_quantity = replenishment_available_of(row, purchase_in_transit=in_transit_qty)
    item = row.product or row.material or row.packaging_config
    item_type = "product" if row.product_id else ("material" if row.material_id else "packaging")
    safety_stock_qty = Decimal(getattr(item, "safety_stock_qty", 0) or 0)
    return {
        "id": row.id,
        "warehouse_id": row.warehouse_id,
        "warehouse_code": row.warehouse.code if row.warehouse else None,
        "warehouse_name": row.warehouse.name if row.warehouse else None,
        "item_id": row.product_id or row.material_id or row.packaging_config_id,
        "item_type": item_type,
        "item_code": row.product.product_code if row.product else (row.material.material_code if row.material else (f"BZ-{row.packaging_config_id}" if row.packaging_config_id else None)),
        "item_name": row.product.name if row.product else (row.material.name if row.material else (row.packaging_config.packaging_name if row.packaging_config else "")),
        "quantity": float(row.quantity),
        "reserved_quantity": float(row.reserved_quantity),
        "in_transit": float(row.in_transit or 0),
        "purchase_in_transit": float(in_transit_qty),
        "available_quantity": float(available_quantity),
        "expected_available_quantity": float(expected_available_quantity),
        "avg_unit_cost": float(row.avg_unit_cost),
        "inventory_amount": float(row.cost_balance or 0),
        "cost_status": row.cost_status or "unpriced",
        "opening_cost_pending": bool(row.opening_cost_pending),
        "safety_stock_qty": float(safety_stock_qty),
        "below_safety_stock": available_quantity < safety_stock_qty,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_transaction(
    row: InventoryTransaction,
    warehouse_map: dict,
    product_map: dict,
    material_map: dict,
    packaging_map: dict | None = None,
    *,
    user_map: dict | None = None,
    operation_request_map: dict | None = None,
) -> dict:
    product = product_map.get(row.product_id)
    material = material_map.get(row.material_id)
    packaging = (packaging_map or {}).get(row.packaging_config_id)
    warehouse = warehouse_map.get(row.warehouse_id)
    operator = (user_map or {}).get(row.created_by_id)
    operation_request = (
        (operation_request_map or {}).get(row.related_id)
        if row.related_type == "inventory_operation_request"
        else None
    )
    reason_parts = []
    if operation_request:
        for value in (
            operation_request.remark,
            operation_request.handled_remark,
            operation_request.adjust_reason,
            operation_request.quantity_difference_remark,
        ):
            cleaned = (value or "").strip()
            if cleaned and cleaned not in reason_parts:
                reason_parts.append(cleaned)
    item_type = "product" if row.product_id else ("material" if row.material_id else "packaging")
    return {
        "id": row.id,
        "transaction_no": row.transaction_no,
        "transaction_type": row.transaction_type,
        "transaction_type_label": transaction_type_label(row.transaction_type),
        "direction": "inbound" if row.quantity > 0 else "outbound",
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "item_id": row.product_id or row.material_id or row.packaging_config_id,
        "item_type": item_type,
        "item_code": product.product_code if product else (material.material_code if material else (f"BZ-{packaging.id}" if packaging else None)),
        "item_name": product.name if product else (material.name if material else (packaging.packaging_name if packaging else None)),
        "batch_no": row.batch_no,
        "quantity": float(row.quantity),
        "unit_cost": float(row.unit_cost),
        "total_cost": float(row.total_cost),
        "cost_status": row.cost_status or "unpriced",
        "related_type": row.related_type,
        "related_id": row.related_id,
        "sales_order_item_id": row.sales_order_item_id,
        "related_no": (operation_request.source_no or operation_request.request_no) if operation_request else None,
        "reason": "；".join(reason_parts) or transaction_type_label(row.transaction_type),
        "created_by_id": row.created_by_id,
        "created_by_name": (operator.full_name or operator.username) if operator else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_stock_take_task(task: StockTakeTask, warehouse_map: dict[int, Warehouse], items_map: dict[int, list[StockTakeItem]]) -> dict:
    items = items_map.get(task.id, [])
    difference_count = sum(1 for item in items if Decimal(item.difference_quantity or 0) != 0)
    return {
        "id": task.id,
        "task_no": task.task_no,
        "warehouse_id": task.warehouse_id,
        "warehouse_name": warehouse_map.get(task.warehouse_id).name if task.warehouse_id in warehouse_map else None,
        "status": task.status,
        "status_label": status_label(task.status),
        "difference_note": clean_display_text(task.difference_note, "库存盘点差异调整") if task.difference_note else "",
        "item_count": len(items),
        "difference_count": difference_count,
        "approved_at": task.approved_at.isoformat() if task.approved_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


def _serialize_stock_take_item(item: StockTakeItem, inventory: Inventory | None) -> dict:
    item_type = "product" if inventory and inventory.product_id else ("material" if inventory and inventory.material_id else "packaging")
    item_name = None
    item_code = None
    if inventory and inventory.product:
        item_name = inventory.product.name
        item_code = inventory.product.product_code
    elif inventory and inventory.material:
        item_name = inventory.material.name
        item_code = inventory.material.material_code
    elif inventory and inventory.packaging_config:
        item_name = inventory.packaging_config.packaging_name
        item_code = f"BZ-{inventory.packaging_config.id}"
    return {
        "id": item.id,
        "inventory_id": item.inventory_id,
        "item_type": item_type,
        "item_name": item_name,
        "item_code": item_code,
        "system_quantity": float(item.system_quantity),
        "counted_quantity": float(item.counted_quantity),
        "difference_quantity": float(item.difference_quantity),
        "reason": item.reason,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _serialize_stock_take_template_item(inventory: Inventory) -> dict:
    item_type = "product" if inventory.product_id else ("material" if inventory.material_id else "packaging")
    item_name = inventory.product.name if inventory.product else (inventory.material.name if inventory.material else (inventory.packaging_config.packaging_name if inventory.packaging_config else None))
    item_code = inventory.product.product_code if inventory.product else (inventory.material.material_code if inventory.material else (f"BZ-{inventory.packaging_config.id}" if inventory.packaging_config else None))
    return {
        "inventory_id": inventory.id,
        "item_type": item_type,
        "item_name": item_name,
        "item_code": item_code,
        "system_quantity": float(inventory.quantity or 0),
        "counted_quantity": float(inventory.quantity or 0),
        "difference_quantity": 0,
        "reason": None,
    }


def _order_item_name(item: SalesOrderItem) -> str:
    return item.product.name if item.product else f"产品#{item.product_id}"


def _sales_user_name(order: SalesOrder) -> str | None:
    """库存岗位只展示开单销售负责人，不暴露客户公司名称。"""
    user = order.sales_user
    return (user.full_name or user.username) if user else None


def _serialize_shipment_queue(row: InventoryDispatchQueue, db: Session, current_user: User) -> dict:
    order = row.sales_order
    process_status = sync_sales_order_process_status(db, order)
    items = []
    for item in order.items:
        ordered_quantity = Decimal(item.quantity or 0)
        shipped_quantity = Decimal(item.shipped_quantity or 0)
        remaining_quantity = max(ordered_quantity - shipped_quantity, Decimal("0"))
        items.append(
            {
                "sales_order_item_id": item.id,
                "product_id": item.product_id,
                "product_name": _order_item_name(item),
                "product_code": item.product.product_code if item.product else None,
                "display_specification": item.display_specification,
                "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "ordered_quantity": float(ordered_quantity),
                "shipped_quantity": float(shipped_quantity),
                "remaining_quantity": float(remaining_quantity),
                "ship_quantity": float(remaining_quantity),
            }
        )
    return {
        "queue_id": row.id,
        "order_id": row.sales_order_id,
        "order_no": order.order_no,
        "sales_user_name": _sales_user_name(order),
        "order_type": order.order_type,
        "order_type_label": order_type_style(order.order_type)["label"],
        "order_type_color": order_type_style(order.order_type)["color"],
        "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
        "status": row.shipment_status,
        "order_status": order.status,
        "finance_edit_pending": bool(order.finance_edit_pending),
        "finance_edit_confirm_state": _finance_edit_confirm_state(db, order.id, current_user),
        "current_stage": process_status["current_stage"],
        "current_stage_label": process_status["current_stage_label"],
        "blocked_reason": process_status["blocked_reason"],
        "dimensions": process_status["dimensions"],
        "items": items,
    }


def _placement_stock_status(db: Session, order: SalesOrder) -> dict:
    """实时计算置货状态(不落库):
    - green: 所有商品可用库存 >= 待置货量
    - yellow: 部分商品有可用库存(可部分置货)
    - red: 所有商品均无可用库存 或 无法继续部分置货
    同时返回每个明细的待置货/已置货/可用库存。
    """
    product_ids = [item.product_id for item in order.items if item.product_id]
    inventory_rows = (
        db.execute(
            select(Inventory).where(
                Inventory.product_id.in_(product_ids),
                Inventory.material_id.is_(None),
            )
        ).scalars().all()
        if product_ids
        else []
    )
    # 仓库层面还没被任何订单占用的散量(可供本单继续追加占用)
    unreserved_map: dict[int, Decimal] = {}
    for row in inventory_rows:
        unreserved_map[row.product_id] = (
            unreserved_map.get(row.product_id, Decimal("0")) + max(placeable_of(row), Decimal("0"))
        )
    packaging_rows = db.execute(
        select(Inventory).where(
            Inventory.packaging_config_id.is_not(None),
            Inventory.product_id.is_(None),
            Inventory.material_id.is_(None),
        )
    ).scalars().all()
    packaging_free_pool: dict[int, Decimal] = {}
    for row in packaging_rows:
        packaging_free_pool[row.packaging_config_id] = (
            packaging_free_pool.get(row.packaging_config_id, Decimal("0"))
            + max(placeable_of(row), Decimal("0"))
        )
    items = []
    any_available = False
    all_enough = True
    for item in order.items:
        ordered = Decimal(item.quantity or 0)
        placed = Decimal(item.placed_quantity or 0)
        shipped = Decimal(item.shipped_quantity or 0)
        remaining_to_place = max(ordered - placed - shipped, Decimal("0"))
        # 本明细能置的量 = 审批时为它锁定但还没置的部分 + 仓库里尚未被占用的散量。
        # 前一项是关键:那批货已经被这张订单锁死了,别人拿不走,所以对本单来说是可用的。
        # 漏掉它会把"库存充足"误判成"完全缺货"(库存100/订单100 → reserved=100 → 散量0 → red)。
        reserved_for_me = max(Decimal(item.reserved_quantity or 0) - placed, Decimal("0"))
        # 散量是全仓共享的,必须边分配边扣减:同一订单若有多条明细指向同一产品,
        # 把整个散量重复加给每条明细会让"可置合计"超过实有库存(如实有200、
        # 两条明细各报可置140),库管照此置货就会超卖。
        free_pool = unreserved_map.get(item.product_id, Decimal("0"))
        take_from_pool = min(free_pool, max(remaining_to_place - reserved_for_me, Decimal("0")))
        if item.product_id in unreserved_map:
            unreserved_map[item.product_id] = free_pool - take_from_pool
        available = reserved_for_me + take_from_pool
        packaging = get_inventory_packaging_config(db, item.packaging_config)
        packaging_target = Decimal(calculate_bag_count(placed + remaining_to_place, item.kg_per_bag) or 0) if packaging else Decimal("0")
        packaging_allocated = Decimal(item.packaging_reserved_quantity or 0) + Decimal(item.packaging_shipped_quantity or 0)
        packaging_required = max(packaging_target - packaging_allocated, Decimal("0"))
        packaging_available = packaging_free_pool.get(packaging.id, Decimal("0")) if packaging else Decimal("0")
        packaging_enough = not item.packaging_config_id or (packaging is not None and packaging_available >= packaging_required)
        if packaging is not None and packaging_required > 0:
            packaging_free_pool[packaging.id] = max(packaging_available - min(packaging_required, packaging_available), Decimal("0"))
        if remaining_to_place <= 0:
            item_status = "done"
        elif available >= remaining_to_place and packaging_enough:
            item_status = "enough"
            any_available = True
        elif available > 0 and (not item.packaging_config_id or packaging_available > 0):
            item_status = "partial"
            any_available = True
            all_enough = False
        else:
            item_status = "none"
            all_enough = False
        items.append(
            {
                "sales_order_item_id": item.id,
                "product_id": item.product_id,
                "product_name": _order_item_name(item),
                "product_code": item.product.product_code if item.product else None,
                "display_specification": item.display_specification,
                "ordered_quantity": float(ordered),
                "placed_quantity": float(placed),
                "shipped_quantity": float(shipped),
                "remaining_to_place": float(remaining_to_place),
                "reserved_quantity": float(item.reserved_quantity or 0),
                "available_quantity": float(available),
                "shortage_quantity": float(max(remaining_to_place - available, Decimal("0"))),
                "item_status": item_status,
                "packaging_config_id": packaging.id if packaging else None,
                "packaging_name": packaging.packaging_name if packaging else None,
                "kg_per_bag": float(item.kg_per_bag or 0) if item.kg_per_bag is not None else None,
                "packaging_required_quantity": float(packaging_required),
                "packaging_available_quantity": float(packaging_available),
                "packaging_shortage_quantity": float(max(packaging_required - packaging_available, Decimal("0"))),
            }
        )
    if all_enough:
        stock_status = "green"
    elif any_available:
        stock_status = "yellow"
    else:
        stock_status = "red"
    return {"stock_status": stock_status, "items": items}


def _has_open_demand(db: Session, *, sales_order_item_id: int) -> bool:
    """该订单明细是否已存在未完成的补货需求(避免对同一缺口重复提)。

    这里**包含 converted**,与 replenishment_service.has_open_product_demand 不同:
    本判定针对"这条订单明细的这个缺口",转成采购单/生产任务后货就在路上了,
    再问一次只会生成重复需求;而那边针对"这个产品的安全库存",
    converted 是终态,若也算未完成会导致告警被永久压制。
    """
    return db.execute(
        select(FulfillmentDemand.id).where(
            FulfillmentDemand.sales_order_item_id == sales_order_item_id,
            FulfillmentDemand.status.in_(["pending", "pending_material", "converted"]),
        )
    ).first() is not None


def _placement_blocked_reason(db: Session, order: SalesOrder, stock_status: str) -> str | None:
    """置货被阻止的原因，按财务审批、收款、库存顺序返回。"""
    # 队列会在提交审批时就创建，库管首先应看到财务尚未放行，而不是被库存缺口误导。
    if order.status == "pending_confirmation":
        return "待财务审批并确认收款，暂不可置货"
    if order.status != "pending_shipment":
        return "客户订单尚未完成财务审批，暂不可置货"

    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    if payment_type == "先收定金后收尾款":
        # 只要"累计已收 >= 应收定金"就可以置货,不限定收款是哪个阶段登记的。
        # 原先只统计 payment_stage == "deposit" 的记录,而财务选择"直接收款"时
        # 登记的是 payment_stage == "direct"(库里已有 4 笔共 1 万余元),
        # 定金口径求和恒为 0 —— 钱明明收了,置货却一直被"需先收取定金"拦住。
        # 这里改为对该订单的全部收款求和,定金/尾款/直接收款都算数。
        received = db.execute(
            select(func.coalesce(func.sum(ReceivableRecord.amount), 0)).where(
                ReceivableRecord.sales_order_id == order.id,
            )
        ).scalar_one()
        deposit = Decimal(order.deposit_amount or 0)
        if Decimal(received or 0) < deposit:
            return f"需先收取定金 ¥{float(deposit)} 后方可置货(已收 ¥{float(received or 0)})"
    if stock_status == "red":
        return "成品库存不足,无法置货;请先提交补货需求"
    return None


def _serialize_placement_queue(row: InventoryDispatchQueue, db: Session, current_user: User) -> dict:
    order = row.sales_order
    process_status = sync_sales_order_process_status(db, order)
    stock = _placement_stock_status(db, order)
    blocked_reason = _placement_blocked_reason(db, order, stock["stock_status"])
    return {
        'queue_id': row.id,
        'order_id': row.sales_order_id,
        'order_no': order.order_no,
        'sales_user_name': _sales_user_name(order),
        'order_type': order.order_type,
        'order_type_label': order_type_style(order.order_type)["label"],
        'order_type_color': order_type_style(order.order_type)["color"],
        'expected_delivery_date': order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
        'status': row.placement_status,
        'order_status': order.status,
        'finance_edit_pending': bool(order.finance_edit_pending),
        'finance_edit_confirm_state': _finance_edit_confirm_state(db, order.id, current_user),
        'stock_status': stock["stock_status"],
        'blocked_reason': blocked_reason,
        'items': stock["items"],
        'current_stage': process_status['current_stage'],
        'current_stage_label': process_status['current_stage_label'],
        'dimensions': process_status['dimensions'],
    }


def _serialize_sales_order_as_shipment_queue(order: SalesOrder, db: Session, current_user: User) -> dict:
    process_status = sync_sales_order_process_status(db, order)
    items = []
    for item in order.items:
        ordered_quantity = Decimal(item.quantity or 0)
        shipped_quantity = Decimal(item.shipped_quantity or 0)
        remaining_quantity = max(ordered_quantity - shipped_quantity, Decimal("0"))
        items.append(
            {
                "sales_order_item_id": item.id,
                "product_id": item.product_id,
                "product_name": _order_item_name(item),
                "product_code": item.product.product_code if item.product else None,
                "display_specification": item.display_specification,
                "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "ordered_quantity": float(ordered_quantity),
                "shipped_quantity": float(shipped_quantity),
                "remaining_quantity": float(remaining_quantity),
                "ship_quantity": float(remaining_quantity),
            }
        )
    return {
        "queue_id": None,
        "order_id": order.id,
        "order_no": order.order_no,
        "sales_user_name": _sales_user_name(order),
        "order_type": order.order_type,
        "order_type_label": order_type_style(order.order_type)["label"],
        "order_type_color": order_type_style(order.order_type)["color"],
        "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
        "status": "pending",
        "order_status": order.status,
        "finance_edit_pending": bool(order.finance_edit_pending),
        "finance_edit_confirm_state": _finance_edit_confirm_state(db, order.id, current_user),
        "current_stage": process_status["current_stage"],
        "current_stage_label": process_status["current_stage_label"],
        "blocked_reason": process_status["blocked_reason"],
        "dimensions": process_status["dimensions"],
        "items": items,
    }


def _serialize_inventory_order_detail(order: SalesOrder, process_status: dict | None = None) -> dict:
    process_status = process_status or {}
    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "status_label": status_label(order.status),
        "finance_edit_pending": bool(order.finance_edit_pending),
        "process_status": process_status,
        "sales_user_name": _sales_user_name(order),
        "consignee": order.consignee,
        "consignee_phone": order.consignee_phone,
        "delivery_address": order.delivery_address,
        "order_remark": order.order_remark,
        "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
        "payment_type": order.payment_type,
        "payment_type_label": payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales"),
        "total_amount": None,
        "deposit_amount": None,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": _order_item_name(item),
                "product_code": item.product.product_code if item.product else None,
                "display_specification": item.display_specification,
                "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "quantity": float(item.quantity or 0),
                "shipped_quantity": float(item.shipped_quantity or 0),
                "remaining_shipment_quantity": float(
                    max(Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0), Decimal("0"))
                ),
                "unit_price": None,
                "total_amount": None,
                "remark": item.remark,
            }
            for item in order.items
        ],
    }


def _serialize_inventory_sales_order_summary(order: SalesOrder, db: Session, current_user: User) -> dict:
    process_status = sync_sales_order_process_status(db, order)
    item_names = []
    for item in order.items:
      if item.product and item.product.name:
          item_names.append(item.product.name)
      elif item.product_id:
          item_names.append(f"产品#{item.product_id}")
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_name": order.customer.name if order.customer else None,
        "status": order.status,
        "status_label": status_label(order.status),
        "current_stage": process_status["current_stage"],
        "current_stage_label": process_status["current_stage_label"],
        "blocked_reason": process_status["blocked_reason"],
        "dimensions": process_status["dimensions"],
        "payment_type_label": payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales"),
        "finance_edit_pending": bool(order.finance_edit_pending),
        "finance_edit_confirm_state": _finance_edit_confirm_state(db, order.id, current_user),
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "item_summary": "、".join(item_names) if item_names else "-",
        "item_count": len(order.items),
    }


def _current_finance_edit_items(order: SalesOrder) -> list[dict]:
    return [
        {
            "product_id": item.product_id,
            "product_name": item.product.name if item.product else None,
            "product_code": item.product.product_code if item.product else None,
            "display_specification": item.display_specification,
            "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
            "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
            "bag_count": float(item.bag_count) if item.bag_count is not None else None,
            "quantity": float(item.quantity or 0),
            "remark": item.remark,
        }
        for item in order.items
    ]


def _redact_inventory_finance_edit_money(data: dict) -> dict:
    result = dict(data or {})
    for key in ("total_amount", "deposit_amount", "transport_fee", "packaging_fee"):
        result.pop(key, None)
    redacted_items = []
    for item in result.get("items") or []:
        redacted = dict(item or {})
        redacted.pop("unit_price", None)
        redacted.pop("total_amount", None)
        redacted.pop("packaging_unit_price", None)
        redacted.pop("packaging_fee", None)
        redacted_items.append(redacted)
    result["items"] = redacted_items
    return result


def _enrich_finance_edit_snapshot_data(db: Session, data: dict, order: SalesOrder, fallback_items: list[dict]) -> dict:
    result = dict(data or {})
    result.setdefault("order_id", order.id)
    result.setdefault("order_no", order.order_no)
    if not result.get("customer_name"):
        customer_id = result.get("customer_id") or order.customer_id
        customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none() if customer_id else None
        result["customer_name"] = customer.name if customer else (order.customer.name if order.customer else None)
    if not result.get("operator_name"):
        operator_id = result.get("operator_user_id") or order.operator_user_id
        operator = db.execute(select(User).where(User.id == operator_id)).scalar_one_or_none() if operator_id else None
        result["operator_name"] = operator.full_name if operator else None

    raw_items = result.get("items") or []
    if not raw_items:
        result["items"] = fallback_items
        return _redact_inventory_finance_edit_money(result)

    product_ids = [int(item.get("product_id")) for item in raw_items if item.get("product_id")]
    product_map = {
        product.id: product
        for product in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
    } if product_ids else {}
    enriched_items = []
    for item in raw_items:
        enriched = dict(item or {})
        product = product_map.get(enriched.get("product_id"))
        if product is not None:
            enriched.setdefault("product_name", product.name)
            enriched.setdefault("product_code", product.product_code)
            enriched.setdefault("display_specification", product_specification_text(product.specification))
        enriched_items.append(enriched)
    result["items"] = enriched_items
    return _redact_inventory_finance_edit_money(result)


def _finance_edit_snapshot(db: Session, order: SalesOrder) -> dict | None:
    rows = (
        db.execute(
            select(OperationLog)
            .where(OperationLog.module == "sales", OperationLog.action == "finance_edit_order")
            .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        )
        .scalars()
        .all()
    )
    row = next(
        (
            item
            for item in rows
            if (item.after_data or {}).get("order_id") == order.id
            or (item.before_data or {}).get("order_id") == order.id
            or (item.after_data or {}).get("order_no") == order.order_no
            or (item.before_data or {}).get("order_no") == order.order_no
            or order.order_no in str(item.content or "")
        ),
        None,
    )
    if row is None:
        return None
    current_items = _current_finance_edit_items(order)
    before_data = _enrich_finance_edit_snapshot_data(db, row.before_data or {}, order, current_items)
    after_data = _enrich_finance_edit_snapshot_data(db, row.after_data or {}, order, current_items)
    if not (row.before_data or {}).get("items"):
        before_data["legacy_snapshot_note"] = "历史改单日志未记录修改前明细，当前仅保留该次改单的基础字段。"
    return {
        "id": row.id,
        "changed_by_id": row.user_id,
        "changed_by_name": row.user.full_name if row.user else None,
        "changed_at": row.created_at.isoformat() if row.created_at else None,
        "before_data": before_data,
        "after_data": after_data,
        "content": row.content,
    }


def _finance_edit_confirm_state(db: Session, order_id: int, current_user: User) -> dict:
    role_code = current_user.role.code if current_user.role else ""
    if role_code != "inventory":
        return {"pending": False, "business_type": None}
    pending = (
        db.execute(
            select(TodoItem).where(
                TodoItem.business_type == "sales_order_finance_edit_inventory_confirm",
                TodoItem.business_id == order_id,
                TodoItem.assigned_user_id == current_user.id,
                TodoItem.status != "completed",
            )
        )
        .scalars()
        .first()
        is not None
    )
    return {"pending": pending, "business_type": "sales_order_finance_edit_inventory_confirm"}


def _purchase_operation_source_detail(db: Session, request: InventoryOperationRequest) -> dict | None:
    if request.source_type != "purchase_receipt":
        return None
    receipt = db.execute(
        select(PurchaseReceipt).where(PurchaseReceipt.id == request.source_id)
    ).scalar_one_or_none()
    if receipt is None:
        return None
    order = db.execute(
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.packaging_config),
        )
        .where(PurchaseOrder.id == receipt.purchase_order_id)
    ).unique().scalar_one_or_none()
    receipt_items = db.execute(
        select(PurchaseReceiptItem).where(PurchaseReceiptItem.purchase_receipt_id == receipt.id).order_by(PurchaseReceiptItem.id.asc())
    ).scalars().all()
    order_item_map = {item.id: item for item in (order.items if order else [])}
    receiver = db.execute(select(User).where(User.id == receipt.received_by_id)).scalar_one_or_none()
    return {
        "type": "purchase_receipt",
        "receipt_no": receipt.receipt_no,
        "purchase_order_id": receipt.purchase_order_id,
        "purchase_order_no": order.order_no if order else None,
        "supplier_name": order.supplier.name if order and order.supplier else None,
        "received_by_name": receiver.full_name if receiver else None,
        "recorded_at": receipt.recorded_at.isoformat() if receipt.recorded_at else None,
        "preliminary_acceptance_note": receipt.preliminary_acceptance_note,
        "receipt_remark": receipt.remark,
        "order_total_amount": None,
        "requires_quality": receipt.requires_quality,
        "quality_status": receipt.quality_status,
        "quality_status_label": status_label(receipt.quality_status),
        "receipt_status": receipt.status,
        "receipt_status_label": status_label(receipt.status),
        "items": [
            {
                "item_name": (
                    order_item.product.name
                    if order_item and order_item.product
                    else (order_item.material.name if order_item and order_item.material else (order_item.packaging_config.packaging_name if order_item and order_item.packaging_config else None))
                ),
                "item_code": (
                    order_item.product.product_code
                    if order_item and order_item.product
                    else (order_item.material.material_code if order_item and order_item.material else (f"BZ-{order_item.packaging_config_id}" if order_item and order_item.packaging_config_id else None))
                ),
                "order_quantity": float(order_item.quantity or 0) if order_item else 0,
                "received_quantity": float(row.received_quantity or 0),
                "batch_no": row.batch_no,
                "unit_cost": None,
            }
            for row in receipt_items
            for order_item in [order_item_map.get(row.purchase_order_item_id)]
        ],
    }


def _production_material_operation_source_detail(db: Session, request: InventoryOperationRequest) -> dict | None:
    from models import ProductionMaterialUsage, ProductionTask
    if request.source_type == "production_task":
        task = db.execute(
            select(ProductionTask)
            .options(
                joinedload(ProductionTask.product),
                joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material),
            )
            .where(ProductionTask.id == request.source_id)
        ).unique().scalar_one_or_none()
        if task is None:
            return None
        original_items = request.original_items_json or []
        cost_map = {
            item.get("material_id"): float(item.get("unit_cost") or 0)
            for item in original_items
            if item.get("material_id")
        }
        return {
            "type": "production_task",
            "task_id": task.id,
            "task_no": task.task_no,
            "product_name": task.product.name if task.product else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "preliminary_acceptance_note": task.preliminary_acceptance_note,
            "completion_recorded_by_name": task.completion_recorded_by.full_name if task.completion_recorded_by else None,
            "task_status": task.status,
            "task_status_label": status_label(task.status),
            "items": [
                {
                    "item_name": usage.material.name if usage.material else None,
                    "item_code": usage.material.material_code if usage.material else None,
                    "material_id": usage.material_id,
                    "requested_quantity": float(usage.requested_quantity or 0),
                    "approved_quantity": float(usage.approved_quantity or 0),
                    "issued_quantity": float(usage.issued_quantity or 0),
                    "unit_cost": cost_map.get(usage.material_id, 0),
                    "reason": usage.reason,
                }
                for usage in task.usages
            ],
        }
    if request.source_type != "production_material_usage":
        return None
    usage = db.execute(
        select(ProductionMaterialUsage)
        .options(
            joinedload(ProductionMaterialUsage.material),
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
        )
        .where(ProductionMaterialUsage.id == request.source_id)
    ).unique().scalar_one_or_none()
    if usage is None:
        return None
    task = usage.production_task
    return {
        "type": "production_material_usage",
        "task_id": task.id if task else None,
        "task_no": task.task_no if task else None,
        "product_name": task.product.name if task and task.product else None,
        "completed_at": task.completed_at.isoformat() if task and task.completed_at else None,
        "preliminary_acceptance_note": task.preliminary_acceptance_note if task else None,
        "completion_recorded_by_name": task.completion_recorded_by.full_name if task and task.completion_recorded_by else None,
        "material_name": usage.material.name if usage.material else None,
        "material_id": usage.material_id,
        "material_code": usage.material.material_code if usage.material else None,
        "requested_quantity": float(usage.requested_quantity or 0),
        "approved_quantity": float(usage.approved_quantity or 0),
        "issued_quantity": float(usage.issued_quantity or 0),
        "task_status": task.status if task else None,
        "task_status_label": status_label(task.status) if task else None,
        "reason": usage.reason,
        "items": [
            {
                "item_name": usage.material.name if usage.material else None,
                "item_code": usage.material.material_code if usage.material else None,
                "requested_quantity": float(usage.requested_quantity or 0),
                "approved_quantity": float(usage.approved_quantity or 0),
                "issued_quantity": float(usage.issued_quantity or 0),
                "unit_cost": float((request.original_items_json or [{}])[0].get("unit_cost") or 0),
            }
        ],
    }


def _operation_source_detail(db: Session, request: InventoryOperationRequest) -> dict | None:
    return _purchase_operation_source_detail(db, request) or _production_material_operation_source_detail(db, request)


def _notify_stock_take_finished(db: Session, task: StockTakeTask, *, current_user: User, difference_count: int):
    viewer_user_ids = _role_user_ids(db, "manager", "chairman")
    if viewer_user_ids:
        create_notification(
            db,
            title="盘点任务已完成",
            content=f"盘点任务 {task.task_no} 已完成差异入账，共 {difference_count} 项存在差异，请查看详情。",
            module="inventory",
            recipient_user_ids=viewer_user_ids,
            created_by_id=current_user.id,
            category="info",
        )


def _apply_stock_take_submission(task: StockTakeTask, payload: StockTakeSubmit | StockTakeCreate, current_user: User, db: Session) -> dict:
    if not payload.items:
        raise HTTPException(status_code=409, detail="盘点明细不能为空")

    item_rows = db.execute(
        select(StockTakeItem)
        .options(
            joinedload(StockTakeItem.inventory).joinedload(Inventory.product),
            joinedload(StockTakeItem.inventory).joinedload(Inventory.material),
            joinedload(StockTakeItem.inventory).joinedload(Inventory.packaging_config),
        )
        .where(StockTakeItem.stock_take_task_id == task.id)
    ).scalars().all()
    item_map = {row.id: row for row in item_rows}
    inventory_map = {row.inventory_id: row for row in item_rows}

    difference_count = 0
    for submitted in payload.items:
        item = item_map.get(submitted.stock_take_item_id) if submitted.stock_take_item_id is not None else None
        if item is None and submitted.inventory_id is not None:
            item = inventory_map.get(submitted.inventory_id)
        if item is None:
            raise HTTPException(status_code=404, detail="盘点明细不存在")

        counted_quantity = Decimal(submitted.counted_quantity)
        difference_quantity = counted_quantity - Decimal(item.system_quantity or 0)
        item.counted_quantity = counted_quantity
        item.difference_quantity = difference_quantity
        item.reason = submitted.reason

        if difference_quantity != 0:
            difference_count += 1
            inventory = item.inventory
            if inventory is None:
                raise HTTPException(status_code=404, detail="盘点关联库存不存在")
            apply_inventory_change(
                db,
                warehouse_id=inventory.warehouse_id,
                product_id=inventory.product_id,
                material_id=inventory.material_id,
                packaging_config_id=inventory.packaging_config_id,
                quantity=difference_quantity,
                unit_cost=Decimal(inventory.avg_unit_cost or 0),
                transaction_type="stock_take_adjustment",
                related_type="stock_take_task",
                related_id=task.id,
                created_by_id=current_user.id,
            )
            item.system_quantity = counted_quantity
            item.difference_quantity = Decimal("0")

    task.status = "completed"
    task.approved_by_id = current_user.id
    task.approved_at = now_local()
    task.difference_note = payload.difference_note
    complete_todos(db, business_type="stock_take_task", business_id=task.id)
    _notify_stock_take_finished(db, task, current_user=current_user, difference_count=difference_count)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="inventory",
        action="approve_stock_take",
        content=f"完成盘点任务 {task.task_no}",
        after_data={"task_id": task.id, "difference_count": difference_count, "status": task.status},
    )
    return {"task_id": task.id, "difference_count": difference_count, "status": task.status}


def _transfer_status_label(status: str) -> str:
    return {
        "pending_outbound": "待出库",
        "pending_quality": "待质检",
        "pending_inbound": "待入库",
        "completed": "已完成",
        "returned": "已退回",
        "quality_failed": "质检不通过，待处置",
        "outbound_rejected": "源仓出库已拒绝",
        "inbound_rejected": "目标仓入库已拒绝，待处置",
    }.get(status, status)


