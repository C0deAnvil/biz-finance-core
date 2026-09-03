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




from services.purchase_service._common import _build_order_items, _complete_purchase_replenishment_source, _ensure_order_owner, _filter_purchase_items_for_source, _load_order, _load_purchase_replenishment_source, _manager_user_ids, _order_has_inventory_flow, _reopen_purchase_replenishment_source, _resolve_purchase_operator_user_id, _validate_purchase_order_replenishment_items, _validate_supplier_catalog

def create_purchase_order(payload: PurchaseOrderCreate, current_user: User, db: Session):
    supplier = db.execute(select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.status == "enabled")).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在或已停用")
    if not payload.items:
        raise HTTPException(status_code=400, detail="采购订单至少需要一条明细")
    _validate_supplier_catalog(db, payload.supplier_id, payload.items)
    replenishment_sources = getattr(payload, "replenishment_sources", None)
    multi_source = bool(replenishment_sources)
    if multi_source:
        if len(replenishment_sources) < 2:
            raise HTTPException(status_code=409, detail="合并建单至少需要选择两条补货需求")
        # 先逐个加载来源(校验存在性 / 防重复),再按来源拆分明细校验
        loaded_sources = []
        for src in replenishment_sources:
            source = _load_purchase_replenishment_source(
                db,
                source_type=src.source_type,
                source_id=src.source_id,
                items=payload.items,
                validate_items=False,
            )
            if source is None:
                raise HTTPException(status_code=409, detail="补货来源无效")
            loaded_sources.append(source)
        for source in loaded_sources:
            source_items = _filter_purchase_items_for_source(source, payload.items)
            if not source_items:
                raise HTTPException(status_code=409, detail="补货来源对应的商品未包含在采购明细中")
            _validate_purchase_order_replenishment_items(
                db,
                source_type=source["source_type"],
                source_id=source["source_id"],
                items=source_items,
            )
        replenishment_source = loaded_sources[0]
    else:
        replenishment_source = _load_purchase_replenishment_source(
            db,
            source_type=payload.replenishment_source_type,
            source_id=payload.replenishment_source_id,
            items=payload.items,
        )

    order = PurchaseOrder(
        order_no=next_business_no(db, "CG"),
        supplier_id=payload.supplier_id,
        purchaser_user_id=current_user.id,
        operator_user_id=_resolve_purchase_operator_user_id(db, payload.operator_user_id, current_user),
        payment_type=payment_type_label(payload.payment_type, module="purchase"),
        status="draft",
        replenishment_source_type=replenishment_source["source_type"] if replenishment_source else payload.replenishment_source_type,
        replenishment_source_id=replenishment_source["source_id"] if replenishment_source else payload.replenishment_source_id,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if replenishment_source is not None:
            raise HTTPException(status_code=409, detail="该补货来源已被其他采购订单处理，请刷新后查看") from exc
        raise

    total_amount, threshold_triggered = _build_order_items(db, order, payload.items)
    order.total_amount = total_amount
    order.threshold_triggered = threshold_triggered
    order.status = "pending_approval" if threshold_triggered else "purchasing"

    manager_user_ids = _manager_user_ids(db)
    if threshold_triggered:
        for user_id in manager_user_ids:
            create_todo(
                db,
                title=f"待审批采购订单 {order.order_no}",
                description="采购订单总金额达到审批阈值，请审批。",
                module="purchase",
                assigned_user_id=user_id,
                business_type="purchase_order",
                business_id=order.id,
                priority="high",
            )

    create_notification(
        db,
        title="采购单已创建",
        content=f"采购单 {order.order_no} 已创建，当前状态：{order.status}。",
        module="purchase",
        recipient_user_ids=[current_user.id, *manager_user_ids],
        created_by_id=current_user.id,
    )
    if multi_source:
        source_ids_meta = [f"{s['source_type']}:{s['source_id']}" for s in loaded_sources]
        for source in loaded_sources:
            _complete_purchase_replenishment_source(
                db,
                source=source,
                order=order,
                current_user=current_user,
            )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="purchase",
            action="create_purchase_order",
            content=f"合并创建采购订单 {order.order_no}，来源：{'、'.join(source_ids_meta)}",
            after_data={
                "status": order.status,
                "threshold_triggered": threshold_triggered,
                "replenishment_sources": source_ids_meta,
                "item_count": len(payload.items),
            },
        )
    elif replenishment_source is not None:
        _complete_purchase_replenishment_source(
            db,
            source=replenishment_source,
            order=order,
            current_user=current_user,
        )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="purchase",
            action="create_purchase_order",
            content=f"创建采购订单 {order.order_no}",
            after_data={
                "status": order.status,
                "threshold_triggered": threshold_triggered,
                "replenishment_source_type": order.replenishment_source_type,
                "replenishment_source_id": order.replenishment_source_id,
            },
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if replenishment_source is not None:
            raise HTTPException(status_code=409, detail="该补货来源已被其他采购订单处理，请刷新后查看") from exc
        raise
    return api_response(data={"id": order.id, "order_no": order.order_no})


def update_purchase_order(order_id: int, payload: PurchaseOrderUpdate, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if _order_has_inventory_flow(order):
        raise HTTPException(status_code=409, detail="采购订单已产生到货或退货记录，不能再修改")
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="已作废采购单不能修改")

    supplier = db.execute(select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.status == "enabled")).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在或已停用")
    if not payload.items:
        raise HTTPException(status_code=400, detail="采购订单至少需要一条明细")
    _validate_supplier_catalog(db, payload.supplier_id, payload.items)
    if order.replenishment_source_type and order.replenishment_source_id:
        _validate_purchase_order_replenishment_items(
            db,
            source_type=order.replenishment_source_type,
            source_id=order.replenishment_source_id,
            items=payload.items,
        )

    before_data = {
        "supplier_id": order.supplier_id,
        "status": order.status,
        "total_amount": float(order.total_amount or 0),
        "threshold_triggered": order.threshold_triggered,
        "item_count": len(order.items),
    }

    order.supplier_id = payload.supplier_id
    order.payment_type = payment_type_label(payload.payment_type, module="purchase")
    order.operator_user_id = _resolve_purchase_operator_user_id(db, payload.operator_user_id or order.operator_user_id, current_user)
    order.approved_by_id = None if order.status == "rejected" else order.approved_by_id
    order.approved_at = None if order.status == "rejected" else order.approved_at

    for item in list(order.items):
        db.delete(item)
    db.flush()

    total_amount, threshold_triggered = _build_order_items(db, order, payload.items)
    order.total_amount = total_amount
    order.threshold_triggered = threshold_triggered
    order.status = "pending_approval" if threshold_triggered else "purchasing"

    if order.status != "pending_approval":
        complete_todos(db, business_type="purchase_order", business_id=order.id)
    else:
        manager_user_ids = _manager_user_ids(db)
        complete_todos(db, business_type="purchase_order", business_id=order.id)
        for user_id in manager_user_ids:
            create_todo(
                db,
                title=f"待审批采购订单 {order.order_no}",
                description="采购数量超过阈值，请及时审批。",
                module="purchase",
                assigned_user_id=user_id,
                business_type="purchase_order",
                business_id=order.id,
                priority="high",
            )

    create_notification(
        db,
        title="采购单已更新",
        content=f"采购单 {order.order_no} 已更新，当前状态：{order.status}。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *_manager_user_ids(db)])),
        created_by_id=current_user.id,
        is_popup=False,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="update_purchase_order",
        content=f"更新采购订单 {order.order_no}",
        before_data=before_data,
        after_data={
            "supplier_id": order.supplier_id,
            "status": order.status,
            "total_amount": float(order.total_amount or 0),
            "threshold_triggered": order.threshold_triggered,
            "item_count": len(payload.items),
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no, "status": order.status})


def cancel_purchase_order(order_id: int, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if _order_has_inventory_flow(order):
        raise HTTPException(status_code=409, detail="采购订单已产生到货或退货记录，不能作废")
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="采购订单已作废")

    previous_status = order.status
    order.status = "cancelled"
    complete_todos(db, business_type="purchase_order", business_id=order.id)
    _reopen_purchase_replenishment_source(db, order=order, current_user=current_user)
    create_notification(
        db,
        title="采购单已作废",
        content=f"采购单 {order.order_no} 已作废。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *_manager_user_ids(db)])),
        created_by_id=current_user.id,
        category="warning",
        is_popup=False,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="cancel_purchase_order",
        content=f"作废采购订单 {order.order_no}",
        before_data={"status": previous_status},
        after_data={"status": order.status},
    )
    db.commit()
    return api_response(msg="采购订单已作废")


def approve_purchase_order(order_id: int, payload: PurchaseApprovalRequest, current_user: User, db: Session):
    order = db.execute(select(PurchaseOrder).where(PurchaseOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if order.status != "pending_approval":
        raise HTTPException(status_code=409, detail="当前采购订单无需审批")

    db.add(
        PurchaseOrderApproval(
            purchase_order_id=order.id,
            approver_user_id=current_user.id,
            decision=payload.decision,
            note=payload.note,
        )
    )
    order.approved_by_id = current_user.id
    order.approved_at = now_local()
    order.status = "pending_purchase_confirm" if payload.decision == "approved" else "rejected"
    complete_todos(db, business_type="purchase_order", business_id=order.id)
    if payload.decision == "approved" and order.purchaser_user_id:
        create_todo(
            db,
            title=f"待确认采购订单 {order.order_no}",
            description=f"采购订单 {order.order_no} 已审批通过，请确认后开始采购。",
            module="purchase",
            assigned_user_id=order.purchaser_user_id,
            business_type="purchase_order_confirm",
            business_id=order.id,
            priority="high",
        )
    create_notification(
        db,
        title="采购订单审批通过" if payload.decision == "approved" else "采购订单已驳回",
        content=(
            f"采购订单 {order.order_no} 已审批通过，可继续执行到货与结算。"
            if payload.decision == "approved"
            else f"采购订单 {order.order_no} 审批未通过，请根据审批意见调整后重新提交。"
        ),
        module="purchase",
        recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
        created_by_id=current_user.id,
        category="success" if payload.decision == "approved" else "warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="approve_purchase_order",
        content=f"审批采购订单 {order.order_no}",
        after_data={"status": order.status, "decision": payload.decision},
    )
    db.commit()
    return api_response(msg="采购订单审批完成")


def confirm_purchase_order(order_id: int, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if order.status != "pending_purchase_confirm":
        raise HTTPException(status_code=409, detail="当前采购订单不需要采购确认")
    order.status = "purchasing"
    complete_todos(db, business_type="purchase_order_confirm", business_id=order.id, assigned_user_id=current_user.id)
    create_notification(
        db,
        title="采购订单已确认",
        content=f"采购订单 {order.order_no} 已由采购员确认，状态转为采购中。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *_manager_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="confirm_purchase_order",
        content=f"采购员确认采购订单 {order.order_no}",
        after_data={"status": order.status},
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status})


