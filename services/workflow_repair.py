"""Reconcile workflow records that were created without their routing artifacts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import FulfillmentDemand, InventoryDispatchQueue, InventoryOperationRequest, ProductionTask, QualityTask, SalesOrder, SalesOrderItem, SalesReceiptRequest, User
from services.core import complete_todos, ensure_notification, ensure_todo, resolve_workflow_recipients
from services.request_trace import emit
from services.replenishment_service import ensure_fulfillment_demand_routing
from sqlalchemy.orm import joinedload, selectinload


def repair_sales_receipt_workflows(db: Session) -> int:
    repaired = 0
    requests = db.execute(
        select(SalesReceiptRequest).where(SalesReceiptRequest.status == "pending_finance_check")
    ).scalars().all()
    for request in requests:
        try:
            order_no = request.sales_order.order_no if request.sales_order else str(request.sales_order_id)
            recipient_ids = resolve_workflow_recipients(
                db,
                "finance",
                workflow="sales_receipt_request_repair",
                business_type="sales_receipt_request",
                business_id=request.id,
            )
            title = f"待确认销售收款 {request.request_no}"
            description = f"客户订单 {order_no} 提交收款确认，金额 {float(request.requested_amount or 0)}。"
            for user_id in recipient_ids:
                ensure_todo(
                    db,
                    title=title,
                    description=description,
                    module="sales",
                    assigned_user_id=user_id,
                    business_type="sales_receipt_request",
                    business_id=request.id,
                    priority="high",
                )
            ensure_notification(
                db,
                title="销售收款确认已提交",
                content=f"客户订单 {order_no} 已提交收款确认 {request.request_no}，等待董事长确认。",
                module="sales",
                recipient_user_ids=[request.requested_by_id, *recipient_ids],
                category="warning",
                created_by_id=request.requested_by_id,
            )
            repaired += 1
        except Exception as exc:
            emit(
                "workflow_repair_failed",
                workflow="sales_receipt_request",
                business_type="sales_receipt_request",
                business_id=request.id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return repaired


def repair_quality_workflows(db: Session) -> int:
    repaired = 0
    tasks = db.execute(
        select(QualityTask).where(QualityTask.status == "pending")
    ).scalars().all()
    production_tasks = {
        row.id: row
        for row in db.execute(
            select(ProductionTask).where(ProductionTask.id.in_([task.production_task_id for task in tasks]))
        ).scalars().all()
    } if tasks else {}

    for task in tasks:
        if task.stock_transfer_id or task.source_category == "supplier_sample":
            continue  # 仓库专属质检不能由通用修复广播给全部质检员。
        try:
            production_task = production_tasks.get(task.production_task_id)
            task_no = production_task.task_no if production_task else str(task.production_task_id)
            recipient_ids = resolve_workflow_recipients(
                db,
                "quality",
                "manager",
                "chairman",
                workflow="production_quality_task_repair",
                business_type="quality_task",
                business_id=task.id,
            )
            title = f"待质检任务 {task.quality_no}"
            description = f"生产任务 {task_no} 已完工，请录入质检结果。"
            for user_id in recipient_ids:
                ensure_todo(
                    db,
                    title=title,
                    description=description,
                    module="quality",
                    assigned_user_id=user_id,
                    business_type="quality_task",
                    business_id=task.id,
                )
            ensure_notification(
                db,
                title="新增质检任务",
                content=f"生产任务 {task_no} 已完工，质检任务 {task.quality_no} 已生成。",
                module="quality",
                recipient_user_ids=[task.submitted_by_id, *recipient_ids],
                created_by_id=task.submitted_by_id,
            )
            repaired += 1
        except Exception as exc:
            emit(
                "workflow_repair_failed",
                workflow="production_quality_task",
                business_type="quality_task",
                business_id=task.id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return repaired


def repair_inventory_operation_workflows(db: Session) -> int:
    repaired = 0
    requests = db.execute(
        select(InventoryOperationRequest).where(
            InventoryOperationRequest.status.in_(["pending", "processing", "pending_manager_approval"])
        )
    ).scalars().all()
    for request in requests:
        if request.source_type == "stock_transfer":
            continue  # 移库作业只发给对应仓库负责人。
        try:
            is_manager_approval = request.status == "pending_manager_approval"
            recipient_ids = resolve_workflow_recipients(
                db,
                "manager" if is_manager_approval else "inventory",
                workflow="inventory_operation_request_repair",
                business_type="inventory_operation_request",
                business_id=request.id,
            )
            operation_label = "出库" if request.operation_type == "outbound" else "入库"
            action_label = "审批" if is_manager_approval else "执行"
            title = f"待{action_label}库存{operation_label} {request.source_no or request.request_no}"
            description = f"来源单据 {request.source_no or request.request_no} 已生成库存{operation_label}申请，请及时处理。"
            for user_id in recipient_ids:
                ensure_todo(
                    db,
                    title=title,
                    description=description,
                    module="inventory",
                    assigned_user_id=user_id,
                    business_type="inventory_operation_request",
                    business_id=request.id,
                    priority="high",
                )
            ensure_notification(
                db,
                title=title,
                content=description,
                module="inventory",
                recipient_user_ids=recipient_ids,
                category="warning",
                created_by_id=request.requested_by_id,
            )
            repaired += 1
        except Exception as exc:
            emit(
                "workflow_repair_failed",
                workflow="inventory_operation_request",
                business_type="inventory_operation_request",
                business_id=request.id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return repaired


def repair_sales_dispatch_workflows(db: Session) -> int:
    repaired = 0
    orders = db.execute(
        select(SalesOrder)
        .options(
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.product),
        )
        .where(SalesOrder.status == "pending_shipment")
    ).unique().scalars().all()
    for order in orders:
        try:
            # 销售订单缺货只能由库管在置货队列确认并提交。
            # 启动修复仅补齐置货队列/待办，不得自动新建 BH 补货需求。
            queue = db.execute(
                select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)
            ).scalar_one_or_none()
            if queue is None:
                queue = InventoryDispatchQueue(
                    sales_order_id=order.id,
                    placement_status="pending",
                    shipment_status="waiting_approval",
                )
                db.add(queue)
                db.flush()
            if queue.placement_status != "hidden":
                queue.placement_status = "pending"
                queue.shipment_status = "waiting_approval"
                complete_todos(db, business_type="sales_order", business_id=order.id)
                inventory_user_ids = resolve_workflow_recipients(
                    db,
                    "inventory",
                    workflow="sales_order_placement_repair",
                    business_type="sales_order_placement",
                    business_id=order.id,
                )
                for user_id in inventory_user_ids:
                    ensure_todo(
                        db,
                        title=f"待置货客户订单 {order.order_no}",
                        description="客户订单已通过财务/审批，成品库存满足后请完成置货；未置货前不能发货。",
                        module="inventory",
                        assigned_user_id=user_id,
                        business_type="sales_order_placement",
                        business_id=order.id,
                        priority="high",
                    )
            repaired += 1
        except Exception as exc:
            emit(
                "workflow_repair_failed",
                workflow="sales_dispatch",
                business_type="sales_order",
                business_id=order.id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return repaired


def repair_fulfillment_demand_workflows(db: Session) -> int:
    repaired = 0
    demands = db.execute(
        select(FulfillmentDemand)
        .options(
            joinedload(FulfillmentDemand.sales_order),
            joinedload(FulfillmentDemand.product),
        )
        .where(FulfillmentDemand.status == "pending")
    ).unique().scalars().all()
    fallback_user = db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id.asc())).scalars().first()
    for demand in demands:
        try:
            actor = demand.sales_order.sales_user if demand.sales_order and demand.sales_order.sales_user else fallback_user
            ensure_fulfillment_demand_routing(
                db,
                demand=demand,
                order=demand.sales_order,
                product=demand.product,
                current_user=actor,
            )
            repaired += 1
        except Exception as exc:
            emit(
                "workflow_repair_failed",
                workflow="fulfillment_demand",
                business_type="fulfillment_demand",
                business_id=demand.id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return repaired


def repair_workflows(db: Session) -> dict[str, int]:
    """Repair known pending workflows without creating duplicate todos."""
    result = {
        "sales_receipt_requests": repair_sales_receipt_workflows(db),
        "quality_tasks": repair_quality_workflows(db),
        "inventory_operation_requests": repair_inventory_operation_workflows(db),
        "sales_dispatch_workflows": repair_sales_dispatch_workflows(db),
        "fulfillment_demands": repair_fulfillment_demand_workflows(db),
    }
    emit("workflow_repair_finished", **result)
    return result
