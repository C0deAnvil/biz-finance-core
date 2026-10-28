"""Build a sales-order lifecycle from real business actions, not adjacent status snapshots."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import (
    DocumentRelation, FulfillmentDemand, InventoryDispatchQueue, MaterialReplenishmentDemand,
    InventoryOperationRequest, OperationLog, PayableRecord, ProductionMaterialUsage,
    ProductionTask, PurchaseOrder, PurchasePaymentRequest, PurchaseReceipt,
    QualityInboundApplication, QualityInspection, QualityTask, ReceivableRecord,
    SalesOrder, SalesOrderApproval, SalesOrderPriceChangeLog,
)
from services.quality_workflow import parse_purchase_quality_meta


def _iso(value):
    return value if isinstance(value, str) else value.isoformat() if value else None


def _task(key, label, start, end=None, *, lane="", children=None, document_no=None,
          milestone=False, planned=False, status_text=None, detail=None):
    if not start:
        return None
    return {
        "key": key,
        "label": label,
        "lane": lane,
        "start_at": _iso(start),
        "end_at": _iso(end),
        "status": "planned" if planned else "completed" if end or milestone else "active",
        "status_text": status_text or ("预计" if planned else "已记录" if milestone else "已完成" if end else "处理中"),
        "milestone": milestone,
        "planned": planned,
        "document_no": document_no,
        "detail": detail,
        "children": sorted((child for child in (children or []) if child), key=lambda child: child["start_at"]),
    }


def _branch_copy(node: dict, suffix: str) -> dict:
    """A merged purchase order can be shown under several distinct demand branches."""
    return {
        **node,
        "key": f"{node['key']}::{suffix}",
        "children": [_branch_copy(child, suffix) for child in node["children"]],
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
    item_numbers = {
        item.id: index for index, item in enumerate(sorted(order.items, key=lambda item: item.id), start=1)
    }
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
    # A merged PO stores only its first replenishment source in the FK fields.
    # Creation logs retain every source, so include POs belonging to later order lines.
    source_refs_by_no = defaultdict(set)
    merged_po_nos = set()
    purchase_logs = db.scalars(select(OperationLog).where(
        OperationLog.module == "purchase",
        OperationLog.action == "create_purchase_order",
        OperationLog.created_at >= submitted,
    )).all() if demand_ids or material_demand_ids else []
    for log in purchase_logs:
        match = re.search(r"采购订单\s+([A-Za-z0-9-]+)", log.content or "")
        if not match:
            continue
        po_no = match.group(1)
        data = log.after_data or {}
        sources = list(data.get("replenishment_sources") or [])
        if data.get("replenishment_source_type") and data.get("replenishment_source_id"):
            sources.append(f"{data['replenishment_source_type']}:{data['replenishment_source_id']}")
        for source in sources:
            kind, sep, raw_id = str(source).partition(":")
            if not sep or not raw_id.isdigit():
                continue
            source_id = int(raw_id)
            source_refs_by_no[po_no].add((kind, source_id))
            if (kind in {"fulfillment_demand", "sales_order_material_replenishment"} and source_id in demand_ids) or (
                kind == "production_material_replenishment" and source_id in material_demand_ids
            ):
                merged_po_nos.add(po_no)
    purchase_orders = db.scalars(select(PurchaseOrder).where(or_(
        PurchaseOrder.sales_order_id == order.id,
        PurchaseOrder.id.in_(relation_po_ids or [-1]),
        PurchaseOrder.id.in_(material_po_ids or [-1]),
        PurchaseOrder.order_no.in_(merged_po_nos or ["__none__"]),
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
    payable_records = db.scalars(select(PayableRecord).where(
        PayableRecord.purchase_order_id.in_(po_ids or [-1])
    ).order_by(PayableRecord.paid_at)).all()
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
    payables_by_po = defaultdict(list)
    receipts_by_po = defaultdict(list)
    quality_by_task = defaultdict(list)
    inspections_by_quality = defaultdict(list)
    inbound_by_source = defaultdict(list)
    material_by_task = defaultdict(list)
    apps_by_quality = defaultdict(list)
    for row in payments: payments_by_po[row.purchase_order_id].append(row)
    for row in payable_records: payables_by_po[row.purchase_order_id].append(row)
    for row in receipts: receipts_by_po[row.purchase_order_id].append(row)
    for row in quality_tasks: quality_by_task[row.production_task_id].append(row)
    for row in inspections: inspections_by_quality[row.quality_task_id].append(row)
    for row in inbound_apps: apps_by_quality[row.quality_task_id].append(row)
    for row in inbound_requests: inbound_by_source[(row.source_type, row.source_id)].append(row)
    for row in material_requests: material_by_task[row.source_id].append(row)

    def inbound_nodes(source_type, source_id):
        nodes = []
        for request in inbound_by_source[(source_type, source_id)]:
            children = [_task(f"inbound-request-{request.id}", "提交入库申请",
                              request.created_at, lane="inbound", milestone=True)]
            if request.completed_at:
                children.append(_task(f"inbound-done-{request.id}", "入库完成",
                                      request.completed_at, lane="inbound", milestone=True))
            nodes.append(_task(f"inbound-{request.id}", f"入库处理 {request.request_no}",
                               request.created_at, request.completed_at, lane="inbound",
                               children=children, document_no=request.request_no,
                               status_text="已入库" if request.completed_at else "待库管入库"))
        return nodes

    def quality_nodes(production_task_id):
        result = []
        for q in quality_by_task[production_task_id]:
            q_inspections = inspections_by_quality[q.id]
            inspected_at = max((i.inspected_at for i in q_inspections), default=None)
            quality_finished = q.status in {
                "ready_for_inbound", "inbound_pending", "completed", "rejected", "scrapped"
            }
            quality_end = max((value for value in (inspected_at, q.updated_at) if value), default=None) if quality_finished else None
            children = [_task(f"quality-request-{q.id}", "质检申请", q.created_at,
                              lane="quality", milestone=True)]
            if quality_end:
                children.append(_task(f"quality-result-{q.id}", "质检完成", quality_end,
                                      lane="quality", milestone=True))
            result.append(_task(f"quality-{q.id}", f"质检处理 {q.quality_no}",
                                q.created_at, quality_end,
                                lane="quality", children=children, document_no=q.quality_no,
                                status_text="质检已完成" if quality_end else "待质检"))
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
                          lane="production", status_text="生产完成" if task.completed_at else "生产处理中"),
                    _task(f"production-created-{task.id}", "生产任务创建",
                          task.created_at, lane="production", milestone=True)]
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
        production_done = bool(inbound and all(node["end_at"] for node in inbound))
        quality_pending = bool(task.requires_quality and (
            not quality_by_task[task.id] or any(
                q.status in {"pending", "inspecting", "pending_disposition"}
                for q in quality_by_task[task.id]
            )
        ))
        production_status = ("已入库" if production_done else
                             "待质检" if task.completed_at and quality_pending else
                             "待入库" if task.completed_at else
                             "生产中" if task.started_at else "待领料/开工")
        return _task(f"production-{task.id}", f"生产任务 {task.task_no}", task.created_at,
                     branch_end if production_done else None,
                     lane="production", children=children, document_no=task.task_no,
                     status_text=production_status)

    def purchase_node(po):
        children = [_task(f"purchase-created-{po.id}", "采购订单创建",
                          po.created_at, lane="procurement", milestone=True,
                          document_no=po.order_no)]
        if po.threshold_triggered:
            children.append(_task(f"purchase-approval-{po.id}", "采购订单审批",
                                  po.created_at, po.approved_at, lane="approval",
                                  status_text="审批通过" if po.approved_at else "待审批"))
        payment_rows = payments_by_po[po.id]
        paid_rows = [row for row in payables_by_po[po.id]
                     if row.status in {"paid", "partial_settled", "settled"}]
        paid_amount = sum((Decimal(row.order_amount or 0) for row in paid_rows), Decimal("0"))
        if not paid_rows:
            paid_amount = sum((Decimal(row.actual_amount or 0) for row in payment_rows if row.paid_at), Decimal("0"))
        payment_done = bool(Decimal(po.total_amount or 0) > 0 and paid_amount >= Decimal(po.total_amount or 0))
        payment_end = max((row.paid_at for row in paid_rows), default=None) if payment_done else None
        if payment_done and not payment_end:
            payment_end = max((row.paid_at for row in payment_rows if row.paid_at), default=None)
        payment_children = []
        for payment in payment_rows:
            payment_events = [_task(f"payment-request-{payment.id}", "提交付款申请",
                                    payment.created_at, lane="finance", milestone=True)]
            if payment.paid_at:
                payment_events.append(_task(f"payment-done-{payment.id}", "采购付款完成",
                                            payment.paid_at, lane="finance", milestone=True))
            payment_children.append(_task(f"payment-{payment.id}", f"付款申请 {payment.request_no}",
                                          payment.created_at, payment.paid_at, lane="finance",
                                          children=payment_events, document_no=payment.request_no,
                                          status_text="已付款" if payment.paid_at else "待财务付款"))
        payment_status = ("已付清" if payment_done else "部分付款" if paid_amount > 0
                          else "待财务付款" if payment_rows else "待提交付款申请")
        children.append(_task(f"payment-flow-{po.id}", "采购付款", po.created_at,
                              payment_end, lane="finance", children=payment_children,
                              status_text=payment_status,
                              detail=f"已付 {paid_amount} / 订单金额 {po.total_amount or 0}"))
        receipt_rows = receipts_by_po[po.id]
        arrival_end = max((r.created_at for r in receipt_rows), default=None)
        arrival_children = []
        if po.expected_arrival_date:
            arrival_children.append(_task(
                f"arrival-expected-{po.id}", "预计到货日期",
                f"{po.expected_arrival_date.isoformat()}T12:00:00",
                lane="procurement", milestone=True, planned=True,
                document_no=po.order_no, status_text="计划日期",
                detail=f"采购单 {po.order_no} 预计 {po.expected_arrival_date.isoformat()} 到货",
            ))
        arrival_children.extend(
            _task(f"receipt-{r.id}", f"采购到货登记 {r.receipt_no}",
                  r.created_at, lane="procurement", milestone=True,
                  document_no=r.receipt_no,
                  detail=f"登记的到货日期：{_iso(r.recorded_at)}" if r.recorded_at else None)
            for r in receipt_rows
        )
        children.append(_task(f"arrival-{po.id}", "采购到货", po.approved_at or po.created_at,
                              arrival_end if po.status == "fully_received" else None,
                              lane="procurement", children=arrival_children,
                              status_text="全部到货" if po.status == "fully_received"
                              else "部分到货" if receipt_rows else "待到货"))
        for r in receipt_rows:
            shadow_ids = [task_id for task_id, meta in purchase_shadow.items()
                          if meta.get("purchase_order_id") == po.id
                          and meta.get("purchase_receipt_id") in (None, r.id)]
            for shadow_id in shadow_ids:
                children.extend(quality_nodes(shadow_id))
            children.extend(inbound_nodes("purchase_receipt", r.id))
        related_inbound = [request for receipt in receipt_rows
                           for request in inbound_by_source[("purchase_receipt", receipt.id)]]
        goods_done = bool(po.status == "fully_received" and receipt_rows and all(
            inbound_by_source[("purchase_receipt", receipt.id)] and
            all(request.completed_at for request in inbound_by_source[("purchase_receipt", receipt.id)])
            for receipt in receipt_rows
        ))
        goods_end = max((request.completed_at for request in related_inbound if request.completed_at), default=None)
        pending_step = ("待到货" if not receipt_rows else
                        "待质检" if any(r.requires_quality and r.quality_status not in {"approved", "passed", "completed", "not_required"} for r in receipt_rows) else
                        "待入库" if not goods_done else "待付款" if not payment_done else "已完成")
        purchase_end = max(goods_end, payment_end) if goods_done and payment_done and goods_end and payment_end else None
        return _task(f"purchase-{po.id}", f"采购 {po.order_no}", po.created_at,
                     purchase_end, lane="procurement", children=children,
                     document_no=po.order_no, status_text=pending_step)

    demand_children = defaultdict(list)
    material_children = defaultdict(list)
    for task in production_tasks:
        if task.source_type != "purchase_quality":
            demand_children[task.fulfillment_demand_id].append(production_node(task))
    for po in purchase_orders:
        refs = set(source_refs_by_no[po.order_no])
        if po.replenishment_source_type and po.replenishment_source_id:
            refs.add((po.replenishment_source_type, po.replenishment_source_id))
        refs.update(("production_material_replenishment", batch.id)
                    for batch in material_demands if batch.purchase_order_id == po.id)
        branch = purchase_node(po)
        if len(refs) > 1:
            branch["label"] = f"合并采购 {po.order_no}"
            branch["detail"] = "同一张采购订单关联多条补货需求；各分支展示的是同一单据"
        attached = False
        attached_targets = set()
        for kind, source_id in sorted(refs):
            target_key = ("material" if kind == "production_material_replenishment" else "demand", source_id)
            if target_key in attached_targets:
                continue
            target = (material_children[source_id] if kind == "production_material_replenishment"
                      and source_id in material_demand_ids else
                      demand_children[source_id] if kind in {"fulfillment_demand", "sales_order_material_replenishment"}
                      and source_id in demand_ids else None)
            if target is None:
                continue
            target.append(branch if not attached else _branch_copy(branch, f"{kind}-{source_id}"))
            attached = True
            attached_targets.add(target_key)
        if not attached:
            demand_children[None].append(branch)
    for batch in material_demands:
        purchase_branches = material_children[batch.id]
        end = max((node["end_at"] for node in purchase_branches if node["end_at"]), default=None)
        converted_at = max((node["start_at"] for node in purchase_branches), default=None)
        children = [
            _task(f"material-demand-submit-{batch.id}", "提交原料补货需求",
                  batch.created_at, lane="procurement", milestone=True),
            _task(f"material-demand-process-{batch.id}", "原料采购需求处理",
                  batch.created_at, converted_at, lane="procurement",
                  status_text="已转采购订单" if converted_at else "待创建采购订单"),
            *purchase_branches,
        ]
        demand_children[batch.fulfillment_demand_id].append(_task(
            f"material-demand-{batch.id}", f"原料补货 {batch.demand_no}", batch.created_at,
            end if purchase_branches and all(node["end_at"] for node in purchase_branches) else None,
            lane="procurement", children=children, document_no=batch.demand_no,
            status_text="原料已入库" if end and purchase_branches and all(node["end_at"] for node in purchase_branches)
            else "原料采购中" if purchase_branches else "待处理原料需求",
        ))
    replenishment = []
    for demand in demands:
        supply_tasks = sorted(demand_children[demand.id], key=lambda x: x["start_at"])
        production_mode = demand.fulfillment_mode == "production"
        demand_lane = "production" if production_mode else "procurement"
        demand_kind = "生产" if production_mode else "采购"
        conversion_tasks = [node for node in supply_tasks if node["key"].startswith(("production-", "purchase-"))]
        converted_at = max((node["start_at"] for node in conversion_tasks), default=None)
        fully_ordered = float(demand.shortage_quantity or 0) <= 0
        supply_end = max((node["end_at"] for node in supply_tasks if node["end_at"]), default=None)
        demand_end = supply_end if fully_ordered and supply_tasks and all(node["end_at"] for node in supply_tasks) else None
        product_name = demand.product.name if demand.product else f"产品#{demand.product_id}"
        item_number = item_numbers.get(demand.sales_order_item_id)
        demand_label = f"{demand_kind}补货 · {product_name}"
        if item_number:
            demand_label += f"（明细{item_number}）"
        children = [
            _task(f"demand-submit-{demand.id}", "提交补货需求", demand.created_at,
                  lane=demand_lane, milestone=True, document_no=demand.demand_no),
            _task(f"demand-process-{demand.id}", f"{demand_kind}补货需求处理",
                  demand.created_at, converted_at if fully_ordered else None,
                  lane=demand_lane, status_text="已转为任务/订单" if fully_ordered and converted_at
                  else "部分已下单，仍待补足" if conversion_tasks else
                  "待创建生产任务" if production_mode else "待创建采购订单"),
            *supply_tasks,
        ]
        replenishment.append(_task(
            f"demand-{demand.id}", demand_label,
            demand.created_at, demand_end, lane=demand_lane, children=children,
            document_no=demand.demand_no,
            status_text="补货完成" if demand_end else "补货执行中" if supply_tasks else "待处理补货需求",
            detail=f"补货单 {demand.demand_no}；订单明细第 {item_number or '-'} 行；"
                   f"需求 {demand.required_quantity or demand.shortage_quantity or 0}，"
                   f"待补 {demand.shortage_quantity or 0}",
        ))
    replenishment.extend(demand_children[None])
    replenishment.sort(key=lambda x: x["start_at"])

    finance_children = [_task(f"price-{p.id}", "改价审批", max(p.created_at, submitted),
                              p.approved_at, lane="approval") for p in price_logs]
    finance_children += [_task(f"approval-{a.id}", "客户订单收款确认" if a.decision == "approved" else "客户订单收款确认（驳回）",
                               submitted, a.approved_at, lane="finance") for a in approvals]
    if not approvals and (order.approved_at or order.status == "pending_confirmation"):
        finance_children.append(_task("approval-pending", "客户订单收款确认 / 收款确认",
                                      submitted, order.approved_at, lane="finance"))
    received = db.scalars(select(ReceivableRecord).where(
        ReceivableRecord.sales_order_id == order.id
    ).order_by(ReceivableRecord.received_at)).all()
    approved_at = order.approved_at or max((a.approved_at for a in approvals if a.decision == "approved"), default=None)
    placed_at = queue.placement_marked_at if queue else None
    shipped_at = order.shipped_at or (queue.shipment_marked_at if queue else None)
    placement_children = [
        _task("placement-demand-created", "置货需求生成", submitted,
              lane="placement", milestone=True, document_no=order.order_no),
        _task("placement-work", "置货处理", submitted, placed_at,
              lane="placement", children=replenishment,
              status_text="置货完成" if placed_at else "待财务审批" if not approved_at
              else "补货中" if demands else "待库管置货"),
    ]
    if placed_at:
        placement_children.append(_task("placement-done", "置货完成", placed_at,
                                        lane="placement", milestone=True))
        shipment_children = []
        if shipped_at:
            shipment_children.append(_task("shipment-done", "发货完成", shipped_at,
                                           lane="shipment", milestone=True))
        placement_children.append(_task("shipment", "发货处理", placed_at, shipped_at,
                                        lane="shipment", children=shipment_children,
                                        status_text="已发货" if shipped_at else "待库管发货"))
    roots = [
        _task("submitted", "销售单据提交", submitted, lane="approval", milestone=True,
              document_no=order.order_no),
        _task("finance", "财务审核与收款", submitted, approved_at, lane="finance",
              children=finance_children,
              status_text="审核已通过" if approved_at else
              "待改价审批" if any(log.status == "pending" for log in price_logs) else "待财务审批"),
        _task("placement", "置货与发货", submitted, shipped_at, lane="placement",
              children=placement_children,
              status_text="已发货" if shipped_at else "待发货" if placed_at
              else "待财务审批" if not approved_at else "置货处理中"),
    ]
    roots.extend(_task(f"received-{r.id}", "后续回款", r.received_at,
                       lane="finance", milestone=True) for r in received)
    if order.completed_at:
        roots.append(_task("completed", "订单完结", order.completed_at,
                           lane="shipment", milestone=True))
    return [root for root in roots[:3] if root] + sorted(
        (root for root in roots[3:] if root), key=lambda root: root["start_at"]
    ), submitted
