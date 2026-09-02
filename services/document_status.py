"""Unified document workflow status snapshots and relations.

Business tables remain the source of truth for their own state.  This module
projects those states into a small, queryable status model for cross-module
tracking and audit history.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models import (
    DocumentRelation,
    DocumentStatusHistory,
    DocumentStatusSnapshot,
    FulfillmentDemand,
    Inventory,
    InventoryDispatchQueue,
    PayableRecord,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    QualityInboundApplication,
    QualityTask,
    ReceivableRecord,
    SalesOrder,
    SalesReceiptRequest,
)
from services.display import status_label
from services.quality_workflow import parse_purchase_quality_meta
from services.time_utils import now_local
from services.warehouse_lookup import get_finished_warehouse


STATUS_DIMENSIONS = (
    "approval",
    "fulfillment",
    "procurement",
    "production",
    "quality",
    "inbound",
    "placement",
    "shipment",
    "finance",
)


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return dict(metadata or {})


def set_document_status(
    db: Session,
    *,
    document_type: str,
    document_id: int,
    status_dimension: str,
    current_stage: str,
    status_code: str | None = None,
    blocked_reason_code: str | None = None,
    blocked_reason: str | None = None,
    updated_by_id: int | None = None,
    related_document_type: str | None = None,
    related_document_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    operation_id: str | None = None,
    reason_code: str | None = None,
    reason_text: str | None = None,
) -> DocumentStatusSnapshot:
    """Upsert one current status and append history only when it changes."""
    normalized_status = status_code or current_stage
    normalized_metadata = _clean_metadata(metadata)
    snapshot = db.execute(
        select(DocumentStatusSnapshot).where(
            DocumentStatusSnapshot.document_type == document_type,
            DocumentStatusSnapshot.document_id == document_id,
            DocumentStatusSnapshot.status_dimension == status_dimension,
        )
    ).scalar_one_or_none()

    changed = snapshot is None or any(
        (
            snapshot.current_stage != current_stage,
            snapshot.status_code != normalized_status,
            snapshot.blocked_reason_code != blocked_reason_code,
            snapshot.blocked_reason != blocked_reason,
            snapshot.related_document_type != related_document_type,
            snapshot.related_document_id != related_document_id,
            (snapshot.metadata_json or {}) != normalized_metadata,
        )
    )
    timestamp = now_local()
    if snapshot is None:
        snapshot = DocumentStatusSnapshot(
            document_type=document_type,
            document_id=document_id,
            status_dimension=status_dimension,
            current_stage=current_stage,
            status_code=normalized_status,
            blocked_reason_code=blocked_reason_code,
            blocked_reason=blocked_reason,
            status_version=1,
            status_updated_at=timestamp,
            updated_by_id=updated_by_id,
            related_document_type=related_document_type,
            related_document_id=related_document_id,
            metadata_json=normalized_metadata,
        )
        db.add(snapshot)
        db.flush()
        changed = True
        from_stage = None
        from_status_code = None
    else:
        from_stage = snapshot.current_stage
        from_status_code = snapshot.status_code
        if changed:
            snapshot.current_stage = current_stage
            snapshot.status_code = normalized_status
            snapshot.blocked_reason_code = blocked_reason_code
            snapshot.blocked_reason = blocked_reason
            snapshot.status_version = int(snapshot.status_version or 0) + 1
            snapshot.status_updated_at = timestamp
            snapshot.updated_by_id = updated_by_id
            snapshot.related_document_type = related_document_type
            snapshot.related_document_id = related_document_id
            snapshot.metadata_json = normalized_metadata

    if changed:
        db.add(
            DocumentStatusHistory(
                document_type=document_type,
                document_id=document_id,
                status_dimension=status_dimension,
                from_stage=from_stage,
                to_stage=current_stage,
                from_status_code=from_status_code,
                to_status_code=normalized_status,
                reason_code=reason_code,
                reason_text=reason_text or blocked_reason,
                operator_user_id=updated_by_id,
                operation_id=operation_id,
                related_document_type=related_document_type,
                related_document_id=related_document_id,
                metadata_json=normalized_metadata,
                created_at=timestamp,
            )
        )
        db.flush()
    return snapshot


def ensure_document_relation(
    db: Session,
    *,
    source_document_type: str,
    source_document_id: int,
    target_document_type: str,
    target_document_id: int,
    relation_type: str,
    quantity: Decimal | int | float | None = None,
    metadata: dict[str, Any] | None = None,
    created_by_id: int | None = None,
) -> DocumentRelation:
    """Create or refresh one polymorphic document relation idempotently."""
    relation = db.execute(
        select(DocumentRelation).where(
            DocumentRelation.source_document_type == source_document_type,
            DocumentRelation.source_document_id == source_document_id,
            DocumentRelation.target_document_type == target_document_type,
            DocumentRelation.target_document_id == target_document_id,
            DocumentRelation.relation_type == relation_type,
        )
    ).scalar_one_or_none()
    if relation is None:
        relation = DocumentRelation(
            source_document_type=source_document_type,
            source_document_id=source_document_id,
            target_document_type=target_document_type,
            target_document_id=target_document_id,
            relation_type=relation_type,
            quantity=quantity,
            metadata_json=_clean_metadata(metadata),
            created_by_id=created_by_id,
        )
        db.add(relation)
    else:
        if quantity is not None:
            relation.quantity = quantity
        if metadata is not None:
            relation.metadata_json = _clean_metadata(metadata)
    db.flush()
    return relation


def get_document_status_snapshots(
    db: Session,
    *,
    document_type: str,
    document_id: int,
) -> list[DocumentStatusSnapshot]:
    return db.execute(
        select(DocumentStatusSnapshot)
        .where(
            DocumentStatusSnapshot.document_type == document_type,
            DocumentStatusSnapshot.document_id == document_id,
        )
        .order_by(DocumentStatusSnapshot.id.asc())
    ).scalars().all()


def get_document_status_history(
    db: Session,
    *,
    document_type: str,
    document_id: int,
    limit: int = 100,
) -> list[DocumentStatusHistory]:
    return db.execute(
        select(DocumentStatusHistory)
        .where(
            DocumentStatusHistory.document_type == document_type,
            DocumentStatusHistory.document_id == document_id,
        )
        .order_by(DocumentStatusHistory.created_at.desc(), DocumentStatusHistory.id.desc())
        .limit(limit)
    ).scalars().all()


def _snapshot_data(snapshot: DocumentStatusSnapshot) -> dict[str, Any]:
    return {
        "dimension": snapshot.status_dimension,
        "current_stage": snapshot.current_stage,
        "current_stage_label": status_label(snapshot.current_stage),
        "status_code": snapshot.status_code,
        "status_label": status_label(snapshot.status_code),
        "blocked_reason_code": snapshot.blocked_reason_code,
        "blocked_reason": snapshot.blocked_reason,
        "status_version": snapshot.status_version,
        "status_updated_at": snapshot.status_updated_at.isoformat() if snapshot.status_updated_at else None,
        "related_document_type": snapshot.related_document_type,
        "related_document_id": snapshot.related_document_id,
        "metadata": snapshot.metadata_json or {},
    }


def serialize_status_snapshots(snapshots: list[DocumentStatusSnapshot]) -> list[dict[str, Any]]:
    return [_snapshot_data(snapshot) for snapshot in snapshots]


def serialize_status_history(history: list[DocumentStatusHistory]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "dimension": row.status_dimension,
            "from_stage": row.from_stage,
            "from_stage_label": status_label(row.from_stage),
            "to_stage": row.to_stage,
            "to_stage_label": status_label(row.to_stage),
            "from_status_code": row.from_status_code,
            "to_status_code": row.to_status_code,
            "to_status_label": status_label(row.to_status_code),
            "reason_code": row.reason_code,
            "reason_text": row.reason_text,
            "operator_user_id": row.operator_user_id,
            "operation_id": row.operation_id,
            "related_document_type": row.related_document_type,
            "related_document_id": row.related_document_id,
            "metadata": row.metadata_json or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in history
    ]


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _priority_status(
    statuses: list[str],
    priority: tuple[str, ...],
    *,
    fallback: str = "not_required",
) -> str:
    status_set = set(statuses)
    for status in priority:
        if status in status_set:
            return status
    return fallback


def _load_sales_workflow_context(db: Session, order: SalesOrder) -> dict[str, Any]:
    demands = db.execute(
        select(FulfillmentDemand)
        .where(
            FulfillmentDemand.sales_order_id == order.id,
            FulfillmentDemand.status != "cancelled",
        )
        .order_by(FulfillmentDemand.id.asc())
    ).scalars().all()
    demand_ids = [row.id for row in demands]

    task_filter = ProductionTask.sales_order_id == order.id
    if demand_ids:
        task_filter = or_(task_filter, ProductionTask.fulfillment_demand_id.in_(demand_ids))
    production_tasks = db.execute(
        select(ProductionTask).where(task_filter).order_by(ProductionTask.id.asc())
    ).scalars().all()
    production_task_ids = [row.id for row in production_tasks]

    purchase_filter = PurchaseOrder.replenishment_source_type == "fulfillment_demand"
    if demand_ids:
        direct_trade_purchase = (
            (PurchaseOrder.replenishment_source_type == "fulfillment_demand")
            & PurchaseOrder.replenishment_source_id.in_(demand_ids)
        )
        production_material_purchase = (
            (PurchaseOrder.replenishment_source_type == "sales_order_material_replenishment")
            & PurchaseOrder.replenishment_source_id.in_(demand_ids)
        )
        purchase_filter = or_(
            direct_trade_purchase,
            production_material_purchase,
            PurchaseOrder.id.in_(
                select(DocumentRelation.target_document_id).where(
                    DocumentRelation.source_document_type == "sales_order",
                    DocumentRelation.source_document_id == order.id,
                    DocumentRelation.target_document_type == "purchase_order",
                )
            ),
        )
    else:
        purchase_filter = PurchaseOrder.id.in_(
            select(DocumentRelation.target_document_id).where(
                DocumentRelation.source_document_type == "sales_order",
                DocumentRelation.source_document_id == order.id,
                DocumentRelation.target_document_type == "purchase_order",
            )
        )
    purchase_orders = db.execute(
        select(PurchaseOrder).where(purchase_filter).order_by(PurchaseOrder.id.asc())
    ).scalars().all()
    purchase_order_ids = [row.id for row in purchase_orders]

    quality_filter = QualityTask.production_task_id.in_(production_task_ids or [-1])
    production_quality_tasks = db.execute(
        select(QualityTask).where(quality_filter).order_by(QualityTask.id.asc())
    ).scalars().all()
    purchase_quality_tasks: list[QualityTask] = []
    if purchase_order_ids:
        shadow_tasks = db.execute(
            select(ProductionTask).where(ProductionTask.source_type == "purchase_quality")
        ).scalars().all()
        shadow_task_ids = [
            row.id
            for row in shadow_tasks
            if parse_purchase_quality_meta(row).get("purchase_order_id") in purchase_order_ids
        ]
        if shadow_task_ids:
            purchase_quality_tasks = db.execute(
                select(QualityTask)
                .where(QualityTask.production_task_id.in_(shadow_task_ids))
                .order_by(QualityTask.id.asc())
            ).scalars().all()
    quality_tasks = [*production_quality_tasks, *purchase_quality_tasks]
    quality_task_ids = [row.id for row in quality_tasks]
    inbound_apps = db.execute(
        select(QualityInboundApplication)
        .where(QualityInboundApplication.quality_task_id.in_(quality_task_ids or [-1]))
        .order_by(QualityInboundApplication.id.asc())
    ).scalars().all()
    queue = db.execute(
        select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)
    ).scalar_one_or_none()
    latest_receipt_request = db.execute(
        select(SalesReceiptRequest)
        .where(SalesReceiptRequest.sales_order_id == order.id)
        .order_by(SalesReceiptRequest.id.desc())
    ).scalars().first()
    received_amount = db.execute(
        select(func.coalesce(func.sum(ReceivableRecord.amount), 0)).where(
            ReceivableRecord.sales_order_id == order.id
        )
    ).scalar_one()

    return {
        "demands": demands,
        "production_tasks": production_tasks,
        "purchase_orders": purchase_orders,
        "quality_tasks": quality_tasks,
        "inbound_apps": inbound_apps,
        "queue": queue,
        "latest_receipt_request": latest_receipt_request,
        "received_amount": _decimal(received_amount),
        "purchase_order_ids": purchase_order_ids,
    }


def _sync_sales_relations(
    db: Session,
    *,
    order: SalesOrder,
    context: dict[str, Any],
    updated_by_id: int | None,
) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    for demand in context["demands"]:
        ensure_document_relation(
            db,
            source_document_type="sales_order",
            source_document_id=order.id,
            target_document_type="fulfillment_demand",
            target_document_id=demand.id,
            relation_type="fulfillment_demand",
            quantity=demand.shortage_quantity,
            metadata={"fulfillment_mode": demand.fulfillment_mode},
            created_by_id=updated_by_id,
        )
        related.append(
            {
                "document_type": "fulfillment_demand",
                "document_id": demand.id,
                "document_no": demand.demand_no,
                "status": demand.status,
                "status_label": status_label(demand.status),
                "fulfillment_mode": demand.fulfillment_mode,
                "quantity": float(demand.shortage_quantity or 0),
            }
        )
    for task in context["production_tasks"]:
        ensure_document_relation(
            db,
            source_document_type="sales_order",
            source_document_id=order.id,
            target_document_type="production_task",
            target_document_id=task.id,
            relation_type="production_task",
            quantity=task.planned_quantity,
            metadata={"task_status": task.status},
            created_by_id=updated_by_id,
        )
        related.append(
            {
                "document_type": "production_task",
                "document_id": task.id,
                "document_no": task.task_no,
                "status": task.status,
                "status_label": status_label(task.status),
                "quantity": float(task.planned_quantity or 0),
            }
        )
    for purchase_order in context["purchase_orders"]:
        ensure_document_relation(
            db,
            source_document_type="sales_order",
            source_document_id=order.id,
            target_document_type="purchase_order",
            target_document_id=purchase_order.id,
            relation_type="purchase_order",
            quantity=None,
            metadata={"order_status": purchase_order.status},
            created_by_id=updated_by_id,
        )
        related.append(
            {
                "document_type": "purchase_order",
                "document_id": purchase_order.id,
                "document_no": purchase_order.order_no,
                "status": purchase_order.status,
                "status_label": status_label(purchase_order.status),
            }
        )
    for quality_task in context["quality_tasks"]:
        ensure_document_relation(
            db,
            source_document_type="sales_order",
            source_document_id=order.id,
            target_document_type="quality_task",
            target_document_id=quality_task.id,
            relation_type="quality_task",
            metadata={"source_category": quality_task.source_category},
            created_by_id=updated_by_id,
        )
        related.append(
            {
                "document_type": "quality_task",
                "document_id": quality_task.id,
                "document_no": quality_task.quality_no,
                "status": quality_task.status,
                "status_label": status_label(quality_task.status),
            }
        )
    return related


def _sales_approval_stage(order: SalesOrder) -> tuple[str, str | None, str | None]:
    if order.status == "cancelled":
        return "cancelled", "cancelled", None
    if order.status == "rejected":
        return "rejected", "rejected", order.rejection_reason
    if order.status == "pending_price_approval":
        return "pending_price_approval", None, "等待财务/负责人审批销售改价"
    if order.status == "pending_confirmation":
        return "pending_confirmation", None, "订单已提交，等待收款确认或流程放行"
    return "approved", None, None


def _sales_finance_stage(
    order: SalesOrder,
    *,
    received_amount: Decimal,
    latest_request: SalesReceiptRequest | None,
) -> tuple[str, str | None, str | None, dict[str, Any]]:
    total_amount = _decimal(order.total_amount)
    metadata = {
        "received_amount": float(received_amount),
        "total_amount": float(total_amount),
        "latest_request_id": latest_request.id if latest_request else None,
        "latest_request_no": latest_request.request_no if latest_request else None,
        "latest_request_status": latest_request.status if latest_request else None,
        "latest_finance_status": latest_request.finance_status if latest_request else None,
    }
    if order.finance_edit_pending:
        return "pending_finance_check", "finance_edit_pending", "财务修改待销售和库存确认", metadata
    if latest_request and latest_request.finance_status == "pending":
        return "pending_finance_check", None, "销售收款申请已提交，等待财务确认", metadata
    if latest_request and latest_request.status == "rejected":
        return "rejected", "receipt_rejected", latest_request.approval_remark or "收款申请被驳回", metadata
    if total_amount > 0 and received_amount <= 0:
        return "receipt_pending", None, "尚未登记客户回款", metadata
    if total_amount > 0 and received_amount < total_amount:
        return "partially_received", None, "客户回款尚未结清", metadata
    if total_amount > 0 and received_amount >= total_amount:
        return "paid", None, None, metadata
    return "not_required", None, None, metadata


def _sales_purchase_stage(demands: list[FulfillmentDemand], purchase_orders: list[PurchaseOrder]) -> tuple[str, str | None, str | None, dict[str, Any]]:
    trade_demands = [row for row in demands if row.fulfillment_mode == "trade"]
    raw_material_demand_count = len([row for row in demands if row.fulfillment_mode == "production"])
    statuses = [row.status for row in purchase_orders]
    metadata = {
        "demand_count": len(trade_demands),
        "raw_material_demand_count": raw_material_demand_count,
        "purchase_order_count": len(purchase_orders),
        "purchase_order_ids": [row.id for row in purchase_orders],
        "purchase_order_statuses": statuses,
    }
    pending_demands = [row for row in trade_demands if row.status == "pending"]
    if pending_demands:
        return "pending", "purchase_demand_pending", "存在待采购处理的补货需求", metadata
    if not purchase_orders:
        if trade_demands or raw_material_demand_count:
            return "pending", "purchase_order_missing", "已有采购需求，但尚未生成采购订单", metadata
        return "not_required", None, None, metadata
    if any(status == "rejected" for status in statuses):
        return "rejected", "purchase_rejected", "关联采购订单被驳回", metadata
    if any(status == "cancelled" for status in statuses):
        return "cancelled", "purchase_cancelled", "关联采购订单已取消", metadata
    stage = _priority_status(
        statuses,
        (
            "draft",
            "pending_approval",
            "pending_finance_payment",
            "purchasing",
            "pending_purchase_confirm",
            "partial_received",
            "fully_received",
            "completed",
        ),
        fallback="completed",
    )
    reason = None
    if stage in {"draft", "pending_approval", "pending_finance_payment"}:
        reason = "采购订单尚未进入到货/入库阶段"
    elif stage in {"purchasing", "pending_purchase_confirm"}:
        reason = "采购订单处理中，等待采购到货"
    elif stage in {"partial_received", "fully_received"}:
        reason = "采购已到货，等待质检或入库完成"
    return stage, None, reason, metadata


def _sales_production_stage(demands: list[FulfillmentDemand], tasks: list[ProductionTask]) -> tuple[str, str | None, str | None, dict[str, Any]]:
    production_demands = [row for row in demands if row.fulfillment_mode == "production"]
    statuses = [row.status for row in tasks]
    metadata = {
        "demand_count": len(production_demands),
        "task_count": len(tasks),
        "task_ids": [row.id for row in tasks],
        "task_statuses": statuses,
    }
    if any(row.status == "pending" for row in production_demands):
        return "pending", "production_demand_pending", "存在待转生产任务的补货需求", metadata
    if not tasks:
        if production_demands:
            return "pending", "production_task_missing", "已有生产需求，但尚未生成生产任务", metadata
        return "not_required", None, None, metadata
    if any(status in {"cancelled", "rejected"} for status in statuses):
        return "rejected", "production_rejected", "关联生产任务取消或驳回", metadata
    if any(status in {"scrapped", "partially_scrapped"} for status in statuses):
        return "scrapped", "production_scrapped", "生产或质检存在报废", metadata
    if any(status in {"rework_pending", "rework_created"} for status in statuses):
        return "rework_pending", "production_rework", "生产质检不合格，等待返工处理", metadata
    stage = _priority_status(
        statuses,
        (
            "pending_material",
            "pending_material_approval",
            "pending_special_approval",
            "pending_material_outbound",
            "material_issued",
            "in_progress",
            "waiting_quality",
            "ready_for_inbound",
            "completed",
            "reworked",
        ),
        fallback="completed",
    )
    reason_map = {
        "pending_material": "生产任务待提交领料申请",
        "pending_material_approval": "生产领料等待库存审批",
        "pending_special_approval": "特殊领料等待审批",
        "pending_material_outbound": "领料已审批，等待原料出库",
        "material_issued": "原料已出库，等待生产完工",
        "in_progress": "生产任务进行中",
        "waiting_quality": "生产已完工，等待质检",
        "ready_for_inbound": "生产已通过质检或无需质检，等待入库",
    }
    return stage, None, reason_map.get(stage), metadata


def _sales_quality_stage(tasks: list[ProductionTask], quality_tasks: list[QualityTask]) -> tuple[str, str | None, str | None, dict[str, Any]]:
    task_statuses = [row.status for row in tasks]
    statuses = [row.status for row in quality_tasks]
    metadata = {
        "quality_task_count": len(quality_tasks),
        "quality_task_ids": [row.id for row in quality_tasks],
        "quality_statuses": statuses,
    }
    if not quality_tasks:
        if any(status == "waiting_quality" for status in task_statuses):
            return "waiting_quality", "quality_task_missing", "生产已完工但质检任务尚未生成", metadata
        return "not_required", None, None, metadata
    if any(status in {"pending", "inspecting"} for status in statuses):
        return "waiting_quality", None, "质检任务正在处理", metadata
    if any(status == "pending_disposition" for status in statuses):
        return "pending_disposition", "quality_failed", "质检存在不合格结果，等待返工或报废处理", metadata
    if any(status in {"rework_created", "rework_pending"} for status in statuses):
        return "rework_pending", "quality_rework", "质检不合格，已进入返工流程", metadata
    if any(status == "scrapped" for status in statuses):
        return "scrapped", "quality_scrapped", "质检报废", metadata
    if any(status == "rejected" for status in statuses):
        return "rejected", "quality_inbound_rejected", "质检入库审批被驳回", metadata
    if any(status in {"ready_for_inbound", "inbound_pending"} for status in statuses):
        return "ready_for_inbound", None, "质检通过，等待入库处理", metadata
    return "completed", None, None, metadata


def _sales_inbound_stage(
    tasks: list[ProductionTask],
    purchase_orders: list[PurchaseOrder],
    inbound_apps: list[QualityInboundApplication],
) -> tuple[str, str | None, str | None, dict[str, Any]]:
    task_statuses = [row.status for row in tasks]
    purchase_statuses = [row.status for row in purchase_orders]
    app_statuses = [row.status for row in inbound_apps]
    metadata = {
        "inbound_application_count": len(inbound_apps),
        "inbound_application_statuses": app_statuses,
    }
    if any(status == "pending" for status in app_statuses):
        return "inbound_pending", None, "质检入库申请等待审批", metadata
    if any(status == "rejected" for status in app_statuses):
        return "rejected", "inbound_rejected", "质检入库申请被驳回", metadata
    if any(status in {"ready_for_inbound", "inbound_pending"} for status in task_statuses):
        return "pending_inbound", None, "生产成品等待入库", metadata
    if any(status in {"partial_received", "fully_received"} for status in purchase_statuses):
        return "pending_inbound", None, "采购到货等待质检或入库", metadata
    if tasks or purchase_orders:
        if all(status == "completed" for status in task_statuses or ["completed"]) and all(
            status in {"completed", "settled", "paid", "fully_received"} for status in purchase_statuses or ["completed"]
        ):
            return "completed", None, None, metadata
    return "not_required", None, None, metadata


def _sales_placement_stage(order: SalesOrder, queue: InventoryDispatchQueue | None) -> tuple[str, str | None, str | None, dict[str, Any]]:
    metadata = {
        "queue_id": queue.id if queue else None,
        "placement_status": queue.placement_status if queue else None,
    }
    if order.status in {"cancelled", "rejected"}:
        return order.status, order.status, None, metadata
    if not queue:
        return "pending", "placement_queue_missing", "尚未生成置货队列", metadata
    if queue.placement_status == "hidden":
        return "placed", None, None, metadata
    if queue.placement_status == "placed":
        return "placed", None, None, metadata
    return queue.placement_status or "pending", None, "等待库管置货", metadata


def _sales_shipment_stage(order: SalesOrder, queue: InventoryDispatchQueue | None) -> tuple[str, str | None, str | None, dict[str, Any]]:
    metadata = {
        "queue_id": queue.id if queue else None,
        "shipment_status": queue.shipment_status if queue else None,
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
    }
    if order.status == "completed":
        return "completed", None, None, metadata
    if order.status == "shipped":
        return "shipped", None, None, metadata
    if order.status in {"cancelled", "rejected"}:
        return order.status, order.status, None, metadata
    if not queue:
        return "waiting_approval", "shipment_queue_missing", "尚未生成发货队列", metadata
    if queue.placement_status != "hidden":
        return "waiting_approval", "placement_not_completed", "尚未完成置货，不能发货", metadata
    if queue.shipment_status in {"pending", "waiting_approval"}:
        return "pending_shipment", None, "已完成置货，等待发货出库", metadata
    return queue.shipment_status or "waiting_approval", None, None, metadata


def _overall_sales_stage(
    order: SalesOrder,
    *,
    approval: str,
    finance: str,
    procurement: str,
    production: str,
    quality: str,
    inbound: str,
    placement: str,
    shipment: str,
) -> tuple[str, str | None, str | None]:
    if approval in {"cancelled", "rejected"}:
        return approval, approval, "销售订单审批未通过或已取消"
    if finance in {"pending_finance_check", "receipt_pending", "rejected"}:
        return finance, "finance_pending", "财务收款或确认尚未完成"
    if procurement in {"pending", "purchasing", "partial_received", "rejected", "cancelled"}:
        return procurement, "procurement_pending", "采购链路尚未完成"
    if production in {"pending", "pending_material", "pending_material_approval", "pending_special_approval", "pending_material_outbound", "material_issued", "in_progress", "rework_pending", "scrapped", "rejected"}:
        return production, "production_pending", "生产链路尚未完成"
    if quality in {"waiting_quality", "pending_disposition", "rework_pending", "scrapped", "rejected"}:
        return quality, "quality_pending", "质检链路尚未完成"
    if inbound in {"pending_inbound", "inbound_pending", "rejected"}:
        return inbound, "inbound_pending", "入库链路尚未完成"
    if placement not in {"placed", "completed"}:
        return "pending_placement", "placement_pending", "等待库管置货"
    if shipment not in {"shipped", "completed"}:
        return "pending_shipment", "shipment_pending", "等待发货出库"
    if order.status == "completed":
        return "completed", None, None
    if order.status == "shipped":
        return "shipped", None, None
    return order.status or "pending", None, None


def sync_sales_order_process_status(
    db: Session,
    order: SalesOrder,
    *,
    updated_by_id: int | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Project the current sales order workflow into snapshots and relations."""
    context = _load_sales_workflow_context(db, order)
    approval, approval_reason_code, approval_reason = _sales_approval_stage(order)
    finance, finance_reason_code, finance_reason, finance_metadata = _sales_finance_stage(
        order,
        received_amount=context["received_amount"],
        latest_request=context["latest_receipt_request"],
    )
    procurement, procurement_reason_code, procurement_reason, procurement_metadata = _sales_purchase_stage(
        context["demands"], context["purchase_orders"]
    )
    production, production_reason_code, production_reason, production_metadata = _sales_production_stage(
        context["demands"], context["production_tasks"]
    )
    quality, quality_reason_code, quality_reason, quality_metadata = _sales_quality_stage(
        context["production_tasks"], context["quality_tasks"]
    )
    inbound, inbound_reason_code, inbound_reason, inbound_metadata = _sales_inbound_stage(
        context["production_tasks"], context["purchase_orders"], context["inbound_apps"]
    )
    placement, placement_reason_code, placement_reason, placement_metadata = _sales_placement_stage(
        order, context["queue"]
    )
    shipment, shipment_reason_code, shipment_reason, shipment_metadata = _sales_shipment_stage(
        order, context["queue"]
    )
    overall, overall_reason_code, overall_reason = _overall_sales_stage(
        order,
        approval=approval,
        finance=finance,
        procurement=procurement,
        production=production,
        quality=quality,
        inbound=inbound,
        placement=placement,
        shipment=shipment,
    )

    statuses = [
        ("approval", approval, approval_reason_code, approval_reason, {}, None, None),
        ("finance", finance, finance_reason_code, finance_reason, finance_metadata, None, None),
        ("procurement", procurement, procurement_reason_code, procurement_reason, procurement_metadata, None, None),
        ("production", production, production_reason_code, production_reason, production_metadata, None, None),
        ("quality", quality, quality_reason_code, quality_reason, quality_metadata, None, None),
        ("inbound", inbound, inbound_reason_code, inbound_reason, inbound_metadata, None, None),
        ("placement", placement, placement_reason_code, placement_reason, placement_metadata, None, None),
        ("shipment", shipment, shipment_reason_code, shipment_reason, shipment_metadata, None, None),
        (
            "fulfillment",
            overall,
            overall_reason_code,
            overall_reason,
            {
                "order_status": order.status,
                "order_no": order.order_no,
                "dimensions": {
                    "approval": approval,
                    "finance": finance,
                    "procurement": procurement,
                    "production": production,
                    "quality": quality,
                    "inbound": inbound,
                    "placement": placement,
                    "shipment": shipment,
                },
            },
            None,
            None,
        ),
    ]
    for dimension, stage, reason_code, reason, metadata, related_type, related_id in statuses:
        set_document_status(
            db,
            document_type="sales_order",
            document_id=order.id,
            status_dimension=dimension,
            current_stage=stage,
            status_code=stage,
            blocked_reason_code=reason_code,
            blocked_reason=reason,
            updated_by_id=updated_by_id,
            related_document_type=related_type,
            related_document_id=related_id,
            metadata=metadata,
            operation_id=operation_id,
            reason_code=reason_code,
            reason_text=reason,
        )

    related_documents = _sync_sales_relations(
        db,
        order=order,
        context=context,
        updated_by_id=updated_by_id,
    )
    snapshots = get_document_status_snapshots(db, document_type="sales_order", document_id=order.id)
    history = get_document_status_history(db, document_type="sales_order", document_id=order.id)
    return {
        "current_stage": overall,
        "current_stage_label": status_label(overall),
        "status_code": overall,
        "status_label": status_label(overall),
        "blocked_reason_code": overall_reason_code,
        "blocked_reason": overall_reason,
        "dimensions": serialize_status_snapshots(snapshots),
        "related_documents": related_documents,
        "status_history": serialize_status_history(history),
    }
