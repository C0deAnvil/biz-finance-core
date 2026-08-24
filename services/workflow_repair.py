"""Reconcile workflow records that were created without their routing artifacts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import InventoryOperationRequest, ProductionTask, QualityTask, SalesReceiptRequest
from services.core import ensure_notification, ensure_todo, resolve_workflow_recipients
from services.request_trace import emit


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


def repair_workflows(db: Session) -> dict[str, int]:
    """Repair known pending workflows without creating duplicate todos."""
    result = {
        "sales_receipt_requests": repair_sales_receipt_workflows(db),
        "quality_tasks": repair_quality_workflows(db),
        "inventory_operation_requests": repair_inventory_operation_workflows(db),
    }
    emit("workflow_repair_finished", **result)
    return result
