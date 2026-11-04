import re
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import Customer, DocumentRelation, DocumentStatusSnapshot, order_type_style, FulfillmentDemand, InventoryDispatchQueue, InventoryOperationRequest, InventoryTransaction, Notification, NotificationRecipient, OperationLog, Product, ProductionTask, PurchaseOrder, PurchaseReceipt, QualityTask, ReceivableRecord, RefundRecord, Role, SalesOrder, SalesOrderApproval, SalesOrderItem, SalesOrderPriceChangeLog, SalesReceiptRequest, SalesReturn, SalesReturnItem, TodoItem, User, Warehouse
from responses import api_response
from services.financial_accounts import account_label, settlement_account_fields
from schemas.sales import SalesApprovalRequest, SalesFinanceEditConfirmRequest, SalesMergedReceiptRequestCreate, SalesOrderDraftPayload, SalesOrderFinanceEditRequest, SalesOrderSubmitRequest, SalesOrderUpdate, SalesPriceApprovalRequest, SalesReceiptApprovalRequest, SalesReceiptRequestCreate, SalesReturnApprovalRequest, SalesReturnCreate
from services.display import clean_display_text, format_legacy_remark, payment_stage_label, payment_type_label, product_specification_text, return_type_label, status_label
from services.organization import get_scoped_user_ids, user_in_scope
from services.document_status import (
    get_document_status_history,
    get_document_status_snapshots,
    sync_sales_order_process_status,
)
from services.time_utils import now_local
from services.sales_order_timeline import build_sales_order_tasks
from services.sales_service import (
    approve_order as approve_order_service,
    approve_sales_price_change as approve_sales_price_change_service,
    approve_sales_receipt_request as approve_sales_receipt_request_service,
    approve_return as approve_return_service,
    confirm_finance_edit_order as confirm_finance_edit_order_service,
    complete_order as complete_order_service,
    create_order as create_order_service,
    create_return as create_return_service,
    finance_edit_order as finance_edit_order_service,
    submit_merged_sales_receipt_request as submit_merged_sales_receipt_request_service,
    submit_order as submit_order_service,
    submit_sales_receipt_request as submit_sales_receipt_request_service,
    update_order as update_order_service,
)
from services.workflow import sales_flow_steps


router = APIRouter(prefix="/sales", tags=["销售"])
RECEIPT_MERGE_NO_PATTERN = re.compile(r"合并收款批次[:：]\s*(?P<merge_no>[A-Za-z0-9-]+)")


def _find_role_user_ids(db: Session, *role_codes: str) -> list[int]:
    return [
        user.id
        for user in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def _ensure_sales_scope(db: Session, current_user: User, owner_user_id: int | None) -> None:
    if not user_in_scope(db, current_user, business_role_code="sales", owner_user_id=owner_user_id):
        raise HTTPException(status_code=403, detail="仅可查看数据范围内的客户订单")


def _received_amount_map(rows: list[ReceivableRecord]) -> dict[int, Decimal]:
    amount_map: dict[int, Decimal] = {}
    for row in rows:
        amount_map[row.sales_order_id] = amount_map.get(row.sales_order_id, Decimal("0")) + Decimal(row.amount or 0)
    return amount_map


def _settlement_status(total_amount: Decimal, received_amount: Decimal) -> str:
    if received_amount <= 0:
        return "unpaid"
    if received_amount >= total_amount:
        return "settled"
    return "partial_settled"


def _current_finance_edit_items(order: SalesOrder) -> list[dict]:
    return [
        {
            "product_id": item.product_id,
            "product_name": item.product.name if item.product else None,
            "product_code": item.product.product_code if item.product else None,
            "display_specification": item.display_specification,
            "packaging_config_id": item.packaging_config_id,
            "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
            "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
            "bag_count": float(item.bag_count) if item.bag_count is not None else None,
            "packaging_unit_price": float(item.packaging_unit_price or 0),
            "packaging_fee": float(item.packaging_fee or 0),
            "quantity": float(item.quantity or 0),
            "unit_price": float(item.unit_price or 0),
            "total_amount": float(item.total_amount or 0),
            "remark": item.remark,
        }
        for item in order.items
    ]


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
        result["operator_name"] = operator.full_name if operator else (order.operator_user.full_name if order.operator_user else None)

    raw_items = result.get("items") or []
    if not raw_items:
        result["items"] = fallback_items
        return result

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
        quantity = Decimal(str(enriched.get("quantity") or 0))
        unit_price = Decimal(str(enriched.get("unit_price") or 0))
        if enriched.get("total_amount") is None:
            enriched["total_amount"] = float(quantity * unit_price)
        enriched_items.append(enriched)
    result["items"] = enriched_items
    return result


def _order_related_documents(db: Session, order_id: int) -> list[dict]:
    """销售订单全链看板:从 document_relations 查所有关联子单据,组装状态树。"""
    relations = db.execute(
        select(DocumentRelation)
        .where(
            or_(
                and_(DocumentRelation.source_document_type == "sales_order", DocumentRelation.source_document_id == order_id),
                and_(DocumentRelation.target_document_type == "sales_order", DocumentRelation.target_document_id == order_id),
            )
        )
        .order_by(DocumentRelation.id.asc())
    ).scalars().all()
    nodes: list[dict] = []
    demand_ids = [r.target_document_id for r in relations if r.target_document_type == "fulfillment_demand"]
    task_ids = [r.target_document_id for r in relations if r.target_document_type == "production_task"]
    po_ids = [r.target_document_id for r in relations if r.target_document_type == "purchase_order"]
    quality_ids = [r.target_document_id for r in relations if r.target_document_type == "quality_task"]
    demands = {
        row.id: row
        for row in db.execute(select(FulfillmentDemand).where(FulfillmentDemand.id.in_(demand_ids or [-1]))).scalars().all()
    } if demand_ids else {}
    tasks = {
        row.id: row
        for row in db.execute(select(ProductionTask).where(ProductionTask.id.in_(task_ids or [-1]))).scalars().all()
    } if task_ids else {}
    purchase_orders = {
        row.id: row
        for row in db.execute(select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids or [-1]))).scalars().all()
    } if po_ids else {}
    quality_tasks = {
        row.id: row
        for row in db.execute(select(QualityTask).where(QualityTask.id.in_(quality_ids or [-1]))).scalars().all()
    } if quality_ids else {}

    for relation in relations:
        target_type = relation.target_document_type
        target_id = relation.target_document_id
        if target_type == "fulfillment_demand" and target_id in demands:
            demand = demands[target_id]
            nodes.append(
                {
                    "document_type": "fulfillment_demand",
                    "document_type_label": "补货需求",
                    "document_id": demand.id,
                    "document_no": demand.demand_no,
                    "status": demand.status,
                    "status_label": "待补料" if demand.status == "pending_material" else status_label(demand.status),
                    "quantity": float(demand.shortage_quantity or 0),
                    "product_name": demand.product.name if demand.product else None,
                    "fulfillment_mode": demand.fulfillment_mode,
                }
            )
        elif target_type == "production_task" and target_id in tasks:
            task = tasks[target_id]
            nodes.append(
                {
                    "document_type": "production_task",
                    "document_type_label": "生产任务",
                    "document_id": task.id,
                    "document_no": task.task_no,
                    "status": task.status,
                    "status_label": status_label(task.status),
                    "quantity": float(task.planned_quantity or 0),
                    "product_name": task.product.name if task.product else None,
                }
            )
        elif target_type == "purchase_order" and target_id in purchase_orders:
            po = purchase_orders[target_id]
            nodes.append(
                {
                    "document_type": "purchase_order",
                    "document_type_label": "采购订单",
                    "document_id": po.id,
                    "document_no": po.order_no,
                    "status": po.status,
                    "status_label": status_label(po.status),
                    "quantity": float(po.total_amount or 0),
                    "supplier_name": po.supplier.name if po.supplier else None,
                    "expected_arrival_date": po.expected_arrival_date.isoformat() if po.expected_arrival_date else None,
                }
            )
        elif target_type == "quality_task" and target_id in quality_tasks:
            q = quality_tasks[target_id]
            nodes.append(
                {
                    "document_type": "quality_task",
                    "document_type_label": "质检任务",
                    "document_id": q.id,
                    "document_no": q.quality_no,
                    "status": q.status,
                    "status_label": status_label(q.status),
                    "product_name": None,
                }
            )
    return nodes


def _finance_edit_snapshot(db: Session, order: SalesOrder) -> dict | None:
    row = (
        db.execute(
            select(OperationLog)
            .where(OperationLog.module == "sales", OperationLog.action == "finance_edit_order")
            .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
        )
        .scalars()
        .all()
    )
    matched = next(
        (
            item
            for item in row
            if (item.after_data or {}).get("order_id") == order.id
            or (item.before_data or {}).get("order_id") == order.id
            or (item.after_data or {}).get("order_no") == order.order_no
            or (item.before_data or {}).get("order_no") == order.order_no
            or order.order_no in str(item.content or "")
        ),
        None,
    )
    if matched is None:
        return None
    current_items = _current_finance_edit_items(order)
    before_data = _enrich_finance_edit_snapshot_data(db, matched.before_data or {}, order, current_items)
    after_data = _enrich_finance_edit_snapshot_data(db, matched.after_data or {}, order, current_items)
    if not (matched.before_data or {}).get("items"):
        before_data["legacy_snapshot_note"] = "历史改单日志未记录修改前明细，当前仅保留该次改单的基础字段。"
    return {
        "id": matched.id,
        "changed_by_id": matched.user_id,
        "changed_by_name": matched.user.full_name if matched.user else None,
        "changed_at": matched.created_at.isoformat() if matched.created_at else None,
        "before_data": before_data,
        "after_data": after_data,
        "content": matched.content,
    }


def _finance_edit_confirm_state(db: Session, order_id: int, current_user: User) -> dict:
    role_code = current_user.role.code if current_user.role else ""
    business_type = None
    if role_code == "sales":
        business_type = "sales_order_finance_edit_sales_confirm"
    elif role_code == "inventory":
        business_type = "sales_order_finance_edit_inventory_confirm"
    if business_type is None:
        return {"pending": False, "business_type": None}
    pending = (
        db.execute(
            select(TodoItem).where(
                TodoItem.business_type == business_type,
                TodoItem.business_id == order_id,
                TodoItem.assigned_user_id == current_user.id,
                TodoItem.status != "completed",
            )
        )
        .scalars()
        .first()
        is not None
    )
    return {"pending": pending, "business_type": business_type}


def _requires_settlement_collaboration(order: SalesOrder) -> bool:
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    return payment_type in {"先付定金后付尾款", "全款发货", "货到付款/分期付款"}


def _extract_receipt_merge_no(remark: str | None) -> str | None:
    text = str(remark or "").strip()
    if not text:
        return None
    match = RECEIPT_MERGE_NO_PATTERN.search(text)
    return match.group("merge_no") if match else None


@router.get("/flow-board")
def order_flow_board(
    tab: str = Query(default="all", pattern="^(all|reviewing|shipping|completed)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全链订单看板：聚合每个客户订单的 财务/置货/生产/采购/质检/入库/发货 状态。"""
    query = (
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user))
        .order_by(SalesOrder.created_at.desc())
    )
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        if current_user.id not in scoped_user_ids:
            scoped_user_ids = [*scoped_user_ids, current_user.id]
        query = query.where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    tab_filters = {
        "reviewing": SalesOrder.status.in_(["pending_confirmation", "pending_price_approval", "rejected"]),
        "shipping": SalesOrder.status.in_(["pending_shipment", "shipped"]),
        "completed": SalesOrder.status.in_(["completed", "cancelled"]),
        "all": True,
    }
    query = query.where(tab_filters[tab])
    total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    order_ids = [row.id for row in rows]

    # 修复缺失的全链快照：对没有快照的订单实时同步生成（历史订单一次性补齐）
    existing_snapshot_ids = {
        r[0] for r in db.execute(
            select(DocumentStatusSnapshot.document_id).where(
                DocumentStatusSnapshot.document_type == "sales_order",
                DocumentStatusSnapshot.document_id.in_(order_ids or [-1]),
            )
        ).all()
    }
    missing_order_ids = [oid for oid in order_ids if oid not in existing_snapshot_ids]
    if missing_order_ids:
        for oid in missing_order_ids:
            missing_order = next((r for r in rows if r.id == oid), None)
            if missing_order is not None:
                try:
                    sync_sales_order_process_status(db, missing_order)
                except Exception:
                    pass
        db.commit()

    # 批量读全链状态快照
    snapshots = (
        db.execute(
            select(DocumentStatusSnapshot).where(
                DocumentStatusSnapshot.document_type == "sales_order",
                DocumentStatusSnapshot.document_id.in_(order_ids or [-1]),
            )
        ).scalars().all()
        if order_ids
        else []
    )
    snapshot_map: dict[int, dict[str, dict]] = {}
    for snap in snapshots:
        snapshot_map.setdefault(snap.document_id, {})[snap.status_dimension] = {
            "stage": snap.current_stage,
            "code": snap.status_code,
            "reason": snap.blocked_reason,
            "updated_at": snap.status_updated_at.isoformat() if snap.status_updated_at else None,
        }

    receivables = (
        db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids)).order_by(ReceivableRecord.id.desc())).scalars().all()
        if order_ids
        else []
    )
    received_amount_map = _received_amount_map(receivables)

    DIM_ORDER = ["approval", "finance", "placement", "production", "procurement", "quality", "inbound", "shipment"]
    DIM_LABELS = {
        "approval": "审批", "finance": "财务", "placement": "置货", "production": "生产",
        "procurement": "采购", "quality": "质检", "inbound": "入库", "shipment": "发货",
    }
    items = []
    for row in rows:
        dims = snapshot_map.get(row.id, {})
        stages = {d: (dims.get(d) or {}).get("stage", "") for d in DIM_ORDER}
        # 汇总当前环节：取第一个未完成的关键环节
        current_stage = "created"
        for d in DIM_ORDER:
            s = stages.get(d, "")
            if s and s not in ("completed", "done", "passed", "paid", "cleared", "approved", "shipped", "not_required", "settled", "received"):
                current_stage = d
                break
        else:
            current_stage = "completed" if row.status == "completed" else "shipment"

        items.append({
            "id": row.id,
            "order_no": row.order_no,
            "customer_name": row.customer.name if row.customer else None,
            "sales_user": row.sales_user.full_name if row.sales_user else None,
            "status": row.status,
            "status_label": status_label(row.status),
            "total_amount": float(row.total_amount),
            "received_amount": float(received_amount_map.get(row.id, Decimal("0"))),
            "settlement_status": _settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0"))),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "current_stage": current_stage,
            "current_stage_label": DIM_LABELS.get(current_stage, "已完成"),
            "dimensions": [
                {
                    "key": d,
                    "label": DIM_LABELS[d],
                    "stage": stages.get(d, ""),
                    "reason": (dims.get(d) or {}).get("reason"),
                    "updated_at": (dims.get(d) or {}).get("updated_at"),
                }
                for d in DIM_ORDER
            ],
        })
    return api_response(
        data={
            "items": items,
            "pagination": {"page": page, "page_size": page_size, "total": total},
        }
    )


@router.get("/orders")
def list_orders(
    tab: str = Query(default="draft", pattern="^(draft|reviewing|shipping|completed|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user), joinedload(SalesOrder.operator_user), joinedload(SalesOrder.approved_by), joinedload(SalesOrder.parent_order), joinedload(SalesOrder.receipt_account))
        .order_by(SalesOrder.created_at.desc())
    )
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        # A salesperson must always be able to see orders they submitted, even
        # when department-based scope resolution has no matching rows.
        if current_user.id not in scoped_user_ids:
            scoped_user_ids = [*scoped_user_ids, current_user.id]
        query = query.where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    # draft 必须单独成一个 tab:建单先落 draft,若任何 tab 都不含它,
    # 销售保存草稿后订单就从列表消失,再也无法编辑/提交/删除。
    tab_filters = {
        "draft": SalesOrder.status.in_(["draft"]),
        "reviewing": SalesOrder.status.in_(["pending_confirmation", "pending_price_approval", "rejected"]),
        "shipping": SalesOrder.status.in_(["pending_shipment", "shipped"]),
        "completed": SalesOrder.status.in_(["completed", "cancelled"]),
        "all": True,
    }
    query = query.where(tab_filters[tab])
    total = db.execute(select(func.count()).select_from(query.order_by(None).subquery())).scalar_one()
    # 各 tab 的单据数(供前端页签角标使用:原先前端用当页行数充当总数,
    # 未选中的页签恒显示 0,选中的也只显示当页条数)
    tab_counts = {
        key: db.execute(
            select(func.count()).select_from(
                select(SalesOrder.id)
                .where(
                    SalesOrder.sales_user_id.in_(scoped_user_ids) if scoped_user_ids is not None else True,
                    condition,
                )
                .subquery()
            )
        ).scalar_one()
        for key, condition in tab_filters.items()
    }
    rows = db.execute(
        query.offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    order_ids = [row.id for row in rows]
    receivables = (
        db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids)).order_by(ReceivableRecord.id.desc())).scalars().all()
        if order_ids
        else []
    )
    received_amount_map = _received_amount_map(receivables)

    # 全链状态快照（供列表迷你进度条展示）
    snapshots = (
        db.execute(
            select(DocumentStatusSnapshot).where(
                DocumentStatusSnapshot.document_type == "sales_order",
                DocumentStatusSnapshot.document_id.in_(order_ids or [-1]),
            )
        ).scalars().all()
        if order_ids
        else []
    )
    snapshot_map: dict[int, dict[str, str]] = {}
    for snap in snapshots:
        snapshot_map.setdefault(snap.document_id, {})[snap.status_dimension] = snap.current_stage

    DIM_ORDER = ["approval", "finance", "placement", "production", "procurement", "quality", "inbound", "shipment"]
    DIM_LABELS = {
        "approval": "审批", "finance": "财务", "placement": "置货", "production": "生产",
        "procurement": "采购", "quality": "质检", "inbound": "入库", "shipment": "发货",
    }

    def _flow_dims(order_id: int) -> list[dict]:
        dims = snapshot_map.get(order_id, {})
        return [
            {"key": d, "label": DIM_LABELS[d], "stage": dims.get(d, "")}
            for d in DIM_ORDER
        ]

    return api_response(
        data={
            "items": [
            {
                "id": row.id,
                "order_no": row.order_no,
                "parent_order_id": row.parent_order_id,
                "parent_order_no": row.parent_order.order_no if row.parent_order else None,
                "split_group_no": row.split_group_no,
                "split_phase": row.split_phase,
                "is_split_child": bool(row.parent_order_id),
                "customer_name": row.customer.name if row.customer else None,
                "sales_user": row.sales_user.full_name if row.sales_user else None,
                "operator_user_id": row.operator_user_id,
                "operator_name": row.approved_by.full_name if row.approved_by else None,
                "order_remark": row.order_remark,
                "status": row.status,
                "status_label": status_label(row.status),
                "price_approval_status": row.price_approval_status,
                "price_approval_status_label": status_label(row.price_approval_status),
                "finance_edit_pending": bool(row.finance_edit_pending),
                "finance_edit_confirm_state": _finance_edit_confirm_state(db, row.id, current_user),
                "payment_type": row.payment_type,
                "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
                "receipt_account_id": row.receipt_account_id,
                "receipt_account_label": account_label(row.receipt_account),
                "order_type": row.order_type,
                "order_type_label": order_type_style(row.order_type)["label"],
                "order_type_color": order_type_style(row.order_type)["color"],
                "expected_delivery_date": row.expected_delivery_date.isoformat() if row.expected_delivery_date else None,
                "total_amount": float(row.total_amount),
                "deposit_amount": float(row.deposit_amount),
                "received_amount": float(received_amount_map.get(row.id, Decimal("0"))),
                "remaining_amount": float(max(Decimal(row.total_amount or 0) - received_amount_map.get(row.id, Decimal("0")), Decimal("0"))),
                "settlement_status": _settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0"))),
                "settlement_status_label": status_label(_settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0")))),
                "flow_dims": _flow_dims(row.id),
                "is_completed_order": row.status == "completed",
                "can_mark_completed": row.status == "shipped" and _settlement_status(Decimal(row.total_amount or 0), received_amount_map.get(row.id, Decimal("0"))) == "settled",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
            ],
            "pagination": {"page": page, "page_size": page_size, "total": total},
            "tab_counts": tab_counts,
        }
    )


@router.get("/orders/{order_id}")
def get_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.operator_user),
            joinedload(SalesOrder.approved_by),
            joinedload(SalesOrder.parent_order),
            joinedload(SalesOrder.receipt_account),
        )
        .where(SalesOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_sales_scope(db, current_user, order.sales_user_id)

    # 不完整草稿只需要回填编辑表单；不要再走已提交订单的结算、关联单据和流程汇总，
    # 否则空客户/空明细会被旧详情逻辑当作完整订单处理而报 500。
    if order.status == "draft" and (order.ext_json or {}).get("draft_payload") is not None:
        return api_response(
            data={
                "id": order.id,
                "order_no": order.order_no,
                "status": order.status,
                "status_label": status_label(order.status),
                "customer": {"id": order.customer.id, "name": order.customer.name} if order.customer else None,
                "operator_user_id": order.operator_user_id,
                "consignee": order.consignee,
                "consignee_phone": order.consignee_phone,
                "delivery_address": order.delivery_address,
                "payment_type": order.payment_type,
                "order_type": order.order_type or "normal",
                "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
                "deposit_amount": float(order.deposit_amount or 0),
                "transport_fee": float(order.transport_fee or 0),
                "order_remark": order.order_remark,
                "items": [],
                "draft_payload": (order.ext_json or {}).get("draft_payload"),
            }
        )
    receivables = db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id == order.id)).scalars().all()
    received_amount = _received_amount_map(receivables).get(order.id, Decimal("0"))
    settlement_status = _settlement_status(Decimal(order.total_amount or 0), received_amount)

    child_orders = db.execute(
        select(SalesOrder).where(SalesOrder.parent_order_id == order.id).order_by(SalesOrder.split_phase.asc(), SalesOrder.id.asc())
    ).scalars().all()
    return api_response(
        data={
            "id": order.id,
            "order_no": order.order_no,
            "parent_order_id": order.parent_order_id,
            "parent_order_no": order.parent_order.order_no if order.parent_order else None,
            "split_group_no": order.split_group_no,
            "split_phase": order.split_phase,
            "child_orders": [
                {"id": row.id, "order_no": row.order_no, "split_phase": row.split_phase, "status": row.status, "status_label": status_label(row.status)}
                for row in child_orders
            ],
            "status": order.status,
            "status_label": status_label(order.status),
            "price_approval_status": order.price_approval_status,
            "price_approval_status_label": status_label(order.price_approval_status),
            "finance_edit_pending": bool(order.finance_edit_pending),
            "price_approved_by_id": order.price_approved_by_id,
            "price_approved_at": order.price_approved_at.isoformat() if order.price_approved_at else None,
            "customer": {"id": order.customer.id, "name": order.customer.name} if order.customer else None,
            "draft_payload": (order.ext_json or {}).get("draft_payload") if order.status == "draft" else None,
            "operator_user_id": order.operator_user_id,
            "operator_name": order.approved_by.full_name if order.approved_by else None,
            "consignee": order.consignee,
            "consignee_phone": order.consignee_phone,
            "delivery_address": order.delivery_address,
            "order_remark": order.order_remark,
            "order_type": order.order_type,
            "order_type_label": order_type_style(order.order_type)["label"],
            "order_type_color": order_type_style(order.order_type)["color"],
            "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
            "payment_type": order.payment_type,
            "payment_type_label": payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales"),
            "receipt_account_id": order.receipt_account_id,
            "receipt_account_label": account_label(order.receipt_account),
            "total_amount": float(order.total_amount),
            "deposit_amount": float(order.deposit_amount),
            "received_amount": float(received_amount),
            "remaining_amount": float(max(Decimal(order.total_amount or 0) - received_amount, Decimal("0"))),
            "settlement_status": settlement_status,
            "settlement_status_label": status_label(settlement_status),
            "flow_steps": sales_flow_steps(order, settlement_status=settlement_status),
            "related_documents": _order_related_documents(db, order.id),
            "transport_fee": float(order.transport_fee),
            "packaging_fee": float(order.packaging_fee),
            "finance_edit_snapshot": _finance_edit_snapshot(db, order),
            "finance_edit_confirm_state": _finance_edit_confirm_state(db, order.id, current_user),
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else None,
                    "quantity": float(item.quantity),
                    "shipped_quantity": float(item.shipped_quantity or 0),
                    "remaining_shipment_quantity": float(max(Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0), Decimal("0"))),
                    "unit_price": float(item.unit_price),
                    "total_amount": float(item.total_amount),
                    "packaging_config_id": item.packaging_config_id,
                    "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                    "display_specification": item.display_specification,
                    "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                    "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                    "packaging_unit_price": float(item.packaging_unit_price or 0),
                    "packaging_fee": float(item.packaging_fee or 0),
                    "remark": item.remark,
                }
                for item in order.items
            ],
            "price_change_logs": [
                {
                    "id": row.id,
                    "sales_order_item_id": row.sales_order_item_id,
                    "product_id": row.product_id,
                    "product_name": row.product.name if row.product else None,
                    "standard_price": float(row.standard_price or 0),
                    "actual_price": float(row.actual_price or 0),
                    "remark": row.remark,
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "changed_by_name": row.changed_by.full_name if row.changed_by else None,
                    "approved_by_name": row.approved_by.full_name if row.approved_by else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in db.execute(
                    select(SalesOrderPriceChangeLog)
                    .options(joinedload(SalesOrderPriceChangeLog.product), joinedload(SalesOrderPriceChangeLog.changed_by), joinedload(SalesOrderPriceChangeLog.approved_by))
                    .where(SalesOrderPriceChangeLog.sales_order_id == order.id)
                    .order_by(SalesOrderPriceChangeLog.created_at.desc(), SalesOrderPriceChangeLog.id.desc())
                ).scalars().all()
            ],
        }
    )


# 全链泳道图顺序(业务流转顺序,与看板各维度一一对应)
SALES_FLOW_LANES = [
    ("approval", "销售审批"),
    ("finance", "财务收款"),
    ("placement", "置货备货"),
    ("procurement", "采购补货"),
    ("production", "生产制造"),
    ("quality", "质检"),
    ("inbound", "入库"),
    ("shipment", "发货出库"),
]


# 关联单据类型 -> 单据号 解析(时间轴事件上展示关联单号,便于跳转定位)
_DOCUMENT_NO_RESOLVERS = {
    "fulfillment_demand": (FulfillmentDemand, "demand_no"),
    "production_task": (ProductionTask, "task_no"),
    "purchase_order": (PurchaseOrder, "order_no"),
    "quality_task": (QualityTask, "quality_no"),
    "sales_receipt_request": (SalesReceiptRequest, "request_no"),
    "purchase_receipt": (PurchaseReceipt, "receipt_no"),
}


def _related_document_no(db: Session, document_type: str | None, document_id: int | None) -> str | None:
    if not document_type or not document_id:
        return None
    resolver = _DOCUMENT_NO_RESOLVERS.get(document_type)
    if not resolver:
        return None
    model, no_field = resolver
    row = db.get(model, document_id)
    if row is None:
        return None
    return str(getattr(row, no_field) or "")


@router.get("/orders/{order_id}/timeline")
def get_order_timeline(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """订单全链时间轴:真实业务任务树 + 状态事件明细。"""
    order = db.execute(
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.operator_user))
        .where(SalesOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_sales_scope(db, current_user, order.sales_user_id)

    history_asc = list(
        reversed(get_document_status_history(db, document_type="sales_order", document_id=order.id))
    )
    snapshots = {
        snapshot.status_dimension: snapshot
        for snapshot in get_document_status_snapshots(db, document_type="sales_order", document_id=order.id)
    }

    user_ids = {row.operator_user_id for row in history_asc if row.operator_user_id}
    user_names: dict[int, str] = {}
    if user_ids:
        for user in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all():
            user_names[user.id] = user.full_name or user.username

    tasks, submitted_at = build_sales_order_tasks(db, order, history_asc)
    fallback_operator_name = (order.operator_user.full_name or order.operator_user.username) if order.operator_user else None
    events_by_dimension: dict[str, list[dict]] = defaultdict(list)
    for row in history_asc:
        events_by_dimension[row.status_dimension].append(
            {
                "id": row.id,
                "from_stage": row.from_stage,
                "from_stage_label": status_label(row.from_stage),
                "to_stage": row.to_stage,
                "to_stage_label": status_label(row.to_stage),
                "reason_text": row.reason_text,
                # 自动同步的状态沿用发起销售的姓名，避免列表中只出现“系统”而无法追责。
                "operator_name": user_names.get(row.operator_user_id) or fallback_operator_name or "系统自动",
                "related_document_type": row.related_document_type,
                "related_document_id": row.related_document_id,
                "related_document_no": _related_document_no(
                    db, row.related_document_type, row.related_document_id
                ),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    lanes = []
    for key, label in SALES_FLOW_LANES:
        snapshot = snapshots.get(key)
        lanes.append(
            {
                "key": key,
                "label": label,
                "current_stage": snapshot.current_stage if snapshot else None,
                "current_stage_label": status_label(snapshot.current_stage) if snapshot else None,
                "status_updated_at": snapshot.status_updated_at.isoformat() if snapshot and snapshot.status_updated_at else None,
                "blocked_reason": snapshot.blocked_reason if snapshot else None,
                "not_required": bool(snapshot and snapshot.current_stage == "not_required"),
                "events": events_by_dimension.get(key, []),
            }
        )

    fulfillment = snapshots.get("fulfillment")
    def task_times(nodes):
        for node in nodes:
            yield node["start_at"]
            if node["end_at"]:
                yield node["end_at"]
            yield from task_times(node["children"])

    timeline_end = max(task_times(tasks), default=None)
    return api_response(
        data={
            "order": {
                "id": order.id,
                "order_no": order.order_no,
                "status": order.status,
                "status_label": status_label(order.status),
                "order_type": order.order_type,
                "order_type_label": order_type_style(order.order_type)["label"],
                "order_type_color": order_type_style(order.order_type)["color"],
                "customer_name": order.customer.name if order.customer else None,
                "sales_user": order.operator_user.full_name if order.operator_user else None,
                "payment_type_label": payment_type_label(
                    order.payment_type, deposit_amount=order.deposit_amount, module="sales"
                ),
                "total_amount": float(order.total_amount or 0),
                "expected_delivery_date": order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "submitted_at": submitted_at.isoformat() if submitted_at else None,
                "updated_at": order.updated_at.isoformat() if order.updated_at else None,
            },
            "overall_stage": fulfillment.current_stage if fulfillment else order.status,
            "overall_stage_label": status_label(fulfillment.current_stage) if fulfillment else status_label(order.status),
            "overall_blocked_reason": fulfillment.blocked_reason if fulfillment else None,
            "lanes": lanes,
            "tasks": tasks,
            "relations": _order_related_documents(db, order.id),
            "time_start": submitted_at.isoformat() if submitted_at else None,
            "time_end": timeline_end,
        }
    )


@router.post("/orders")
def create_order(payload: SalesOrderDraftPayload, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    return create_order_service(payload, current_user, db)


@router.post("/orders/{order_id}/submit")
def submit_order(
    order_id: int,
    payload: SalesOrderSubmitRequest | None = None,
    current_user: User = Depends(require_roles("sales")),
    db: Session = Depends(get_db),
):
    """草稿/已驳回订单提交财务审批(可指定处理该单的财务人员)。"""
    return submit_order_service(
        order_id,
        current_user,
        db,
        assigned_finance_user_id=payload.assigned_finance_user_id if payload else None,
        update_customer_address=payload.update_customer_address if payload else False,
        customer_address_before=payload.customer_address_before if payload else None,
    )


@router.put("/orders/{order_id}")
def update_order(order_id: int, payload: SalesOrderDraftPayload, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    return update_order_service(order_id, payload, current_user, db)


@router.put("/orders/{order_id}/finance-edit")
def finance_edit_order(
    order_id: int,
    payload: SalesOrderFinanceEditRequest,
    current_user: User = Depends(require_roles("finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return finance_edit_order_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/finance-edit/confirm")
def confirm_finance_edit_order(
    order_id: int,
    payload: SalesFinanceEditConfirmRequest,
    current_user: User = Depends(require_roles("sales", "inventory", "system_admin")),
    db: Session = Depends(get_db),
):
    return confirm_finance_edit_order_service(order_id, current_user, db, note=payload.note)


@router.delete("/orders/{order_id}")
def delete_order(order_id: int, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.sales_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可删除本人订单")
    if order.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail="仅草稿或已驳回订单允许删除")

    # 下游单据检查:有生产任务/采购单/退货单的订单不允许删除
    item_ids = [item.id for item in order.items]
    downsteam_count = 0
    if item_ids:
        downsteam_count += db.execute(
            select(func.count()).select_from(FulfillmentDemand).where(
                FulfillmentDemand.sales_order_item_id.in_(item_ids),
                FulfillmentDemand.status != "pending",
            )
        ).scalar_one()
    downsteam_count += db.execute(
        select(func.count()).select_from(SalesReturn).where(SalesReturn.sales_order_id == order.id)
    ).scalar_one()
    if downsteam_count:
        raise HTTPException(status_code=409, detail="订单已产生生产/采购/退货下游单据,不允许删除")

    # 清理补货需求引用(历史数据可能挂明细外键)
    demand_where = (
        [FulfillmentDemand.sales_order_id == order.id]
        if not item_ids
        else [FulfillmentDemand.sales_order_id == order.id, FulfillmentDemand.sales_order_item_id.in_(item_ids)]
    )
    for demand in db.execute(select(FulfillmentDemand).where(*demand_where)).scalars().all():
        db.delete(demand)

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue:
        db.delete(queue)
    for approval in db.execute(
        select(SalesOrderApproval).where(SalesOrderApproval.sales_order_id == order.id)
    ).scalars().all():
        db.delete(approval)
    for todo in db.execute(
        select(TodoItem).where(
            or_(
                and_(TodoItem.business_type.in_(["sales_order", "sales_order_approval", "sales_order_price_approval"]), TodoItem.business_id == order.id),
                and_(TodoItem.business_type == "sales_order_material_replenishment", TodoItem.business_id == order.id),
            )
        )
    ).scalars().all():
        db.delete(todo)
    db.delete(order)
    db.commit()
    return api_response(msg="客户订单已删除")


@router.get("/approvals/pending")
def pending_approvals(
    current_user: User = Depends(require_roles("finance", "finance_super", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    query = (
        select(SalesOrder)
        .options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user))
        .where(SalesOrder.status.in_(["pending_confirmation", "pending_price_approval"]))
        .order_by(SalesOrder.updated_at.desc(), SalesOrder.id.desc())
    )
    # 已指定财务的订单只展示给该处理人；未指定的历史订单仍由全体财务承接。
    role_code = current_user.role.code if current_user.role else ""
    if role_code in {"finance", "finance_super"}:
        query = query.where(
            or_(
                SalesOrder.assigned_finance_user_id == current_user.id,
                SalesOrder.assigned_finance_user_id.is_(None),
            )
        )
    rows = db.execute(query).scalars().all()
    return api_response(
        data=[
            {
                "id": row.id,
                "order_no": row.order_no,
                "customer_name": row.customer.name if row.customer else None,
                "sales_user": row.sales_user.full_name if row.sales_user else None,
                "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
                "receipt_account_label": account_label(row.receipt_account),
                "total_amount": float(row.total_amount or 0),
                "deposit_amount": float(row.deposit_amount or 0),
                "receipt_amount": float(
                    row.deposit_amount if payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales") == "先收定金后收尾款"
                    else (0 if payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales") == "先发货后收款" else row.total_amount or 0)
                ),
                "receivable_amount": float(
                    max(Decimal(row.total_amount or 0) - Decimal(row.deposit_amount or 0), Decimal("0"))
                    if payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales") == "先收定金后收尾款"
                    else (Decimal(row.total_amount or 0) if payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales") == "先发货后收款" else Decimal("0"))
                ),
                "expected_delivery_date": row.expected_delivery_date.isoformat() if row.expected_delivery_date else None,
                "order_type": row.order_type,
                "order_type_label": order_type_style(row.order_type).get("label"),
                "order_type_color": order_type_style(row.order_type).get("color"),
                "status": row.status,
                "status_label": status_label(row.status),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "rejection_reason": row.rejection_reason,
            }
            for row in rows
        ]
    )


@router.get("/approvals/history")
def approval_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str = Query(default=""),
    current_user: User = Depends(require_roles("finance", "finance_super", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    query = select(SalesOrderApproval, SalesOrder, User).join(
        SalesOrder, SalesOrderApproval.sales_order_id == SalesOrder.id
    ).join(User, SalesOrderApproval.approver_user_id == User.id)
    role_code = current_user.role.code if current_user.role else ""
    if role_code in {"finance", "finance_super"}:
        query = query.where(or_(SalesOrder.assigned_finance_user_id == current_user.id,
                                SalesOrder.assigned_finance_user_id.is_(None),
                                SalesOrderApproval.approver_user_id == current_user.id))
    if keyword.strip():
        query = query.where(SalesOrder.order_no.ilike(f"%{keyword.strip()}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    records = db.execute(query.order_by(SalesOrderApproval.approved_at.desc(), SalesOrderApproval.id.desc())
                         .offset((page - 1) * page_size).limit(page_size)).all()
    return api_response(data={"total": total, "page": page, "page_size": page_size, "items": [
        {"id": approval.id, "order_id": order.id, "order_no": order.order_no,
         "decision": approval.decision, "note": approval.note,
         "approval_mode": "转为应收" if (approval.note or "").startswith("转为应收") else "收款确认",
         "order_amount": float(order.total_amount or 0),
         "approver_name": approver.full_name or approver.username,
         "approved_at": approval.approved_at.isoformat() if approval.approved_at else None}
        for approval, order, approver in records
    ]})


@router.post("/orders/{order_id}/approve")
def approve_order(
    order_id: int,
    payload: SalesApprovalRequest,
    current_user: User = Depends(require_roles("finance", "finance_super", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_order_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/convert-to-receivable")
def convert_order_to_receivable(
    order_id: int,
    current_user: User = Depends(require_roles("finance", "finance_super", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    """财务将待审批订单转为应收并放行后续置货/发货流程。"""
    return approve_order_service(
        order_id,
        SalesApprovalRequest(decision="approved", convert_to_receivable=True, note="转为应收放行"),
        current_user,
        db,
    )


@router.post("/orders/{order_id}/price-approval")
def approve_price_change(
    order_id: int,
    payload: SalesPriceApprovalRequest,
    current_user: User = Depends(require_roles("finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_sales_price_change_service(order_id, payload, current_user, db)


@router.post("/orders/{order_id}/complete")
def complete_order(order_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return complete_order_service(order_id, current_user, db)


@router.post("/settlements/receipt-requests")
def submit_sales_receipt_request(
    payload: SalesReceiptRequestCreate,
    current_user: User = Depends(require_roles("sales", "finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return submit_sales_receipt_request_service(payload, current_user, db)


@router.post("/settlements/receipt-requests/merged")
def submit_merged_sales_receipt_request(
    payload: SalesMergedReceiptRequestCreate,
    current_user: User = Depends(require_roles("sales", "finance", "system_admin")),
    db: Session = Depends(get_db),
):
    return submit_merged_sales_receipt_request_service(payload, current_user, db)


@router.post("/settlements/receipt-requests/{request_id}/approve")
def approve_sales_receipt_request(
    request_id: int,
    payload: SalesReceiptApprovalRequest,
    current_user: User = Depends(require_roles("chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_sales_receipt_request_service(request_id, payload, current_user, db)


@router.get("/settlements")
def list_sales_settlements(
    current_user: User = Depends(require_roles("sales", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    query = (
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.parent_order),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
            joinedload(SalesOrder.receipt_account),
        )
        .where(SalesOrder.status != "cancelled")
        .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
    )
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        query = query.where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    orders = db.execute(query).unique().scalars().all()
    order_ids = [row.id for row in orders]

    receivables = (
        db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids)).order_by(ReceivableRecord.received_at.desc(), ReceivableRecord.id.desc())).scalars().all()
        if order_ids
        else []
    )
    receipt_requests = (
        db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.sales_order_id.in_(order_ids)).order_by(SalesReceiptRequest.created_at.desc(), SalesReceiptRequest.id.desc())).scalars().all()
        if order_ids
        else []
    )
    records_user_ids = [row.received_by_id for row in receivables if row.received_by_id]
    records_user_ids.extend([row.requested_by_id for row in receipt_requests if row.requested_by_id])
    records_user_ids.extend([row.approved_by_id for row in receipt_requests if row.approved_by_id])

    sales_returns = (
        db.execute(select(SalesReturn).where(SalesReturn.sales_order_id.in_(order_ids)).order_by(SalesReturn.created_at.desc(), SalesReturn.id.desc())).scalars().all()
        if order_ids
        else []
    )
    return_ids = [row.id for row in sales_returns]
    refunds = (
        db.execute(select(RefundRecord).where(RefundRecord.sales_return_id.in_(return_ids)).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())).scalars().all()
        if return_ids
        else []
    )
    records_user_ids.extend([row.processed_by_id for row in refunds if row.processed_by_id])
    user_map = {
        row.id: row
        for row in db.execute(select(User).where(User.id.in_(list({user_id for user_id in records_user_ids if user_id})))).scalars().all()
    } if records_user_ids else {}
    return_map = {row.id: row for row in sales_returns}
    received_amount_map = _received_amount_map(receivables)

    notifications = db.execute(
        select(NotificationRecipient)
        .options(joinedload(NotificationRecipient.notification))
        .join(Notification, Notification.id == NotificationRecipient.notification_id)
        .where(NotificationRecipient.user_id == current_user.id, Notification.module.in_(["sales", "finance"]))
        .order_by(NotificationRecipient.created_at.desc(), NotificationRecipient.id.desc())
    ).scalars().all()

    order_rows = []
    completed_order_rows = []
    for row in orders:
        total_amount = Decimal(row.total_amount or 0)
        received_amount = received_amount_map.get(row.id, Decimal("0"))
        settlement_status = _settlement_status(total_amount, received_amount)
        if not _requires_settlement_collaboration(row):
            continue
        serialized = {
            "id": row.id,
            "order_no": row.order_no,
            "parent_order_no": row.parent_order.order_no if row.parent_order else None,
            "split_phase": row.split_phase,
            "customer_name": row.customer.name if row.customer else None,
            "customer_id": row.customer_id,
            "status": row.status,
            "status_label": status_label(row.status),
            "payment_type_label": payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales"),
            "receipt_account_id": row.receipt_account_id,
            "receipt_account_label": account_label(row.receipt_account),
            "total_amount": float(total_amount),
            "deposit_amount": float(row.deposit_amount or 0),
            "received_amount": float(received_amount),
            "remaining_amount": float(max(total_amount - received_amount, Decimal("0"))),
            "settlement_status": settlement_status,
            "settlement_status_label": status_label(settlement_status),
            "order_remark": row.order_remark,
            "settlement_completed": settlement_status == "settled",
            "can_mark_completed": row.status == "shipped" and settlement_status == "settled",
            "items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product.name if item.product else None,
                    "product_code": item.product.product_code if item.product else None,
                    "quantity": float(item.quantity or 0),
                    "shipped_quantity": float(item.shipped_quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "total_amount": float(item.total_amount or 0),
                    "display_specification": item.display_specification,
                    "packaging_config_id": item.packaging_config_id,
                    "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                    "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                    "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                    "packaging_unit_price": float(item.packaging_unit_price or 0),
                    "packaging_fee": float(item.packaging_fee or 0),
                }
                for item in row.items
            ],
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if row.status == "completed":
            completed_order_rows.append(serialized)
        else:
            order_rows.append(serialized)

    return api_response(
        data={
            "summary": {
                "order_count": len(order_rows),
                "receipt_request_count": len(receipt_requests),
                "receivable_count": len(receivables),
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
            "orders": order_rows,
            "completed_orders": completed_order_rows,
            "receivables": [
                {
                    "id": row.id,
                    "sales_order_id": row.sales_order_id,
                    "sales_order_no": next((order.order_no for order in orders if order.id == row.sales_order_id), None),
                    "amount": float(row.amount),
                    "payment_stage": row.payment_stage,
                    "payment_stage_label": payment_stage_label(row.payment_stage),
                    "received_at": row.received_at.isoformat() if row.received_at else None,
                    "received_by_name": user_map[row.received_by_id].full_name if row.received_by_id in user_map else None,
                    "remark": row.remark,
                    "remark_display": format_legacy_remark(row.remark, business_type="receivable"),
                }
                for row in receivables
            ],
            "receipt_requests": [
                {
                    "id": row.id,
                    "request_no": row.request_no,
                    **settlement_account_fields(row),
                    "sales_order_id": row.sales_order_id,
                    "sales_order_no": next((order.order_no for order in orders if order.id == row.sales_order_id), None),
                    "requested_amount": float(row.requested_amount or 0),
                    "actual_amount": float(row.actual_amount or 0),
                    "payment_stage": row.payment_stage,
                    "payment_stage_label": payment_stage_label(row.payment_stage),
                    "installment_total": row.installment_total,
                    "installment_index": row.installment_index,
                    "status": row.status,
                    "status_label": status_label(row.status),
                    "merge_no": _extract_receipt_merge_no(row.remark),
                    "remark": row.remark,
                    "approval_remark": row.approval_remark,
                    "requested_by_name": user_map[row.requested_by_id].full_name if row.requested_by_id in user_map else None,
                    "approved_by_name": user_map[row.approved_by_id].full_name if row.approved_by_id in user_map else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in receipt_requests
            ],
            "refunds": [
                {
                    "id": row.id,
                    "sales_return_id": row.sales_return_id,
                    "sales_return_no": return_map[row.sales_return_id].return_no if row.sales_return_id in return_map else None,
                    "sales_order_id": return_map[row.sales_return_id].sales_order_id if row.sales_return_id in return_map else None,
                    "sales_order_no": next((order.order_no for order in orders if row.sales_return_id in return_map and order.id == return_map[row.sales_return_id].sales_order_id), None),
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


@router.get("/returns")
def list_returns(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(SalesReturn).order_by(SalesReturn.created_at.desc())
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        query = query.where(SalesReturn.requested_by_id.in_(scoped_user_ids))
    rows = db.execute(query).scalars().all()
    order_ids = [row.sales_order_id for row in rows]
    return_ids = [row.id for row in rows]
    orders = {row.id: row for row in db.execute(select(SalesOrder).where(SalesOrder.id.in_(order_ids))).scalars().all()} if order_ids else {}
    refunds = db.execute(select(RefundRecord).where(RefundRecord.sales_return_id.in_(return_ids))).scalars().all() if return_ids else []

    refund_map: dict[int, dict] = {}
    for row in refunds:
        bucket = refund_map.setdefault(row.sales_return_id, {"refunded_amount": Decimal("0"), "last_refunded_at": None})
        bucket["refunded_amount"] += Decimal(row.amount or 0)
        bucket["last_refunded_at"] = row.refunded_at.isoformat() if row.refunded_at else bucket["last_refunded_at"]

    return api_response(
        data=[
            {
                "id": row.id,
                "return_no": row.return_no,
                "sales_order_id": row.sales_order_id,
                "sales_order_no": orders[row.sales_order_id].order_no if row.sales_order_id in orders else None,
                "return_type": row.return_type,
                "return_type_label": return_type_label(row.return_type),
                "reason": clean_display_text(row.reason, "销售退换货申请"),
                "status": row.status,
                "status_label": status_label(row.status),
                "refund_amount": float(row.refund_amount),
                "refunded_amount": float(refund_map.get(row.id, {}).get("refunded_amount", 0)),
                "last_refunded_at": refund_map.get(row.id, {}).get("last_refunded_at"),
                "requires_reinspection": row.requires_reinspection,
                "reinspection_status": row.reinspection_status,
                "reinspection_status_label": status_label(row.reinspection_status),
                "review_note": clean_display_text(row.review_note, "") if row.review_note else "",
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/returns/{return_id}")
def get_return_detail(return_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sales_return = db.execute(select(SalesReturn).where(SalesReturn.id == return_id)).scalar_one_or_none()
    if sales_return is None:
        raise HTTPException(status_code=404, detail="销售退换货单不存在")
    _ensure_sales_scope(db, current_user, sales_return.requested_by_id)

    order = db.execute(select(SalesOrder).where(SalesOrder.id == sales_return.sales_order_id)).scalar_one_or_none()
    return_items = db.execute(select(SalesReturnItem).where(SalesReturnItem.sales_return_id == sales_return.id)).scalars().all()
    order_item_ids = [item.sales_order_item_id for item in return_items]
    order_items = {
        row.id: row
        for row in db.execute(
            select(SalesOrderItem).options(joinedload(SalesOrderItem.product)).where(SalesOrderItem.id.in_(order_item_ids))
        ).scalars().all()
    } if order_item_ids else {}
    refund_records = db.execute(
        select(RefundRecord).where(RefundRecord.sales_return_id == sales_return.id).order_by(RefundRecord.refunded_at.desc(), RefundRecord.id.desc())
    ).scalars().all()
    inventory_request = db.execute(
        select(InventoryOperationRequest)
        .where(
            InventoryOperationRequest.source_type == "sales_return",
            InventoryOperationRequest.source_id == sales_return.id,
            InventoryOperationRequest.request_category == "sales_return_inbound",
        )
        .order_by(InventoryOperationRequest.id.desc())
    ).scalars().first()

    user_ids = [user_id for user_id in {sales_return.requested_by_id, sales_return.approved_by_id} if user_id]
    user_ids.extend([row.processed_by_id for row in refund_records if row.processed_by_id])
    user_map = {row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()} if user_ids else {}
    inventory_transactions = db.execute(
        select(InventoryTransaction)
        .where(InventoryTransaction.related_type == "inventory_operation_request")
        .where(InventoryTransaction.related_id == (inventory_request.id if inventory_request else -1))
        .order_by(InventoryTransaction.created_at.asc(), InventoryTransaction.id.asc())
    ).scalars().all()
    warehouse_ids = [row.warehouse_id for row in inventory_transactions if row.warehouse_id]
    transaction_product_ids = [row.product_id for row in inventory_transactions if row.product_id]
    transaction_user_ids = [row.created_by_id for row in inventory_transactions if row.created_by_id]
    request_product_ids = [
        item.get("product_id")
        for item in ((inventory_request.handled_items_json or inventory_request.original_items_json or []) if inventory_request else [])
        if item.get("product_id")
    ]
    product_ids = list(dict.fromkeys([*transaction_product_ids, *request_product_ids]))
    warehouse_map = {row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids))).scalars().all()} if warehouse_ids else {}
    product_map = {row.id: row for row in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()} if product_ids else {}
    if transaction_user_ids:
        for row in db.execute(select(User).where(User.id.in_(transaction_user_ids))).scalars().all():
            user_map[row.id] = row

    return api_response(
        data={
            "id": sales_return.id,
            "return_no": sales_return.return_no,
            "sales_order_id": sales_return.sales_order_id,
            "sales_order_no": order.order_no if order else None,
            "requested_by_name": user_map[sales_return.requested_by_id].full_name if sales_return.requested_by_id in user_map else None,
            "approved_by_name": user_map[sales_return.approved_by_id].full_name if sales_return.approved_by_id in user_map else None,
            "return_type": sales_return.return_type,
            "return_type_label": return_type_label(sales_return.return_type),
            "reason": clean_display_text(sales_return.reason, "销售退换货申请"),
            "status": sales_return.status,
            "status_label": status_label(sales_return.status),
            "inventory_request_id": inventory_request.id if inventory_request else None,
            "inventory_request_no": inventory_request.request_no if inventory_request else None,
            "inventory_request_status": inventory_request.status if inventory_request else None,
            "inventory_request_status_label": status_label(inventory_request.status) if inventory_request else None,
            "refund_amount": float(sales_return.refund_amount),
            "requires_reinspection": sales_return.requires_reinspection,
            "reinspection_status": sales_return.reinspection_status,
            "reinspection_status_label": status_label(sales_return.reinspection_status),
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
            "review_note": clean_display_text(sales_return.review_note, "") if sales_return.review_note else "",
            "approved_at": sales_return.approved_at.isoformat() if sales_return.approved_at else None,
            "created_at": sales_return.created_at.isoformat() if sales_return.created_at else None,
            "items": [
                {
                    "id": item.id,
                    "sales_order_item_id": item.sales_order_item_id,
                    "product_id": order_items[item.sales_order_item_id].product_id if item.sales_order_item_id in order_items else None,
                    "product_name": order_items[item.sales_order_item_id].product.name if item.sales_order_item_id in order_items and order_items[item.sales_order_item_id].product else None,
                    "quantity": float(item.quantity),
                    "batch_suffix": item.batch_suffix,
                }
                for item in return_items
            ],
            "inventory_transactions": [
                {
                    "id": row.id,
                    "transaction_no": row.transaction_no,
                    "transaction_type": row.transaction_type,
                    "warehouse_name": warehouse_map[row.warehouse_id].name if row.warehouse_id in warehouse_map else None,
                    "product_name": product_map[row.product_id].name if row.product_id in product_map else None,
                    "quantity": float(row.quantity),
                    "unit_cost": float(row.unit_cost),
                    "total_cost": float(row.total_cost),
                    "batch_no": row.batch_no,
                    "operator_name": user_map[row.created_by_id].full_name if row.created_by_id in user_map else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in inventory_transactions
            ] or [
                {
                    "id": None,
                    "transaction_no": inventory_request.request_no if inventory_request else None,
                    "transaction_type": "sales_return_inbound",
                    "warehouse_name": None,
                    "product_name": product_map[item.get("product_id")].name if item.get("product_id") in product_map else None,
                    "quantity": float(item.get("quantity") or 0),
                    "unit_cost": float(item.get("unit_cost") or 0),
                    "total_cost": float(Decimal(item.get("quantity") or 0) * Decimal(item.get("unit_cost") or 0)),
                    "batch_no": item.get("batch_no"),
                    "operator_name": None,
                    "created_at": inventory_request.created_at.isoformat() if inventory_request and inventory_request.created_at else None,
                }
                for item in ((inventory_request.handled_items_json or inventory_request.original_items_json or []) if inventory_request else [])
            ],
        }
    )


@router.post("/returns")
def create_return(payload: SalesReturnCreate, current_user: User = Depends(require_roles("sales")), db: Session = Depends(get_db)):
    return create_return_service(payload, current_user, db)


@router.post("/returns/{return_id}/approve")
def approve_return(
    return_id: int,
    payload: SalesReturnApprovalRequest,
    current_user: User = Depends(require_roles("finance", "finance_super", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_return_service(return_id, payload, current_user, db)
