from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from models import (
    Inventory,
    InventoryTransaction,
    InventoryDispatchQueue,
    InventoryOperationRequest,
    PayableRecord,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReturn,
    QualityTask,
    ReceivableRecord,
    Role,
    SalesOrder,
    SalesReturn,
    SalesReturnItem,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.inventory import InventoryOperationApprovalRequest, InventoryOperationHandleRequest
from services.core import apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, resolve_workflow_recipients, role_user_ids, write_operation_log
from services.display import status_label
from services.time_utils import now_local
from services.warehouse_lookup import get_finished_warehouse, get_raw_warehouse


def _normalized_inventory_items(items: list[dict]) -> list[dict]:
    normalized = []
    for item in items or []:
        normalized.append(
            {
                "product_id": item.get("product_id") or None,
                "sales_order_item_id": item.get("sales_order_item_id") or None,
                "material_id": item.get("material_id") or None,
                "packaging_config_id": item.get("packaging_config_id") or None,
                "batch_no": (item.get("batch_no") or None),
                "quantity": str(Decimal(str(item.get("quantity") or 0)).normalize()),
                "unit_cost": str(Decimal(str(item.get("unit_cost") or 0)).normalize()),
            }
        )
    return normalized


def _inventory_request_identity_items(items: list[dict]) -> list[dict]:
    normalized = _normalized_inventory_items(items)
    for index, item in enumerate(items or []):
        if index >= len(normalized):
            break
        normalized[index]["purchase_receipt_item_id"] = item.get("purchase_receipt_item_id") or None
        normalized[index]["usage_id"] = item.get("usage_id") or None
    return normalized


def _quantity_bucket(items: list[dict]) -> dict[tuple[int | None, int | None, int | None, str | None], Decimal]:
    bucket: dict[tuple[int | None, int | None, int | None, str | None], Decimal] = {}
    for item in items or []:
        key = (
            item.get("product_id") or None,
            item.get("material_id") or None,
            item.get("packaging_config_id") or None,
            item.get("batch_no") or None,
        )
        bucket[key] = bucket.get(key, Decimal("0")) + Decimal(str(item.get("quantity") or 0))
    return bucket


def _has_quantity_difference(expected_items: list[dict], actual_items: list[dict]) -> bool:
    return _quantity_bucket(expected_items) != _quantity_bucket(actual_items)


def _operation_quantity_label(operation_type: str) -> tuple[str, str]:
    return ("应到", "实到") if operation_type == "inbound" else ("应出", "实出")


def finished_warehouse_id(db: Session) -> int:
    return get_finished_warehouse(db).id


def raw_warehouse_id(db: Session) -> int:
    return get_raw_warehouse(db).id


def purchase_arrival_status(db: Session, order_id: int) -> str:
    order = db.execute(
        select(PurchaseOrder)
        .options(joinedload(PurchaseOrder.items))
        .where(PurchaseOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        return "not_arrived"
    receipts = db.execute(
        select(PurchaseReceipt).where(PurchaseReceipt.purchase_order_id == order_id)
    ).scalars().all()
    if not receipts:
        return "not_arrived"
    fully_received = all(Decimal(item.received_quantity or 0) >= Decimal(item.quantity or 0) for item in order.items)
    if not fully_received:
        return "not_arrived"
    for receipt in receipts:
        if receipt.requires_quality and receipt.quality_status != "approved":
            return "not_arrived"
    return "arrived"


APPROVAL_REQUIRED_INBOUND_CATEGORIES: set[str] = set()


# 需求变更(2026-09):入库环节不再需要总经理审批,库管可直接执行入库。
# 原有需要审批的入库类别(采购到货/采购质检/生产完工/生产质检)全部改为直接入库。


def _requires_inventory_manager_approval(operation_type: str, request_category: str) -> bool:
    return operation_type == "inbound" and request_category in APPROVAL_REQUIRED_INBOUND_CATEGORIES


def _ensure_inventory_operation_todos(
    db: Session,
    *,
    request: InventoryOperationRequest,
    user_ids: list[int],
    title: str,
    description: str,
) -> None:
    current_user_ids = {
        row.assigned_user_id
        for row in db.execute(
            select(TodoItem).where(
                TodoItem.business_type == "inventory_operation_request",
                TodoItem.business_id == request.id,
                TodoItem.status != "completed",
            )
        ).scalars().all()
    }
    for user_id in user_ids:
        if user_id in current_user_ids:
            continue
        create_todo(
            db,
            title=title,
            description=description,
            module="inventory",
            assigned_user_id=user_id,
            business_type="inventory_operation_request",
            business_id=request.id,
            priority="high",
        )


def normalize_inventory_operation_request_approval(request: InventoryOperationRequest, db: Session) -> bool:
    if not _requires_inventory_manager_approval(request.operation_type, request.request_category):
        return False
    if request.status not in {"pending", "processing"} or request.handled_by_id is not None:
        return False
    request.status = "pending_manager_approval"
    complete_todos(db, business_type="inventory_operation_request", business_id=request.id)
    _ensure_inventory_operation_todos(
        db,
        request=request,
        user_ids=role_user_ids(db, "manager"),
        title=f"待审批库存入库 {request.source_no or request.request_no}",
        description="库存入库申请需要负责人审批，审批通过后才可由库管执行。",
    )
    return True


def create_inventory_operation_request(
    db: Session,
    *,
    operation_type: str,
    request_category: str,
    source_module: str,
    source_type: str,
    source_id: int,
    source_no: str | None,
    warehouse_id: int,
    requested_by_id: int,
    items: list[dict],
    remark: str | None = None,
    recipient_user_ids: list[int] | None = None,
    title: str | None = None,
    description: str | None = None,
) -> InventoryOperationRequest:
    requires_manager_approval = _requires_inventory_manager_approval(operation_type, request_category)
    if requires_manager_approval:
        user_ids = resolve_workflow_recipients(
            db,
            "manager",
            workflow="inventory_operation_request",
            business_type="inventory_operation_request",
            business_id=source_id,
        )
    else:
        explicit_user_ids = [user_id for user_id in dict.fromkeys(recipient_user_ids or []) if user_id is not None]
        user_ids = explicit_user_ids or resolve_workflow_recipients(
            db,
            "inventory",
            workflow="inventory_operation_request",
            business_type="inventory_operation_request",
            business_id=source_id,
        )
    request_title = title or f"待{'审批' if requires_manager_approval else '执行'}库存{'出库' if operation_type == 'outbound' else '入库'} {source_no or ''}".strip()
    if requires_manager_approval and title:
        request_title = title.replace("待执行", "待审批")
    request_description = description or f"{source_module}来源单据 {source_no or source_id} 已生成库存作业请求。"
    if requires_manager_approval and description:
        request_description = f"{description} 负责人审批通过后，库管才能执行入库。"
    item_fingerprint = _inventory_request_identity_items(items)

    existing_rows = db.execute(
        select(InventoryOperationRequest).where(
            InventoryOperationRequest.source_type == source_type,
            InventoryOperationRequest.source_id == source_id,
            InventoryOperationRequest.request_category == request_category,
            InventoryOperationRequest.status.in_(["pending", "processing", "pending_manager_approval"]),
        )
    ).scalars().all()
    existing = next(
        (row for row in existing_rows if _inventory_request_identity_items(row.original_items_json or []) == item_fingerprint),
        None,
    )
    if existing is None and len(existing_rows) == 1 and request_category != "purchase_quality_inbound":
        existing = existing_rows[0]

    if existing is not None:
        existing.operation_type = operation_type
        existing.source_module = source_module
        existing.source_no = source_no
        existing.warehouse_id = warehouse_id
        existing.requested_by_id = requested_by_id
        existing.remark = remark
        existing.original_items_json = items
        existing.handled_items_json = existing.handled_items_json or []
        normalized = normalize_inventory_operation_request_approval(existing, db)
        if normalized:
            user_ids = resolve_workflow_recipients(
                db,
                "manager",
                workflow="inventory_operation_request",
                business_type="inventory_operation_request",
                business_id=existing.id,
            )
        _ensure_inventory_operation_todos(
            db,
            request=existing,
            user_ids=user_ids,
            title=request_title,
            description=request_description,
        )
        return existing

    request = InventoryOperationRequest(
        request_no=next_business_no(db, "CK" if operation_type == "outbound" else "RK"),
        operation_type=operation_type,
        request_category=request_category,
        source_module=source_module,
        source_type=source_type,
        source_id=source_id,
        source_no=source_no,
        warehouse_id=warehouse_id,
        requested_by_id=requested_by_id,
        status="pending_manager_approval" if requires_manager_approval else "pending",
        remark=remark,
        original_items_json=items,
        handled_items_json=[],
    )
    db.add(request)
    db.flush()

    request_title = title or f"待{'审批' if requires_manager_approval else '执行'}库存{'出库' if operation_type == 'outbound' else '入库'} {source_no or request.request_no}"
    if requires_manager_approval and title:
        request_title = title.replace("待执行", "待审批")
    _ensure_inventory_operation_todos(
        db,
        request=request,
        user_ids=user_ids,
        title=request_title,
        description=request_description,
    )
    if user_ids:
        create_notification(
            db,
            title=request_title,
            content=request_description,
            module="inventory",
            recipient_user_ids=user_ids,
            created_by_id=requested_by_id,
            category="info",
        )
    return request


def approve_inventory_operation_request(
    request_id: int,
    payload: InventoryOperationApprovalRequest,
    current_user: User,
    db: Session,
):
    request = db.execute(select(InventoryOperationRequest).where(InventoryOperationRequest.id == request_id).with_for_update()).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="库存作业请求不存在")
    if request.status != "pending_manager_approval":
        raise HTTPException(status_code=409, detail="当前库存入库请求没有待经理审批")
    if payload.decision == "rejected":
        request.status = "rejected"
        request.handled_by_id = current_user.id
        request.completed_at = now_local()
        request.handled_remark = (payload.note or "").strip() or "负责人驳回库存入库申请"
        complete_todos(db, business_type="inventory_operation_request", business_id=request.id)
        if request.requested_by_id:
            create_notification(
                db,
                title="库存入库申请已驳回",
                content=f"库存作业 {request.request_no} 已被负责人驳回，原因：{request.handled_remark}",
                module=request.source_module or "inventory",
                recipient_user_ids=[request.requested_by_id],
                created_by_id=current_user.id,
                category="warning",
            )
    else:
        request.status = "pending"
        request.handled_by_id = current_user.id
        request.completed_at = None
        request.handled_remark = (payload.note or "").strip() or None
        complete_todos(db, business_type="inventory_operation_request", business_id=request.id)
        inventory_user_ids = role_user_ids(db, "inventory")
        _ensure_inventory_operation_todos(
            db,
            request=request,
            user_ids=inventory_user_ids,
            title=f"待执行库存入库 {request.source_no or request.request_no}",
            description="负责人已审批通过，请库管核对明细后执行入库。",
        )
        if inventory_user_ids:
            create_notification(
                db,
                title="库存入库申请已审批通过",
                content=f"库存作业 {request.request_no} 已由负责人审批通过，请库管执行入库。",
                module="inventory",
                recipient_user_ids=inventory_user_ids,
                created_by_id=current_user.id,
                category="info",
            )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="inventory",
        action="approve_inventory_operation_request",
        content=f"处理库存入库审批 {request.request_no}",
        after_data={"request_no": request.request_no, "decision": payload.decision, "status": request.status},
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})

def _sync_inventory_request_source(
    db: Session,
    *,
    request: InventoryOperationRequest,
    current_user: User,
) -> None:
    category = request.request_category
    source_id = request.source_id
    if category in {"stock_transfer_outbound", "stock_transfer_inbound"}:
        from services.stock_transfer_workflow import sync_transfer_operation
        sync_transfer_operation(db, request, current_user)
    elif category == "production_material_issue":
        if request.source_type == "production_task":
            task = db.execute(
                select(ProductionTask)
                .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
                .where(ProductionTask.id == source_id)
            ).unique().scalar_one_or_none()
            if task is None:
                return
            usage_map = {usage.material_id: usage for usage in task.usages}
            handled_items = request.handled_items_json or []
            for item in handled_items:
                usage = usage_map.get(item.get("material_id"))
                if usage is None:
                    continue
                quantity = Decimal(str(item.get("quantity") or 0))
                usage.issued_quantity = quantity
                usage.actual_quantity = quantity
                usage.inventory_approval_status = "approved"
                usage.inventory_approved_by_id = current_user.id
                usage.inventory_approved_at = now_local()
                usage.inventory_remark = request.handled_remark
            all_issued = all(Decimal(item.issued_quantity or 0) >= Decimal(item.approved_quantity or item.requested_quantity or 0) for item in task.usages)
            if all_issued:
                task.status = "material_issued"
            create_notification(
                db,
                title="领料出库已完成",
                content=f"生产任务 {task.task_no} 的领料申请已完成出库。{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}",
                module="production",
                recipient_user_ids=[task.created_by_id],
                created_by_id=current_user.id,
                category="success",
            )
            return
        usage = db.execute(
            select(ProductionMaterialUsage)
            .options(joinedload(ProductionMaterialUsage.production_task).joinedload(ProductionTask.product), joinedload(ProductionMaterialUsage.material))
            .where(ProductionMaterialUsage.id == source_id)
        ).unique().scalar_one_or_none()
        if usage is None:
            return
        total_issued = sum(Decimal(str(item.get("quantity") or 0)) for item in (request.handled_items_json or []))
        usage.issued_quantity = total_issued
        usage.actual_quantity = total_issued
        usage.inventory_approval_status = "approved"
        usage.inventory_approved_by_id = current_user.id
        usage.inventory_approved_at = now_local()
        usage.inventory_remark = request.handled_remark
        task = usage.production_task
        all_issued = all(Decimal(item.issued_quantity or 0) >= Decimal(item.approved_quantity or item.requested_quantity or 0) for item in task.usages)
        if all_issued:
            task.status = "material_issued"
        create_notification(
            db,
            title="领料出库已完成",
            content=(
                f"生产任务 {task.task_no} 的原料 {usage.material.name if usage.material else usage.material_id} 已完成出库。"
                f"{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}"
            ),
            module="production",
            recipient_user_ids=[task.created_by_id],
            created_by_id=current_user.id,
            category="success",
        )
    elif category in {"production_finished_inbound", "production_quality_inbound"}:
        task = db.execute(select(ProductionTask).where(ProductionTask.id == source_id)).scalar_one_or_none()
        if task is None:
            return
        task.status = "completed"
        create_notification(
            db,
            title="生产入库已完成",
            content=f"生产任务 {task.task_no} 已完成成品入库。{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}",
            module="production",
            recipient_user_ids=[task.created_by_id],
            created_by_id=current_user.id,
            category="success",
        )
    elif category in {"purchase_receipt_inbound", "purchase_quality_inbound"}:
        receipt = db.execute(select(PurchaseReceipt).where(PurchaseReceipt.id == source_id)).scalar_one_or_none()
        if receipt is None:
            return
        inbound_requests = db.execute(
            select(InventoryOperationRequest).where(
                InventoryOperationRequest.source_type == "purchase_receipt",
                InventoryOperationRequest.source_id == receipt.id,
                InventoryOperationRequest.request_category.in_(["purchase_receipt_inbound", "purchase_quality_inbound"]),
            )
        ).scalars().all()
        if receipt.requires_quality and receipt.quality_status != "approved":
            receipt.status = "pending_quality"
        elif inbound_requests and all(item.status == "completed" for item in inbound_requests):
            receipt.status = "completed"
        else:
            receipt.status = "pending_inbound"
        order = db.execute(
            select(PurchaseOrder)
            .options(joinedload(PurchaseOrder.items))
            .where(PurchaseOrder.id == receipt.purchase_order_id)
        ).unique().scalar_one_or_none()
        purchaser_id = order.purchaser_user_id if order else None
        if order:
            full = purchase_arrival_status(db, order.id)
            if full == "arrived":
                fully_received = all(Decimal(item.received_quantity or 0) >= Decimal(item.quantity or 0) for item in order.items)
                order.status = "fully_received" if fully_received else "partial_received"
        if purchaser_id:
            create_notification(
                db,
                title="采购入库已完成",
                content=f"采购到货单 {receipt.receipt_no} 已完成库存入库。{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}",
                module="purchase",
                recipient_user_ids=[purchaser_id],
                created_by_id=current_user.id,
                category="success",
            )
    elif category == "purchase_return_outbound":
        purchase_return = db.execute(select(PurchaseReturn).where(PurchaseReturn.id == source_id)).scalar_one_or_none()
        if purchase_return is None:
            return
        purchase_return.status = "refunded" if Decimal(purchase_return.refund_amount or 0) > 0 else "returned"
        order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == purchase_return.purchase_order_id)).scalar_one_or_none()
        recipient_user_ids = []
        if order and order.purchaser_user_id:
            recipient_user_ids.append(order.purchaser_user_id)
        create_notification(
            db,
            title="退货处理出库已完成",
            content=f"退货处理单 {purchase_return.return_no} 已完成仓库出库。{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}",
            module="purchase",
            recipient_user_ids=recipient_user_ids,
            created_by_id=current_user.id,
            category="success",
        )
    elif category == "sales_shipment":
        order = db.execute(select(SalesOrder).where(SalesOrder.id == source_id)).scalar_one_or_none()
        if order is None:
            return
        queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
        order.status = "shipped"
        order.shipped_at = now_local()
        if queue is not None:
            queue.shipment_status = "completed"
            queue.shipment_marked_by_id = current_user.id
            queue.shipment_marked_at = now_local()
            queue.placement_status = "hidden"
        create_notification(
            db,
            title="客户订单已发货",
            content=f"客户订单 {order.order_no} 已完成出库发货。{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}",
            module="sales",
            recipient_user_ids=[user_id for user_id in [order.sales_user_id] if user_id],
            created_by_id=current_user.id,
            category="success",
        )
    elif category == "sales_return_inbound":
        sales_return = db.execute(select(SalesReturn).where(SalesReturn.id == source_id)).scalar_one_or_none()
        if sales_return is None:
            return
        # 回库完成不能把已经登记退款的单据从 refunded 倒退回 approved。
        if sales_return.status != "refunded":
            sales_return.status = "approved"
        # 退货入库完成 → 扣减退货在途(实有已由入库作业增加)
        _release_sales_return_in_transit(db, request)
        create_notification(
            db,
            title="销售退货已回库",
            content=f"销售退货单 {sales_return.return_no} 已完成回库。{f' 库管调整原因：{request.adjust_reason}。' if request.adjust_reason else ''}",
            module="sales",
            recipient_user_ids=[sales_return.requested_by_id],
            created_by_id=current_user.id,
            category="success",
        )


def _release_sales_return_in_transit(db: Session, request: InventoryOperationRequest) -> None:
    """退货入库完成 → 按实际入库数量扣减退货在途。"""
    handled_items = request.handled_items_json or request.original_items_json or []
    for item in handled_items:
        product_id = item.get("product_id") if isinstance(item, dict) else None
        quantity = item.get("quantity") if isinstance(item, dict) else None
        if not product_id or quantity is None:
            continue
        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == request.warehouse_id,
                Inventory.product_id == product_id,
                Inventory.material_id.is_(None),
            )
        ).scalar_one_or_none()
        if inventory is None:
            continue
        inventory.in_transit = max(Decimal("0"), Decimal(inventory.in_transit or 0) - Decimal(quantity))


def _ensure_production_material_issue_approved(request: InventoryOperationRequest, db: Session) -> None:
    if request.request_category != 'production_material_issue':
        return
    if request.source_type == 'production_material_usage':
        usages = db.execute(
            select(ProductionMaterialUsage).where(ProductionMaterialUsage.id == request.source_id)
        ).scalars().all()
    elif request.source_type == 'production_task':
        usages = db.execute(
            select(ProductionMaterialUsage).where(ProductionMaterialUsage.production_task_id == request.source_id)
        ).scalars().all()
    else:
        usages = []
    if not usages or any(
        usage.inventory_approval_status != 'approved'
        or (
            usage.special_request_status != 'not_required'
            and usage.manager_approval_status != 'approved'
        )
        for usage in usages
    ):
        raise HTTPException(status_code=409, detail='请先完成领料审批，再执行原料出库')


def complete_inventory_operation_request(
    request_id: int,
    payload: InventoryOperationHandleRequest,
    current_user: User,
    db: Session,
):
    request = db.execute(select(InventoryOperationRequest).where(InventoryOperationRequest.id == request_id).with_for_update()).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="库存作业请求不存在")
    _ensure_production_material_issue_approved(request, db)
    normalize_inventory_operation_request_approval(request, db)
    if _requires_inventory_manager_approval(request.operation_type, request.request_category) and request.handled_by_id is None:
        raise HTTPException(status_code=409, detail="当前库存入库请求尚未完成负责人审批")
    if request.status not in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="当前库存作业已处理")
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少填写一条库存作业明细")

    original_items = request.original_items_json or []
    handled_items = [
        {
            "product_id": item.product_id,
            "sales_order_item_id": item.sales_order_item_id,
            "material_id": item.material_id,
            "packaging_config_id": item.packaging_config_id,
            "batch_no": item.batch_no,
            "quantity": float(item.quantity),
            "unit_cost": float(item.unit_cost or 0),
        }
        for item in payload.items
    ]
    if request.request_category in {"purchase_receipt_inbound", "purchase_quality_inbound"}:
        original_costs = [Decimal(str(item.get("unit_cost") or 0)) for item in original_items]
        handled_costs = [Decimal(str(item.get("unit_cost") or 0)) for item in handled_items]
        if original_costs != handled_costs:
            raise HTTPException(status_code=409, detail="采购入库成本必须与采购到货单一致；请在来源单据更正价格")
    changed = _normalized_inventory_items(handled_items) != _normalized_inventory_items(original_items)
    quantity_changed = _has_quantity_difference(original_items, handled_items)
    if changed and not (payload.adjust_reason or "").strip():
        raise HTTPException(status_code=409, detail="库存作业修改了系统预填明细，请填写调整原因")
    if quantity_changed and not (payload.quantity_difference_remark or "").strip():
        expected_label, actual_label = _operation_quantity_label(request.operation_type)
        raise HTTPException(status_code=409, detail=f"{expected_label}与{actual_label}不一致时必须填写差异备注")

    sign = Decimal("1") if request.operation_type == "inbound" else Decimal("-1")
    transaction_type = (
        "stock_transfer_in" if request.request_category == "stock_transfer_inbound" else
        "stock_transfer_out" if request.request_category == "stock_transfer_outbound" else
        "manual_inbound" if request.operation_type == "inbound" else "manual_outbound"
    )
    transfer_outbound_cost = None
    production_input_cost = None
    return_origin_cost = None
    return_origin_line_cost = None
    if request.source_type == "stock_transfer":
        from models import StockTransfer
        transfer = db.execute(select(StockTransfer).where(StockTransfer.id == request.source_id).with_for_update()).scalar_one_or_none()
        expected_status = "pending_outbound" if request.operation_type == "outbound" else "pending_inbound"
        if transfer is None or transfer.status != expected_status:
            raise HTTPException(status_code=409, detail="移库单状态已变化，请刷新库存作业")
        if len(payload.items) != 1 or any(
            getattr(payload.items[0], key) != getattr(transfer, key)
            for key in ("product_id", "material_id", "packaging_config_id")
        ):
            raise HTTPException(status_code=409, detail="移库作业不能更换货品或增加其他明细")
        if request.operation_type == "inbound" and Decimal(payload.items[0].quantity) != Decimal(transfer.quantity):
            raise HTTPException(status_code=409, detail="移库入库数量须与实际出库数量一致；有差异请先处理异常")
        if request.operation_type == "outbound":
            stock = db.execute(select(Inventory).where(
                Inventory.warehouse_id == request.warehouse_id,
                Inventory.product_id == transfer.product_id,
                Inventory.material_id == transfer.material_id,
                Inventory.packaging_config_id == transfer.packaging_config_id,
            ).with_for_update()).scalar_one_or_none()
            available = max(Decimal(stock.quantity or 0) - Decimal(stock.reserved_quantity or 0), Decimal("0")) if stock else Decimal("0")
            if Decimal(payload.items[0].quantity) > available:
                raise HTTPException(status_code=409, detail=f"源仓库可用库存不足（可用 {available}）")
        else:
            outbound_request = db.execute(select(InventoryOperationRequest).where(
                InventoryOperationRequest.source_type == "stock_transfer",
                InventoryOperationRequest.source_id == transfer.id,
                InventoryOperationRequest.request_category == "stock_transfer_outbound",
                InventoryOperationRequest.status == "completed",
            )).scalar_one_or_none()
            transfer_outbound_cost = db.execute(select(InventoryTransaction).where(
                InventoryTransaction.related_type == "inventory_operation_request",
                InventoryTransaction.related_id == (outbound_request.id if outbound_request else -1),
                InventoryTransaction.transaction_type == "stock_transfer_out",
            )).scalar_one_or_none()
            if transfer_outbound_cost is None:
                raise HTTPException(status_code=409, detail="未找到源仓出库成本，无法确定移库入库成本")
    if request.request_category in {"production_finished_inbound", "production_quality_inbound"}:
        task = db.execute(select(ProductionTask).where(ProductionTask.id == request.source_id).with_for_update()).scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=409, detail="未找到生产任务，不能计算成品入库成本")
        prior_inbound_request_ids = db.execute(select(InventoryOperationRequest.id).where(
            InventoryOperationRequest.source_id == task.id,
            InventoryOperationRequest.source_type.in_(("production_task", "quality_inbound")),
            InventoryOperationRequest.request_category.in_(("production_finished_inbound", "production_quality_inbound")),
            InventoryOperationRequest.status == "completed",
        )).scalars().all()
        if prior_inbound_request_ids and db.execute(select(InventoryTransaction.id).where(
            InventoryTransaction.related_type == "inventory_operation_request",
            InventoryTransaction.related_id.in_(prior_inbound_request_ids),
            InventoryTransaction.quantity > 0,
        )).first():
            raise HTTPException(status_code=409, detail="该生产任务已完成成品入库，不能重复结转领料成本")
        usage_ids = db.execute(select(ProductionMaterialUsage.id).where(ProductionMaterialUsage.production_task_id == task.id)).scalars().all()
        issue_requests = db.execute(select(InventoryOperationRequest.id).where(
            InventoryOperationRequest.request_category == "production_material_issue",
            InventoryOperationRequest.status == "completed",
            ((InventoryOperationRequest.source_type == "production_task") & (InventoryOperationRequest.source_id == task.id))
            | ((InventoryOperationRequest.source_type == "production_material_usage") & (InventoryOperationRequest.source_id.in_(usage_ids or [-1]))),
        )).scalars().all()
        input_rows = db.execute(select(InventoryTransaction).where(
            InventoryTransaction.related_type == "inventory_operation_request",
            InventoryTransaction.related_id.in_(issue_requests or [-1]),
            InventoryTransaction.quantity < 0,
        )).scalars().all()
        if usage_ids and not input_rows:
            raise HTTPException(status_code=409, detail="尚未找到已执行的生产领料出库，无法确认成品入库成本")
        production_input_cost = (
            -sum((Decimal(row.total_cost or 0) for row in input_rows), Decimal("0"))
            + Decimal(task.direct_labor_cost or 0)
            + Decimal(task.manufacturing_overhead_cost or 0)
        )
        if production_input_cost < 0:
            raise HTTPException(status_code=409, detail="生产领料成本异常，请检查出库流水")
        planned_output = sum((Decimal(str(row.get("quantity") or 0)) for row in original_items), Decimal("0"))
        if planned_output <= 0:
            raise HTTPException(status_code=409, detail="生产入库申请数量无效")
        production_cost_status = "unpriced" if production_input_cost <= 0 or any(row.cost_status == "unpriced" for row in input_rows) else "estimated"
    if request.request_category == "sales_return_inbound" and request.source_type == "sales_return":
        sales_return = db.get(SalesReturn, request.source_id)
        if sales_return is None:
            raise HTTPException(status_code=409, detail="销售退货来源不存在")
        sold_rows = db.execute(select(InventoryTransaction).where(
            InventoryTransaction.related_type == "sales_order",
            InventoryTransaction.related_id == sales_return.sales_order_id,
            InventoryTransaction.transaction_type == "sales_outbound",
        )).scalars().all()
        approved_return_items = db.execute(select(SalesReturnItem).where(
            SalesReturnItem.sales_return_id == sales_return.id,
        )).scalars().all()
        approved_line_product_ids = {
            item.sales_order_item_id: next(
                (row.product_id for row in sold_rows if row.sales_order_item_id == item.sales_order_item_id),
                None,
            )
            for item in approved_return_items
        }
        for item in payload.items:
            if item.sales_order_item_id is not None and approved_line_product_ids.get(item.sales_order_item_id) != item.product_id:
                raise HTTPException(status_code=409, detail="退货明细不属于当前已审批退货单，或商品与原销售明细不一致")
        return_origin_cost = {}
        return_origin_line_cost = {}
        for row in sold_rows:
            entry = return_origin_cost.setdefault(row.product_id, {"quantity": Decimal("0"), "cost": Decimal("0"), "status": "actual"})
            entry["quantity"] += -Decimal(row.quantity)
            entry["cost"] += -Decimal(row.total_cost)
            if row.cost_status == "unpriced" or (row.cost_status == "estimated" and entry["status"] != "unpriced"):
                entry["status"] = row.cost_status
            if row.sales_order_item_id is not None:
                line_entry = return_origin_line_cost.setdefault(row.sales_order_item_id, {"quantity": Decimal("0"), "cost": Decimal("0"), "status": "actual"})
                line_entry["quantity"] += -Decimal(row.quantity)
                line_entry["cost"] += -Decimal(row.total_cost)
                if row.cost_status == "unpriced" or (row.cost_status == "estimated" and line_entry["status"] != "unpriced"):
                    line_entry["status"] = row.cost_status
    for index, item in enumerate(payload.items):
        try:
            return_cost = (
                (return_origin_line_cost or {}).get(item.sales_order_item_id)
                or (return_origin_cost or {}).get(item.product_id)
            ) if return_origin_cost is not None else None
            if return_origin_cost is not None and (return_cost is None or return_cost["quantity"] <= 0):
                raise HTTPException(status_code=409, detail="找不到原销售发货成本，退货不能按当前库存均价入库")
            production_total = (
                (production_input_cost * Decimal(item.quantity) / planned_output).quantize(Decimal("0.0001"))
                if production_input_cost is not None else None
            )
            return_total = (
                (return_cost["cost"] * Decimal(item.quantity) / return_cost["quantity"]).quantize(Decimal("0.0001"))
                if return_cost is not None else None
            )
            _, transaction = apply_inventory_change(
                db,
                warehouse_id=request.warehouse_id,
                product_id=item.product_id,
                material_id=item.material_id,
                packaging_config_id=item.packaging_config_id,
                quantity=Decimal(item.quantity) * sign,
                unit_cost=Decimal(transfer_outbound_cost.unit_cost if transfer_outbound_cost else item.unit_cost or 0),
                total_cost=-Decimal(transfer_outbound_cost.total_cost) if transfer_outbound_cost else production_total if production_total is not None else return_total,
                cost_status=(
                    transfer_outbound_cost.cost_status if transfer_outbound_cost else
                    production_cost_status if production_input_cost is not None else
                    return_cost["status"] if return_cost is not None else
                    "estimated" if request.request_category == "manual_inbound_request" and Decimal(item.unit_cost or 0) > 0 else
                    None
                ),
                batch_no=item.batch_no,
                transaction_type=transaction_type,
                related_type="inventory_operation_request",
                related_id=request.id,
                sales_order_item_id=item.sales_order_item_id if return_cost is not None else None,
                replace_opening_estimate=request.request_category in {"purchase_receipt_inbound", "purchase_quality_inbound"},
                created_by_id=current_user.id,
            )
            handled_items[index]["unit_cost"] = float(transaction.unit_cost)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    request.status = "completed"
    request.handled_by_id = current_user.id
    request.completed_at = now_local()
    request.handled_remark = (payload.handled_remark or "").strip() or None
    request.adjust_reason = (payload.adjust_reason or "").strip() or None
    request.quantity_difference_remark = (payload.quantity_difference_remark or "").strip() or None
    if quantity_changed:
        request.difference_notified_at = now_local()
    request.handled_items_json = handled_items
    complete_todos(db, business_type="inventory_operation_request", business_id=request.id)

    _sync_inventory_request_source(db, request=request, current_user=current_user)

    notice_recipient_ids = [request.requested_by_id] if request.requested_by_id else []
    notice_title = "库存作业已完成"
    notice_content = None
    if request.request_category == "sales_return_inbound" and request.source_type == "sales_return":
        # 局部导入避免 sales_return_workflow -> workflow 的模块加载环。
        from services.sales_return_workflow import sales_return_notice_user_ids
        sales_return = db.get(SalesReturn, request.source_id)
        if sales_return is not None:
            notice_recipient_ids = sales_return_notice_user_ids(db, sales_return)
            notice_title = "销售退换货已完成回库"
            notice_content = f"退换货单 {sales_return.return_no} 已完成库存入库作业 {request.request_no}。"
    if notice_recipient_ids:
        difference_text = (
            f" 差异备注：{request.quantity_difference_remark}"
            if request.quantity_difference_remark
            else ""
        )
        create_notification(
            db,
            title=notice_title,
            content=(notice_content or f"库存作业 {request.request_no} 已完成，当前状态：{status_label(request.status)}。") + difference_text,
            module=request.source_module,
            recipient_user_ids=notice_recipient_ids,
            created_by_id=current_user.id,
            category="warning" if request.quantity_difference_remark else "success",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="inventory",
        action="complete_inventory_operation",
        content=f"完成库存作业 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "request_category": request.request_category,
            "status": request.status,
            "changed": changed,
            "quantity_changed": quantity_changed,
            "adjust_reason": request.adjust_reason,
            "quantity_difference_remark": request.quantity_difference_remark,
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def _build_flow_tooltip(step: dict, next_step: dict | None) -> str:
    status = step.get("status")
    actor = step.get("actor") or "相关责任人"
    action = step.get("action") or "处理当前步骤"

    if status == "done":
        current_line = step.get("done_text") or f"本步骤已由{actor}{action}。"
    elif status == "current":
        current_line = step.get("current_text") or f"当前正在由{actor}{action}。"
    else:
        current_line = step.get("pending_text") or f"到达本步骤后，将由{actor}{action}。"

    if next_step is None:
        next_line = "下一步：完成本步骤后，当前流程闭环。"
    else:
        next_actor = next_step.get("actor") or "相关责任人"
        next_action = next_step.get("action") or "继续处理后续步骤"
        next_line = f"下一步需要由{next_actor}{next_action}。"
    return f"{current_line}\n{next_line}"


def _finalize_flow_steps(steps: list[dict]) -> list[dict]:
    first_pending = next((index for index, step in enumerate(steps) if not step["done"]), None)
    for index, step in enumerate(steps):
        step["status"] = "done" if step["done"] else "current" if first_pending == index else "pending"
    for index, step in enumerate(steps):
        next_step = steps[index + 1] if index + 1 < len(steps) else None
        step["tooltip"] = _build_flow_tooltip(step, next_step)
    return steps


def purchase_flow_steps(order: PurchaseOrder, *, arrival_status: str, settlement_status: str) -> list[dict]:
    steps = [
        {
            "key": "created",
            "label": "采购建单",
            "done": True,
            "actor": "采购人员",
            "action": "创建采购订单并维护供应商、采购明细和付款方式",
        },
        {
            "key": "approved",
            "label": "采购审批",
            "done": order.status not in {"pending_approval", "rejected", "draft"},
            "actor": "经理或有审批权限人员",
            "action": "完成采购审批并确认订单进入执行阶段",
        },
        {
            "key": "arrival",
            "label": "到货/质检",
            "done": arrival_status == "arrived"},
        {
            "key": "inbound",
            "label": "仓库入库",
            "done": order.status == "fully_received",
            "actor": "库管人员",
            "action": "根据到货或质检结果执行入库",
        },
        {
            "key": "payment",
            "label": "付款结算",
            "done": settlement_status == "settled",
            "actor": "采购人员、经理与财务相关人员",
            "action": "提交付款申请并完成出账结算",
        },
        {
            "key": "completed",
            "label": "采购闭环",
            "done": order.status == "fully_received" and settlement_status == "settled",
            "actor": "采购人员",
            "action": "确认采购订单已全部到货且已结算完成",
        },
    ]
    steps[2]["actor"] = "采购人员、库管人员或质检人员"
    steps[2]["action"] = "登记到货、处理质检并确认订单到货状态"
    return _finalize_flow_steps(steps)


def sales_flow_steps(order: SalesOrder, *, settlement_status: str) -> list[dict]:
    steps = [
        {
            "key": "created",
            "label": "客户订单创建",
            "done": True,
            "actor": "销售人员",
            "action": "创建客户订单并录入客户、商品和收货信息",
        },
        {
            "key": "payment_check",
            "label": "到账确认",
            "done": order.status not in {"pending_confirmation", "rejected"},
            "actor": "销售人员与董事长/财务相关人员",
            "action": "提交并确认首笔到账或放行条件",
        },
        {
            "key": "prepare",
            "label": "置货备货",
            "done": order.status in {"shipped", "completed"},
            "actor": "库管人员",
            "action": "根据客户订单执行置货与备货",
        },
        {
            "key": "shipment",
            "label": "仓储出库",
            "done": order.status in {"shipped", "completed"},
            "actor": "库管人员",
            "action": "执行出库发货并登记本次发货数量",
        },
        {
            "key": "receipt",
            "label": "收款结算",
            "done": settlement_status == "settled",
            "actor": "销售人员与董事长/财务相关人员",
            "action": "处理尾款、到货款或分期回款确认",
        },
        {
            "key": "completed",
            "label": "客户订单闭环",
            "done": order.status == "completed",
            "actor": "销售人员",
            "action": "确认客户订单已发货且已收款完成",
        },
    ]
    return _finalize_flow_steps(steps)


def production_flow_steps(task: ProductionTask) -> list[dict]:
    steps = [
        {
            "key": "planned",
            "label": "任务创建",
            "done": True,
            "actor": "生产人员",
            "action": "创建生产任务并确认产品、数量和原料需求",
        },
        {
            "key": "material_apply",
            "label": "领料申请",
            "done": task.status not in {"pending_material"},
            "actor": "生产人员",
            "action": "提交领料申请并等待原料库处理",
        },
        {
            "key": "material",
            "label": "原料出库",
            "done": task.status not in {"pending_material", "pending_material_approval", "pending_special_approval"},
            "actor": "原料库库管",
            "action": "审核领料申请并执行原料出库",
        },
        {
            "key": "produce",
            "label": "生产完工",
            "done": task.status in {"waiting_quality", "ready_for_inbound", "completed"},
            "actor": "生产人员",
            "action": "录入产出、报废并提交入库申请",
        },
        {
            "key": "quality",
            "label": "质检处理",
            "done": task.status in {"ready_for_inbound", "completed"} or not bool(task.requires_quality),
            "actor": "质检人员",
            "action": "完成质检并决定是否准许入库",
        },
        {
            "key": "inbound",
            "label": "成品入库",
            "done": task.status == "completed",
            "actor": "库管人员",
            "action": "核对成品信息后执行入库",
        },
    ]
    steps = _finalize_flow_steps(steps)
    if not bool(task.requires_quality):
        for index, step in enumerate(steps):
            if step["key"] == "quality":
                next_step = steps[index + 1] if index + 1 < len(steps) else None
                next_line = "下一步：完成本步骤后，当前流程闭环。"
                if next_step is not None:
                    next_line = f"下一步需要由{next_step.get('actor') or '相关责任人'}{next_step.get('action') or '继续处理后续步骤'}。"
                step["tooltip"] = f"当前任务无需质检，该步骤自动跳过。\n{next_line}"
                break
    return steps
