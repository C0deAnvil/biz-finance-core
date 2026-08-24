"""采购域服务。"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
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
from services.quality_workflow import create_purchase_quality_tasks, create_return_reinspection_quality_task
from services.time_utils import now_local
from services.workflow import create_inventory_operation_request, purchase_arrival_status, purchase_flow_steps


REPLENISHMENT_SOURCE_TYPES = {
    "raw_material_replenishment",
    "low_stock_replenishment",
    "fulfillment_demand",
}


def _role_user_ids(db: Session, *role_codes: str) -> list[int]:
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def _manager_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "manager", workflow="manager_workflow")


def _chairman_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "chairman", workflow="chairman_workflow")


def _finance_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "finance", workflow="finance_workflow")


def _resolve_purchase_operator_user_id(db: Session, operator_user_id: int | None, current_user: User) -> int:
    candidate_id = operator_user_id or current_user.id
    user = db.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(User.id == candidate_id, User.is_active.is_(True), Role.code == "purchase")
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=409, detail="经办人必须为启用状态的采购人员")
    return user.id


def _load_order(db: Session, order_id: int) -> PurchaseOrder | None:
    return (
        db.execute(
            select(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.supplier),
                joinedload(PurchaseOrder.operator_user),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            )
            .where(PurchaseOrder.id == order_id)
        )
        .unique()
        .scalar_one_or_none()
    )


def _ensure_order_owner(order: PurchaseOrder, current_user: User) -> None:
    if current_user.role.code == "purchase" and order.purchaser_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可操作本人创建的采购单")


def _order_has_inventory_flow(order: PurchaseOrder) -> bool:
    return any(Decimal(item.received_quantity or 0) > 0 or Decimal(item.returned_quantity or 0) > 0 for item in order.items)


def _build_order_items(db: Session, order: PurchaseOrder, payload_items) -> tuple[Decimal, bool]:
    total_amount = Decimal("0")
    quantity_threshold_triggered = False
    for item_payload in payload_items:
        if item_payload.product_id:
            product = db.execute(select(Product).where(Product.id == item_payload.product_id)).scalar_one_or_none()
            if product is None:
                raise HTTPException(status_code=404, detail="采购产品不存在")
            item_threshold = Decimal(product.purchase_threshold_qty or 0)
        elif item_payload.material_id:
            material = db.execute(select(Material).where(Material.id == item_payload.material_id)).scalar_one_or_none()
            if material is None:
                raise HTTPException(status_code=404, detail="采购原料不存在")
            item_threshold = Decimal(material.purchase_threshold_qty or 0)
        else:
            raise HTTPException(status_code=400, detail="采购明细必须选择产品或原料")

        quantity = Decimal(item_payload.quantity or 0)
        if item_threshold > 0 and quantity >= item_threshold:
            quantity_threshold_triggered = True

        line_total = Decimal(item_payload.quantity) * Decimal(item_payload.unit_price)
        total_amount += line_total
        packaging = None
        if item_payload.product_id:
            packaging = get_default_packaging_config(db, entity_type="product", entity_id=item_payload.product_id)
        elif item_payload.material_id:
            packaging = get_default_packaging_config(db, entity_type="material", entity_id=item_payload.material_id)

        db.add(
            PurchaseOrderItem(
                purchase_order_id=order.id,
                product_id=item_payload.product_id,
                material_id=item_payload.material_id,
                quantity=item_payload.quantity,
                unit_price=item_payload.unit_price,
                total_amount=line_total,
                packaging_config_id=packaging.id if packaging else None,
                kg_per_bag=packaging.kg_per_bag if packaging else None,
                bag_count=calculate_bag_count(item_payload.quantity, packaging.kg_per_bag) if packaging else None,
                display_specification=packaging_spec_text(packaging.kg_per_bag) if packaging else None,
            )
        )
    threshold_amount = _purchase_approval_amount_threshold(db)
    amount_threshold_triggered = threshold_amount > 0 and total_amount >= threshold_amount
    threshold_triggered = amount_threshold_triggered or quantity_threshold_triggered
    return total_amount, threshold_triggered


def _purchase_approval_amount_threshold(db: Session) -> Decimal:
    row = db.execute(select(AppSetting).where(AppSetting.setting_key == "purchase_approval_amount_threshold")).scalar_one_or_none()
    if row is None:
        row = AppSetting(
            setting_key="purchase_approval_amount_threshold",
            setting_value="50000",
            description="采购订单审批金额阈值，订单总金额达到或超过该值时需要经理审批",
        )
        db.add(row)
        db.flush()
    try:
        return Decimal(str(row.setting_value or "0"))
    except Exception:
        return Decimal("0")


def _refresh_purchase_order_status(order: PurchaseOrder, *, force_fully_received: bool = False) -> None:
    if not order.items:
        return
    fully_received = all(Decimal(item.received_quantity or 0) >= Decimal(item.quantity or 0) for item in order.items)
    partially_received = any(Decimal(item.received_quantity or 0) > 0 for item in order.items)
    if fully_received:
        order.status = "fully_received"
    elif partially_received:
        order.status = "partial_received"
    elif force_fully_received:
        order.status = "fully_received"


def _refresh_purchase_payment_request_arrival_status(db: Session, order_id: int) -> None:
    arrival_status = purchase_arrival_status(db, order_id)
    requests = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.purchase_order_id == order_id)
    ).scalars().all()
    for request in requests:
        request.arrival_status = arrival_status


def _validate_supplier_catalog(db: Session, supplier_id: int, payload_items) -> None:
    rows = db.execute(select(SupplierCatalogItem).where(SupplierCatalogItem.supplier_id == supplier_id)).scalars().all()
    if not rows:
        raise HTTPException(status_code=409, detail="当前供应商尚未维护可供货名录，不能创建采购单")
    allowed_product_ids = {row.product_id for row in rows if row.product_id}
    allowed_material_ids = {row.material_id for row in rows if row.material_id}
    for item_payload in payload_items:
        if item_payload.product_id and item_payload.product_id not in allowed_product_ids:
            raise HTTPException(status_code=409, detail="当前供应商名录中不包含所选产品")
        if item_payload.material_id and item_payload.material_id not in allowed_material_ids:
            raise HTTPException(status_code=409, detail="当前供应商名录中不包含所选原料")


def _latest_fund_record(db: Session, order_id: int) -> PayableRecord | None:
    return db.execute(
        select(PayableRecord)
        .where(PayableRecord.purchase_order_id == order_id)
        .order_by(PayableRecord.id.desc())
    ).scalars().first()


def _purchase_paid_amount(db: Session, order_id: int) -> Decimal:
    total = db.execute(
        select(PayableRecord).where(PayableRecord.purchase_order_id == order_id).order_by(PayableRecord.id.desc())
    ).scalars().all()
    amount = Decimal("0")
    for row in total:
        if row.status in {"paid", "partial_settled", "settled"}:
            amount += Decimal(row.order_amount or row.amount or 0)
    return amount


def _latest_payment_request(db: Session, order_id: int) -> PurchasePaymentRequest | None:
    return db.execute(
        select(PurchasePaymentRequest)
        .where(PurchasePaymentRequest.purchase_order_id == order_id)
        .order_by(PurchasePaymentRequest.id.desc())
    ).scalars().first()


def _validate_order_payment_requestable(order: PurchaseOrder, current_user: User, db: Session) -> None:
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"finance", "system_admin"}:
        _ensure_order_owner(order, current_user)
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail=f"采购订单 {order.order_no} 已作废，不能发起付款申请")
    latest_request = _latest_payment_request(db, order.id)
    if latest_request and latest_request.status in {"pending_manager_approval", "pending_chairman_approval", "pending_finance_payment", "pending_completion"}:
        raise HTTPException(status_code=409, detail=f"采购订单 {order.order_no} 存在未完成的付款申请")


def _remaining_order_amount(db: Session, order: PurchaseOrder) -> Decimal:
    return max(Decimal(order.total_amount or 0) - _purchase_paid_order_amount(db, order.id), Decimal("0"))


def _validate_payment_amounts(db: Session, order: PurchaseOrder, order_amount: Decimal, other_fee_amount: Decimal) -> None:
    if order_amount < 0 or other_fee_amount < 0:
        raise HTTPException(status_code=409, detail="订单金额和其他费用不能小于 0")
    remaining_amount = _remaining_order_amount(db, order)
    if order_amount <= 0 and other_fee_amount <= 0:
        raise HTTPException(status_code=409, detail="订单金额和其他费用不能同时为 0")
    if order_amount > remaining_amount:
        raise HTTPException(status_code=409, detail=f"采购订单 {order.order_no} 的订单付款金额不能超过剩余未结算金额")


def _validate_merged_orders_same_supplier(orders: list[PurchaseOrder], supplier_id: int | None = None, *, min_count: int = 2) -> int:
    if len(orders) < min_count:
        raise HTTPException(status_code=409, detail="请选择需要付款的采购订单")
    supplier_ids = {row.supplier_id for row in orders}
    if supplier_id is not None:
        supplier_ids.add(supplier_id)
    if len({item for item in supplier_ids if item is not None}) != 1:
        raise HTTPException(status_code=409, detail="仅支持同一供应商的采购订单合并付款")
    return orders[0].supplier_id


def _create_purchase_payment_request_row(
    db: Session,
    *,
    order: PurchaseOrder,
    current_user: User,
    requested_amount: Decimal,
    order_amount: Decimal,
    other_fee_amount: Decimal,
    payment_detail_remark: str | None,
    merge_no: str | None = None,
    preset_status: str = "pending_chairman_approval",
    approved_by_id: int | None = None,
    approved_at: datetime | None = None,
    manager_remark: str | None = None,
) -> PurchasePaymentRequest:
    request = PurchasePaymentRequest(
        request_no=next_business_no(db, "FK"),
        purchase_order_id=order.id,
        merge_no=merge_no,
        requested_by_id=current_user.id,
        requested_amount=requested_amount,
        order_amount=order_amount,
        other_fee_amount=other_fee_amount,
        actual_amount=requested_amount,
        arrival_status=purchase_arrival_status(db, order.id),
        status=preset_status,
        payment_detail_remark=payment_detail_remark,
        approved_by_id=approved_by_id,
        approved_at=approved_at,
        manager_remark=manager_remark,
    )
    db.add(request)
    db.flush()
    return request



def _purchase_paid_order_amount(db: Session, order_id: int) -> Decimal:
    rows = db.execute(
        select(PayableRecord).where(PayableRecord.purchase_order_id == order_id).order_by(PayableRecord.id.asc())
    ).scalars().all()
    total = Decimal("0")
    for row in rows:
        if row.status in {"paid", "partial_settled", "settled"}:
            total += Decimal(row.order_amount or 0)
    return total


def _purchase_order_business_completed(
    db: Session,
    order: PurchaseOrder,
    *,
    settlement_status: str,
) -> bool:
    if purchase_arrival_status(db, order.id) != "arrived" or settlement_status != "settled":
        return False
    latest_request = _latest_payment_request(db, order.id)
    if latest_request is None:
        return True
    return latest_request.status == "completed"


def _purchase_arrival_display_label(order: PurchaseOrder, arrival_status: str) -> str:
    if arrival_status == "arrived":
        return "已到货"
    if order.status == "fully_received":
        return "待质检/待入库"
    if order.status == "partial_received":
        return "部分到货"
    return "未到货"


def _purchase_contract_attachments(db: Session, order_id: int) -> list[dict]:
    rows = db.execute(
        select(Attachment)
        .where(Attachment.business_type == "purchase_order", Attachment.business_id == order_id)
        .order_by(Attachment.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": row.id,
            "attachment_type": row.attachment_type,
            "original_name": row.original_name,
            "relative_path": row.relative_path,
        }
        for row in rows
    ]


def _purchase_order_attachment_summary(db: Session, order_id: int) -> list[dict]:
    return _purchase_contract_attachments(db, order_id)


def _has_replenishment_item(items, *, product_id: int | None = None, material_id: int | None = None) -> bool:
    if product_id:
        return any(item.product_id == product_id for item in items)
    if material_id:
        return any(item.material_id == material_id for item in items)
    return False


def _parse_low_stock_replenishment_payload(todo: TodoItem) -> dict:
    description = todo.description or ""
    if "|replenishment:" not in description:
        return {}
    try:
        return json.loads(description.split("|replenishment:", 1)[1])
    except (TypeError, ValueError):
        return {}


def _validate_purchase_order_replenishment_items(
    db: Session,
    *,
    source_type: str,
    source_id: int,
    items,
) -> None:
    if source_type == "raw_material_replenishment":
        usage = db.execute(
            select(ProductionMaterialUsage).where(ProductionMaterialUsage.id == source_id)
        ).scalar_one_or_none()
        if usage is None:
            raise HTTPException(status_code=404, detail="原料补货需求不存在")
        if not _has_replenishment_item(items, material_id=usage.material_id):
            raise HTTPException(status_code=409, detail="采购订单必须包含补货需求对应的原料")
        return

    if source_type == "low_stock_replenishment":
        todos = db.execute(
            select(TodoItem)
            .where(
                TodoItem.business_type == source_type,
                TodoItem.business_id == source_id,
                TodoItem.module == "purchase",
            )
            .order_by(TodoItem.id.asc())
        ).scalars().all()
        source_payload = next(
            (
                payload
                for payload in (_parse_low_stock_replenishment_payload(todo) for todo in todos)
                if payload.get("product_id") or payload.get("material_id")
            ),
            {},
        )
        product_id = source_payload.get("product_id")
        material_id = source_payload.get("material_id")
        if not product_id and not material_id:
            raise HTTPException(status_code=409, detail="安全库存补货需求缺少产品或原料信息")
        if not _has_replenishment_item(items, product_id=product_id, material_id=material_id):
            raise HTTPException(status_code=409, detail="采购订单必须包含补货需求对应的产品或原料")
        return

    if source_type == "fulfillment_demand":
        demand = db.execute(
            select(FulfillmentDemand).where(FulfillmentDemand.id == source_id)
        ).scalar_one_or_none()
        if demand is None:
            raise HTTPException(status_code=404, detail="采购补货需求不存在")
        if not _has_replenishment_item(items, product_id=demand.product_id):
            raise HTTPException(status_code=409, detail="采购订单必须包含补货需求对应的产品")
        return

    raise HTTPException(status_code=409, detail="不支持的补货来源类型")


def _load_purchase_replenishment_source(
    db: Session,
    *,
    source_type: str | None,
    source_id: int | None,
    items,
    existing_order_id: int | None = None,
) -> dict | None:
    if bool(source_type) != bool(source_id):
        raise HTTPException(status_code=409, detail="补货来源类型和来源编号必须同时提交")
    if source_type is None:
        return None
    if source_type not in REPLENISHMENT_SOURCE_TYPES:
        raise HTTPException(status_code=409, detail="不支持的补货来源类型")

    existing_order = db.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.replenishment_source_type == source_type,
            PurchaseOrder.replenishment_source_id == source_id,
        )
    ).scalar_one_or_none()
    if existing_order is not None and existing_order.id != existing_order_id:
        raise HTTPException(
            status_code=409,
            detail=f"该补货来源已关联采购订单 {existing_order.order_no}，不能重复创建采购订单",
        )

    if source_type == "raw_material_replenishment":
        usage = db.execute(
            select(ProductionMaterialUsage)
            .options(joinedload(ProductionMaterialUsage.material), joinedload(ProductionMaterialUsage.production_task))
            .where(ProductionMaterialUsage.id == source_id)
        ).unique().scalar_one_or_none()
        if usage is None:
            raise HTTPException(status_code=404, detail="原料补货需求不存在")
        pending_todos = db.execute(
            select(TodoItem).where(
                TodoItem.business_type == source_type,
                TodoItem.business_id == source_id,
                TodoItem.status != "completed",
            )
        ).scalars().all()
        if not pending_todos:
            raise HTTPException(status_code=409, detail="原料补货需求已处理")
        _validate_purchase_order_replenishment_items(
            db,
            source_type=source_type,
            source_id=source_id,
            items=items,
        )
        return {
            "source_type": source_type,
            "source_id": source_id,
            "usage": usage,
            "pending_todos": pending_todos,
        }

    if source_type == "low_stock_replenishment":
        pending_todos = db.execute(
            select(TodoItem)
            .where(
                TodoItem.business_type == source_type,
                TodoItem.business_id == source_id,
                TodoItem.module == "purchase",
                TodoItem.status != "completed",
            )
            .order_by(TodoItem.id.asc())
        ).scalars().all()
        if not pending_todos:
            raise HTTPException(status_code=409, detail="安全库存补货需求已处理")
        source_payload = next(
            (
                payload
                for payload in (_parse_low_stock_replenishment_payload(todo) for todo in pending_todos)
                if payload.get("product_id") or payload.get("material_id")
            ),
            {},
        )
        _validate_purchase_order_replenishment_items(
            db,
            source_type=source_type,
            source_id=source_id,
            items=items,
        )
        return {
            "source_type": source_type,
            "source_id": source_id,
            "pending_todos": pending_todos,
            "payload": source_payload,
        }

    demand = db.execute(
        select(FulfillmentDemand)
        .options(joinedload(FulfillmentDemand.product), joinedload(FulfillmentDemand.sales_order))
        .where(FulfillmentDemand.id == source_id, FulfillmentDemand.fulfillment_mode == "trade")
    ).unique().scalar_one_or_none()
    if demand is None:
        raise HTTPException(status_code=404, detail="采购补货需求不存在")
    if demand.status != "pending":
        raise HTTPException(status_code=409, detail="当前采购补货需求已处理")
    _validate_purchase_order_replenishment_items(
        db,
        source_type=source_type,
        source_id=source_id,
        items=items,
    )
    return {
        "source_type": source_type,
        "source_id": source_id,
        "demand": demand,
    }


def _complete_purchase_replenishment_source(
    db: Session,
    *,
    source: dict,
    order: PurchaseOrder,
    current_user: User,
) -> None:
    source_type = source["source_type"]
    source_id = source["source_id"]

    if source_type == "raw_material_replenishment":
        completed_todos = complete_todos(db, business_type=source_type, business_id=source_id)
        usage = source["usage"]
        recipient_user_ids = [
            current_user.id,
            usage.production_task.created_by_id if usage.production_task else None,
            *(todo.assigned_user_id for todo in completed_todos),
        ]
        material_name = usage.material.name if usage.material else f"原料#{usage.material_id}"
        task_no = usage.production_task.task_no if usage.production_task else "-"
        create_notification(
            db,
            title="原料补货需求已转采购单",
            content=f"生产任务 {task_no} 的原料 {material_name} 已转为采购订单 {order.order_no}。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys(user_id for user_id in recipient_user_ids if user_id)),
            created_by_id=current_user.id,
            category="success",
        )
        return

    if source_type == "low_stock_replenishment":
        completed_todos = complete_todos(db, business_type=source_type, business_id=source_id)
        source_payload = source["payload"]
        item_name = source_payload.get("product_name") or source_payload.get("material_name") or "库存补货物料"
        create_notification(
            db,
            title="安全库存补货需求已转采购单",
            content=f"{item_name} 已转为采购订单 {order.order_no}。",
            module="purchase",
            recipient_user_ids=list(
                dict.fromkeys(
                    user_id
                    for user_id in [current_user.id, *(todo.assigned_user_id for todo in completed_todos)]
                    if user_id
                )
            ),
            created_by_id=current_user.id,
            category="success",
        )
        return

    demand = source["demand"]
    demand.status = "converted"
    demand.processed_by_id = current_user.id
    demand.process_note = f"已转采购订单 {order.order_no}"
    complete_todos(db, business_type="fulfillment_demand", business_id=demand.id)
    recipient_user_ids = [current_user.id]
    if demand.sales_order and demand.sales_order.sales_user_id:
        recipient_user_ids.append(demand.sales_order.sales_user_id)
    create_notification(
        db,
        title="采购补货需求已转采购单",
        content=f"补货需求 {demand.demand_no} 已转为采购订单 {order.order_no}。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys(recipient_user_ids)),
        created_by_id=current_user.id,
        category="success",
    )


def link_purchase_order_to_replenishment_source(
    db: Session,
    *,
    order: PurchaseOrder,
    source_type: str,
    source_id: int,
    current_user: User,
) -> None:
    if order.replenishment_source_type or order.replenishment_source_id:
        if (
            order.replenishment_source_type == source_type
            and order.replenishment_source_id == source_id
        ):
            return
        raise HTTPException(status_code=409, detail="采购订单已关联其他补货来源")

    source = _load_purchase_replenishment_source(
        db,
        source_type=source_type,
        source_id=source_id,
        items=order.items,
        existing_order_id=order.id,
    )
    order.replenishment_source_type = source_type
    order.replenishment_source_id = source_id
    _complete_purchase_replenishment_source(db, source=source, order=order, current_user=current_user)


def _reopen_completed_replenishment_todos(
    db: Session,
    *,
    source_type: str,
    source_id: int,
    module: str | None = None,
) -> list[TodoItem]:
    statement = select(TodoItem).where(
        TodoItem.business_type == source_type,
        TodoItem.business_id == source_id,
    )
    if module:
        statement = statement.where(TodoItem.module == module)
    rows = db.execute(statement).scalars().all()
    for todo in rows:
        todo.status = "pending"
        todo.completed_at = None
    db.flush()
    return rows


def _reopen_purchase_replenishment_source(
    db: Session,
    *,
    order: PurchaseOrder,
    current_user: User,
) -> None:
    source_type = order.replenishment_source_type
    source_id = order.replenishment_source_id
    if not source_type or not source_id:
        return

    if source_type == "raw_material_replenishment":
        usage = db.execute(
            select(ProductionMaterialUsage)
            .options(joinedload(ProductionMaterialUsage.material), joinedload(ProductionMaterialUsage.production_task))
            .where(ProductionMaterialUsage.id == source_id)
        ).unique().scalar_one_or_none()
        todos = _reopen_completed_replenishment_todos(
            db,
            source_type=source_type,
            source_id=source_id,
            module="purchase",
        )
        recipient_user_ids = [current_user.id, *(todo.assigned_user_id for todo in todos)]
        if usage and usage.production_task and usage.production_task.created_by_id:
            recipient_user_ids.append(usage.production_task.created_by_id)
        item_name = usage.material.name if usage and usage.material else "原料补货物料"
        create_notification(
            db,
            title="原料补货需求已恢复",
            content=f"采购订单 {order.order_no} 已作废，{item_name} 的补货需求已恢复待采购。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys(user_id for user_id in recipient_user_ids if user_id)),
            created_by_id=current_user.id,
            category="warning",
        )
    elif source_type == "low_stock_replenishment":
        todos = _reopen_completed_replenishment_todos(
            db,
            source_type=source_type,
            source_id=source_id,
            module="purchase",
        )
        source_payload = next(
            (
                payload
                for payload in (_parse_low_stock_replenishment_payload(todo) for todo in todos)
                if payload.get("product_name") or payload.get("material_name")
            ),
            {},
        )
        item_name = source_payload.get("product_name") or source_payload.get("material_name") or "安全库存补货物料"
        create_notification(
            db,
            title="安全库存补货需求已恢复",
            content=f"采购订单 {order.order_no} 已作废，{item_name} 的补货需求已恢复待采购。",
            module="purchase",
            recipient_user_ids=list(
                dict.fromkeys(
                    user_id
                    for user_id in [current_user.id, *(todo.assigned_user_id for todo in todos)]
                    if user_id
                )
            ),
            created_by_id=current_user.id,
            category="warning",
        )
    elif source_type == "fulfillment_demand":
        demand = db.execute(
            select(FulfillmentDemand)
            .options(joinedload(FulfillmentDemand.sales_order))
            .where(FulfillmentDemand.id == source_id)
        ).unique().scalar_one_or_none()
        todos = _reopen_completed_replenishment_todos(
            db,
            source_type=source_type,
            source_id=source_id,
        )
        if demand is not None:
            demand.status = "pending"
            demand.processed_by_id = None
            demand.process_note = f"采购订单 {order.order_no} 已作废，待重新采购"
        recipient_user_ids = [current_user.id, *(todo.assigned_user_id for todo in todos)]
        if demand and demand.sales_order and demand.sales_order.sales_user_id:
            recipient_user_ids.append(demand.sales_order.sales_user_id)
        create_notification(
            db,
            title="采购补货需求已恢复",
            content=f"采购订单 {order.order_no} 已作废，关联补货需求已恢复待处理。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys(user_id for user_id in recipient_user_ids if user_id)),
            created_by_id=current_user.id,
            category="warning",
        )

    order.replenishment_source_type = None
    order.replenishment_source_id = None


def create_purchase_order(payload: PurchaseOrderCreate, current_user: User, db: Session):
    supplier = db.execute(select(Supplier).where(Supplier.id == payload.supplier_id, Supplier.status == "enabled")).scalar_one_or_none()
    if supplier is None:
        raise HTTPException(status_code=404, detail="供应商不存在或已停用")
    if not payload.items:
        raise HTTPException(status_code=400, detail="采购订单至少需要一条明细")
    _validate_supplier_catalog(db, payload.supplier_id, payload.items)
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
        replenishment_source_type=payload.replenishment_source_type,
        replenishment_source_id=payload.replenishment_source_id,
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
    if replenishment_source is not None:
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


def request_purchase_fund(payload: PurchaseFundRequestCreate, current_user: User, db: Session):
    order = _load_order(db, payload.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="已作废采购单不能申请拨款")

    latest_record = _latest_fund_record(db, order.id)
    if latest_record and latest_record.status in {"pending_manager_approval", "pending_finance_approval", "paid"}:
        raise HTTPException(status_code=409, detail="当前采购单已有未结清拨款流程")

    record = PayableRecord(
        purchase_order_id=order.id,
        amount=payload.amount,
        paid_at=now_local(),
        paid_by_id=current_user.id,
        status="pending_manager_approval",
        remark=(payload.remark or f"采购拨款申请：{order.order_no}").strip(),
    )
    db.add(record)
    db.flush()

    chairman_user_ids = _chairman_user_ids(db)
    finance_user_ids = _finance_user_ids(db)
    for user_id in chairman_user_ids:
        create_todo(
            db,
            title=f"待经理审批采购拨款 {order.order_no}",
            description=f"采购申请拨款 {float(payload.amount)}，请审批。",
            module="purchase",
            assigned_user_id=user_id,
            business_type="purchase_fund_request",
            business_id=record.id,
            priority="high",
        )
    create_notification(
        db,
        title="新增采购拨款申请",
        content=f"采购订单 {order.order_no} 已申请拨款 {float(payload.amount)}，等待经理与财务协同处理。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="request_purchase_fund",
        content=f"为采购订单 {order.order_no} 申请拨款",
        after_data={"payable_id": record.id, "amount": float(record.amount), "status": record.status},
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})


def submit_purchase_payment_request(payload: PurchasePaymentRequestCreate, current_user: User, db: Session):
    order = _load_order(db, payload.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _validate_order_payment_requestable(order, current_user, db)

    if Decimal(payload.requested_amount or 0) != Decimal(payload.order_amount or 0) + Decimal(payload.other_fee_amount or 0):
        raise HTTPException(status_code=409, detail="申请总金额必须等于订单金额加其他费用")
    _validate_payment_amounts(db, order, Decimal(payload.order_amount or 0), Decimal(payload.other_fee_amount or 0))

    request = _create_purchase_payment_request_row(
        db,
        order=order,
        current_user=current_user,
        requested_amount=Decimal(payload.requested_amount or 0),
        order_amount=Decimal(payload.order_amount or 0),
        other_fee_amount=Decimal(payload.other_fee_amount or 0),
        payment_detail_remark=payload.payment_detail_remark,
    )

    chairman_user_ids = _chairman_user_ids(db)
    for user_id in chairman_user_ids:
        create_todo(
            db,
            title=f"待审批采购付款申请 {request.request_no}",
            description=f"采购订单 {order.order_no} 提交付款申请，申请金额 {float(request.requested_amount)}。",
            module="purchase",
            assigned_user_id=user_id,
            business_type="purchase_payment_request",
            business_id=request.id,
            priority="high",
        )
    finance_user_ids = _finance_user_ids(db)
    recipient_user_ids = list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids]))
    create_notification(
        db,
        title="采购付款申请已提交",
        content=f"采购订单 {order.order_no} 已提交付款申请 {request.request_no}，等待董事长审批。",
        module="purchase",
        recipient_user_ids=recipient_user_ids,
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="submit_purchase_payment_request",
        content=f"提交采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "purchase_order_id": order.id,
            "requested_amount": float(request.requested_amount or 0),
            "order_amount": float(request.order_amount or 0),
            "other_fee_amount": float(request.other_fee_amount or 0),
            "arrival_status": request.arrival_status,
            "status": request.status,
            "contract_attachments": _purchase_contract_attachments(db, order.id),
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "request_no": request.request_no, "status": request.status})


def submit_merged_purchase_payment_request(payload: PurchaseMergedPaymentRequestCreate, current_user: User, db: Session):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择两张采购订单")
    order_ids = [item.purchase_order_id for item in payload.items]
    if len(set(order_ids)) != len(order_ids):
        raise HTTPException(status_code=409, detail="合并付款中存在重复采购订单")
    orders = [order for order in (_load_order(db, order_id) for order_id in order_ids) if order is not None]
    if len(orders) != len(order_ids):
        raise HTTPException(status_code=404, detail="存在不存在的采购订单")
    _validate_merged_orders_same_supplier(orders, payload.supplier_id)

    payload_map = {item.purchase_order_id: item for item in payload.items}
    merge_no = next_business_no(db, "HFK")
    chairman_user_ids = _chairman_user_ids(db)
    finance_user_ids = _finance_user_ids(db)
    created_rows: list[PurchasePaymentRequest] = []
    requested_total = Decimal("0")

    first_request_id: int | None = None
    for order in orders:
        _validate_order_payment_requestable(order, current_user, db)
        item = payload_map[order.id]
        order_amount = Decimal(item.order_amount or 0)
        other_fee_amount = Decimal(item.other_fee_amount or 0)
        _validate_payment_amounts(db, order, order_amount, other_fee_amount)
        requested_amount = order_amount + other_fee_amount
        request = _create_purchase_payment_request_row(
            db,
            order=order,
            current_user=current_user,
            requested_amount=requested_amount,
            order_amount=order_amount,
            other_fee_amount=other_fee_amount,
            payment_detail_remark=payload.payment_detail_remark,
            merge_no=merge_no,
        )
        created_rows.append(request)
        if first_request_id is None:
            first_request_id = request.id
        requested_total += requested_amount
    for user_id in chairman_user_ids:
        create_todo(
            db,
            title=f"待审批合并付款申请 {merge_no}",
            description=f"合并付款批次 {merge_no} 包含 {len(created_rows)} 张采购订单，申请总额 {float(requested_total)}。",
            module="purchase",
            assigned_user_id=user_id,
            business_type="purchase_payment_request",
            business_id=first_request_id,
            priority="high",
        )

    create_notification(
        db,
        title="采购合并付款申请已提交",
        content=f"供应商合并付款批次 {merge_no} 已提交，共 {len(created_rows)} 张采购订单，申请总额 {float(requested_total)}。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="submit_purchase_payment_request",
        content=f"提交采购合并付款申请 {merge_no}",
        after_data={
            "merge_no": merge_no,
            "order_ids": order_ids,
            "requested_total": float(requested_total),
            "request_ids": [row.id for row in created_rows],
        },
    )
    db.commit()
    return api_response(
        data={
            "merge_no": merge_no,
            "request_ids": [row.id for row in created_rows],
            "request_count": len(created_rows),
            "requested_total": float(requested_total),
        }
    )


def register_merged_purchase_payment(payload, current_user: User, db: Session):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一张采购订单")
    order_ids = [item.purchase_order_id for item in payload.items]
    if len(set(order_ids)) != len(order_ids):
        raise HTTPException(status_code=409, detail="合并付款中存在重复采购订单")
    orders = [order for order in (_load_order(db, order_id) for order_id in order_ids) if order is not None]
    if len(orders) != len(order_ids):
        raise HTTPException(status_code=404, detail="存在不存在的采购订单")
    _validate_merged_orders_same_supplier(orders, payload.supplier_id, min_count=1)

    merge_no = next_business_no(db, "HFK")
    now = now_local()
    payload_map = {item.purchase_order_id: item for item in payload.items}
    expected_total = Decimal("0")
    request_ids: list[int] = []
    payable_ids: list[int] = []

    for order in orders:
        item = payload_map[order.id]
        order_amount = Decimal(item.order_amount or 0)
        other_fee_amount = Decimal(item.other_fee_amount or 0)
        _validate_payment_amounts(db, order, order_amount, other_fee_amount)
        requested_amount = order_amount + other_fee_amount
        expected_total += requested_amount

    if Decimal(payload.actual_amount or 0) != expected_total:
        raise HTTPException(status_code=409, detail="合并付款实付金额必须等于所有订单金额与其他费用之和")

    for order in orders:
        item = payload_map[order.id]
        order_amount = Decimal(item.order_amount or 0)
        other_fee_amount = Decimal(item.other_fee_amount or 0)
        requested_amount = order_amount + other_fee_amount
        request = _create_purchase_payment_request_row(
            db,
            order=order,
            current_user=current_user,
            requested_amount=requested_amount,
            order_amount=order_amount,
            other_fee_amount=other_fee_amount,
            payment_detail_remark=payload.remark,
            merge_no=merge_no,
            preset_status="pending_completion",
            approved_by_id=current_user.id,
            approved_at=now,
            manager_remark="应付管理直接登记付款",
        )
        request_ids.append(request.id)
        paid_order_amount = _purchase_paid_order_amount(db, order.id) + order_amount
        payable_status = "settled" if paid_order_amount >= Decimal(order.total_amount or 0) else "partial_settled"
        payable_record = PayableRecord(
            purchase_order_id=order.id,
            purchase_payment_request_id=request.id,
            merge_no=merge_no,
            amount=requested_amount,
            order_amount=order_amount,
            other_fee_amount=other_fee_amount,
            paid_at=now,
            paid_by_id=current_user.id,
            status=payable_status,
            remark=payload.remark or f"采购合并付款 {merge_no}",
        )
        db.add(payable_record)
        db.flush()
        payable_ids.append(payable_record.id)
        create_cash_flow(
            db,
            flow_type="outgoing",
            business_type="purchase_order",
            business_id=order.id,
            related_no=order.order_no,
            amount=requested_amount,
            happened_at=now,
            remark=payload.remark or f"采购合并付款 {merge_no}",
        )
        create_notification(
            db,
            title="采购合并付款已登记",
            content=f"采购订单 {order.order_no} 已纳入合并付款批次 {merge_no}，请采购确认完成。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_purchase_payment_request",
        content=f"登记采购合并付款 {merge_no}",
        after_data={
            "merge_no": merge_no,
            "order_ids": order_ids,
            "request_ids": request_ids,
            "payable_ids": payable_ids,
            "actual_amount": float(expected_total),
        },
    )
    db.commit()
    return api_response(data={"merge_no": merge_no, "request_ids": request_ids, "payable_ids": payable_ids})


def approve_purchase_payment_request(request_id: int, payload: PurchasePaymentApprovalRequest, current_user: User, db: Session):
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if request.status not in {"pending_manager_approval", "pending_chairman_approval"}:
        raise HTTPException(status_code=409, detail="当前付款申请状态不允许审批")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"chairman", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权审批采购付款申请")

    if payload.decision == "rejected":
        request.status = "rejected"
        request.manager_remark = payload.manager_remark
        request.approved_by_id = current_user.id
        request.approved_at = now_local()
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)
        create_notification(
            db,
            title="采购付款申请已驳回",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已被驳回，请重新填写。",
            module="purchase",
            recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
            created_by_id=current_user.id,
            category="warning",
        )
    else:
        actual_amount = Decimal(payload.actual_amount or 0)
        if actual_amount <= 0:
            actual_amount = Decimal(request.requested_amount or 0)
        if actual_amount != Decimal(request.requested_amount or 0):
            request.status = "rejected"
            request.manager_remark = payload.manager_remark or "审批金额与申请金额不一致，已退回采购重填"
            request.approved_by_id = current_user.id
            request.approved_at = now_local()
            complete_todos(db, business_type="purchase_payment_request", business_id=request.id)
            create_notification(
                db,
                title="采购付款申请金额不一致",
                content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 审批金额与申请金额不一致，已退回采购。",
                module="purchase",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
            db.commit()
            return api_response(data={"id": request.id, "status": request.status})

        request.actual_amount = actual_amount
        request.manager_remark = payload.manager_remark
        request.approved_by_id = current_user.id
        request.approved_at = now_local()
        request.status = "pending_finance_payment"
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)

        finance_user_ids = _finance_user_ids(db)
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"采购付款待出账 {request.request_no}",
                description=f"采购订单 {order.order_no} 的付款申请已由董事长审批通过，请财务上传回单并确认出账。",
                module="finance",
                assigned_user_id=user_id,
                business_type="purchase_payment_request",
                business_id=request.id,
                priority="high",
            )
        create_notification(
            db,
            title="采购付款申请已通过",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已通过，已流转至财务确认出账。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *finance_user_ids])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="approve_purchase_payment_request",
        content=f"审批采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "decision": payload.decision,
            "actual_amount": float(payload.actual_amount or 0),
            "status": request.status,
            "contract_attachments": _purchase_contract_attachments(db, order.id),
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def register_purchase_payment_request(request_id: int, payload: PurchasePaymentApprovalRequest, current_user: User, db: Session):
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if request.status != "pending_finance_payment":
        raise HTTPException(status_code=409, detail="当前付款申请状态不允许登记出账")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"finance", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权登记采购出账")

    actual_amount = Decimal(payload.actual_amount or 0)
    if actual_amount <= 0:
        raise HTTPException(status_code=409, detail="实际出账金额必须大于 0")
    if not getattr(payload, "bank_receipt_attachment_id", None):
        raise HTTPException(status_code=409, detail="请先上传银行回单或出账凭证")

    now = now_local()
    request.actual_amount = actual_amount
    request.manager_remark = payload.manager_remark or request.manager_remark
    request.payment_method = getattr(payload, "payment_method", None) or request.payment_method
    request.bank_receipt_attachment_id = getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id
    request.paid_at = getattr(payload, "paid_at", None) or now
    if request.approved_by_id is None:
        request.approved_by_id = current_user.id
    if request.approved_at is None:
        request.approved_at = now

    if actual_amount != Decimal(request.requested_amount or 0):
        request.status = "rejected"
        request.manager_remark = payload.manager_remark or "出账金额与申请金额不一致，已退回采购重填"
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)
        create_notification(
            db,
            title="采购付款申请金额不一致",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 出账金额与申请金额不一致，已退回采购。",
            module="purchase",
            recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
            created_by_id=current_user.id,
            category="warning",
        )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="finance",
            action="register_purchase_payment_request",
            content=f"退回采购订单 {order.order_no} 的付款申请 {request.request_no}",
            after_data={"request_no": request.request_no, "actual_amount": float(actual_amount), "status": request.status},
        )
        db.commit()
        return api_response(data={"id": request.id, "status": request.status})

    request.status = "pending_completion"
    complete_todos(db, business_type="purchase_payment_request", business_id=request.id)

    paid_order_amount = _purchase_paid_order_amount(db, order.id) + Decimal(request.order_amount or 0)
    payable_status = "settled" if paid_order_amount >= Decimal(order.total_amount or 0) else "partial_settled"
    payable_record = PayableRecord(
        purchase_order_id=order.id,
        purchase_payment_request_id=request.id,
        merge_no=request.merge_no,
        amount=actual_amount,
        order_amount=request.order_amount,
        other_fee_amount=request.other_fee_amount,
        paid_at=getattr(payload, "paid_at", None) or now,
        paid_by_id=current_user.id,
        status=payable_status,
        remark=request.payment_detail_remark or f"采购付款申请 {request.request_no}",
        payment_method=getattr(payload, "payment_method", None) or request.payment_method,
        bank_receipt_attachment_id=getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id,
    )
    db.add(payable_record)
    create_cash_flow(
        db,
        flow_type="outgoing",
        business_type="purchase_order",
        business_id=order.id,
        related_no=order.order_no,
        amount=actual_amount,
        happened_at=getattr(payload, "paid_at", None) or now,
        remark=request.payment_detail_remark or f"采购付款申请 {request.request_no}",
    )
    create_notification(
        db,
        title="采购出账已登记",
        content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已登记出账，请采购确认完成。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_chairman_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_purchase_payment_request",
        content=f"登记采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={"request_no": request.request_no, "actual_amount": float(actual_amount), "status": request.status},
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def register_purchase_payment_requests_batch(
    payload: FinanceMergedPurchasePaymentRequestRegisterRequest,
    current_user: User,
    db: Session,
):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一条采购付款申请")
    request_ids = [item.purchase_payment_request_id for item in payload.items]
    if len(set(request_ids)) != len(request_ids):
        raise HTTPException(status_code=409, detail="合并登记中存在重复付款申请")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"finance", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权登记采购出账")

    requests = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id.in_(request_ids))
    ).scalars().all()
    request_map = {row.id: row for row in requests}
    if len(request_map) != len(request_ids):
        raise HTTPException(status_code=404, detail="存在不存在的付款申请")
    orders = [_load_order(db, request_map[request_id].purchase_order_id) for request_id in request_ids]
    if any(order is None for order in orders):
        raise HTTPException(status_code=404, detail="存在不存在的采购订单")
    for request in requests:
        if request.status != "pending_finance_payment":
            raise HTTPException(status_code=409, detail=f"付款申请 {request.request_no} 状态不允许登记出账")

    now = now_local()
    amount_map = {item.purchase_payment_request_id: Decimal(item.actual_amount or 0) for item in payload.items}
    payable_ids: list[int] = []
    rejected_ids: list[int] = []
    processed_ids: list[int] = []
    actual_total = Decimal("0")

    for request_id in request_ids:
        request = request_map[request_id]
        order = _load_order(db, request.purchase_order_id)
        actual_amount = amount_map[request_id]
        if actual_amount <= 0:
            raise HTTPException(status_code=409, detail="实际出账金额必须大于 0")
        if not getattr(payload, "bank_receipt_attachment_id", None):
            raise HTTPException(status_code=409, detail="请先上传银行回单或出账凭证")
        request.actual_amount = actual_amount
        request.manager_remark = payload.remark or request.manager_remark
        request.payment_method = getattr(payload, "payment_method", None) or request.payment_method
        request.bank_receipt_attachment_id = getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id
        request.paid_at = getattr(payload, "paid_at", None) or now
        if request.approved_by_id is None:
            request.approved_by_id = current_user.id
        if request.approved_at is None:
            request.approved_at = now
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)

        if actual_amount != Decimal(request.requested_amount or 0):
            request.status = "rejected"
            request.manager_remark = payload.remark or "出账金额与申请金额不一致，已退回采购重填"
            rejected_ids.append(request.id)
            create_notification(
                db,
                title="采购付款申请金额不一致",
                content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 出账金额与申请金额不一致，已退回采购。",
                module="purchase",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
            continue

        request.status = "pending_completion"
        paid_order_amount = _purchase_paid_order_amount(db, order.id) + Decimal(request.order_amount or 0)
        payable_status = "settled" if paid_order_amount >= Decimal(order.total_amount or 0) else "partial_settled"
        payable_record = PayableRecord(
            purchase_order_id=order.id,
            purchase_payment_request_id=request.id,
            merge_no=request.merge_no,
            amount=actual_amount,
            order_amount=request.order_amount,
            other_fee_amount=request.other_fee_amount,
            paid_at=getattr(payload, "paid_at", None) or now,
            paid_by_id=current_user.id,
            status=payable_status,
            remark=payload.remark or request.payment_detail_remark or f"采购付款申请 {request.request_no}",
            payment_method=getattr(payload, "payment_method", None) or request.payment_method,
            bank_receipt_attachment_id=getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id,
        )
        db.add(payable_record)
        db.flush()
        payable_ids.append(payable_record.id)
        processed_ids.append(request.id)
        actual_total += actual_amount
        create_cash_flow(
            db,
            flow_type="outgoing",
            business_type="purchase_order",
            business_id=order.id,
            related_no=order.order_no,
            amount=actual_amount,
            happened_at=getattr(payload, "paid_at", None) or now,
            remark=payload.remark or request.payment_detail_remark or f"采购付款申请 {request.request_no}",
        )
        create_notification(
            db,
            title="采购出账已登记",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已登记出账，请采购确认完成。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_chairman_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_purchase_payment_requests_batch",
        content="批量登记采购付款申请出账",
        after_data={
            "request_ids": request_ids,
            "processed_ids": processed_ids,
            "rejected_ids": rejected_ids,
            "payable_ids": payable_ids,
            "actual_total": float(actual_total),
        },
    )
    db.commit()
    return api_response(
        data={
            "processed_ids": processed_ids,
            "rejected_ids": rejected_ids,
            "payable_ids": payable_ids,
            "actual_total": float(actual_total),
        }
    )


def complete_purchase_payment_request(request_id: int, payload: PurchasePaymentCompleteRequest, current_user: User, db: Session):
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if request.status != "pending_completion":
        raise HTTPException(status_code=409, detail="当前付款申请还不能完成")

    request.status = "completed"
    request.completed_by_id = current_user.id
    request.completed_at = now_local()
    request.completion_remark = payload.completion_remark
    create_notification(
        db,
        title="采购付款流程已完成",
        content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已由采购确认完成。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_manager_user_ids(db), *_finance_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="complete_purchase_payment_request",
        content=f"完成采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "status": request.status,
            "completion_remark": request.completion_remark,
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def build_purchase_settlement_snapshot(current_user: User, db: Session, *, scoped_user_ids: list[int] | None = None):
    query = (
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
        )
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
    )
    if scoped_user_ids is not None:
        query = query.where(PurchaseOrder.purchaser_user_id.in_(scoped_user_ids or [-1]))
    orders = db.execute(query).unique().scalars().all()
    order_ids = [row.id for row in orders]

    payment_requests = (
        db.execute(
            select(PurchasePaymentRequest)
            .where(PurchasePaymentRequest.purchase_order_id.in_(order_ids))
            .order_by(PurchasePaymentRequest.created_at.desc(), PurchasePaymentRequest.id.desc())
        ).scalars().all()
        if order_ids
        else []
    )
    payables = (
        db.execute(
            select(PayableRecord)
            .where(PayableRecord.purchase_order_id.in_(order_ids))
            .order_by(PayableRecord.paid_at.desc(), PayableRecord.id.desc())
        ).scalars().all()
        if order_ids
        else []
    )

    paid_amount_map: dict[int, Decimal] = {}
    paid_order_amount_map: dict[int, Decimal] = {}
    for row in payables:
        if row.status not in {"paid", "partial_settled", "settled"}:
            continue
        paid_amount_map[row.purchase_order_id] = paid_amount_map.get(row.purchase_order_id, Decimal("0")) + Decimal(row.amount or 0)
        paid_order_amount_map[row.purchase_order_id] = paid_order_amount_map.get(row.purchase_order_id, Decimal("0")) + Decimal(row.order_amount or 0)

    request_user_ids = {
        user_id
        for row in payment_requests
        for user_id in [row.requested_by_id, row.approved_by_id, row.completed_by_id]
        if user_id
    }
    user_map = {
        row.id: row
        for row in db.execute(select(User).where(User.id.in_(list(request_user_ids)))).scalars().all()
    } if request_user_ids else {}

    order_rows = []
    completed_rows = []
    arrival_status_map: dict[int, str] = {}
    arrival_label_map: dict[int, str] = {}
    for row in orders:
        total_amount = Decimal(row.total_amount or 0)
        order_amount_paid = paid_order_amount_map.get(row.id, Decimal("0"))
        cash_paid = paid_amount_map.get(row.id, Decimal("0"))
        settlement_status = "unpaid"
        if order_amount_paid > 0:
            settlement_status = "settled" if order_amount_paid >= total_amount else "partial_settled"
        arrival_status = purchase_arrival_status(db, row.id)
        arrival_label = _purchase_arrival_display_label(row, arrival_status)
        arrival_status_map[row.id] = arrival_status
        arrival_label_map[row.id] = arrival_label
        item_summary = "；".join(
            f"{item.product.name if item.product else (item.material.name if item.material else '-') } × {float(item.quantity or 0)}"
            for item in row.items[:3]
        )
        contract_attachments = _purchase_contract_attachments(db, row.id)
        is_completed_order = _purchase_order_business_completed(db, row, settlement_status=settlement_status)
        serialized = {
            "id": row.id,
            "order_no": row.order_no,
            "supplier_id": row.supplier_id,
            "supplier_name": row.supplier.name if row.supplier else None,
            "status": row.status,
            "status_label": status_label(row.status),
            "payment_type": row.payment_type,
            "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
            "arrival_status": arrival_status,
            "arrival_status_label": arrival_label,
            "total_amount": float(total_amount),
            "paid_amount": float(cash_paid),
            "paid_order_amount": float(order_amount_paid),
            "remaining_amount": float(max(total_amount - order_amount_paid, Decimal("0"))),
            "fund_status": settlement_status,
            "fund_status_label": status_label(settlement_status),
            "item_summary": item_summary,
            "items": [
                {
                    "item_name": item.product.name if item.product else (item.material.name if item.material else "-"),
                    "item_code": item.product.product_code if item.product else (item.material.material_code if item.material else "-"),
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "total_amount": float(item.total_amount or 0),
                    "received_quantity": float(item.received_quantity or 0),
                    "display_specification": item.display_specification,
                }
                for item in row.items
            ],
            "contract_attachment_count": len(contract_attachments),
            "contract_attachment_names": [item["original_name"] for item in contract_attachments],
            "flow_steps": purchase_flow_steps(row, arrival_status=arrival_status, settlement_status=settlement_status),
            "can_submit_payment_request": row.status != "cancelled",
            "is_completed_order": is_completed_order,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if serialized["is_completed_order"]:
            completed_rows.append(serialized)
        else:
            order_rows.append(serialized)

    request_rows = [
        {
            "id": row.id,
            "request_no": row.request_no,
            "merge_no": row.merge_no,
            "purchase_order_id": row.purchase_order_id,
            "purchase_order_no": row.purchase_order.order_no if row.purchase_order else None,
            "requested_amount": float(row.requested_amount or 0),
            "order_amount": float(row.order_amount or 0),
            "other_fee_amount": float(row.other_fee_amount or 0),
            "actual_amount": float(row.actual_amount or 0),
            "arrival_status": arrival_status_map.get(row.purchase_order_id, row.arrival_status),
            "arrival_status_label": arrival_label_map.get(
                row.purchase_order_id,
                "已到货" if row.arrival_status == "arrived" else "未到货",
            ),
            "status": row.status,
            "status_label": status_label(row.status),
            "payment_detail_remark": row.payment_detail_remark,
            "manager_remark": row.manager_remark,
            "completion_remark": row.completion_remark,
            "requested_by_name": user_map[row.requested_by_id].full_name if row.requested_by_id in user_map else None,
            "approved_by_name": user_map[row.approved_by_id].full_name if row.approved_by_id in user_map else None,
            "completed_by_name": user_map[row.completed_by_id].full_name if row.completed_by_id in user_map else None,
            "contract_attachment_count": len(_purchase_contract_attachments(db, row.purchase_order_id)),
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in payment_requests
    ]
    return {
        "orders": order_rows,
        "completed_orders": completed_rows,
        "payment_requests": request_rows,
        "payables": payables,
    }


def approve_purchase_fund(payable_id: int, payload: PurchaseFundApprovalRequest, current_user: User, db: Session):
    record = db.execute(select(PayableRecord).where(PayableRecord.id == payable_id)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="采购拨款申请不存在")
    order = _load_order(db, record.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")

    role_code = current_user.role.code if current_user.role else ""
    note = (payload.note or "").strip() or None

    if record.status == "pending_manager_approval":
        if role_code not in {"manager", "system_admin"}:
            raise HTTPException(status_code=403, detail="当前仅经理可以处理该拨款申请")
        if payload.decision == "rejected":
            record.status = "rejected"
            record.remark = note or "经理驳回采购拨款申请"
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            create_notification(
                db,
                title="采购拨款申请已驳回",
                content=f"采购订单 {order.order_no} 的拨款申请已被经理驳回。",
                module="purchase",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
        else:
            if payload.amount is not None:
                record.amount = payload.amount
            record.status = "pending_finance_approval"
            record.remark = note or record.remark
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            finance_user_ids = _finance_user_ids(db)
            for user_id in finance_user_ids:
                create_todo(
                    db,
                    title=f"待财务审批采购拨款 {order.order_no}",
                    description=f"经理已通过采购拨款申请，请确认拨款金额 {float(record.amount)}。",
                    module="finance",
                    assigned_user_id=user_id,
                    business_type="purchase_fund_request",
                    business_id=record.id,
                    priority="high",
                )
            create_notification(
                db,
                title="采购拨款待财务审批",
                content=f"采购订单 {order.order_no} 已通过经理审批，待财务确认拨款 {float(record.amount)}。",
                module="finance",
                recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *finance_user_ids])),
                created_by_id=current_user.id,
                category="info",
            )
    elif record.status == "pending_finance_approval":
        if role_code not in {"finance", "system_admin"}:
            raise HTTPException(status_code=403, detail="当前仅财务可以完成拨款审批")
        if payload.decision == "rejected":
            record.status = "rejected"
            record.remark = note or "财务驳回采购拨款申请"
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            create_notification(
                db,
                title="采购拨款申请已驳回",
                content=f"采购订单 {order.order_no} 的拨款申请已被财务驳回。",
                module="finance",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
        else:
            approved_amount = Decimal(payload.amount if payload.amount is not None else record.amount or 0)
            if approved_amount <= 0:
                raise HTTPException(status_code=409, detail="拨款金额必须大于 0")
            record.amount = approved_amount
            record.status = "paid"
            record.paid_by_id = current_user.id
            record.paid_at = now_local()
            record.remark = note or record.remark
            create_cash_flow(
                db,
                flow_type="outgoing",
                business_type="purchase_order",
                business_id=order.id,
                related_no=order.order_no,
                amount=approved_amount,
                happened_at=record.paid_at,
                remark=record.remark,
            )
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            create_notification(
                db,
                title="采购拨款已到账",
                content=f"采购订单 {order.order_no} 已完成拨款 {float(approved_amount)}，采购可继续预算协同与结算。",
                module="finance",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="success",
            )
    else:
        raise HTTPException(status_code=409, detail="当前拨款申请状态不允许审批")

    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="approve_purchase_fund",
        content=f"审批采购订单 {order.order_no} 的拨款申请",
        after_data={"payable_id": record.id, "status": record.status, "amount": float(record.amount or 0), "decision": payload.decision},
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})


def settle_purchase_fund(payable_id: int, current_user: User, db: Session):
    record = db.execute(select(PayableRecord).where(PayableRecord.id == payable_id)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="采购拨款记录不存在")
    order = _load_order(db, record.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if record.status != "paid":
        raise HTTPException(status_code=409, detail="当前拨款记录未到账，不能标记结算完成")

    record.status = "settled"
    create_notification(
        db,
        title="采购结算已完成",
        content=f"采购订单 {order.order_no} 已由采购标记为结算完成。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_finance_user_ids(db), *_manager_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="settle_purchase_fund",
        content=f"完成采购订单 {order.order_no} 的采购结算",
        after_data={"payable_id": record.id, "status": record.status},
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})


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
