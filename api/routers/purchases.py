import json
from decimal import Decimal
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import Inventory, InventoryOperationRequest, InventoryTransaction, Material, Notification, NotificationRecipient, PayableRecord, Product, ProductionMaterialUsage, ProductionTask, PurchaseOrder, PurchaseOrderApproval, PurchaseOrderItem, PurchaseReceipt, PurchaseReceiptItem, PurchaseReturn, RefundRecord, Role, SalesOrder, SalesOrderItem, Supplier, SupplierCatalogItem, TodoItem, User, Warehouse
from models import FulfillmentDemand
from responses import api_response
from schemas.purchases import (
    PurchaseApprovalRequest,
    PurchaseMergedPaymentRequestCreate,
    PurchaseOrderCreate,
    PurchasePaymentApprovalRequest,
    PurchasePaymentCompleteRequest,
    PurchasePaymentRequestCreate,
    PurchaseOrderUpdate,
    PurchaseReceiptCreate,
    PurchaseReturnCreate,
)
from services.display import clean_display_text, format_legacy_remark, payment_type_label, return_type_label, status_label
from services.organization import get_inventory_scope_warehouse_ids, get_scoped_user_ids, user_in_scope, warehouse_in_scope
from services.purchase_service import (
    approve_purchase_order as approve_purchase_order_service,
    approve_purchase_payment_request as approve_purchase_payment_request_service,
    build_purchase_settlement_snapshot,
    cancel_purchase_order as cancel_purchase_order_service,
    complete_purchase_payment_request as complete_purchase_payment_request_service,
    confirm_purchase_order as confirm_purchase_order_service,
    create_purchase_order as create_purchase_order_service,
    create_purchase_return as create_purchase_return_service,
    create_receipt as create_receipt_service,
    submit_merged_purchase_payment_request as submit_merged_purchase_payment_request_service,
    submit_purchase_payment_request as submit_purchase_payment_request_service,
    update_purchase_order as update_purchase_order_service,
)
from services.core import complete_todos, create_notification, sync_low_stock_alerts, write_operation_log
from services.warehouse_lookup import get_finished_warehouse, get_raw_warehouse
from services.workflow import purchase_arrival_status, purchase_flow_steps
from services.time_utils import now_local


router = APIRouter(prefix="/purchases", tags=["采购"])


def _role_user_ids(db: Session, *role_codes: str) -> list[int]:
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def _ensure_purchase_scope(db: Session, current_user: User, owner_user_id: int | None) -> None:
    if not user_in_scope(db, current_user, business_role_code="purchase", owner_user_id=owner_user_id):
        raise HTTPException(status_code=403, detail="仅可查看数据范围内的采购单")


def _latest_payable_map(payables: list[PayableRecord]) -> dict[int, PayableRecord]:
    latest_map: dict[int, PayableRecord] = {}
    for row in payables:
        if row.purchase_order_id not in latest_map or row.id > latest_map[row.purchase_order_id].id:
            latest_map[row.purchase_order_id] = row
    return latest_map


def _paid_amount_map(payables: list[PayableRecord]) -> dict[int, Decimal]:
    amount_map: dict[int, Decimal] = {}
    for row in payables:
        if row.status not in {"paid", "partial_settled", "settled"}:
            continue
        amount_map[row.purchase_order_id] = amount_map.get(row.purchase_order_id, Decimal("0")) + Decimal(row.amount or 0)
    return amount_map


def _paid_order_amount_map(payables: list[PayableRecord]) -> dict[int, Decimal]:
    amount_map: dict[int, Decimal] = {}
    for row in payables:
        if row.status not in {"paid", "partial_settled", "settled"}:
            continue
        order_amount = Decimal(row.order_amount or 0)
        if order_amount <= 0:
            order_amount = Decimal(row.amount or 0)
        amount_map[row.purchase_order_id] = amount_map.get(row.purchase_order_id, Decimal("0")) + order_amount
    return amount_map


def _settlement_status(total_amount: Decimal, paid_amount: Decimal) -> str:
    if paid_amount <= 0:
        return "unpaid"
    if paid_amount >= total_amount:
        return "settled"
    return "partial_settled"


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


@router.get("/orders")
def list_purchase_orders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = (
        select(PurchaseOrder)
        .options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.purchaser), joinedload(PurchaseOrder.operator_user), joinedload(PurchaseOrder.items))
        .order_by(PurchaseOrder.created_at.desc())
    )
    scoped_user_ids = get_scoped_user_ids(db, current_user, "purchase")
    if scoped_user_ids is not None:
        query = query.where(PurchaseOrder.purchaser_user_id.in_(scoped_user_ids))
    rows = db.execute(query).unique().scalars().all()
    order_ids = [row.id for row in rows]
    payables = (
        db.execute(select(PayableRecord).where(PayableRecord.purchase_order_id.in_(order_ids)).order_by(PayableRecord.id.desc())).scalars().all()
        if order_ids
        else []
    )
    paid_amount_map = _paid_amount_map(payables)
    paid_order_amount_map = _paid_order_amount_map(payables)
    return api_response(
        data=[
            {
                "id": row.id,
                "order_no": row.order_no,
                "supplier_name": row.supplier.name if row.supplier else None,
                "operator_user_id": row.operator_user_id,
                "operator_name": row.operator_user.full_name if row.operator_user else None,
                "status": row.status,
                "status_label": status_label(row.status),
                "payment_type": row.payment_type,
                "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
                "total_amount": float(row.total_amount),
                "threshold_triggered": row.threshold_triggered,
                "paid_amount": float(paid_amount_map.get(row.id, Decimal("0"))),
                "paid_order_amount": float(paid_order_amount_map.get(row.id, Decimal("0"))),
                "remaining_amount": float(max(Decimal(row.total_amount or 0) - paid_order_amount_map.get(row.id, Decimal("0")), Decimal("0"))),
                "fund_status": _settlement_status(Decimal(row.total_amount or 0), paid_order_amount_map.get(row.id, Decimal("0"))),
                "fund_status_label": status_label(_settlement_status(Decimal(row.total_amount or 0), paid_order_amount_map.get(row.id, Decimal("0")))),
                "arrival_status": purchase_arrival_status(db, row.id),
                "arrival_status_label": status_label(purchase_arrival_status(db, row.id)),
                "receivable_completed": bool(row.items) and all(Decimal(item.received_quantity or 0) >= Decimal(item.quantity or 0) for item in row.items),
                "is_completed_order": (
                    purchase_arrival_status(db, row.id) == "arrived"
                    and _settlement_status(Decimal(row.total_amount or 0), paid_order_amount_map.get(row.id, Decimal("0"))) == "settled"
                ),
            }
            for row in rows
        ]
    )


@router.get("/orders/{order_id}")
def get_purchase_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.execute(
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.operator_user),
        )
        .where(PurchaseOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_purchase_scope(db, current_user, order.purchaser_user_id)
    paid_amount = _paid_amount_map(
        db.execute(select(PayableRecord).where(PayableRecord.purchase_order_id == order.id).order_by(PayableRecord.id.desc())).scalars().all()
    ).get(order.id, Decimal("0"))
    paid_order_amount = _paid_order_amount_map(
        db.execute(select(PayableRecord).where(PayableRecord.purchase_order_id == order.id).order_by(PayableRecord.id.desc())).scalars().all()
    ).get(order.id, Decimal("0"))
    settlement_status = _settlement_status(Decimal(order.total_amount or 0), paid_order_amount)
    return api_response(
        data={
            "id": order.id,
            "order_no": order.order_no,
            "supplier_id": order.supplier_id,
            "supplier_name": order.supplier.name if order.supplier else None,
            "operator_user_id": order.operator_user_id,
            "operator_name": order.operator_user.full_name if order.operator_user else None,
            "payment_type": order.payment_type,
            "payment_type_label": payment_type_label(order.payment_type, module="purchase"),
            "status": order.status,
            "status_label": status_label(order.status),
            "fund_status": settlement_status,
            "fund_status_label": status_label(settlement_status),
            "paid_amount": float(paid_amount),
            "paid_order_amount": float(paid_order_amount),
            "remaining_amount": float(max(Decimal(order.total_amount or 0) - paid_order_amount, Decimal("0"))),
            "arrival_status": purchase_arrival_status(db, order.id),
            "arrival_status_label": status_label(purchase_arrival_status(db, order.id)),
            "flow_steps": purchase_flow_steps(order, arrival_status=purchase_arrival_status(db, order.id), settlement_status=settlement_status),
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else None,
                    "product_code": item.product.product_code if item.product else None,
                    "material_id": item.material_id,
                    "material_name": item.material.name if item.material else None,
                    "material_code": item.material.material_code if item.material else None,
                    "quantity": float(item.quantity),
                    "unit_price": float(item.unit_price),
                    "display_specification": item.display_specification,
                    "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                    "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                    "received_quantity": float(item.received_quantity),
                    "returned_quantity": float(item.returned_quantity),
                }
                for item in order.items
            ],
        }
    )


@router.get("/approvals/pending")
def pending_purchase_approvals(current_user: User = Depends(require_roles("manager", "chairman", "system_admin")), db: Session = Depends(get_db)):
    rows = db.execute(
        select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier)).where(PurchaseOrder.status == "pending_approval").order_by(PurchaseOrder.created_at.desc())
    ).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "order_no": row.order_no,
                "supplier_name": row.supplier.name if row.supplier else None,
                "total_amount": float(row.total_amount),
                "threshold_triggered": row.threshold_triggered,
                "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
            }
            for row in rows
        ]
    )


@router.get("/approvals/history")
def purchase_approval_history(current_user: User = Depends(require_roles("manager", "chairman", "system_admin")), db: Session = Depends(get_db)):
    approvals = db.execute(
        select(PurchaseOrderApproval)
        .where(PurchaseOrderApproval.approver_user_id == current_user.id)
        .order_by(PurchaseOrderApproval.approved_at.desc(), PurchaseOrderApproval.id.desc())
    ).scalars().all()
    order_ids = [row.purchase_order_id for row in approvals]
    orders = (
        {
            row.id: row
            for row in db.execute(
                select(PurchaseOrder)
                .options(joinedload(PurchaseOrder.supplier))
                .where(PurchaseOrder.id.in_(order_ids))
            ).scalars().all()
        }
        if order_ids
        else {}
    )
    return api_response(
        data=[
            {
                "id": row.id,
                "purchase_order_id": row.purchase_order_id,
                "order_no": orders[row.purchase_order_id].order_no if row.purchase_order_id in orders else None,
                "supplier_name": (
                    orders[row.purchase_order_id].supplier.name
                    if row.purchase_order_id in orders and orders[row.purchase_order_id].supplier
                    else None
                ),
                "total_amount": float(orders[row.purchase_order_id].total_amount or 0) if row.purchase_order_id in orders else 0,
                "payment_type_label": payment_type_label(
                    orders[row.purchase_order_id].payment_type if row.purchase_order_id in orders else None,
                    module="purchase",
                ),
                "decision": row.decision,
                "decision_label": "通过" if row.decision == "approved" else "驳回",
                "note": row.note,
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "approver_name": current_user.full_name,
                "order_status": orders[row.purchase_order_id].status if row.purchase_order_id in orders else None,
                "order_status_label": status_label(orders[row.purchase_order_id].status if row.purchase_order_id in orders else None),
            }
            for row in approvals
        ]
    )


@router.post("/orders")
def create_purchase_order(payload: PurchaseOrderCreate, current_user: User = Depends(require_roles("purchase")), db: Session = Depends(get_db)):
    return create_purchase_order_service(payload, current_user, db)


@router.put("/orders/{order_id}")
def update_purchase_order(order_id: int, payload: PurchaseOrderUpdate, current_user: User = Depends(require_roles("purchase")), db: Session = Depends(get_db)):
    return update_purchase_order_service(order_id, payload, current_user, db)


@router.delete("/orders/{order_id}")
def cancel_purchase_order(order_id: int, current_user: User = Depends(require_roles("purchase")), db: Session = Depends(get_db)):
    return cancel_purchase_order_service(order_id, current_user, db)


@router.post("/orders/{order_id}/approve")
def approve_purchase_order(order_id: int, payload: PurchaseApprovalRequest, current_user: User = Depends(require_roles("manager", "system_admin")), db: Session = Depends(get_db)):
    return approve_purchase_order_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/confirm")
def confirm_purchase_order(order_id: int, current_user: User = Depends(require_roles("purchase", "system_admin")), db: Session = Depends(get_db)):
    return confirm_purchase_order_service(order_id, current_user, db)


@router.post("/settlements/payment-requests")
def submit_purchase_payment_request(
    payload: PurchasePaymentRequestCreate,
    current_user: User = Depends(require_roles("purchase", "finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return submit_purchase_payment_request_service(payload, current_user, db)


@router.post("/settlements/payment-requests/merged")
def submit_merged_purchase_payment_request(
    payload: PurchaseMergedPaymentRequestCreate,
    current_user: User = Depends(require_roles("purchase", "finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return submit_merged_purchase_payment_request_service(payload, current_user, db)


@router.post("/settlements/payment-requests/{request_id}/approve")
def approve_purchase_payment_request(
    request_id: int,
    payload: PurchasePaymentApprovalRequest,
    current_user: User = Depends(require_roles("chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_purchase_payment_request_service(request_id, payload, current_user, db)


@router.post("/settlements/payment-requests/{request_id}/complete")
def complete_purchase_payment_request(
    request_id: int,
    payload: PurchasePaymentCompleteRequest,
    current_user: User = Depends(require_roles("purchase", "system_admin")),
    db: Session = Depends(get_db),
):
    return complete_purchase_payment_request_service(request_id, payload, current_user, db)


@router.get("/receipts")
def list_receipts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(PurchaseReceipt).order_by(PurchaseReceipt.created_at.desc())
    warehouse_ids = get_inventory_scope_warehouse_ids(db, current_user)
    if current_user.role.code == "purchase":
        scoped_user_ids = get_scoped_user_ids(db, current_user, "purchase")
        if scoped_user_ids is not None:
            order_ids = [
                row.id for row in db.execute(select(PurchaseOrder.id).where(PurchaseOrder.purchaser_user_id.in_(scoped_user_ids))).all()
            ]
            query = query.where(PurchaseReceipt.purchase_order_id.in_(order_ids or [-1]))
    elif warehouse_ids is not None:
        query = query.where(PurchaseReceipt.warehouse_id.in_(warehouse_ids or [-1]))

    rows = db.execute(query).scalars().all()
    order_ids = [row.purchase_order_id for row in rows]
    receipt_ids = [row.id for row in rows]
    orders = {row.id: row for row in db.execute(select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier)).where(PurchaseOrder.id.in_(order_ids))).scalars().all()} if order_ids else {}
    warehouses = {row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_([row.warehouse_id for row in rows]))).scalars().all()} if rows else {}
    receivers = {row.id: row for row in db.execute(select(User).where(User.id.in_([row.received_by_id for row in rows if row.received_by_id]))).scalars().all()} if rows else {}
    receipt_items = db.execute(select(PurchaseReceiptItem).where(PurchaseReceiptItem.purchase_receipt_id.in_(receipt_ids))).scalars().all() if receipt_ids else []

    item_count_map: dict[int, int] = {}
    quantity_map: dict[int, Decimal] = {}
    for item in receipt_items:
        item_count_map[item.purchase_receipt_id] = item_count_map.get(item.purchase_receipt_id, 0) + 1
        quantity_map[item.purchase_receipt_id] = quantity_map.get(item.purchase_receipt_id, Decimal("0")) + Decimal(item.received_quantity or 0)

    return api_response(
        data=[
            {
                "id": row.id,
                "receipt_no": row.receipt_no,
                "purchase_order_id": row.purchase_order_id,
                "purchase_order_no": orders[row.purchase_order_id].order_no if row.purchase_order_id in orders else None,
                "supplier_name": orders[row.purchase_order_id].supplier.name if row.purchase_order_id in orders and orders[row.purchase_order_id].supplier else None,
                "warehouse_name": warehouses[row.warehouse_id].name if row.warehouse_id in warehouses else None,
                "status": row.status,
                "status_label": status_label(row.status),
                "requires_quality": row.requires_quality,
                "quality_status": row.quality_status,
                "quality_status_label": status_label(row.quality_status),
                "remark": row.remark,
                "preliminary_acceptance_note": row.preliminary_acceptance_note,
                "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
                "received_by_name": receivers[row.received_by_id].full_name if row.received_by_id in receivers else None,
                "item_count": item_count_map.get(row.id, 0),
                "received_quantity": float(quantity_map.get(row.id, 0)),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/receipts/{receipt_id}")
def get_receipt_detail(receipt_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    receipt = db.execute(select(PurchaseReceipt).where(PurchaseReceipt.id == receipt_id)).scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=404, detail="到货单不存在")
    if not warehouse_in_scope(db, current_user, receipt.warehouse_id):
        order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == receipt.purchase_order_id)).scalar_one_or_none()
        if order is None or not user_in_scope(db, current_user, business_role_code="purchase", owner_user_id=order.purchaser_user_id):
            raise HTTPException(status_code=403, detail="仅可查看权限范围内的到货单")

    order = db.execute(
        select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.purchaser)).where(PurchaseOrder.id == receipt.purchase_order_id)
    ).scalar_one_or_none()
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == receipt.warehouse_id)).scalar_one_or_none()
    receiver = db.execute(select(User).where(User.id == receipt.received_by_id)).scalar_one_or_none()
    items = db.execute(select(PurchaseReceiptItem).where(PurchaseReceiptItem.purchase_receipt_id == receipt.id).order_by(PurchaseReceiptItem.id.asc())).scalars().all()
    order_item_ids = [row.purchase_order_item_id for row in items]
    order_items = {
        row.id: row
        for row in db.execute(
            select(PurchaseOrderItem)
            .options(joinedload(PurchaseOrderItem.product), joinedload(PurchaseOrderItem.material))
            .where(PurchaseOrderItem.id.in_(order_item_ids))
        ).scalars().all()
    } if order_item_ids else {}
    inventory_request = db.execute(
        select(InventoryOperationRequest)
        .where(
            InventoryOperationRequest.source_type == "purchase_receipt",
            InventoryOperationRequest.source_id == receipt.id,
            InventoryOperationRequest.request_category.in_(["purchase_receipt_inbound", "purchase_quality_inbound"]),
        )
        .order_by(InventoryOperationRequest.id.desc())
    ).scalars().first()

    return api_response(
        data={
            "id": receipt.id,
            "receipt_no": receipt.receipt_no,
            "purchase_order_id": receipt.purchase_order_id,
            "purchase_order_no": order.order_no if order else None,
            "supplier_name": order.supplier.name if order and order.supplier else None,
            "warehouse_name": warehouse.name if warehouse else None,
            "status": receipt.status,
            "status_label": status_label(receipt.status),
            "requires_quality": receipt.requires_quality,
            "quality_status": receipt.quality_status,
            "quality_status_label": status_label(receipt.quality_status),
            "inventory_request_id": inventory_request.id if inventory_request else None,
            "inventory_request_no": inventory_request.request_no if inventory_request else None,
            "inventory_request_status": inventory_request.status if inventory_request else None,
            "inventory_request_status_label": status_label(inventory_request.status) if inventory_request else None,
            "remark": receipt.remark,
            "preliminary_acceptance_note": receipt.preliminary_acceptance_note,
            "recorded_at": receipt.recorded_at.isoformat() if receipt.recorded_at else None,
            "received_by_name": receiver.full_name if receiver else None,
            "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
            "items": [
                {
                    "id": row.id,
                    "purchase_order_item_id": row.purchase_order_item_id,
                    "item_code": order_items[row.purchase_order_item_id].product.product_code if row.purchase_order_item_id in order_items and order_items[row.purchase_order_item_id].product else (order_items[row.purchase_order_item_id].material.material_code if row.purchase_order_item_id in order_items and order_items[row.purchase_order_item_id].material else None),
                    "item_name": order_items[row.purchase_order_item_id].product.name if row.purchase_order_item_id in order_items and order_items[row.purchase_order_item_id].product else (order_items[row.purchase_order_item_id].material.name if row.purchase_order_item_id in order_items and order_items[row.purchase_order_item_id].material else None),
                    "received_quantity": float(row.received_quantity),
                    "expected_quantity": float(row.expected_quantity or 0),
                    "difference_remark": row.difference_remark,
                    "batch_no": row.batch_no,
                    "unit_cost": float(row.unit_cost),
                    "total_cost": float(Decimal(row.received_quantity or 0) * Decimal(row.unit_cost or 0)),
                }
                for row in items
            ],
        }
    )


@router.post("/receipts")
def create_receipt(payload: PurchaseReceiptCreate, current_user: User = Depends(require_roles("purchase", "inventory")), db: Session = Depends(get_db)):
    if current_user.role.code == "inventory" and not warehouse_in_scope(db, current_user, payload.warehouse_id):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的到货")
    return create_receipt_service(payload, current_user, db)


@router.post("/returns")
def create_purchase_return(payload: PurchaseReturnCreate, current_user: User = Depends(require_roles("purchase", "inventory")), db: Session = Depends(get_db)):
    if current_user.role.code == "inventory" and not warehouse_in_scope(db, current_user, payload.warehouse_id):
        raise HTTPException(status_code=403, detail="仅可处理本仓库范围内的退货")
    return create_purchase_return_service(payload, current_user, db)


@router.get("/settlements")
def list_purchase_settlements(
    current_user: User = Depends(require_roles("purchase", "finance", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    scoped_user_ids = get_scoped_user_ids(db, current_user, "purchase")
    snapshot = build_purchase_settlement_snapshot(current_user, db, scoped_user_ids=scoped_user_ids)
    orders = snapshot["orders"]
    completed_orders = snapshot["completed_orders"]
    payment_requests = snapshot["payment_requests"]
    payables = snapshot["payables"]
    order_ids = [row["id"] for row in orders] + [row["id"] for row in completed_orders]
    purchase_returns = (
        db.execute(select(PurchaseReturn).where(PurchaseReturn.purchase_order_id.in_(order_ids)).order_by(PurchaseReturn.created_at.desc(), PurchaseReturn.id.desc())).scalars().all()
        if order_ids
        else []
    )
    return_ids = [row.id for row in purchase_returns]
    refunds = (
        db.execute(select(RefundRecord).where(RefundRecord.purchase_return_id.in_(return_ids)).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())).scalars().all()
        if return_ids
        else []
    )
    user_ids = [row.paid_by_id for row in payables if row.paid_by_id] + [row.processed_by_id for row in refunds if row.processed_by_id]
    user_map = {
        row.id: row
        for row in db.execute(select(User).where(User.id.in_(list({user_id for user_id in user_ids if user_id})))).scalars().all()
    } if user_ids else {}
    return_map = {row.id: row for row in purchase_returns}
    all_order_rows = [*orders, *completed_orders]
    order_no_map = {row["id"]: row["order_no"] for row in all_order_rows}
    notifications = db.execute(
        select(NotificationRecipient)
        .options(joinedload(NotificationRecipient.notification))
        .join(Notification, Notification.id == NotificationRecipient.notification_id)
        .where(NotificationRecipient.user_id == current_user.id, Notification.module.in_(["purchase", "finance"]))
        .order_by(NotificationRecipient.created_at.desc(), NotificationRecipient.id.desc())
    ).scalars().all()

    return api_response(
        data={
            "summary": {
                "order_count": len(orders),
                "completed_order_count": len(completed_orders),
                "payment_request_count": len(payment_requests),
                "payable_count": len(payables),
                "refund_count": len(refunds),
                "notification_count": len(notifications),
            },
            "notifications": [
                {
                    "id": row.id,
                    "title": row.notification.title if row.notification else "",
                    "content": row.notification.content if row.notification else "",
                    "module": row.notification.module if row.notification else "",
                    "category": row.notification.category if row.notification else "info",
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in notifications
            ],
            "orders": [
                {
                    **row,
                    "status_label": status_label(row["status"]),
                    "fund_status_label": status_label(row["fund_status"]),
                }
                for row in orders
            ],
            "completed_orders": [
                {
                    **row,
                    "status_label": status_label(row["status"]),
                    "fund_status_label": status_label(row["fund_status"]),
                }
                for row in completed_orders
            ],
            "payment_requests": [
                {
                    **row,
                    "status_label": status_label(row["status"]),
                }
                for row in payment_requests
            ],
            "payables": [
                {
                    "id": row.id,
                    "purchase_order_id": row.purchase_order_id,
                    "purchase_order_no": order_no_map.get(row.purchase_order_id),
                    "amount": float(row.amount),
                    "order_amount": float(row.order_amount or 0),
                    "other_fee_amount": float(row.other_fee_amount or 0),
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "paid_at": row.paid_at.isoformat() if row.paid_at else None,
                    "paid_by_name": user_map[row.paid_by_id].full_name if row.paid_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="payable"),
                }
                for row in payables
            ],
            "refunds": [
                {
                    "id": row.id,
                    "purchase_return_id": row.purchase_return_id,
                    "purchase_return_no": return_map[row.purchase_return_id].return_no if row.purchase_return_id in return_map else None,
                    "purchase_order_id": return_map[row.purchase_return_id].purchase_order_id if row.purchase_return_id in return_map else None,
                    "purchase_order_no": order_no_map.get(return_map[row.purchase_return_id].purchase_order_id) if row.purchase_return_id in return_map else None,
                    "amount": float(row.amount),
                    "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
                    "processed_by_name": user_map[row.processed_by_id].full_name if row.processed_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="refund"),
                }
                for row in refunds
            ],
        }
    )


@router.get("/fulfillment-demands")
def list_purchase_fulfillment_demands(
    current_user: User = Depends(require_roles("purchase", "system_admin")),
    db: Session = Depends(get_db),
):
    sync_low_stock_alerts(db)
    db.commit()
    query = (
        select(FulfillmentDemand)
        .options(joinedload(FulfillmentDemand.product), joinedload(FulfillmentDemand.sales_order))
        .where(FulfillmentDemand.fulfillment_mode == "trade", FulfillmentDemand.status == "pending")
        .order_by(FulfillmentDemand.created_at.desc(), FulfillmentDemand.id.desc())
    )
    if current_user.role.code == "purchase":
        scoped_user_ids = get_scoped_user_ids(db, current_user, "purchase")
        if scoped_user_ids is not None:
            query = query.where(
                (FulfillmentDemand.assigned_user_id.in_(scoped_user_ids or [-1])) | FulfillmentDemand.assigned_user_id.is_(None)
            )
    rows = db.execute(query).unique().scalars().all()
    raw_warehouse = get_raw_warehouse(db)
    finished_warehouse = get_finished_warehouse(db)
    pending_sales, purchase_in_transit = _expected_available_maps(db)
    product_ids = [row.product_id for row in rows if row.product_id]
    product_inventory_map = {
        row.product_id: row
        for row in db.execute(
            select(Inventory).where(Inventory.warehouse_id == finished_warehouse.id, Inventory.product_id.in_(product_ids or [-1]))
        ).scalars().all()
    } if product_ids else {}
    raw_rows = db.execute(
        select(TodoItem, ProductionMaterialUsage)
        .join(ProductionMaterialUsage, TodoItem.business_id == ProductionMaterialUsage.id)
        .options(
            joinedload(ProductionMaterialUsage.material),
            joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product),
        )
        .where(TodoItem.business_type == "raw_material_replenishment", TodoItem.status != "completed")
        .order_by(TodoItem.created_at.desc(), TodoItem.id.desc())
    ).unique().all()
    material_ids = [usage.material_id for _, usage in raw_rows]
    inventory_map = {}
    if raw_warehouse and material_ids:
        inventory_map = {
            row.material_id: row
            for row in db.execute(
                select(Inventory).where(Inventory.warehouse_id == raw_warehouse.id, Inventory.material_id.in_(material_ids))
            ).scalars().all()
        }
    low_stock_rows = db.execute(
        select(TodoItem)
        .where(TodoItem.business_type == "low_stock_replenishment", TodoItem.status != "completed", TodoItem.module == "purchase")
        .order_by(TodoItem.created_at.desc(), TodoItem.id.desc())
    ).scalars().all()
    low_stock_demands = []
    for todo in low_stock_rows:
        raw_description = todo.description or ""
        payload = {}
        if "|replenishment:" in raw_description:
            try:
                payload = json.loads(raw_description.split("|replenishment:", 1)[1])
            except Exception:
                payload = {}
        product_id = payload.get("product_id")
        material_id = payload.get("material_id")
        low_stock_demands.append(
            {
                "id": f"low-{todo.id}",
                "low_stock_todo_id": todo.id,
                "demand_type": "low_stock_product" if product_id else "low_stock_material",
                "source_type": "low_stock_replenishment",
                "demand_no": f"AQ-{todo.id}",
                "sales_order_id": None,
                "sales_order_no": None,
                "production_task_id": None,
                "production_task_no": None,
                "production_product_name": None,
                "product_id": product_id,
                "product_name": payload.get("product_name"),
                "material_id": material_id,
                "material_name": payload.get("material_name"),
                "material_code": None,
                "item_name": payload.get("product_name") or payload.get("material_name") or todo.title,
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
    raw_demands = []
    for todo, usage in raw_rows:
        task = usage.production_task
        material = usage.material
        inventory = inventory_map.get(usage.material_id)
        available_quantity = Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0) if inventory else Decimal("0")
        expected_available_quantity = available_quantity + Decimal(purchase_in_transit.get(_stock_key(material_id=usage.material_id), 0))
        shortage_quantity = max(Decimal(usage.requested_quantity or 0) - available_quantity, Decimal("0"))
        raw_demands.append(
            {
                "id": f"raw-{todo.id}",
                "raw_todo_id": todo.id,
                "usage_id": usage.id,
                "demand_no": f"YL-{todo.id}",
                "demand_type": "raw_material",
                "source_type": "production_material_shortage",
                "sales_order_id": None,
                "sales_order_no": None,
                "production_task_id": task.id if task else None,
                "production_task_no": task.task_no if task else None,
                "production_product_name": task.product.name if task and task.product else None,
                "product_id": None,
                "product_name": None,
                "material_id": usage.material_id,
                "material_name": material.name if material else None,
                "material_code": material.material_code if material else None,
                "item_name": material.name if material else None,
                "shortage_quantity": float(shortage_quantity),
                "safety_stock_qty": float(material.safety_stock_qty or 0) if material else 0,
                "expected_available_quantity": float(expected_available_quantity),
                "status": "pending",
                "status_label": "待处理",
                "assigned_user_id": todo.assigned_user_id,
                "process_note": todo.description,
                "cancel_reason": None,
                "created_at": todo.created_at.isoformat() if todo.created_at else None,
            }
        )
    return api_response(
        data=[
            {
                "id": row.id,
                "demand_type": "trade_product",
                "source_type": "sales_shortage",
                "demand_no": row.demand_no,
                "sales_order_id": row.sales_order_id,
                "sales_order_no": row.sales_order.order_no if row.sales_order else None,
                "production_task_id": None,
                "production_task_no": None,
                "production_product_name": None,
                "product_id": row.product_id,
                "product_name": row.product.name if row.product else None,
                "material_id": None,
                "material_name": None,
                "material_code": None,
                "item_name": row.product.name if row.product else None,
                "shortage_quantity": float(row.shortage_quantity or 0),
                "safety_stock_qty": float(row.product.safety_stock_qty or 0) if row.product else 0,
                "expected_available_quantity": float(
                    (
                        Decimal((product_inventory_map.get(row.product_id).quantity if product_inventory_map.get(row.product_id) else 0) or 0)
                        - Decimal((product_inventory_map.get(row.product_id).reserved_quantity if product_inventory_map.get(row.product_id) else 0) or 0)
                        - Decimal(pending_sales.get(_stock_key(product_id=row.product_id), 0))
                        + Decimal(purchase_in_transit.get(_stock_key(product_id=row.product_id), 0))
                    )
                ),
                "status": row.status,
                "status_label": status_label(row.status),
                "assigned_user_id": row.assigned_user_id,
                "process_note": row.process_note,
                "cancel_reason": row.cancel_reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ] + raw_demands + low_stock_demands
    )


def _latest_supplier_price_for_item(db: Session, supplier_id: int, *, product_id: int | None = None, material_id: int | None = None) -> dict:
    item_filter = PurchaseOrderItem.product_id == product_id if product_id else PurchaseOrderItem.material_id == material_id
    row = db.execute(
        select(PurchaseOrderItem, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.supplier_id == supplier_id, PurchaseOrder.status != "cancelled", item_filter)
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrderItem.id.desc())
    ).first()
    if row is None:
        return {"latest_price": None, "latest_order_no": None, "latest_price_at": None}
    item, order = row
    return {
        "latest_price": float(item.unit_price or 0),
        "latest_order_no": order.order_no,
        "latest_price_at": order.created_at.isoformat() if order.created_at else None,
    }


@router.get("/supplier-options")
def supplier_options_for_item(
    product_id: int | None = None,
    material_id: int | None = None,
    current_user: User = Depends(require_roles("purchase", "system_admin")),
    db: Session = Depends(get_db),
):
    if bool(product_id) == bool(material_id):
        raise HTTPException(status_code=409, detail="请指定一个产品或原料")
    catalog_filter = SupplierCatalogItem.product_id == product_id if product_id else SupplierCatalogItem.material_id == material_id
    rows = db.execute(
        select(SupplierCatalogItem, Supplier)
        .join(Supplier, Supplier.id == SupplierCatalogItem.supplier_id)
        .where(Supplier.status == "enabled", catalog_filter)
    ).all()
    options = []
    for catalog_item, supplier in rows:
        latest = _latest_supplier_price_for_item(db, supplier.id, product_id=product_id, material_id=material_id)
        options.append(
            {
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "supplier_code": supplier.code,
                "catalog_item_id": catalog_item.id,
                **latest,
            }
        )
    options.sort(key=lambda item: (item["latest_price"] is None, item["latest_price"] or 0, item["supplier_name"] or ""))
    return api_response(data=options)


@router.post("/fulfillment-demands/{demand_id}/cancel")
def cancel_purchase_fulfillment_demand(
    demand_id: int,
    payload: dict = None,
    current_user: User = Depends(require_roles("purchase")),
    db: Session = Depends(get_db),
):
    reason = (payload or {}).get("reason", "").strip() if isinstance(payload, dict) else ""
    if len(reason) < 2:
        raise HTTPException(status_code=409, detail="请填写取消原因")
    row = db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id == demand_id)).scalar_one_or_none()
    if row is None or row.fulfillment_mode != "trade":
        raise HTTPException(status_code=404, detail="补货需求不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="当前补货需求已处理")
    row.status = "cancelled"
    row.processed_by_id = current_user.id
    row.cancel_reason = reason
    complete_todos(db, business_type="fulfillment_demand", business_id=row.id)
    recipient_user_ids = [current_user.id]
    if row.sales_order and row.sales_order.sales_user_id:
        recipient_user_ids.append(row.sales_order.sales_user_id)
    create_notification(
        db,
        title="采购补货需求已取消",
        content=f"补货需求 {row.demand_no} 已取消，原因：{row.cancel_reason}",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys(recipient_user_ids)),
        created_by_id=current_user.id,
        category="warning",
    )
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})


@router.post("/fulfillment-demands/{demand_id}/link-order/{order_id}")
def link_purchase_fulfillment_demand_order(
    demand_id: int,
    order_id: int,
    current_user: User = Depends(require_roles("purchase")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id == demand_id)).scalar_one_or_none()
    if row is None or row.fulfillment_mode != "trade":
        raise HTTPException(status_code=404, detail="补货需求不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="当前补货需求已处理")
    order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if order.purchaser_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可关联本人创建的采购订单")

    row.status = "converted"
    row.processed_by_id = current_user.id
    row.process_note = f"已转采购订单 {order.order_no}"
    complete_todos(db, business_type="fulfillment_demand", business_id=row.id)
    recipient_user_ids = [current_user.id]
    if row.sales_order and row.sales_order.sales_user_id:
        recipient_user_ids.append(row.sales_order.sales_user_id)
    create_notification(
        db,
        title="采购补货需求已转采购单",
        content=f"补货需求 {row.demand_no} 已转为采购订单 {order.order_no}。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys(recipient_user_ids)),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="convert_fulfillment_demand",
        content=f"补货需求 {row.demand_no} 关联采购订单 {order.order_no}",
        after_data={"demand_id": row.id, "purchase_order_id": order.id, "status": row.status},
    )
    db.commit()
    return api_response(data={"id": row.id, "status": row.status})


@router.post("/raw-material-replenishments/{todo_id}/link-order/{order_id}")
def link_raw_material_replenishment_order(
    todo_id: int,
    order_id: int,
    current_user: User = Depends(require_roles("purchase")),
    db: Session = Depends(get_db),
):
    todo = db.execute(
        select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.business_type == "raw_material_replenishment",
        )
    ).scalar_one_or_none()
    if todo is None:
        raise HTTPException(status_code=404, detail="原料补货需求不存在")
    if todo.status == "completed":
        raise HTTPException(status_code=409, detail="原料补货需求已处理")

    order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if order.purchaser_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可关联本人创建的采购订单")

    usage = db.execute(
        select(ProductionMaterialUsage)
        .options(joinedload(ProductionMaterialUsage.material), joinedload(ProductionMaterialUsage.production_task))
        .where(ProductionMaterialUsage.id == todo.business_id)
    ).unique().scalar_one_or_none()

    todo.status = "completed"
    todo.completed_at = todo.completed_at or now_local()

    recipient_user_ids = [current_user.id]
    if usage and usage.production_task and usage.production_task.created_by_id:
        recipient_user_ids.append(usage.production_task.created_by_id)
    create_notification(
        db,
        title="原料补货需求已转采购单",
        content=(
            f"原料补货需求 {todo.title} 已转为采购订单 {order.order_no}。"
            if usage is None
            else f"生产任务 {usage.production_task.task_no if usage.production_task else '-'} 的原料 {usage.material.name if usage.material else usage.material_id} 已转为采购订单 {order.order_no}。"
        ),
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([user_id for user_id in recipient_user_ids if user_id])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="convert_raw_material_replenishment",
        content=f"原料补货需求 {todo.id} 关联采购订单 {order.order_no}",
        after_data={"todo_id": todo.id, "purchase_order_id": order.id, "status": todo.status},
    )
    db.commit()
    return api_response(data={"id": todo.id, "status": todo.status})


@router.get("/returns/{return_id}")
def get_purchase_return_detail(return_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    purchase_return = db.execute(select(PurchaseReturn).where(PurchaseReturn.id == return_id)).scalar_one_or_none()
    if purchase_return is None:
        raise HTTPException(status_code=404, detail="退货处理单不存在")
    if not warehouse_in_scope(db, current_user, purchase_return.warehouse_id):
        order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == purchase_return.purchase_order_id)).scalar_one_or_none()
        if order is None or not user_in_scope(db, current_user, business_role_code="purchase", owner_user_id=order.purchaser_user_id):
            raise HTTPException(status_code=403, detail="仅可查看权限范围内的退货处理单")

    order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == purchase_return.purchase_order_id)).scalar_one_or_none()
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == purchase_return.warehouse_id)).scalar_one_or_none()
    refund_records = db.execute(
        select(RefundRecord).where(RefundRecord.purchase_return_id == purchase_return.id).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())
    ).scalars().all()
    inventory_request = db.execute(
        select(InventoryOperationRequest)
        .where(
            InventoryOperationRequest.source_type == "purchase_return",
            InventoryOperationRequest.source_id == purchase_return.id,
            InventoryOperationRequest.request_category == "purchase_return_outbound",
        )
        .order_by(InventoryOperationRequest.id.desc())
    ).scalars().first()
    transactions = db.execute(
        select(InventoryTransaction)
        .where(InventoryTransaction.related_type == "inventory_operation_request")
        .where(InventoryTransaction.related_id == (inventory_request.id if inventory_request else -1))
        .order_by(InventoryTransaction.created_at.asc())
    ).scalars().all()

    product_ids = {row.product_id for row in transactions if row.product_id}
    material_ids = {row.material_id for row in transactions if row.material_id}
    if inventory_request:
        for item in (inventory_request.handled_items_json or inventory_request.original_items_json or []):
            if item.get("product_id"):
                product_ids.add(item.get("product_id"))
            if item.get("material_id"):
                material_ids.add(item.get("material_id"))
    products = {row.id: row for row in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()} if product_ids else {}
    materials = {row.id: row for row in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars().all()} if material_ids else {}
    user_ids = [purchase_return.returned_by_id] + [row.created_by_id for row in transactions if row.created_by_id] + [row.processed_by_id for row in refund_records if row.processed_by_id]
    user_map = {row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()} if user_ids else {}

    return api_response(
        data={
            "id": purchase_return.id,
            "return_no": purchase_return.return_no,
            "purchase_order_id": purchase_return.purchase_order_id,
            "purchase_order_no": order.order_no if order else None,
            "warehouse_id": purchase_return.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else None,
            "returned_by_name": user_map[purchase_return.returned_by_id].full_name if purchase_return.returned_by_id in user_map else None,
            "return_type": purchase_return.return_type,
            "return_type_label": return_type_label(purchase_return.return_type),
            "reason": clean_display_text(purchase_return.reason, f"采购{return_type_label(purchase_return.return_type)}申请"),
            "status": purchase_return.status,
            "status_label": status_label(purchase_return.status),
            "requires_reinspection": purchase_return.requires_reinspection,
            "reinspection_status": purchase_return.reinspection_status,
            "reinspection_status_label": status_label(purchase_return.reinspection_status),
            "inventory_request_id": inventory_request.id if inventory_request else None,
            "inventory_request_no": inventory_request.request_no if inventory_request else None,
            "inventory_request_status": inventory_request.status if inventory_request else None,
            "inventory_request_status_label": status_label(inventory_request.status) if inventory_request else None,
            "refund_amount": float(purchase_return.refund_amount),
            "refund_records": [
                {
                    "id": row.id,
                    "amount": float(row.amount),
                    "remark": row.remark,
                    "processed_by_name": user_map[row.processed_by_id].full_name if row.processed_by_id in user_map else None,
                    "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
                }
                for row in refund_records
            ],
            "created_at": purchase_return.created_at.isoformat() if purchase_return.created_at else None,
            "items": [
                {
                    "transaction_id": row.id,
                    "transaction_no": row.transaction_no,
                    "item_code": products[row.product_id].product_code if row.product_id in products else (materials[row.material_id].material_code if row.material_id in materials else None),
                    "item_name": products[row.product_id].name if row.product_id in products else (materials[row.material_id].name if row.material_id in materials else None),
                    "quantity": float(abs(Decimal(row.quantity or 0))),
                    "unit_cost": float(row.unit_cost or 0),
                    "total_cost": float(abs(Decimal(row.total_cost or 0))),
                    "item_type": "product" if row.product_id else "material",
                    "batch_no": row.batch_no,
                    "operator_name": user_map[row.created_by_id].full_name if row.created_by_id in user_map else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in transactions
            ] or [
                {
                    "transaction_id": None,
                    "transaction_no": inventory_request.request_no if inventory_request else None,
                    "item_code": products[item.get("product_id")].product_code if item.get("product_id") in products else (materials[item.get("material_id")].material_code if item.get("material_id") in materials else None),
                    "item_name": products[item.get("product_id")].name if item.get("product_id") in products else (materials[item.get("material_id")].name if item.get("material_id") in materials else None),
                    "quantity": float(abs(Decimal(item.get("quantity") or 0))),
                    "unit_cost": float(item.get("unit_cost") or 0),
                    "total_cost": float(abs(Decimal(item.get("quantity") or 0)) * Decimal(item.get("unit_cost") or 0)),
                    "item_type": "product" if item.get("product_id") else "material",
                    "batch_no": item.get("batch_no"),
                    "operator_name": None,
                    "created_at": inventory_request.created_at.isoformat() if inventory_request and inventory_request.created_at else None,
                }
                for item in ((inventory_request.handled_items_json or inventory_request.original_items_json or []) if inventory_request else [])
            ],
        }
    )


@router.get("/returns")
def list_purchase_returns(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(PurchaseReturn).order_by(PurchaseReturn.created_at.desc())
    warehouse_ids = get_inventory_scope_warehouse_ids(db, current_user)
    if current_user.role.code == "purchase":
        scoped_user_ids = get_scoped_user_ids(db, current_user, "purchase")
        if scoped_user_ids is not None:
            order_ids = [row.id for row in db.execute(select(PurchaseOrder.id).where(PurchaseOrder.purchaser_user_id.in_(scoped_user_ids))).all()]
            query = query.where(PurchaseReturn.purchase_order_id.in_(order_ids or [-1]))
    elif warehouse_ids is not None:
        query = query.where(PurchaseReturn.warehouse_id.in_(warehouse_ids or [-1]))

    rows = db.execute(query).scalars().all()
    order_ids = [row.purchase_order_id for row in rows]
    warehouse_ids_list = [row.warehouse_id for row in rows]
    return_ids = [row.id for row in rows]
    orders = {row.id: row for row in db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(order_ids))).scalars().all()} if order_ids else {}
    warehouses = {row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids_list))).scalars().all()} if warehouse_ids_list else {}
    refunds = db.execute(select(RefundRecord).where(RefundRecord.purchase_return_id.in_(return_ids))).scalars().all() if return_ids else []
    inventory_requests = db.execute(
        select(InventoryOperationRequest).where(
            InventoryOperationRequest.source_type == "purchase_return",
            InventoryOperationRequest.source_id.in_(return_ids or [-1]),
            InventoryOperationRequest.request_category == "purchase_return_outbound",
        )
    ).scalars().all() if return_ids else []
    transactions = db.execute(
        select(InventoryTransaction).where(
            InventoryTransaction.related_type == "inventory_operation_request",
            InventoryTransaction.related_id.in_([row.id for row in inventory_requests] or [-1]),
        )
    ).scalars().all() if return_ids else []
    request_map = {row.source_id: row for row in inventory_requests}
    request_id_map = {row.id: row for row in inventory_requests}

    transaction_map: dict[int, dict] = {}
    for row in transactions:
        request = request_id_map.get(row.related_id)
        if request is None:
            continue
        bucket = transaction_map.setdefault(request.source_id, {"item_count": 0, "total_return_quantity": Decimal("0")})
        bucket["item_count"] += 1
        bucket["total_return_quantity"] += abs(Decimal(row.quantity or 0))

    for source_id, request in request_map.items():
        if source_id in transaction_map:
            continue
        items = request.handled_items_json or request.original_items_json or []
        transaction_map[source_id] = {
            "item_count": len(items),
            "total_return_quantity": sum((abs(Decimal(item.get("quantity") or 0)) for item in items), Decimal("0")),
        }

    refund_map: dict[int, dict] = {}
    for row in refunds:
        bucket = refund_map.setdefault(row.purchase_return_id, {"refunded_amount": Decimal("0"), "last_refunded_at": None})
        bucket["refunded_amount"] += Decimal(row.amount or 0)
        bucket["last_refunded_at"] = row.refunded_at.isoformat() if row.refunded_at else bucket["last_refunded_at"]

    return api_response(
        data=[
            {
                "id": row.id,
                "return_no": row.return_no,
                "purchase_order_id": row.purchase_order_id,
                "purchase_order_no": orders[row.purchase_order_id].order_no if row.purchase_order_id in orders else None,
                "warehouse_id": row.warehouse_id,
                "warehouse_name": warehouses[row.warehouse_id].name if row.warehouse_id in warehouses else None,
                "return_type": row.return_type,
                "return_type_label": return_type_label(row.return_type),
                "reason": clean_display_text(row.reason, f"采购{return_type_label(row.return_type)}申请"),
                "status": row.status,
                "status_label": status_label(row.status),
                "requires_reinspection": row.requires_reinspection,
                "reinspection_status": row.reinspection_status,
                "reinspection_status_label": status_label(row.reinspection_status),
                "refund_amount": float(row.refund_amount),
                "refunded_amount": float(refund_map.get(row.id, {}).get("refunded_amount", 0)),
                "last_refunded_at": refund_map.get(row.id, {}).get("last_refunded_at"),
                "item_count": transaction_map.get(row.id, {}).get("item_count", 0),
                "total_return_quantity": float(transaction_map.get(row.id, {}).get("total_return_quantity", 0)),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )
