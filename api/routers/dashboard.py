from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user
from models import (
    CashFlowRecord,
    Inventory,
    InventoryDispatchQueue,
    Notification,
    NotificationRecipient,
    Product,
    ProductionTask,
    PurchaseOrder,
    QualityCostRecord,
    QualityDisposition,
    QualityTask,
    SalesOrder,
    TodoItem,
    User,
)
from responses import api_response
from schemas.dashboard import BatchIdsRequest
from services.core import complete_todos, sync_low_stock_alerts
from services.display import status_label
from services.organization import get_inventory_scope_warehouse_ids, get_scoped_user_ids
from services.realtime import realtime_manager
from services.time_utils import now_local

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

APPROVAL_TODO_TYPES = {
    "sales_order_approval": "sales_order",
    "purchase_order": "purchase_order",
    "purchase_payment_request": "purchase_payment_request",
    "sales_receipt_request": "sales_receipt_request",
    "sales_return_approval": "sales_return",
    "production_material_request": "production_material_request",
    "production_special_request": "production_special_request",
    "business_matter": "business_matter",
    "reimbursement": "reimbursement",
}

TODO_ROUTE_BY_BUSINESS_TYPE = {
    "inventory_operation_request": "/inventory/stocks",
    "fulfillment_demand": "/sales/orders",
    "purchase_payment_request": "/finance/payables",
    "purchase_order_confirm": "/purchases/orders",
    "sales_receipt_request": "/finance/receivables",
    "production_plan": "/production/tasks",
    "quality_cost_record": "/finance/quality-costs",
    "quality_scrap": "/quality/tasks",
    "sales_order": "/sales/orders",
    "sales_return": "/sales/returns",
    "purchase_return": "/purchases/returns",
    "production_task": "/production/tasks",
    "quality_task": "/quality/tasks",
    "receivable_plan": "/finance/receivables",
    "receivable_record": "/finance/receivables",
    "payable_record": "/finance/payables",
    "refund_record": "/finance/receivables",
    "stock_take_task": "/inventory/stock-takes",
    "low_stock_alert": "/inventory/stocks",
    "raw_material_replenishment": "/purchases/orders",
    "low_stock_product_material_replenishment": "/purchases/orders",
    "reimbursement_payment": "/finance/reimbursements",
}

TODO_ROUTE_BY_MODULE = {
    "sales": "/sales/orders",
    "inventory": "/inventory/queues",
    "purchase": "/purchases/orders",
    "production": "/production/tasks",
    "quality": "/quality/tasks",
    "finance": "/finance/quality-costs",
    "admin": "/admin/users",
    "master": "/products",
}


def _iso(value):
    return value.isoformat() if value else None


def _approval_id(todo: TodoItem) -> str | None:
    approval_type = APPROVAL_TODO_TYPES.get(todo.business_type)
    if approval_type is None or todo.business_id is None:
        return None
    return f"{approval_type}:{todo.business_id}"


def _todo_route(todo: TodoItem) -> tuple[str, dict]:
    if todo.business_type == "sales_order_approval" and todo.business_id is not None:
        return "/finance/receivables", {"focusId": str(todo.business_id), "focusType": "sales_order_approval", "mode": "register"}
    if todo.business_type == "purchase_payment_request" and todo.business_id is not None:
        if todo.module == "finance":
            return "/finance/payables", {"focusId": str(todo.business_id), "focusType": todo.business_type, "mode": "register"}
        approval_id = _approval_id(todo)
        return "/approvals", {"focusId": approval_id, "focusType": "purchase_payment_request", "mode": "approve"}
    if todo.business_type == "sales_receipt_request" and todo.business_id is not None:
        return "/finance/receivables", {"focusId": str(todo.business_id), "focusType": todo.business_type, "mode": "register"}
    if todo.business_type == "sales_order_price_approval" and todo.business_id is not None:
        return "/sales/orders", {"focusId": str(todo.business_id), "focusType": "sales_order_price_approval", "mode": "approve"}
    if todo.business_type == "sales_order_finance_edit_sales_confirm" and todo.business_id is not None:
        return "/sales/orders", {"focusId": str(todo.business_id), "focusType": todo.business_type, "mode": "confirm"}
    if todo.business_type == "sales_order_finance_edit_inventory_confirm" and todo.business_id is not None:
        return "/inventory/queues", {"focusId": str(todo.business_id), "focusType": todo.business_type, "mode": "confirm"}
    if todo.module == "production" and todo.business_type == "quality_task":
        return "/production/tasks", {"focusId": str(todo.business_id), "focusType": "quality_task"}
    if todo.business_type == "inventory_operation_request":
        return "/inventory/stocks", {"focusId": str(todo.business_id), "focusType": todo.business_type, "mode": "execute"}
    if todo.business_type == "reimbursement_payment" and todo.business_id is not None:
        return "/finance/reimbursements", {"focusId": str(todo.business_id), "focusType": todo.business_type, "mode": "pay"}
    if todo.module == "inventory" and todo.business_type in {"sales_order", "sales_order_placement"}:
        return "/inventory/queues", {}
    approval_id = _approval_id(todo)
    if approval_id:
        return "/approvals", {"focusId": approval_id, "focusType": approval_id.split(":", 1)[0], "mode": "readonly"}
    if todo.business_type == "fulfillment_demand" and todo.business_id is not None:
        route = "/purchases/orders" if todo.module == "purchase" else "/production/tasks"
        return route, {"focusId": str(todo.business_id), "focusType": todo.business_type}
    if todo.business_type == "low_stock_replenishment" and todo.business_id is not None:
        route = "/purchases/orders" if todo.module == "purchase" else "/production/tasks"
        return route, {"focusId": str(todo.id), "focusType": todo.business_type}
    if todo.business_type == "low_stock_product_material_replenishment" and todo.business_id is not None:
        return "/purchases/orders", {"focusId": str(todo.id), "focusType": todo.business_type}
    route = TODO_ROUTE_BY_BUSINESS_TYPE.get(todo.business_type) or TODO_ROUTE_BY_MODULE.get(todo.module) or "/dashboard"
    query: dict[str, str] = {}
    if todo.business_id is not None:
        query["focusId"] = str(todo.business_id)
    if todo.business_type:
        query["focusType"] = todo.business_type
    return route, query


def _serialize_todo(todo: TodoItem) -> dict:
    approval_id = _approval_id(todo)
    detail_route, detail_query = _todo_route(todo)
    is_action_routed = todo.business_type in {
        "sales_order_approval",
        "sales_receipt_request",
        "inventory_operation_request",
        "purchase_order_confirm",
        "sales_order_price_approval",
        "sales_order_finance_edit_sales_confirm",
        "sales_order_finance_edit_inventory_confirm",
        "reimbursement_payment",
    } or (todo.business_type == "purchase_payment_request" and todo.module == "finance")
    return {
        "id": todo.id,
        "title": todo.title,
        "description": todo.description,
        "module": todo.module,
        "status": todo.status,
        "status_label": status_label(todo.status),
        "business_type": todo.business_type,
        "business_id": todo.business_id,
        "created_at": _iso(todo.created_at),
        "completed_at": _iso(todo.completed_at),
        "approval_id": approval_id,
        "can_approve": approval_id is not None and not is_action_routed,
        "can_reject": approval_id is not None and not is_action_routed,
        "requires_action_route": is_action_routed,
        "detail_route": detail_route,
        "detail_query": detail_query,
        "detail_mode": "readonly" if approval_id else "default",
        "action_route": detail_route,
        "action_query": detail_query,
    }


def _serialize_notification(recipient: NotificationRecipient) -> dict:
    notification = recipient.notification
    return {
        "id": recipient.id,
        "notification_id": recipient.notification_id,
        "title": notification.title if notification else "",
        "content": notification.content if notification else "",
        "module": notification.module if notification else "",
        "category": notification.category if notification else "info",
        "is_read": recipient.is_read,
        "created_at": _iso(recipient.created_at),
        "read_at": _iso(recipient.read_at),
    }


def _sales_kpis(current_user: User, db: Session) -> dict:
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales") or [current_user.id]
    total_sales = db.execute(
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    ).scalar_one()
    order_count = db.execute(
        select(func.count(SalesOrder.id)).where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    ).scalar_one()
    pending_receive = db.execute(
        select(func.coalesce(func.sum(SalesOrder.total_amount - SalesOrder.deposit_amount), 0)).where(
            SalesOrder.sales_user_id.in_(scoped_user_ids)
        )
    ).scalar_one()
    return {
        "权限范围销售额": float(total_sales or 0),
        "权限范围订单数": int(order_count or 0),
        "待回款总金额": float(pending_receive or 0),
    }


def _purchase_kpis(current_user: User, db: Session) -> dict:
    scoped_user_ids = get_scoped_user_ids(db, current_user, "purchase") or [current_user.id]
    total_purchase = db.execute(
        select(func.coalesce(func.sum(PurchaseOrder.total_amount), 0)).where(
            PurchaseOrder.purchaser_user_id.in_(scoped_user_ids)
        )
    ).scalar_one()
    pending_approval_count = db.execute(
        select(func.count(PurchaseOrder.id)).where(
            PurchaseOrder.status == "pending_approval",
            PurchaseOrder.purchaser_user_id.in_(scoped_user_ids),
        )
    ).scalar_one()
    purchase_count = db.execute(
        select(func.count(PurchaseOrder.id)).where(PurchaseOrder.purchaser_user_id.in_(scoped_user_ids))
    ).scalar_one()
    return {
        "待审批采购单数": int(pending_approval_count or 0),
        "权限范围采购额": float(total_purchase or 0),
        "权限范围采购单数": int(purchase_count or 0),
    }


def _inventory_kpis(current_user: User, db: Session) -> dict:
    warehouse_ids = get_inventory_scope_warehouse_ids(db, current_user)
    query = select(Inventory).options(joinedload(Inventory.product), joinedload(Inventory.material))
    if warehouse_ids is not None:
        query = query.where(Inventory.warehouse_id.in_(warehouse_ids or [-1]))
    inventory_rows = db.execute(query).scalars().all()

    low_stock_count = 0
    for row in inventory_rows:
        threshold = None
        if row.product is not None:
            threshold = row.product.safety_stock_qty
        elif row.material is not None:
            threshold = row.material.safety_stock_qty
        if threshold is not None and Decimal(row.quantity or 0) < Decimal(threshold or 0):
            low_stock_count += 1

    return {
        "权限仓库数": len({row.warehouse_id for row in inventory_rows}),
        "库存品项数": len(inventory_rows),
        "低库存品项数": low_stock_count,
    }


def _finance_kpis(db: Session) -> tuple[dict, list[dict]]:
    incoming = db.execute(
        select(func.coalesce(func.sum(CashFlowRecord.amount), 0)).where(CashFlowRecord.flow_type == "incoming")
    ).scalar_one()
    outgoing = db.execute(
        select(func.coalesce(func.sum(CashFlowRecord.amount), 0)).where(CashFlowRecord.flow_type == "outgoing")
    ).scalar_one()
    pending_quality_cost = db.execute(
        select(func.coalesce(func.sum(QualityCostRecord.estimated_amount), 0)).where(QualityCostRecord.status == "pending")
    ).scalar_one()
    recognized_quality_cost = db.execute(
        select(func.coalesce(func.sum(QualityCostRecord.recognized_amount), 0)).where(QualityCostRecord.status == "recognized")
    ).scalar_one()
    pending_quality_count = db.execute(
        select(func.count(QualityCostRecord.id)).where(QualityCostRecord.status == "pending")
    ).scalar_one()
    quality_rows = db.execute(
        select(
            QualityCostRecord.id,
            QualityCostRecord.estimated_amount,
            QualityCostRecord.recognized_amount,
            QualityCostRecord.status,
            QualityCostRecord.created_at,
            QualityDisposition.disposition_type,
            QualityDisposition.quantity,
            QualityDisposition.reason,
            QualityTask.quality_no,
            ProductionTask.task_no,
            Product.name,
        )
        .join(QualityDisposition, QualityCostRecord.quality_disposition_id == QualityDisposition.id)
        .join(QualityTask, QualityDisposition.quality_task_id == QualityTask.id)
        .join(ProductionTask, QualityTask.production_task_id == ProductionTask.id)
        .join(Product, ProductionTask.product_id == Product.id)
        .order_by(QualityCostRecord.created_at.desc())
        .limit(6)
    ).all()
    quality_costs = [
        {
            "id": row.id,
            "quality_no": row.quality_no,
            "task_no": row.task_no,
            "product_name": row.name,
            "disposition_type": row.disposition_type,
            "quantity": float(row.quantity or 0),
            "reason": row.reason,
            "estimated_amount": float(row.estimated_amount or 0),
            "recognized_amount": float(row.recognized_amount or 0),
            "status": row.status,
            "status_label": status_label(row.status),
            "created_at": _iso(row.created_at),
        }
        for row in quality_rows
    ]
    return (
        {
            "资金流入总额": float(incoming or 0),
            "资金流出总额": float(outgoing or 0),
            "待确认质量成本": float(pending_quality_cost or 0),
            "已确认质量成本": float(recognized_quality_cost or 0),
            "待确认成本单数": int(pending_quality_count or 0),
        },
        quality_costs,
    )


def _build_kpis(current_user: User, db: Session) -> tuple[dict, list[dict]]:
    role_code = current_user.role.code if current_user.role else ""
    # 超级角色继承基础角色看板
    SUPER_BASE = {"inventory_super": "inventory", "quality_super": "quality", "finance_super": "finance"}
    base_role = SUPER_BASE.get(role_code, role_code)
    if base_role == "sales":
        return _sales_kpis(current_user, db), []
    if base_role == "purchase":
        return _purchase_kpis(current_user, db), []
    if base_role == "inventory":
        return _inventory_kpis(current_user, db), []
    if base_role == "quality":
        # 质检:展示质检任务统计
        task_count = db.execute(select(func.count(QualityTask.id))).scalar_one()
        pending_count = db.execute(
            select(func.count(QualityTask.id)).where(QualityTask.status == "pending")
        ).scalar_one()
        return {
            "质检任务总数": int(task_count or 0),
            "待质检任务": int(pending_count or 0),
            "质检通过率": "-",
        }, []
    if base_role in {"manager", "chairman", "finance", "system_admin"}:
        return _finance_kpis(db)
    return {}, []


def _ensure_inventory_home_todos(current_user: User, db: Session) -> None:
    if (current_user.role.code if current_user.role else "") != "inventory":
        return
    active_todos = db.execute(
        select(TodoItem).where(
            TodoItem.module == "inventory",
            TodoItem.status != "completed",
            TodoItem.business_type.in_(("sales_order", "sales_order_placement")),
        )
    ).scalars().all()
    if not active_todos:
        return

    order_ids = [todo.business_id for todo in active_todos if todo.business_id]
    order_map = {
        row.id: row
        for row in db.execute(select(SalesOrder).where(SalesOrder.id.in_(order_ids or [-1]))).scalars().all()
    }
    queue_map = {
        row.sales_order_id: row
        for row in db.execute(
            select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id.in_(order_ids or [-1]))
        ).scalars().all()
    }
    for todo in active_todos:
        order = order_map.get(todo.business_id)
        queue = queue_map.get(todo.business_id)
        is_legacy_backfill = todo.description in {
            "客户订单已进入待发货列表，请前往库存作业看板查看并处理。",
            "客户订单已进入待置货列表，请前往库存作业看板查看并处理。",
        }
        should_keep = True
        if todo.business_type == "sales_order":
            should_keep = (
                order is not None
                and order.status == "pending_shipment"
                and queue is not None
                and queue.placement_status == "hidden"
                and queue.shipment_status in {"pending", "waiting_approval"}
            )
        elif todo.business_type == "sales_order_placement":
            should_keep = (
                order is not None
                and order.status in {"pending_confirmation", "pending_shipment"}
                and queue is not None
                and queue.placement_status == "pending"
            )
        if is_legacy_backfill or not should_keep:
            todo.status = "completed"
            todo.completed_at = now_local()
    db.flush()


@router.get("")
def dashboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sync_low_stock_alerts(db)
    _ensure_inventory_home_todos(current_user, db)
    db.commit()
    todos = db.execute(
        select(TodoItem)
        .where(TodoItem.assigned_user_id == current_user.id, TodoItem.status != "completed")
        .order_by(TodoItem.created_at.desc())
    ).scalars().all()
    notifications = db.execute(
        select(NotificationRecipient)
        .options(joinedload(NotificationRecipient.notification))
        .where(NotificationRecipient.user_id == current_user.id, NotificationRecipient.is_read.is_(False))
        .order_by(NotificationRecipient.created_at.desc())
    ).scalars().all()

    kpis, quality_costs = _build_kpis(current_user, db)
    if not kpis:
        kpis = {
            "待办总数": len(todos),
            "未读通知": len(notifications),
            "处理进度": max(0, 100 - len(todos) * 5),
        }

    return api_response(
        data={
            "role_code": current_user.role.code if current_user.role else "",
            "todos": [_serialize_todo(item) for item in todos],
            "notifications": [_serialize_notification(item) for item in notifications],
            "kpis": kpis,
            "quality_costs": quality_costs,
        }
    )


def _get_notification_recipient(recipient_id: int, current_user: User, db: Session) -> NotificationRecipient:
    recipient = db.execute(
        select(NotificationRecipient).where(
            NotificationRecipient.id == recipient_id,
            NotificationRecipient.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    return recipient


@router.get("/notifications")
def list_my_notifications(
    read: str | None = Query(default=None, pattern="^(read|unread)$"),
    module: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前用户通知列表(分页/已读筛选/模块筛选),供通知中心页面使用。"""
    base_filters = [NotificationRecipient.user_id == current_user.id]
    filtered_filters = list(base_filters)
    if read == "read":
        filtered_filters.append(NotificationRecipient.is_read.is_(True))
    elif read == "unread":
        filtered_filters.append(NotificationRecipient.is_read.is_(False))
    if module:
        filtered_filters.append(Notification.module == module)

    query = (
        select(NotificationRecipient)
        .options(joinedload(NotificationRecipient.notification))
        .where(*filtered_filters)
        .order_by(NotificationRecipient.created_at.desc(), NotificationRecipient.id.desc())
    )
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).unique().scalars().all()
    unread_count = db.execute(
        select(func.count()).select_from(NotificationRecipient).where(
            NotificationRecipient.user_id == current_user.id,
            NotificationRecipient.is_read.is_(False),
        )
    ).scalar_one()
    return api_response(
        data={
            "items": [_serialize_notification(item) for item in rows],
            "pagination": {"page": page, "page_size": page_size, "total": total},
            "unread_count": unread_count,
        }
    )


@router.post("/notifications/{recipient_id}/read")
def mark_notification_read(recipient_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    recipient = _get_notification_recipient(recipient_id, current_user, db)
    if not recipient.is_read:
        recipient.is_read = True
        recipient.read_at = now_local()
        db.commit()
        realtime_manager.push_to_user(current_user.id, {"type": "notification_read", "id": recipient_id})
    return api_response(msg="通知已标记为已读")


@router.post("/notifications/batch-read")
def batch_mark_notification_read(payload: BatchIdsRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.ids:
        return api_response(msg="批量已读完成", data={"count": 0})
    rows = db.execute(
        select(NotificationRecipient).where(
            NotificationRecipient.user_id == current_user.id,
            NotificationRecipient.id.in_(payload.ids),
            NotificationRecipient.is_read.is_(False),
        )
    ).scalars().all()
    now = now_local()
    for row in rows:
        row.is_read = True
        row.read_at = now
        realtime_manager.push_to_user(current_user.id, {"type": "notification_read", "id": row.id})
    db.commit()
    return api_response(msg="批量已读完成", data={"count": len(rows)})


@router.post("/notifications/{recipient_id}/unread")
def mark_notification_unread(recipient_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    recipient = _get_notification_recipient(recipient_id, current_user, db)
    recipient.is_read = False
    recipient.read_at = None
    db.commit()
    realtime_manager.push_to_user(current_user.id, {"type": "notification_unread", "id": recipient_id})
    return api_response(msg="通知已标记为未读")


@router.post("/todos/{todo_id}/done")
def mark_todo_done(todo_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = complete_todos(db, todo_ids=[todo_id], assigned_user_id=current_user.id)
    db.commit()
    if not rows:
        raise HTTPException(status_code=404, detail="待办不存在或已处理")
    return api_response(msg="待办已完成")


@router.post("/todos/batch-done")
def batch_mark_todo_done(payload: BatchIdsRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not payload.ids:
        return api_response(msg="批量处理完成", data={"count": 0})
    rows = complete_todos(db, todo_ids=payload.ids, assigned_user_id=current_user.id)
    db.commit()
    return api_response(msg="批量处理完成", data={"count": len(rows)})
