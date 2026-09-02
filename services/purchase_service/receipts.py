from __future__ import annotations
"""采购域服务。"""


import json
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from models import (
    Attachment,
    AppSetting,
    FulfillmentDemand,
    Inventory,
    Material,
    PayableRecord,
    Product,
    ProductionMaterialUsage,
    PurchaseOrder,
    PurchaseOrderApproval,
    PurchaseOrderItem,
    PurchasePaymentRequest,
    PurchaseReceipt,
    PurchaseReceiptItem,
    PurchaseReturn,
    Role,
    SupplierCatalogItem,
    Supplier,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.purchases import (
    PurchasePaymentInvoiceUpdate,
    PurchaseApprovalRequest,
    PurchaseFundApprovalRequest,
    PurchaseFundRequestCreate,
    PurchaseMergedPaymentRequestCreate,
    PurchaseOrderCreate,
    PurchasePaymentApprovalRequest,
    PurchasePaymentCompleteRequest,
    PurchasePaymentRequestCreate,
    PurchaseOrderUpdate,
    PurchaseReceiptCreate,
    PurchaseReturnCreate,
)
from schemas.finance import FinanceMergedPurchasePaymentRequestRegisterRequest
from services.core import apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, next_business_no, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label, status_label
from services.inventory_dimensions import sync_inventory_dimensions
from services.quality_workflow import create_purchase_quality_tasks, create_return_reinspection_quality_task
from services.time_utils import now_local
from services.workflow import create_inventory_operation_request, purchase_arrival_status, purchase_flow_steps


REPLENISHMENT_SOURCE_TYPES = {
    "raw_material_replenishment",
    "low_stock_replenishment",
    "low_stock_product_material_replenishment",
    "fulfillment_demand",
    "sales_order_material_replenishment",
}




from services.purchase_service._common import _refresh_purchase_order_status, _refresh_purchase_payment_request_arrival_status, _role_user_ids

def create_receipt(payload: PurchaseReceiptCreate, current_user: User, db: Session):
    order = db.execute(select(PurchaseOrder).options(joinedload(PurchaseOrder.items)).where(PurchaseOrder.id == payload.purchase_order_id)).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _refresh_purchase_order_status(order)
    if order.status == "fully_received" or purchase_arrival_status(db, order.id) == "arrived":
        raise HTTPException(status_code=409, detail="当前采购订单已全部到货，不能重复登记到货")
    if order.status not in {"purchasing", "partial_received", "fully_received"}:
        raise HTTPException(status_code=409, detail="当前采购订单不可到货入库")
    if not payload.items:
        raise HTTPException(status_code=400, detail="采购到货至少需要一条明细")
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.warehouse_id)).scalar_one_or_none()
    if warehouse is None:
        raise HTTPException(status_code=404, detail="入库仓库不存在")
    force_quality = (
        "inspection" in (warehouse.warehouse_type or "").lower()
        or "inspection" in (warehouse.code or "").lower()
        or "待检" in (warehouse.name or "")
    )
    requires_quality = bool(payload.requires_quality or force_quality)

    receipt = PurchaseReceipt(
        receipt_no=next_business_no(db, "RK"),
        purchase_order_id=order.id,
        warehouse_id=payload.warehouse_id,
        received_by_id=current_user.id,
        requires_quality=requires_quality,
        quality_status="pending" if requires_quality else "not_required",
        status="pending_quality" if requires_quality else "pending_inbound",
        remark=payload.remark,
        preliminary_acceptance_note=payload.preliminary_acceptance_note,
        recorded_at=payload.recorded_at or now_local(),
    )
    db.add(receipt)
    db.flush()
    request_items: list[dict] = []

    for item_payload in payload.items:
        order_item = db.execute(select(PurchaseOrderItem).where(PurchaseOrderItem.id == item_payload.purchase_order_item_id)).scalar_one_or_none()
        if order_item is None or order_item.purchase_order_id != order.id:
            raise HTTPException(status_code=404, detail="采购明细不存在")
        remaining_quantity = Decimal(order_item.quantity or 0) - Decimal(order_item.received_quantity or 0)
        if remaining_quantity <= 0:
            raise HTTPException(status_code=409, detail="存在已全部到货的采购明细，不能重复登记")
        if Decimal(item_payload.received_quantity) > remaining_quantity:
            raise HTTPException(status_code=409, detail="到货数量不能大于该明细剩余未到货数量")
        expected_quantity = remaining_quantity
        if Decimal(item_payload.received_quantity) != expected_quantity and not (item_payload.difference_remark or "").strip():
            raise HTTPException(status_code=409, detail="应到数量与实到数量不一致时必须填写差异备注")
        order_item.received_quantity = Decimal(order_item.received_quantity or 0) + Decimal(item_payload.received_quantity)
        db.add(
            PurchaseReceiptItem(
                purchase_receipt_id=receipt.id,
                purchase_order_item_id=order_item.id,
                expected_quantity=expected_quantity,
                received_quantity=item_payload.received_quantity,
                batch_no=item_payload.batch_no,
                unit_cost=item_payload.unit_cost,
                difference_remark=item_payload.difference_remark,
            )
        )

        if not requires_quality:
            request_items.append(
                {
                    "product_id": order_item.product_id,
                    "material_id": order_item.material_id,
                    "batch_no": item_payload.batch_no,
                    "quantity": float(item_payload.received_quantity or 0),
                    "unit_cost": float(item_payload.unit_cost or 0),
                }
            )

    db.flush()
    _refresh_purchase_order_status(order)
    if all(Decimal(item.received_quantity or 0) >= Decimal(item.quantity or 0) for item in order.items):
        order.status = "fully_received"
    _refresh_purchase_payment_request_arrival_status(db, order.id)

    quality_task_count = 0
    if requires_quality:
        quality_tasks = create_purchase_quality_tasks(db, order=order, receipt=receipt, submitted_by_id=current_user.id)
        quality_task_count = len(quality_tasks)
        quality_user_ids = resolve_workflow_recipients(
            db,
            "quality",
            "manager",
            "chairman",
            workflow="purchase_quality_task",
            business_type="purchase_receipt",
            business_id=receipt.id,
        )
        for task in quality_tasks:
            for user_id in quality_user_ids:
                create_todo(
                    db,
                    title=f"待处理采购质检任务 {task.quality_no}",
                    description=f"采购到货单 {receipt.receipt_no} 已到货，请执行来料质检。",
                    module="quality",
                    assigned_user_id=user_id,
                    business_type="quality_task",
                    business_id=task.id,
                    priority="high",
                )
        if quality_user_ids:
            create_notification(
                db,
                title="采购到货触发质检",
                content=f"采购到货单 {receipt.receipt_no} 已创建 {quality_task_count} 条来料质检任务。",
                module="quality",
                recipient_user_ids=quality_user_ids,
                created_by_id=current_user.id,
                category="warning",
            )
    else:
        inventory_user_ids = _role_user_ids(db, "inventory")
        if request_items:
            create_inventory_operation_request(
                db,
                operation_type="inbound",
                request_category="purchase_receipt_inbound",
                source_module="purchase",
                source_type="purchase_receipt",
                source_id=receipt.id,
                source_no=receipt.receipt_no,
                warehouse_id=payload.warehouse_id,
                requested_by_id=current_user.id,
                items=request_items,
                remark=payload.remark,
                recipient_user_ids=inventory_user_ids,
                title=f"待执行采购入库 {receipt.receipt_no}",
                description=f"采购到货单 {receipt.receipt_no} 已登记，无需质检，请库管执行入库。",
            )
        create_notification(
            db,
            title="采购到货已登记",
            content=f"采购到货单 {receipt.receipt_no} 已登记，等待库管入库。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([current_user.id, order.purchaser_user_id, *inventory_user_ids])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="create_purchase_receipt",
        content=f"创建采购到货单 {receipt.receipt_no}",
        after_data={
            "purchase_order_id": order.id,
            "receipt_no": receipt.receipt_no,
            "requires_quality": requires_quality,
            "quality_task_count": quality_task_count,
            "status": receipt.status,
        },
    )
    sync_inventory_dimensions(db)
    db.commit()
    return api_response(
        data={
            "id": receipt.id,
            "receipt_no": receipt.receipt_no,
            "requires_quality": requires_quality,
            "quality_task_count": quality_task_count,
        }
    )


def create_purchase_return(payload: PurchaseReturnCreate, current_user: User, db: Session):
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.warehouse_id)).scalar_one_or_none()
    if warehouse is None:
        raise HTTPException(status_code=404, detail="退货仓库不存在")

    order = db.execute(select(PurchaseOrder).options(joinedload(PurchaseOrder.items)).where(PurchaseOrder.id == payload.purchase_order_id)).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if order.status not in {"partial_received", "fully_received"}:
        raise HTTPException(status_code=409, detail="当前采购订单尚未入库，不能发起退货处理")
    if not payload.items:
        raise HTTPException(status_code=400, detail="退货处理至少需要一条明细")

    order_item_map = {item.id: item for item in order.items}
    purchase_return = PurchaseReturn(
        return_no=next_business_no(db, "PT"),
        purchase_order_id=payload.purchase_order_id,
        warehouse_id=payload.warehouse_id,
        returned_by_id=current_user.id,
        return_type=payload.return_type,
        reason=payload.reason,
        refund_amount=payload.refund_amount,
        requires_reinspection=payload.requires_reinspection,
        reinspection_status="pending" if payload.requires_reinspection else "not_required",
        status="processing",
    )
    db.add(purchase_return)
    db.flush()

    total_return_quantity = Decimal("0")
    request_items: list[dict] = []
    reinspection_names: list[str] = []
    for item_payload in payload.items:
        order_item = order_item_map.get(item_payload.purchase_order_item_id)
        if order_item is None:
            raise HTTPException(status_code=404, detail="采购退货明细不存在")

        available_quantity = Decimal(order_item.received_quantity or 0) - Decimal(order_item.returned_quantity or 0)
        if item_payload.returned_quantity > available_quantity:
            raise HTTPException(status_code=409, detail="退货数量超过可退数量")

        inventory = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == payload.warehouse_id,
                Inventory.product_id == order_item.product_id,
                Inventory.material_id == order_item.material_id,
            )
        ).scalar_one_or_none()
        unit_cost = Decimal(inventory.avg_unit_cost or 0) if inventory else Decimal("0")
        order_item.returned_quantity = Decimal(order_item.returned_quantity or 0) + item_payload.returned_quantity
        total_return_quantity += item_payload.returned_quantity
        if order_item.product and order_item.product.name:
            reinspection_names.append(order_item.product.name)
        elif order_item.material and order_item.material.name:
            reinspection_names.append(order_item.material.name)
        request_items.append(
            {
                "product_id": order_item.product_id,
                "material_id": order_item.material_id,
                "batch_no": None,
                "quantity": float(item_payload.returned_quantity or 0),
                "unit_cost": float(unit_cost),
            }
        )

    finance_user_ids = _role_user_ids(db, "finance", "manager", "chairman")
    inventory_user_ids = _role_user_ids(db, "inventory")
    return_type_text = "退货" if payload.return_type == "return" else "换货"
    if request_items:
        create_inventory_operation_request(
            db,
            operation_type="outbound",
            request_category="purchase_return_outbound",
            source_module="purchase",
            source_type="purchase_return",
            source_id=purchase_return.id,
            source_no=purchase_return.return_no,
            warehouse_id=payload.warehouse_id,
            requested_by_id=current_user.id,
            items=request_items,
            remark=payload.reason,
            recipient_user_ids=inventory_user_ids,
            title=f"待执行采购退货出库 {purchase_return.return_no}",
            description=f"采购{return_type_text}单 {purchase_return.return_no} 已登记，请库管执行出库。",
        )
    if payload.requires_reinspection:
        quality_task = create_return_reinspection_quality_task(
            db,
            business_type="purchase_return",
            business_id=purchase_return.id,
            business_no=purchase_return.return_no,
            source_label="采购退换货复检",
            item_label="、".join(dict.fromkeys(reinspection_names)) or "采购退换货物料",
            quantity=total_return_quantity,
            submitted_by_id=current_user.id,
        )
        quality_user_ids = _role_user_ids(db, "quality", "manager", "chairman")
        for user_id in quality_user_ids:
            create_todo(
                db,
                title=f"待复检采购{return_type_text} {purchase_return.return_no}",
                description=f"采购{return_type_text}单 {purchase_return.return_no} 标记需要复检，请处理质检任务 {quality_task.quality_no}。",
                module="quality",
                assigned_user_id=user_id,
                business_type="quality_task",
                business_id=quality_task.id,
                priority="high",
            )
        if quality_user_ids:
            create_notification(
                db,
                title="采购退换货已发起复检",
                content=f"采购{return_type_text}单 {purchase_return.return_no} 已生成复检任务 {quality_task.quality_no}。",
                module="quality",
                recipient_user_ids=quality_user_ids,
                created_by_id=current_user.id,
                category="warning",
            )
    for user_id in finance_user_ids:
        create_todo(
            db,
            title=f"待处理采购{return_type_text}退款 {purchase_return.return_no}",
            description=payload.reason,
            module="purchase",
            assigned_user_id=user_id,
            business_type="purchase_return",
            business_id=purchase_return.id,
        )
    create_notification(
        db,
        title="退货处理已提交",
        content=f"{return_type_text}单 {purchase_return.return_no} 已提交，待库管出库和财务处理退款。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([*finance_user_ids, *inventory_user_ids])),
        created_by_id=current_user.id,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="create_purchase_return",
        content=f"创建采购{return_type_text} {purchase_return.return_no}",
        after_data={
            "purchase_order_id": purchase_return.purchase_order_id,
            "warehouse_id": purchase_return.warehouse_id,
            "return_type": purchase_return.return_type,
            "refund_amount": float(purchase_return.refund_amount),
            "total_return_quantity": float(total_return_quantity),
        },
    )
    db.commit()
    return api_response(data={"id": purchase_return.id, "return_no": purchase_return.return_no})

