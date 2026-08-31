from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from database import get_db
from dependencies import get_current_user, require_roles
from models import (
    Customer,
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
    SalesOrderItem,
    SalesOrder,
    StockTakeItem,
    StockTakeTask,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.inventory import InventoryManualOperationCreateRequest, InventoryOperationApprovalRequest, InventoryOperationHandleRequest, InventoryOperationRejectRequest, StockTakeCreate, StockTakeSubmit
from schemas.sales import QueueActionRequest, SalesShipmentRequest
from services.core import apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, sync_low_stock_alerts, write_operation_log
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
from services.sales_service import ensure_sales_order_fulfillment_demands
from services.document_status import sync_sales_order_process_status
from services.inventory_dimensions import sync_inventory_dimensions


router = APIRouter(prefix="/inventory", tags=["inventory"])


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
    if warehouse_in_scope(db, current_user, row.warehouse_id):
        return True
    return row.id in set(_assigned_operation_request_ids(db, current_user))


def _finished_warehouse_id(db: Session) -> int:
    return get_finished_warehouse(db).id


def _role_user_ids(db: Session, *role_codes: str) -> list[int]:
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def _stock_key(*, product_id: int | None = None, material_id: int | None = None) -> tuple[str, int | None]:
    return ("product", product_id) if product_id else ("material", material_id)


def _expected_available_maps(db: Session) -> tuple[dict[tuple[str, int | None], Decimal], dict[tuple[str, int | None], Decimal]]:
    pending_sales: dict[tuple[str, int | None], Decimal] = {}
    for product_id, ordered_quantity, shipped_quantity in db.execute(
        select(
            SalesOrderItem.product_id,
            func.coalesce(func.sum(SalesOrderItem.quantity), 0),
            func.coalesce(func.sum(SalesOrderItem.shipped_quantity), 0),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .where(SalesOrder.status == "pending_shipment")
        .group_by(SalesOrderItem.product_id)
    ).all():
        pending_sales[_stock_key(product_id=product_id)] = max(Decimal(ordered_quantity or 0) - Decimal(shipped_quantity or 0), Decimal("0"))

    purchase_in_transit: dict[tuple[str, int | None], Decimal] = {}
    for product_id, material_id, ordered_quantity, received_quantity in db.execute(
        select(
            PurchaseOrderItem.product_id,
            PurchaseOrderItem.material_id,
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.status.in_(["purchasing", "partial_received", "pending_purchase_confirm", "fully_received"]))
        .group_by(PurchaseOrderItem.product_id, PurchaseOrderItem.material_id)
    ).all():
        purchase_in_transit[_stock_key(product_id=product_id, material_id=material_id)] = max(
            Decimal(ordered_quantity or 0) - Decimal(received_quantity or 0),
            Decimal("0"),
        )
    return pending_sales, purchase_in_transit


def _serialize_stock(
    row: Inventory,
    *,
    pending_sales: dict[tuple[str, int | None], Decimal] | None = None,
    purchase_in_transit: dict[tuple[str, int | None], Decimal] | None = None,
) -> dict:
    available_quantity = Decimal(row.quantity or 0) - Decimal(row.reserved_quantity or 0)
    item = row.product or row.material
    item_type = "product" if row.product_id else "material"
    key = _stock_key(product_id=row.product_id, material_id=row.material_id)
    expected_available_quantity = available_quantity - Decimal((pending_sales or {}).get(key, 0)) + Decimal((purchase_in_transit or {}).get(key, 0))
    safety_stock_qty = Decimal(item.safety_stock_qty or 0) if item else Decimal("0")
    return {
        "id": row.id,
        "warehouse_id": row.warehouse_id,
        "warehouse_code": row.warehouse.code if row.warehouse else None,
        "warehouse_name": row.warehouse.name if row.warehouse else None,
        "item_id": row.product_id or row.material_id,
        "item_type": item_type,
        "item_code": row.product.product_code if row.product else (row.material.material_code if row.material else None),
        "item_name": row.product.name if row.product else (row.material.name if row.material else ""),
        "quantity": float(row.quantity),
        "reserved_quantity": float(row.reserved_quantity),
        "in_production": float(row.in_production or 0),
        "in_transit": float(row.in_transit or 0),
        "on_order": float(row.on_order or 0),
        "available_quantity": float(available_quantity),
        "available_stock": float(row.available if row.available is not None else available_quantity),
        "expected_available_quantity": float(expected_available_quantity),
        "avg_unit_cost": float(row.avg_unit_cost),
        "inventory_amount": float(Decimal(row.quantity or 0) * Decimal(row.avg_unit_cost or 0)),
        "safety_stock_qty": float(safety_stock_qty),
        "below_safety_stock": available_quantity < safety_stock_qty,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_transaction(row: InventoryTransaction, warehouse_map: dict, product_map: dict, material_map: dict) -> dict:
    product = product_map.get(row.product_id)
    material = material_map.get(row.material_id)
    warehouse = warehouse_map.get(row.warehouse_id)
    item_type = "product" if row.product_id else "material"
    return {
        "id": row.id,
        "transaction_no": row.transaction_no,
        "transaction_type": row.transaction_type,
        "transaction_type_label": transaction_type_label(row.transaction_type),
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "item_id": row.product_id or row.material_id,
        "item_type": item_type,
        "item_code": product.product_code if product else (material.material_code if material else None),
        "item_name": product.name if product else (material.name if material else None),
        "batch_no": row.batch_no,
        "quantity": float(row.quantity),
        "unit_cost": float(row.unit_cost),
        "total_cost": float(row.total_cost),
        "related_type": row.related_type,
        "related_id": row.related_id,
        "created_by_id": row.created_by_id,
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
    item_type = "product" if inventory and inventory.product_id else "material"
    item_name = None
    item_code = None
    if inventory and inventory.product:
        item_name = inventory.product.name
        item_code = inventory.product.product_code
    elif inventory and inventory.material:
        item_name = inventory.material.name
        item_code = inventory.material.material_code
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
    item_type = "product" if inventory.product_id else "material"
    item_name = inventory.product.name if inventory.product else (inventory.material.name if inventory.material else None)
    item_code = inventory.product.product_code if inventory.product else (inventory.material.material_code if inventory.material else None)
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
        "customer_name": order.customer.name if order.customer else None,
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


def _serialize_placement_queue(row: InventoryDispatchQueue, db: Session, current_user: User) -> dict:
    order = row.sales_order
    process_status = sync_sales_order_process_status(db, order)
    return {
        'queue_id': row.id,
        'order_id': row.sales_order_id,
        'order_no': order.order_no,
        'customer_name': order.customer.name if order.customer else None,
        'status': row.placement_status,
        'order_status': order.status,
        'finance_edit_pending': bool(order.finance_edit_pending),
        'finance_edit_confirm_state': _finance_edit_confirm_state(db, order.id, current_user),
        'current_stage': process_status['current_stage'],
        'current_stage_label': process_status['current_stage_label'],
        'blocked_reason': process_status['blocked_reason'],
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
        "customer_name": order.customer.name if order.customer else None,
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
        "customer": {"id": order.customer.id, "name": order.customer.name} if order.customer else None,
        "consignee": order.consignee,
        "consignee_phone": order.consignee_phone,
        "delivery_address": order.delivery_address,
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
            enriched.setdefault("display_specification", product.specification)
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
                    else (order_item.material.name if order_item and order_item.material else None)
                ),
                "item_code": (
                    order_item.product.product_code
                    if order_item and order_item.product
                    else (order_item.material.material_code if order_item and order_item.material else None)
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


@router.get("/queues")
def list_dispatch_queues(
    placement_page: int = Query(default=1, ge=1),
    placement_page_size: int = Query(default=10, ge=1, le=100),
    shipment_page: int = Query(default=1, ge=1),
    shipment_page_size: int = Query(default=10, ge=1, le=100),
    sales_order_page: int = Query(default=1, ge=1),
    sales_order_page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role.code == "inventory":
        warehouse_ids = _scoped_warehouse_ids(db, current_user)
        if warehouse_ids is not None and _finished_warehouse_id(db) not in warehouse_ids:
            return api_response(data={"placement": [], "shipment": []})
    placement_filter = (
        SalesOrder.status.in_(["pending_confirmation", "pending_shipment"]),
        InventoryDispatchQueue.placement_status == "pending",
    )
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
            joinedload(InventoryDispatchQueue.sales_order).joinedload(SalesOrder.customer),
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
            joinedload(SalesOrder.customer),
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
                joinedload(SalesOrder.customer),
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
            "placement": [
                {
                    "queue_id": row.id,
                    "order_id": row.sales_order_id,
                    "order_no": row.sales_order.order_no,
                    "customer_name": row.sales_order.customer.name,
                    "status": row.placement_status,
                    "finance_edit_pending": bool(row.sales_order.finance_edit_pending),
                    "finance_edit_confirm_state": _finance_edit_confirm_state(db, row.sales_order_id, current_user),
                }
                for row in rows
            ],
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
    if current_user.role.code == "inventory":
        warehouse_ids = _scoped_warehouse_ids(db, current_user)
        if warehouse_ids is not None and _finished_warehouse_id(db) not in warehouse_ids:
            raise HTTPException(status_code=403, detail="当前账号无权查看成品仓客户订单")
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
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
    payload: QueueActionRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    if not warehouse_in_scope(db, current_user, _finished_warehouse_id(db)):
        raise HTTPException(status_code=403, detail="当前账号无权处理成品仓置货任务")
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.id == queue_id)).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="置货队列不存在")
    if payload.action != "placed":
        raise HTTPException(status_code=409, detail="置货操作只允许提交 placed")
    order = db.execute(
        select(SalesOrder)
        .options(selectinload(SalesOrder.items).joinedload(SalesOrderItem.product))
        .where(SalesOrder.id == queue.sales_order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="置货对应的客户订单不存在")

    ensure_sales_order_fulfillment_demands(db, order, current_user)
    shortage_rows = []
    finished_warehouse_id = _finished_warehouse_id(db)
    for item in order.items:
        remaining_quantity = max(
            Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0),
            Decimal("0"),
        )
        if remaining_quantity <= 0:
            continue
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == finished_warehouse_id,
                Inventory.product_id == item.product_id,
                Inventory.material_id.is_(None),
            )
        ).scalar_one_or_none()
        available_quantity = max(
            Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0)
            if inventory is not None
            else Decimal("0"),
            Decimal("0"),
        )
        if available_quantity < remaining_quantity:
            shortage_rows.append(
                {
                    "product_id": item.product_id,
                    "product_name": _order_item_name(item),
                    "required_quantity": float(remaining_quantity),
                    "available_quantity": float(available_quantity),
                    "shortage_quantity": float(remaining_quantity - available_quantity),
                }
            )
    if shortage_rows:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "finished_product_stock_shortage",
                "message": "成品库存不足，暂不能完成置货；已生成生产/采购处理需求",
                "shortages": shortage_rows,
            },
        )

    queue.placement_status = "placed"
    queue.placement_marked_by_id = current_user.id
    queue.placement_marked_at = now_local()
    if queue.placement_status == "placed":
        queue.placement_status = "hidden"
        complete_todos(db, business_type="sales_order_placement", business_id=queue.sales_order_id)
        if order is not None and order.status == "pending_shipment":
            queue.shipment_status = "pending"
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


@router.post("/queues/{queue_id}/shipment")
def mark_shipment(
    queue_id: int,
    payload: SalesShipmentRequest | QueueActionRequest,
    current_user: User = Depends(require_roles("inventory")),
    db: Session = Depends(get_db),
):
    warehouse_id = _finished_warehouse_id(db)
    if not warehouse_in_scope(db, current_user, warehouse_id):
        raise HTTPException(status_code=403, detail="当前账号无权处理成品仓发货任务")
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.id == queue_id)).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status_code=404, detail="发货队列不存在")
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
    dimensions = {d["status_dimension"]: d for d in process_status.get("dimensions", [])}

    def _dim_stage(name: str) -> str:
        return (dimensions.get(name) or {}).get("current_stage") or ""

    finance_stage = _dim_stage("finance")
    quality_stage = _dim_stage("quality")
    inbound_stage = _dim_stage("inbound")
    blocked_conditions: list[str] = []
    if finance_stage not in ("paid", "cleared", "partial_paid", "approved", "received", "complete", "completed"):
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
            select(Inventory).where(
                Inventory.warehouse_id == warehouse_id,
                Inventory.product_id == order_item.product_id,
                Inventory.material_id.is_(None),
            )
        ).scalar_one_or_none()
        available_quantity = Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0) if inventory else Decimal("0")
        if ship_quantity > available_quantity:
            raise HTTPException(status_code=409, detail=f"{_order_item_name(order_item)} 当前可用库存不足")
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
            created_by_id=current_user.id,
        )
        order_item.shipped_quantity = Decimal(order_item.shipped_quantity or 0) + ship_quantity
        total_shipped_this_time += ship_quantity

    if total_shipped_this_time <= 0:
        raise HTTPException(status_code=409, detail="本次发货数量必须大于 0")

    all_shipped = all(Decimal(item.shipped_quantity or 0) >= Decimal(item.quantity or 0) for item in order.items)
    order.status = "shipped" if all_shipped else "pending_shipment"
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
    sync_inventory_dimensions(db)
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
