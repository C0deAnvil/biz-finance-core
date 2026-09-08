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
from services.core import _role_user_ids, apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, next_business_no, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label, status_label
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


def _manager_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "manager", workflow="manager_workflow")


def _chairman_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "chairman", workflow="chairman_workflow")


def _finance_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "finance", workflow="finance_workflow")


def _round_robin_finance_user_id(db: Session) -> int | None:
    """未指定财务时,轮询分配:选当前待处理付款申请最少的普通财务。"""
    finance_user_ids = [
        row.id
        for row in db.execute(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(
                User.is_active.is_(True),
                Role.code.in_(["finance", "finance_super"]),
            )
        ).scalars().all()
    ]
    if not finance_user_ids:
        return None
    pending_counts = dict(
        db.execute(
            select(PurchasePaymentRequest.assigned_finance_user_id, func.count(PurchasePaymentRequest.id))
            .where(
                PurchasePaymentRequest.assigned_finance_user_id.in_(finance_user_ids),
                PurchasePaymentRequest.status.in_(["pending", "pending_finance_payment", "pending_completion"]),
            )
            .group_by(PurchasePaymentRequest.assigned_finance_user_id)
        ).all()
    )
    return min(finance_user_ids, key=lambda uid: (pending_counts.get(uid, 0), uid))


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


# 尚在流程中、会导致重复付款的付款申请状态(已驳回/已完成不算)
UNFINISHED_PAYMENT_REQUEST_STATUSES = (
    "pending_manager_approval",
    "pending_chairman_approval",
    "pending_finance_payment",
    "pending_completion",
)


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
    # 必须查"是否存在任何未完成申请",不能只看最新一张。
    # 原先用 _latest_payment_request(按 id 倒序取 first):
    # 只要最新那张是已驳回/已完成,校验就放行,而更早一张可能仍在
    # pending_finance_payment —— 同一采购单于是挂上两张有效申请,存在重复付款风险
    # (CG20260907001 就出现了 FK20260907001 待财务付款 + FK20260907002 待完成)。
    # 单笔与合并两条提交路径都走这里,一处修好即可同时覆盖。
    unfinished_request = db.execute(
        select(PurchasePaymentRequest)
        .where(
            PurchasePaymentRequest.purchase_order_id == order.id,
            PurchasePaymentRequest.status.in_(UNFINISHED_PAYMENT_REQUEST_STATUSES),
        )
        .order_by(PurchasePaymentRequest.id.desc())
    ).scalars().first()
    if unfinished_request is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"采购订单 {order.order_no} 存在未完成的付款申请 "
                f"{unfinished_request.request_no}，请先处理该申请后再提交，避免重复付款"
            ),
        )


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
    assigned_finance_user_id: int | None = None,
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
        assigned_finance_user_id=assigned_finance_user_id,
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


def _parse_sales_order_material_replenishment_payload(todo: TodoItem) -> dict:
    description = todo.description or ""
    marker = "|sales_order_material_replenishment:"
    if marker not in description:
        return {}
    try:
        return json.loads(description.split(marker, 1)[1])
    except (TypeError, ValueError):
        return {}


def _validate_purchase_order_replenishment_items(
    db: Session,
    *,
    source_type: str,
    source_id: int,
    items,
) -> None:
    if source_type in {"sales_order_material_replenishment", "low_stock_product_material_replenishment"}:
        todos = db.execute(
            select(TodoItem).where(
                TodoItem.business_type == source_type,
                TodoItem.business_id == source_id,
                TodoItem.status != "completed",
            )
        ).scalars().all()
        payload = next(
            (
                value
                for value in (_parse_sales_order_material_replenishment_payload(todo) for todo in todos)
                if value.get("items")
            ),
            {},
        )
        shortage_map = {
            int(item["material_id"]): Decimal(str(item.get("shortage_quantity") or 0))
            for item in payload.get("items", [])
            if item.get("material_id")
        }
        provided_map = {}
        for item in items:
            if not item.material_id or item.material_id not in shortage_map:
                continue
            provided_map[item.material_id] = Decimal(item.quantity or 0)
            if Decimal(item.quantity or 0) > shortage_map[item.material_id]:
                raise HTTPException(status_code=409, detail="采购数量不能超过原料缺口")
        missing_material_ids = set(shortage_map) - set(provided_map)
        if missing_material_ids:
            raise HTTPException(status_code=409, detail="采购订单必须一次性包含该原料需求中的全部原料")
        return

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
    validate_items: bool = True,
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

    if source_type in {"sales_order_material_replenishment", "low_stock_product_material_replenishment"}:
        pending_todos = db.execute(
            select(TodoItem).where(
                TodoItem.business_type == source_type,
                TodoItem.business_id == source_id,
                TodoItem.status != "completed",
            )
        ).scalars().all()
        if not pending_todos:
            raise HTTPException(status_code=409, detail="原料采购需求已处理")
        if validate_items:
            _validate_purchase_order_replenishment_items(
                db,
                source_type=source_type,
                source_id=source_id,
                items=items,
            )
        payload = next(
            (
                value
                for value in (_parse_sales_order_material_replenishment_payload(todo) for todo in pending_todos)
                if value.get("items")
            ),
            {},
        )
        return {
            "source_type": source_type,
            "source_id": source_id,
            "pending_todos": pending_todos,
            "payload": payload,
        }

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
        if validate_items:
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
        if validate_items:
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
    if validate_items:
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


def _filter_purchase_items_for_source(source: dict, items) -> list:
    """从合并采购明细中,过滤出属于某个补货来源的商品明细。"""
    source_type = source["source_type"]
    if source_type in {"sales_order_material_replenishment", "low_stock_product_material_replenishment"}:
        wanted = {int(i["material_id"]) for i in (source.get("payload") or {}).get("items", []) if i.get("material_id")}
        return [it for it in items if it.material_id in wanted]
    if source_type == "raw_material_replenishment":
        usage = source.get("usage")
        wanted = {usage.material_id} if usage else set()
        return [it for it in items if it.material_id in wanted]
    if source_type == "low_stock_replenishment":
        payload = source.get("payload") or {}
        wanted = {payload.get("product_id"), payload.get("material_id")}
        return [it for it in items if (it.product_id in wanted or it.material_id in wanted)]
    demand = source.get("demand")
    wanted = {demand.product_id} if demand else set()
    return [it for it in items if it.product_id in wanted]


def _complete_purchase_replenishment_source(
    db: Session,
    *,
    source: dict,
    order: PurchaseOrder,
    current_user: User,
) -> None:
    source_type = source["source_type"]
    source_id = source["source_id"]

    if source_type in {"sales_order_material_replenishment", "low_stock_product_material_replenishment"}:
        completed_todos = complete_todos(db, business_type=source_type, business_id=source_id)
        payload = source.get("payload") or {}
        order_no = payload.get("sales_order_no") or payload.get("source_no") or "补货来源"
        title = (
            "安全库存成品生产原料采购需求已转采购单"
            if source_type == "low_stock_product_material_replenishment"
            else "销售订单原料采购需求已转采购单"
        )
        create_notification(
            db,
            title=title,
            content=f"{order_no} 的生产原料采购需求已转为采购订单 {order.order_no}。",
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
    # 记录补货进度:按本单为该产品实际下单的量累加已补量。
    # 原先这里只置 status="converted",从不动 fulfilled_quantity ——
    # 而进度条读的是 fulfilled/required,于是采购型补货需求即便采购单已全部收货,
    # 界面仍显示"已补 0 / 还差 N"(生产路径在 production_service.py:779 是有累加的,
    # 两条路径口径不一致)。
    ordered_quantity = sum(
        (Decimal(item.quantity or 0) for item in order.items if item.product_id == demand.product_id),
        Decimal("0"),
    )
    required_quantity = Decimal(demand.required_quantity or 0) or Decimal(demand.shortage_quantity or 0)
    demand.required_quantity = required_quantity
    if ordered_quantity > 0:
        demand.fulfilled_quantity = min(
            Decimal(demand.fulfilled_quantity or 0) + ordered_quantity, required_quantity
        )
        demand.shortage_quantity = max(
            required_quantity - Decimal(demand.fulfilled_quantity or 0), Decimal("0")
        )
    # 未全额覆盖时保持 pending,库管/采购才能对剩余缺口继续下单;
    # 全额覆盖才算 converted(与生产路径一致)。
    remaining_quantity = max(required_quantity - Decimal(demand.fulfilled_quantity or 0), Decimal("0"))
    demand.status = "converted" if remaining_quantity <= 0 else "pending"
    demand.processed_by_id = current_user.id
    demand.process_note = (
        f"已转采购订单 {order.order_no}，本单 {float(ordered_quantity)}，"
        f"累计已补 {float(demand.fulfilled_quantity or 0)}/{float(required_quantity)}，"
        f"剩余 {float(remaining_quantity)}。"
    )
    if remaining_quantity <= 0:
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
    elif source_type in {"sales_order_material_replenishment", "low_stock_product_material_replenishment"}:
        todos = _reopen_completed_replenishment_todos(
            db,
            source_type=source_type,
            source_id=source_id,
            module="purchase",
        )
        source_payload = next(
            (
                payload
                for payload in (_parse_sales_order_material_replenishment_payload(todo) for todo in todos)
                if payload.get("items") or payload.get("sales_order_no") or payload.get("source_no")
            ),
            {},
        )
        source_no = source_payload.get("sales_order_no") or source_payload.get("source_no") or "补货来源"
        product_name = source_payload.get("product_name") or "生产原料"
        title = (
            "安全库存成品生产原料采购需求已恢复"
            if source_type == "low_stock_product_material_replenishment"
            else "销售订单生产原料采购需求已恢复"
        )
        create_notification(
            db,
            title=title,
            content=f"采购订单 {order.order_no} 已作废，{source_no} 的 {product_name} 原料采购需求已恢复待采购。",
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


