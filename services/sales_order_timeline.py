"""Build a sales-order lifecycle from real business actions, not adjacent status snapshots."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import (
    DocumentRelation, FulfillmentDemand, InventoryDispatchQueue, MaterialReplenishmentDemand,
    InventoryOperationRequest, OperationLog, ProductionMaterialUsage,
    ProductionTask, PurchaseOrder, PurchasePaymentRequest, PurchaseReceipt,
    QualityInboundApplication, QualityInspection, QualityTask, ReceivableRecord,
    SalesOrder, SalesOrderApproval, SalesOrderPriceChangeLog,
)
from services.quality_workflow import parse_purchase_quality_meta


def _iso(value):
    return value if isinstance(value, str) else value.isoformat() if value else None


def _task(key, label, start, end=None, *, lane="", children=None, document_no=None, milestone=False):
    if not start:
        return None
    return {
        "key": key,
        "label": label,
        "lane": lane,
        "start_at": _iso(start),
        "end_at": _iso(end),
        "status": "completed" if end or milestone else "active",
        "milestone": milestone,
        "document_no": document_no,
        "children": sorted((child for child in (children or []) if child), key=lambda child: child["start_at"]),
    }


def _submission_time(db: Session, order: SalesOrder, history: list):
    # Saving a draft must never start the lifecycle. The submit operation is
    # authoritative; old imported orders may only have status history.
    logs = db.scalars(
        select(OperationLog).where(
            OperationLog.module == "sales",
            OperationLog.action == "submit_order",
            OperationLog.content.contains(order.order_no),
        ).order_by(OperationLog.created_at)
    ).all()
    for log in logs:
        if (log.after_data or {}).get("order_id") == order.id:
            return log.created_at
    if order.status == "draft":
        return None
    return next((row.created_at for row in history if row.created_at and row.to_stage != "draft"), None)


def build_sales_order_tasks(db: Session, order: SalesOrder, history: list) -> tuple[list[dict], object]:
    submitted = _submission_time(db, order, history)
    if not submitted:
        return [], None

    price_logs = db.scalars(select(SalesOrderPriceChangeLog).where(
        SalesOrderPriceChangeLog.sales_order_id == order.id
    ).order_by(SalesOrderPriceChangeLog.created_at)).all()
    approvals = db.scalars(select(SalesOrderApproval).where(
        SalesOrderApproval.sales_order_id == order.id
    ).order_by(SalesOrderApproval.approved_at)).all()
    queue = db.scalar(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id))
    demands = db.scalars(select(FulfillmentDemand).where(
        FulfillmentDemand.sales_order_id == order.id,
        FulfillmentDemand.status != "cancelled",
    ).order_by(FulfillmentDemand.created_at)).all()
    demand_ids = [row.id for row in demands]
    material_demands = db.scalars(select(MaterialReplenishmentDemand).where(
        MaterialReplenishmentDemand.fulfillment_demand_id.in_(demand_ids or [-1])
    ).order_by(MaterialReplenishmentDemand.created_at)).all()
    material_demand_ids = [row.id for row in material_demands]
    material_po_ids = [row.purchase_order_id for row in material_demands if row.purchase_order_id]
    production_tasks = db.scalars(select(ProductionTask).where(or_(
        ProductionTask.sales_order_id == order.id,
        ProductionTask.fulfillment_demand_id.in_(demand_ids or [-1]),
    )).order_by(ProductionTask.created_at)).all()
    production_ids = [row.id for row in production_tasks]
    relation_po_ids = db.scalars(select(DocumentRelation.target_document_id).where(
        DocumentRelation.source_document_type == "sales_order",
        DocumentRelation.source_document_id == order.id,
        DocumentRelation.target_document_type == "purchase_order",
    )).all()
    purchase_orders = db.scalars(select(PurchaseOrder).where(or_(
        PurchaseOrder.sales_order_id == order.id,
        PurchaseOrder.id.in_(relation_po_ids or [-1]),
        PurchaseOrder.id.in_(material_po_ids or [-1]),
        (PurchaseOrder.replenishment_source_type.in_(
            ["fulfillment_demand", "sales_order_material_replenishment"]
        )) & PurchaseOrder.replenishment_source_id.in_(demand_ids or [-1]),
        (PurchaseOrder.replenishment_source_type == "production_material_replenishment") &
        PurchaseOrder.replenishment_source_id.in_(material_demand_ids or [-1]),
    )).order_by(PurchaseOrder.created_at)).all()
    po_ids = [row.id for row in purchase_orders]
    payments = db.scalars(select(PurchasePaymentRequest).where(
        PurchasePaymentRequest.purchase_order_id.in_(po_ids or [-1])
    ).order_by(PurchasePaymentRequest.created_at)).all()
    receipts = db.scalars(select(PurchaseReceipt).where(
        PurchaseReceipt.purchase_order_id.in_(po_ids or [-1])
    ).order_by(PurchaseReceipt.created_at)).all()
    shadow_tasks = db.scalars(select(ProductionTask).where(
        ProductionTask.source_type == "purchase_quality"
    )).all() if po_ids else []
    purchase_shadow = {
        row.id: meta
        for row in shadow_tasks
        if (meta := parse_purchase_quality_meta(row)).get("purchase_order_id") in po_ids
    }
    quality_tasks = db.scalars(select(QualityTask).where(
        QualityTask.production_task_id.in_(production_ids + list(purchase_shadow) or [-1])
    ).order_by(QualityTask.created_at)).all()
    quality_ids = [row.id for row in quality_tasks]
    inspections = db.scalars(select(QualityInspection).where(
        QualityInspection.quality_task_id.in_(quality_ids or [-1])
    ).order_by(QualityInspection.inspected_at)).all()
    inbound_apps = db.scalars(select(QualityInboundApplication).where(
        QualityInboundApplication.quality_task_id.in_(quality_ids or [-1])
    )).all()
    inbound_requests = db.scalars(select(InventoryOperationRequest).where(
        InventoryOperationRequest.operation_type == "inbound",
        or_(
            (InventoryOperationRequest.source_type == "purchase_receipt") &
            InventoryOperationRequest.source_id.in_([r.id for r in receipts] or [-1]),
            InventoryOperationRequest.source_type.in_(["production_task", "quality_inbound"]) &
            InventoryOperationRequest.source_id.in_(production_ids or [-1]),
        ),
    ).order_by(InventoryOperationRequest.created_at)).all()
    material_requests = db.scalars(select(InventoryOperationRequest).where(
        InventoryOperationRequest.request_category == "production_material_issue",
        InventoryOperationRequest.source_type == "production_task",
        InventoryOperationRequest.source_id.in_(production_ids or [-1]),
    ).order_by(InventoryOperationRequest.created_at)).all()

    payments_by_po = defaultdict(list)
    receipts_by_po = defaultdict(list)
    quality_by_task = defaultdict(list)
    inspections_by_quality = defaultdict(list)
    inbound_by_source = defaultdict(list)
    material_by_task = defaultdict(list)
    apps_by_quality = defaultdict(list)
    for row in payments: payments_by_po[row.purchase_order_id].append(row)
    for row in receipts: receipts_by_po[row.purchase_order_id].append(row)
    for row in quality_tasks: quality_by_task[row.production_task_id].append(row)
    for row in inspections: inspections_by_quality[row.quality_task_id].append(row)
    for row in inbound_apps: apps_by_quality[row.quality_task_id].append(row)
    for row in inbound_requests: inbound_by_source[(row.source_type, row.source_id)].append(row)
    for row in material_requests: material_by_task[row.source_id].append(row)

    def inbound_nodes(source_type, source_id):
        return [_task(f"inbound-{r.id}", f"入库 {r.request_no}", r.created_at,
                      r.completed_at, lane="inbound", document_no=r.request_no)
                for r in inbound_by_source[(source_type, source_id)]]

    def quality_nodes(production_task_id):
        result = []
        for q in quality_by_task[production_task_id]:
            q_inspections = inspections_by_quality[q.id]
            inspected_at = max((i.inspected_at for i in q_inspections), default=None)
            children = [_task(f"quality-request-{q.id}", "质检申请", q.created_at,
                              lane="quality", milestone=True)]
            if inspected_at:
                children.append(_task(f"quality-result-{q.id}", "质检完成", inspected_at,
                                      lane="quality", milestone=True))
            result.append(_task(f"quality-{q.id}", f"质检 {q.quality_no}",
                                q.created_at, inspected_at if q.status in {"passed", "completed", "failed"} else None,
                                lane="quality", children=children, document_no=q.quality_no))
            for app in apps_by_quality[q.id]:
                result.append(_task(f"inbound-approval-{app.id}", "入库申请审核",
                                    app.created_at, app.approved_at, lane="inbound"))
        return result

    def production_node(task):
        usages = db.scalars(select(ProductionMaterialUsage).where(
            ProductionMaterialUsage.production_task_id == task.id
        )).all()
        children = [_task(f"manufacture-{task.id}", "生产加工",
                          task.started_at or task.created_at, task.completed_at,
                          lane="production")]
        for request in material_by_task[task.id]:
            children.append(_task(f"material-request-{request.id}",
                                  f"领料申请与出库 {request.request_no}", request.created_at,
                                  request.completed_at, lane="production",
                                  document_no=request.request_no))
        for usage in usages:
            if usage.inventory_approved_at or usage.manager_approved_at:
                children.append(_task(f"material-{usage.id}", "领料审批",
                                      usage.created_at,
                                      max((t for t in (usage.inventory_approved_at, usage.manager_approved_at) if t), default=None),
                                      lane="production"))
        children.extend(quality_nodes(task.id))
        inbound = inbound_nodes("production_task", task.id) + inbound_nodes("quality_inbound", task.id)
        children.extend(inbound)
        branch_end = max((r.completed_at for r in
                          inbound_by_source[("production_task", task.id)] +
                          inbound_by_source[("quality_inbound", task.id)]
                          if r.completed_at), default=None)
        return _task(f"production-{task.id}", f"生产任务 {task.task_no}", task.created_at,
                     branch_end if inbound and all(node["end_at"] for node in inbound) else None,
                     lane="production", children=children, document_no=task.task_no)

    def purchase_node(po):
        children = []
        for p in payments_by_po[po.id]:
            children.append(_task(f"payment-{p.id}", f"采购付款 {p.request_no}",
                                  p.created_at, p.paid_at, lane="finance", document_no=p.request_no))
        receipt_rows = receipts_by_po[po.id]
        if po.status != "draft" or receipt_rows:
            arrival_end = max((r.recorded_at or r.created_at for r in receipt_rows), default=None)
            children.append(_task(f"arrival-{po.id}", "采购到货", po.approved_at or po.created_at,
                                  arrival_end if po.status == "fully_received" else None,
                                  lane="procurement", children=[
                                      _task(f"receipt-{r.id}", f"到货登记 {r.receipt_no}", r.created_at,
                                            r.recorded_at, lane="procurement", document_no=r.receipt_no)
                                      for r in receipt_rows
                                  ]))
        for r in receipt_rows:
            shadow_ids = [task_id for task_id, meta in purchase_shadow.items()
                          if meta.get("purchase_order_id") == po.id
                          and meta.get("purchase_receipt_id") in (None, r.id)]
            for shadow_id in shadow_ids:
                children.extend(quality_nodes(shadow_id))
            children.extend(inbound_nodes("purchase_receipt", r.id))
        related_inbound = [request for receipt in receipt_rows
                           for request in inbound_by_source[("purchase_receipt", receipt.id)]]
        purchase_end = max((request.completed_at for request in related_inbound if request.completed_at), default=None)
        return _task(f"purchase-{po.id}", f"采购 {po.order_no}", po.created_at,
                     purchase_end if po.status == "fully_received" and related_inbound
                     and all(request.completed_at for request in related_inbound) else None,
                     lane="procurement", children=children, document_no=po.order_no)

    demand_children = defaultdict(list)
    material_children = defaultdict(list)
    for task in production_tasks:
        if task.source_type != "purchase_quality":
            demand_children[task.fulfillment_demand_id].append(production_node(task))
    for po in purchase_orders:
        if po.replenishment_source_type == "production_material_replenishment" and po.replenishment_source_id in material_demand_ids:
            material_children[po.replenishment_source_id].append(purchase_node(po))
            continue
        material_batch = next((row for row in material_demands if row.purchase_order_id == po.id), None)
        if material_batch:
            material_children[material_batch.id].append(purchase_node(po))
            continue
        demand_id = po.replenishment_source_id if po.replenishment_source_type in {
            "fulfillment_demand", "sales_order_material_replenishment"
        } else None
        demand_children[demand_id].append(purchase_node(po))
    for batch in material_demands:
        children = material_children[batch.id]
        end = max((node["end_at"] for node in children if node["end_at"]), default=None)
        demand_children[batch.fulfillment_demand_id].append(_task(
            f"material-demand-{batch.id}", f"原料补货 {batch.demand_no}", batch.created_at,
            end if children and all(node["end_at"] for node in children) else None,
            lane="procurement", children=children, document_no=batch.demand_no,
        ))
    replenishment = []
    for demand in demands:
        children = sorted(demand_children[demand.id], key=lambda x: x["start_at"])
        demand_end = max((node["end_at"] for node in children if node["end_at"]), default=None)
        replenishment.append(_task(f"demand-{demand.id}", f"补货需求 {demand.demand_no}",
                                   demand.created_at, demand_end if children and all(node["end_at"] for node in children)
                                   and float(demand.shortage_quantity or 0) <= 0 else None,
                                   lane="production" if demand.fulfillment_mode == "production" else "procurement",
                                   children=children, document_no=demand.demand_no))
    replenishment.extend(demand_children[None])
    replenishment.sort(key=lambda x: x["start_at"])

    finance_children = [_task(f"price-{p.id}", "改价审批", max(p.created_at, submitted),
                              p.approved_at, lane="approval") for p in price_logs]
    finance_children += [_task(f"approval-{a.id}", "客户订单审批" if a.decision == "approved" else "客户订单审批（驳回）",
                               submitted, a.approved_at, lane="finance") for a in approvals]
    if not approvals and (order.approved_at or order.status == "pending_confirmation"):
        finance_children.append(_task("approval-pending", "客户订单审批 / 收款确认",
                                      submitted, order.approved_at, lane="finance"))
    received = db.scalars(select(ReceivableRecord).where(
        ReceivableRecord.sales_order_id == order.id
    ).order_by(ReceivableRecord.received_at)).all()
    approved_at = order.approved_at or max((a.approved_at for a in approvals if a.decision == "approved"), default=None)
    placed_at = queue.placement_marked_at if queue else None
    shipped_at = order.shipped_at or (queue.shipment_marked_at if queue else None)
    roots = [
        _task("submitted", "销售单据提交", submitted, lane="approval", milestone=True,
              document_no=order.order_no),
        _task("finance", "财务审核与收款", submitted, approved_at, lane="finance",
              children=finance_children),
        _task("placement", "置货", submitted, placed_at, lane="placement",
              children=replenishment),
    ]
    roots.extend(_task(f"received-{r.id}", "后续回款", r.received_at,
                       lane="finance", milestone=True) for r in received)
    if placed_at:
        roots.append(_task("shipment", "发货", placed_at, shipped_at, lane="shipment"))
    if order.completed_at:
        roots.append(_task("completed", "订单完结", order.completed_at,
                           lane="shipment", milestone=True))
    return [root for root in roots[:3] if root] + sorted(
        (root for root in roots[3:] if root), key=lambda root: root["start_at"]
    ), submitted
