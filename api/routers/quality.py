from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import (
    BOM,
    InventoryTransaction,
    Product,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReturn,
    QualityCostRecord,
    QualityDisposition,
    QualityInboundApplication,
    QualityInspection,
    QualityTask,
    Role,
    SalesReturn,
    StockTransfer,
    SupplierSample,
    User,
    Warehouse,
)
from responses import api_response
from schemas.quality import (
    InspectionCreate,
    QualityAllowInbound,
    QualityInboundApproval,
    QualityInboundCreate,
    QualityReworkCreate,
    QualityScrapCreate,
    QualityTaskManualCreate,
    QualityTaskAssign,
)
from services.core import _role_user_ids, apply_inventory_change, complete_todos, create_notification, create_todo, next_business_no, resolve_assigned_inspector_id, write_operation_log
from services.display import clean_display_text, status_label
from services.quality_workflow import (
    resolve_quality_recipients_for_warehouse,
    build_purchase_quality_notes,
    create_purchase_quality_tasks,
    parse_purchase_quality_meta,
    parse_return_quality_meta,
    quality_sample_consumption,
    resolve_purchase_quality_context,
)
from services.sales_return_workflow import create_sales_return_inbound_request, sales_return_notice_user_ids
from services.organization import get_inventory_scope_warehouse_ids
from services.warehouse_lookup import get_finished_warehouse, get_raw_warehouse
from services.workflow import create_inventory_operation_request
from services.stock_transfer_workflow import repair_pending_transfer_jobs, sync_transfer_inspection
from services.time_utils import now_local
from services.workflow_repair import repair_quality_workflows


router = APIRouter(prefix="/quality", tags=["quality"])

QUALITY_ASSIGNABLE_ROLE_CODES = ("quality", "quality_super")


@router.get("/assignable-inspectors")
def list_assignable_inspectors(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """可被指派质检任务的在岗人员(普通质检员 + 超级质检员)。

    申请质检的发起人可能是采购、生产、库管等任何角色,故这里只要求登录、不限角色,
    与 /api/finance/assignable-users 的处理方式一致。仅返回 id 与姓名。
    """
    users = db.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(Role.code.in_(QUALITY_ASSIGNABLE_ROLE_CODES), User.is_active.is_(True))
        .order_by(User.id.asc())
    ).scalars().all()
    return api_response(
        data=[{"id": user.id, "full_name": user.full_name or user.username} for user in users]
    )


def _purchase_quality_target_warehouse_id(db: Session, order_item: PurchaseOrderItem) -> int:
    if order_item.material_id:
        return get_raw_warehouse(db).id
    if order_item.product_id:
        return get_finished_warehouse(db).id
    if order_item.packaging_config_id:
        return get_raw_warehouse(db).id
    raise HTTPException(status_code=409, detail="采购质检入库明细缺少库存对象信息")


def _is_failed_grade(grade: str | None) -> bool:
    normalized = str(grade or "").strip().lower()
    return normalized in {"不合格", "ng", "fail", "failed"}


def _serialize_inspection(inspection: QualityInspection) -> dict:
    return {
        "id": inspection.id,
        "quality_task_id": inspection.quality_task_id,
        "sequence_no": inspection.sequence_no,
        "result_text": inspection.result_text,
        "grade": inspection.grade,
        "is_passed": inspection.is_passed,
        "inspected_quantity": float(inspection.inspected_quantity),
        "sample_consumption_qty": float(inspection.sample_consumption_qty or 0),
        "report_summary": inspection.report_summary,
        "inspected_at": inspection.inspected_at.isoformat() if inspection.inspected_at else None,
        "created_at": inspection.created_at.isoformat() if inspection.created_at else None,
    }


def _serialize_disposition(disposition: QualityDisposition | None, related_task_map: dict[int, ProductionTask] | None = None) -> dict | None:
    if disposition is None:
        return None
    related_task = related_task_map.get(disposition.related_production_task_id) if related_task_map else None
    return {
        "id": disposition.id,
        "type": disposition.disposition_type,
        "quantity": float(disposition.quantity),
        "reason": disposition.reason,
        "related_production_task_id": disposition.related_production_task_id,
        "related_production_task_no": related_task.task_no if related_task else None,
        "created_by_id": disposition.created_by_id,
        "created_at": disposition.created_at.isoformat() if disposition.created_at else None,
    }


def _serialize_quality_task(
    task: QualityTask,
    production_task: ProductionTask | None,
    inspections: list[QualityInspection],
    inbound: QualityInboundApplication | None,
    disposition: QualityDisposition | None,
    quality_cost: QualityCostRecord | None = None,
    related_task_map: dict[int, ProductionTask] | None = None,
    warehouse_name: str | None = None,
    sample: SupplierSample | None = None,
) -> dict:
    passed_tests = sum(1 for item in inspections if item.is_passed)
    latest_grade = inspections[-1].grade if inspections else None
    latest_report_summary = next((item.report_summary for item in reversed(inspections) if item.report_summary), None)
    sample_consumption_total = sum((Decimal(item.sample_consumption_qty or 0) for item in inspections), Decimal("0"))
    disposition_data = _serialize_disposition(disposition, related_task_map)
    status = task.status
    source_type = production_task.source_type if production_task else "manual"
    transfer = task.stock_transfer if task.stock_transfer_id else None
    if transfer:
        source_type = "stock_transfer"
    if sample:
        source_type = "supplier_sample"
    return_meta = parse_return_quality_meta(production_task)
    source_label = (
        return_meta.get("source_label")
        if return_meta
        else "供应商样品" if sample else "移库质检" if transfer else "采购来料" if source_type == "purchase_quality" else "返工任务" if source_type == "rework" else "生产任务"
    )
    purchase_meta = parse_purchase_quality_meta(production_task)
    item_name = return_meta.get("item_label") or purchase_meta.get("item_name") or (production_task.product.name if production_task and production_task.product else None)
    if transfer:
        item_name = transfer.product.name if transfer.product else (transfer.material.name if transfer.material else (transfer.packaging_config.packaging_name if transfer.packaging_config else None))
    if sample:
        item_name = sample.product.name if sample.product else sample.material.name if sample.material else sample.packaging_config.packaging_name if sample.packaging_config else None
    return {
        "id": task.id,
        "quality_no": task.quality_no,
        "batch_no": task.batch_no,
        "production_task_id": task.production_task_id,
        "production_task_no": production_task.task_no if production_task else None,
        "stock_transfer_id": transfer.id if transfer else None,
        "stock_transfer_no": transfer.transfer_no if transfer else None,
        "supplier_sample_id": sample.id if sample else None,
        "supplier_sample_no": sample.sample_no if sample else None,
        "sample_quantity": float(sample.received_quantity or sample.quantity) if sample else None,
        "source_type": source_type,
        "source_category": task.source_category,
        "source_label": source_label,
        "product_id": sample.product_id if sample else transfer.product_id if transfer else (production_task.product_id if production_task else None),
        "product_name": item_name,
        "item_name": item_name,
        "item_type": ("product" if sample.product_id else "material" if sample.material_id else "packaging") if sample else ("product" if transfer.product_id else "material" if transfer.material_id else "packaging") if transfer else purchase_meta.get("item_type"),
        "item_code": (sample.product.product_code if sample.product else sample.material.material_code if sample.material else f"BZ-{sample.packaging_config_id}") if sample else purchase_meta.get("item_code"),
        "purchase_order_id": purchase_meta.get("purchase_order_id"),
        "purchase_order_no": purchase_meta.get("purchase_order_no"),
        "purchase_receipt_id": purchase_meta.get("purchase_receipt_id"),
        "purchase_receipt_no": purchase_meta.get("purchase_receipt_no"),
        "return_business_type": return_meta.get("business_type"),
        "return_business_id": return_meta.get("business_id"),
        "return_business_no": return_meta.get("business_no"),
        "supplier_name": sample.supplier.name if sample else purchase_meta.get("supplier_name"),
        "warehouse_id": sample.warehouse_id if sample else transfer.target_warehouse_id if transfer else purchase_meta.get("warehouse_id") or (production_task.warehouse_id if production_task else None),
        "warehouse_name": sample.warehouse.name if sample else (task.target_warehouse.name if task.target_warehouse else None) if transfer else purchase_meta.get("warehouse_name") or warehouse_name,
        "display_specification": purchase_meta.get("display_specification"),
        "required_tests": task.required_tests,
        # 指定质检员(派给谁检)与归属仓库,供前端展示与权限提示
        "assigned_inspector_id": task.assigned_inspector_id,
        # 只有 id 前端没法显示,补上姓名与仓库名
        "assigned_inspector_name": (
            (task.assigned_inspector.full_name or task.assigned_inspector.username)
            if getattr(task, "assigned_inspector", None)
            else None
        ),
        "assigned_by_id": task.assigned_by_id,
        "assigned_by_name": (
            (task.assigned_by.full_name or task.assigned_by.username)
            if getattr(task, "assigned_by", None)
            else None
        ),
        "assigned_at": task.assigned_at.isoformat() if task.assigned_at else None,
        "target_warehouse_id": task.target_warehouse_id,
        "target_warehouse_name": (
            task.target_warehouse.name if getattr(task, "target_warehouse", None) else None
        ),
        "completed_tests": len(inspections),
        "passed_tests": passed_tests,
        "latest_grade": latest_grade,
        "latest_report_summary": latest_report_summary,
        "sample_consumption_total": float(sample_consumption_total),
        "status": status,
        "status_label": status_label(status),
        "inbound_status": inbound.status if inbound else None,
        "inbound_status_label": status_label(inbound.status) if inbound else None,
        "inbound_application_id": inbound.id if inbound else None,
        "submitted_by_id": task.submitted_by_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "disposition_type": disposition_data["type"] if disposition_data else None,
        "disposition_quantity": disposition_data["quantity"] if disposition_data else None,
        "disposition_reason": disposition_data["reason"] if disposition_data else None,
        "related_production_task_id": disposition_data["related_production_task_id"] if disposition_data else None,
        "related_production_task_no": disposition_data["related_production_task_no"] if disposition_data else None,
        "quality_cost_record_id": quality_cost.id if quality_cost else None,
        "quality_cost_status": quality_cost.status if quality_cost else None,
        "quality_cost_status_label": status_label(quality_cost.status) if quality_cost else None,
        "can_record_inspection": status not in {"completed", "rejected", "scrapped", "rework_created"},
        "can_rework": status == "pending_disposition" and not transfer and not sample,
        "can_scrap": status == "pending_disposition" and not transfer and not sample,
        "can_allow_inbound": status == "ready_for_inbound" and not transfer and not sample,
        "can_apply_inbound": status == "ready_for_inbound" and source_type not in {
            "purchase_quality",
            "sales_return_reinspection",
            "purchase_return_reinspection",
            "stock_transfer",
            "supplier_sample",
        },
        "workflow_hint": (
            ("请录入样品质检结果并上传报告；完成后自动归档至供应商可供货名录，不生成库存入库。" if status == "pending" else "样品质检完成；结果已归档，不生成库存入库。")
            if sample else
            "继续录入质检结果，达到规定次数后自动进入后续处理。"
            if status in {"pending", "inspecting"}
            else "存在不合格结果，请处理返工或报废。"
            if status == "pending_disposition"
            else "质检通过，可执行准许入库。"
            if status == "ready_for_inbound"
            else "入库申请处理中，等待审批。"
            if status == "inbound_pending"
            else "该质检任务已完成闭环。"
        ),
    }


def _ensure_failed_task(task: QualityTask, inspections: list[QualityInspection]) -> None:
    if not inspections or all(item.grade != "不合格" and item.is_passed for item in inspections):
        raise HTTPException(status_code=409, detail="当前质检任务不存在不合格结果")


def _round_amount(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.0001"))


def _usage_cost(usage: ProductionMaterialUsage) -> Decimal:
    quantity = Decimal(usage.actual_quantity or 0)
    if quantity <= 0:
        quantity = Decimal(usage.approved_quantity or usage.requested_quantity or usage.planned_quantity or 0)
    unit_price = Decimal(usage.material.price if usage.material else 0)
    return quantity * unit_price


def _estimate_scrap_cost(task: ProductionTask, scrap_quantity: Decimal) -> Decimal:
    produced_quantity = Decimal(task.produced_quantity or 0)
    if produced_quantity <= 0:
        return Decimal("0.00")
    total_material_cost = sum((_usage_cost(usage) for usage in task.usages), Decimal("0"))
    ratio = Decimal(scrap_quantity) / produced_quantity
    if ratio > Decimal("1"):
        ratio = Decimal("1")
    return _round_amount(total_material_cost * ratio)


def _estimate_rework_cost(bom: BOM | None, quantity: Decimal) -> Decimal:
    if bom is None:
        return Decimal("0.00")
    total = Decimal("0")
    for item in bom.items:
        unit_price = Decimal(item.material.price if item.material else 0)
        total += Decimal(quantity) * Decimal(item.standard_quantity) * unit_price
    return _round_amount(total)


def _quality_recipient_role_codes(task: QualityTask) -> tuple[str, ...]:
    return (
        ("purchase", "inventory", "finance", "manager", "chairman")
        if task.source_category == "purchase"
        else ("production", "inventory", "finance", "manager", "chairman")
    )


def _quality_context(task: QualityTask, production_task: ProductionTask | None) -> dict:
    purchase_meta = parse_purchase_quality_meta(production_task)
    return_meta = parse_return_quality_meta(production_task)
    if return_meta:
        return {
            "source_label": return_meta.get("source_label") or "退换货复检",
            "item_name": return_meta.get("item_label") or "退换货物料",
            "item_code": None,
            "purchase_order_no": None,
            "purchase_receipt_no": return_meta.get("business_no"),
            "production_task_no": production_task.task_no if production_task else None,
        }
    if task.source_category == "purchase":
        return {
            "source_label": "采购来料",
            "item_name": purchase_meta.get("item_name") or (production_task.product.name if production_task and production_task.product else "来料"),
            "item_code": purchase_meta.get("item_code"),
            "purchase_order_no": purchase_meta.get("purchase_order_no"),
            "purchase_receipt_no": purchase_meta.get("purchase_receipt_no"),
            "production_task_no": production_task.task_no if production_task else None,
        }
    return {
        "source_label": "生产质检",
        "item_name": production_task.product.name if production_task and production_task.product else "产成品",
        "item_code": production_task.product.product_code if production_task and production_task.product else None,
        "purchase_order_no": None,
        "purchase_receipt_no": None,
        "production_task_no": production_task.task_no if production_task else None,
    }


def _quality_snapshot(inspections: list[QualityInspection]) -> dict:
    latest = inspections[-1] if inspections else None
    sample_consumption_total = sum((Decimal(item.sample_consumption_qty or 0) for item in inspections), Decimal("0"))
    return {
        "latest_grade": latest.grade if latest else None,
        "latest_passed": latest.is_passed if latest else None,
        "latest_summary": latest.report_summary if latest else None,
        "sample_consumption_total": sample_consumption_total,
    }


def _build_quality_sync_content(
    task: QualityTask,
    production_task: ProductionTask | None,
    *,
    action_text: str,
    inspections: list[QualityInspection] | None = None,
    inspected_sequence: int | None = None,
    inbound_quantity: Decimal | None = None,
    disposition: QualityDisposition | None = None,
    remark: str | None = None,
) -> str:
    context = _quality_context(task, production_task)
    snapshot = _quality_snapshot(inspections or [])
    parts = [
        f"{context['source_label']}任务 {task.quality_no}",
        f"批次 {task.batch_no}",
        action_text,
        f"当前状态：{status_label(task.status)}",
    ]
    if context["item_name"]:
        parts.append(f"对象：{context['item_name']}")
    if context["purchase_order_no"]:
        parts.append(f"采购订单：{context['purchase_order_no']}")
    if context["purchase_receipt_no"]:
        parts.append(f"到货单：{context['purchase_receipt_no']}")
    if context["production_task_no"] and task.source_category != "purchase":
        parts.append(f"来源任务：{context['production_task_no']}")
    if inspected_sequence is not None:
        parts.append(f"质检轮次：第 {inspected_sequence} 次")
    if snapshot["latest_grade"]:
        result_text = "通过" if snapshot["latest_passed"] else "不通过"
        parts.append(f"最新等级：{snapshot['latest_grade']}（{result_text}）")
    if snapshot["latest_summary"]:
        parts.append(f"报告摘要：{snapshot['latest_summary']}")
    if snapshot["sample_consumption_total"] > 0:
        parts.append(f"累计取样耗用：{float(snapshot['sample_consumption_total'])}")
    if inbound_quantity is not None:
        parts.append(f"本次准许入库数量：{float(inbound_quantity)}")
    if disposition is not None:
        parts.append(
            f"处理方式：{'返工' if disposition.disposition_type == 'rework' else '报废'}，数量 {float(disposition.quantity)}，原因：{disposition.reason}"
        )
    if remark:
        parts.append(f"备注：{remark}")
    return "；".join(parts) + "。"


def _sync_return_reinspection_status(
    db: Session,
    *,
    task: QualityTask,
    production_task: ProductionTask | None,
    inspections: list[QualityInspection],
    current_user: User,
) -> None:
    meta = parse_return_quality_meta(production_task)
    if not meta:
        return
    business_type = meta.get("business_type")
    business_id = meta.get("business_id")
    if not business_type or not business_id:
        return
    if len(inspections) < task.required_tests:
        status = "pending"
    else:
        status = "rejected" if any(_is_failed_grade(item.grade) or not item.is_passed for item in inspections) else "approved"
    if business_type == "sales_return":
        row = db.execute(select(SalesReturn).where(SalesReturn.id == int(business_id))).scalar_one_or_none()
        module = "sales"
        recipient_ids = sales_return_notice_user_ids(db, row) if row else []
    elif business_type == "purchase_return":
        row = db.execute(select(PurchaseReturn).where(PurchaseReturn.id == int(business_id))).scalar_one_or_none()
        module = "purchase"
        recipient_ids = [row.returned_by_id] if row else []
    else:
        return
    if row is None:
        return
    row.reinspection_status = status
    inventory_request = None
    if business_type == "sales_return" and status == "approved":
        inventory_request, inventory_user_ids = create_sales_return_inbound_request(
            db,
            row,
            requested_by_id=current_user.id,
        )
        task.status = "completed"
        if production_task is not None:
            production_task.status = "completed"
        complete_todos(db, business_type="quality_task", business_id=task.id)
        recipient_ids = list(dict.fromkeys([*recipient_ids, *inventory_user_ids]))
    if status in {"approved", "rejected"}:
        create_notification(
            db,
            title="退换货复检结果已更新",
            content=(
                f"{meta.get('source_label') or '退换货复检'} {meta.get('business_no') or row.id} "
                f"已完成复检，结果：{status_label(status)}。"
                + (f" 已生成库存入库作业 {inventory_request.request_no}。" if inventory_request else "")
            ),
            module=module,
            recipient_user_ids=recipient_ids,
            created_by_id=current_user.id,
            category="success" if status == "approved" else "warning",
        )


@router.get("/tasks")
def list_quality_tasks(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repair_pending_transfer_jobs(db)
    repair_quality_workflows(db)
    tasks = db.execute(select(QualityTask).order_by(QualityTask.created_at.desc())).scalars().all()
    # 普通质检员只能看所属仓库的任务;超级质检员看所有。
    # 指定给本人的任务始终可见(即使任务归属仓库不在其范围内),
    # 否则"指定了质检员却看不到"会造成流程卡死。
    if current_user.role.code == "quality":
        scoped_warehouse_ids = get_inventory_scope_warehouse_ids(db, current_user)
        if scoped_warehouse_ids is None:
            tasks = [
                task for task in tasks
                if (not task.stock_transfer_id and task.source_category != "supplier_sample")
                or task.assigned_inspector_id == current_user.id
            ]
        else:
            scoped_set = set(scoped_warehouse_ids)
            # 批量取影子生产任务的仓库,避免在循环里逐条查库(N+1)
            pt_ids = [row.production_task_id for row in tasks if row.production_task_id]
            pt_map = {
                row.id: row
                for row in db.execute(
                    select(ProductionTask).where(ProductionTask.id.in_(pt_ids))
                ).scalars().all()
            } if pt_ids else {}
            filtered: list[QualityTask] = []
            for task in tasks:
                if task.assigned_inspector_id == current_user.id:
                    filtered.append(task)
                    continue
                if (task.stock_transfer_id or task.source_category == "supplier_sample") and task.assigned_inspector_id:
                    continue
                pt = pt_map.get(task.production_task_id)
                meta = parse_purchase_quality_meta(pt) if pt else {}
                warehouse_id = (
                    task.target_warehouse_id
                    or meta.get("warehouse_id")
                    or (pt.warehouse_id if pt else None)
                )
                # 仓库未知的历史任务按可见处理,避免旧数据凭空消失
                if warehouse_id is None or warehouse_id in scoped_set:
                    filtered.append(task)
            tasks = filtered
    task_ids = [row.id for row in tasks]
    sample_map = {
        row.quality_task_id: row
        for row in db.execute(
            select(SupplierSample).options(
                joinedload(SupplierSample.supplier), joinedload(SupplierSample.product),
                joinedload(SupplierSample.material), joinedload(SupplierSample.packaging_config), joinedload(SupplierSample.warehouse),
            ).where(SupplierSample.quality_task_id.in_(task_ids))
        ).scalars().all()
    } if task_ids else {}
    production_task_ids = [row.production_task_id for row in tasks]

    production_tasks = {
        row.id: row
        for row in db.execute(
            select(ProductionTask).options(joinedload(ProductionTask.product)).where(ProductionTask.id.in_(production_task_ids))
        ).scalars().all()
    } if production_task_ids else {}

    inspections = db.execute(
        select(QualityInspection).where(QualityInspection.quality_task_id.in_(task_ids)).order_by(QualityInspection.sequence_no.asc())
    ).scalars().all() if task_ids else []
    inbounds = db.execute(
        select(QualityInboundApplication).where(QualityInboundApplication.quality_task_id.in_(task_ids))
    ).scalars().all() if task_ids else []
    dispositions = db.execute(
        select(QualityDisposition).where(QualityDisposition.quality_task_id.in_(task_ids))
    ).scalars().all() if task_ids else []

    inspection_map: dict[int, list[QualityInspection]] = {}
    for inspection in inspections:
        inspection_map.setdefault(inspection.quality_task_id, []).append(inspection)
    inbound_map = {row.quality_task_id: row for row in inbounds}
    disposition_map = {row.quality_task_id: row for row in dispositions}
    disposition_ids = [row.id for row in dispositions]
    quality_cost_map = {
        row.quality_disposition_id: row
        for row in db.execute(select(QualityCostRecord).where(QualityCostRecord.quality_disposition_id.in_(disposition_ids))).scalars().all()
    } if disposition_ids else {}

    related_task_ids = [row.related_production_task_id for row in dispositions if row.related_production_task_id]
    related_task_map = {
        row.id: row for row in db.execute(select(ProductionTask).where(ProductionTask.id.in_(related_task_ids))).scalars().all()
    } if related_task_ids else {}

    warehouse_ids = {
        pt.warehouse_id for pt in production_tasks.values() if pt.warehouse_id
    } | {
        meta.get("warehouse_id") for meta in (parse_purchase_quality_meta(pt) for pt in production_tasks.values())
    }
    warehouse_map = {
        row.id: row.name
        for row in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids or [-1]))).scalars().all()
    } if warehouse_ids else {}

    return api_response(
        data=[
            _serialize_quality_task(
                task,
                production_tasks.get(task.production_task_id),
                inspection_map.get(task.id, []),
                inbound_map.get(task.id),
                disposition_map.get(task.id),
                quality_cost_map.get(disposition_map.get(task.id).id) if disposition_map.get(task.id) else None,
                related_task_map,
                warehouse_map.get(
                    (production_tasks.get(task.production_task_id).warehouse_id if production_tasks.get(task.production_task_id) else None)
                    or 0
                ),
                sample_map.get(task.id),
            )
            for task in tasks
        ]
    )


@router.post("/tasks/manual")
def create_manual_quality_task(
    payload: QualityTaskManualCreate,
    current_user: User = Depends(require_roles("quality", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    created_tasks: list[dict] = []

    if payload.purchase_order_id:
        order = db.execute(
            select(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.supplier),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            )
            .where(PurchaseOrder.id == payload.purchase_order_id)
        ).unique().scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="采购订单不存在")

        receipts = db.execute(
            select(PurchaseReceipt)
            .where(
                PurchaseReceipt.purchase_order_id == order.id,
                PurchaseReceipt.requires_quality.is_(True),
            )
            .order_by(PurchaseReceipt.created_at.asc(), PurchaseReceipt.id.asc())
        ).scalars().all()
        if not receipts:
            raise HTTPException(status_code=409, detail="该采购订单尚无需要质检的到货记录")

        existing_task_rows = db.execute(select(QualityTask).where(QualityTask.source_category == "purchase")).scalars().all()
        existing_receipt_item_ids: set[int] = set()
        if existing_task_rows:
            production_task_ids = [row.production_task_id for row in existing_task_rows]
            production_task_map = {
                row.id: row
                for row in db.execute(select(ProductionTask).where(ProductionTask.id.in_(production_task_ids))).scalars().all()
            }
            for row in existing_task_rows:
                meta = parse_purchase_quality_meta(production_task_map.get(row.production_task_id))
                if meta.get("purchase_order_id") == order.id and meta.get("purchase_receipt_item_id"):
                    existing_receipt_item_ids.add(int(meta["purchase_receipt_item_id"]))

        for receipt in receipts:
            tasks = create_purchase_quality_tasks(
                db,
                order=order,
                receipt=receipt,
                submitted_by_id=current_user.id,
                required_tests=payload.required_tests,
                skip_receipt_item_ids=existing_receipt_item_ids,
                assigned_inspector_id=payload.assigned_inspector_id,
            )
            for task in tasks:
                production_task = db.execute(select(ProductionTask).where(ProductionTask.id == task.production_task_id)).scalar_one()
                meta = parse_purchase_quality_meta(production_task)
                created_tasks.append(
                    {
                        "id": task.id,
                        "quality_no": task.quality_no,
                        "batch_no": task.batch_no,
                        "item_name": meta.get("item_name"),
                        "item_type": meta.get("item_type"),
                        "purchase_order_no": meta.get("purchase_order_no"),
                        "purchase_receipt_no": meta.get("purchase_receipt_no"),
                    }
                )

        if not created_tasks:
            raise HTTPException(status_code=409, detail="该采购订单的来料质检任务已全部生成，无需重复创建")

        quality_user_ids = _role_user_ids(db, "quality", "manager", "chairman")
        # 按仓库路由质检员：优先到货仓库绑定的质检
        quality_user_ids = resolve_quality_recipients_for_warehouse(
            db,
            warehouse_id=payload.warehouse_id if getattr(payload, "warehouse_id", None) else None,
            fallback_user_ids=quality_user_ids,
        )
        for task_info in created_tasks:
            for user_id in quality_user_ids:
                create_todo(
                    db,
                    title=f"待处理采购质检任务 {task_info['quality_no']}",
                    description=f"采购订单 {task_info['purchase_order_no']} 已补录来料质检任务。",
                    module="quality",
                    assigned_user_id=user_id,
                    business_type="quality_task",
                    business_id=task_info["id"],
                    priority="high",
                )
        if quality_user_ids:
            create_notification(
                db,
                title="采购来料质检任务已补录",
                content=f"采购订单 {order.order_no} 已补录 {len(created_tasks)} 条来料质检任务。",
                module="quality",
                recipient_user_ids=quality_user_ids,
                created_by_id=current_user.id,
                category="warning",
            )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="quality",
            action="create_task",
            content=f"基于采购订单 {order.order_no} 补录来料质检任务",
            after_data={"purchase_order_id": order.id, "task_count": len(created_tasks), "tasks": created_tasks},
        )
        db.commit()
        return api_response(data={"count": len(created_tasks), "tasks": created_tasks})

    product = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if product is None or payload.quantity is None or not payload.batch_no:
        raise HTTPException(status_code=409, detail="请填写完整的手工质检任务信息")

    shadow_task = ProductionTask(
        task_no=next_business_no(db, "SC"),
        source_type="purchase_quality",
        product_id=payload.product_id,
        planned_quantity=payload.quantity,
        produced_quantity=payload.quantity,
        fluctuation_ratio=0,
        status="waiting_quality",
        created_by_id=current_user.id,
        notes=build_purchase_quality_notes(
            {
                "item_type": "product",
                "item_id": product.id,
                "item_code": product.product_code,
                "item_name": product.name,
                "quantity": float(payload.quantity),
                "remark": payload.remark,
            }
        ),
    )
    db.add(shadow_task)
    db.flush()

    quality_task = QualityTask(
        quality_no=next_business_no(db, "ZJY"),
        production_task_id=shadow_task.id,
        batch_no=payload.batch_no,
        required_tests=payload.required_tests,
        status="pending",
        submitted_by_id=current_user.id,
        # 手工建质检任务同样支持指定质检员与归属仓库
        assigned_inspector_id=resolve_assigned_inspector_id(db, payload.assigned_inspector_id),
        assigned_by_id=current_user.id if payload.assigned_inspector_id else None,
        assigned_at=now_local() if payload.assigned_inspector_id else None,
        target_warehouse_id=payload.target_warehouse_id,
    )
    db.add(quality_task)
    db.flush()

    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="create_task",
        content=f"创建采购来料质检任务 {quality_task.quality_no}",
        after_data={
            "quality_no": quality_task.quality_no,
            "product_name": product.name,
            "batch_no": payload.batch_no,
            "required_tests": payload.required_tests,
        },
    )
    db.commit()
    return api_response(data={"id": quality_task.id, "quality_no": quality_task.quality_no})


@router.post("/tasks/{task_id}/assign")
def assign_quality_task(
    task_id: int,
    payload: QualityTaskAssign,
    current_user: User = Depends(require_roles("quality_super", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    """指派或改派质检员，并保留分配人和时间。"""
    task = db.scalar(select(QualityTask).where(QualityTask.id == task_id).with_for_update())
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.status in {"completed", "rework_created", "scrapped", "rejected"}:
        raise HTTPException(status_code=409, detail="历史质检任务不能重新分配")
    inspector = db.scalar(
        select(User).join(Role, User.role_id == Role.id).where(
            User.id == payload.assigned_inspector_id,
            User.is_active.is_(True),
            Role.code.in_(QUALITY_ASSIGNABLE_ROLE_CODES),
        )
    )
    if inspector is None:
        raise HTTPException(status_code=404, detail="指定人员不是在岗质检员")
    old_id = task.assigned_inspector_id
    task.assigned_inspector_id = inspector.id
    task.assigned_by_id = current_user.id
    task.assigned_at = now_local()
    complete_todos(db, business_type="quality_task", business_id=task.id)
    create_todo(
        db,
        title=f"待处理质检任务 {task.quality_no}",
        description=f"由 {current_user.full_name or current_user.username} 分配，请及时处理。",
        module="quality",
        assigned_user_id=inspector.id,
        business_type="quality_task",
        business_id=task.id,
        priority="high",
    )
    create_notification(
        db,
        title="质检任务已分配",
        content=f"质检任务 {task.quality_no} 已分配给您。",
        module="quality",
        recipient_user_ids=[inspector.id],
        created_by_id=current_user.id,
        category="info",
    )
    write_operation_log(
        db, user_id=current_user.id, module="quality", action="assign_task",
        content=f"分配质检任务 {task.quality_no} 给 {inspector.full_name or inspector.username}",
        after_data={"task_id": task.id, "old_assigned_inspector_id": old_id, "assigned_inspector_id": inspector.id},
    )
    db.commit()
    return api_response(msg="质检任务分配成功")


@router.get("/tasks/{task_id}")
def get_quality_task_detail(task_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.execute(select(QualityTask).where(QualityTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.stock_transfer_id and current_user.role.code == "quality":
        scoped = get_inventory_scope_warehouse_ids(db, current_user)
        if task.assigned_inspector_id != current_user.id and (scoped is None or task.target_warehouse_id not in scoped):
            raise HTTPException(status_code=403, detail="仅目标仓库质检员可查看此移库质检任务")
    sample = db.scalar(select(SupplierSample).options(
        joinedload(SupplierSample.supplier), joinedload(SupplierSample.product),
        joinedload(SupplierSample.material), joinedload(SupplierSample.warehouse),
    ).where(SupplierSample.quality_task_id == task.id))
    if sample and current_user.role.code == "quality":
        scoped = get_inventory_scope_warehouse_ids(db, current_user)
        if task.assigned_inspector_id != current_user.id and sample.warehouse_id not in (scoped or []) and sample.warehouse.qc_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="仅目标仓库质检员可查看此样品质检任务")
    production_task = db.execute(
        select(ProductionTask).options(joinedload(ProductionTask.product)).where(ProductionTask.id == task.production_task_id)
    ).scalar_one_or_none()
    inspections = db.execute(
        select(QualityInspection).where(QualityInspection.quality_task_id == task.id).order_by(QualityInspection.sequence_no.asc())
    ).scalars().all()
    inbound = db.execute(
        select(QualityInboundApplication).where(QualityInboundApplication.quality_task_id == task.id)
    ).scalar_one_or_none()
    disposition = db.execute(
        select(QualityDisposition).where(QualityDisposition.quality_task_id == task.id)
    ).scalar_one_or_none()
    quality_cost = db.execute(
        select(QualityCostRecord).where(QualityCostRecord.quality_disposition_id == disposition.id)
    ).scalar_one_or_none() if disposition else None
    related_task_map: dict[int, ProductionTask] = {}
    if disposition and disposition.related_production_task_id:
        related_task = db.execute(select(ProductionTask).where(ProductionTask.id == disposition.related_production_task_id)).scalar_one_or_none()
        if related_task:
            related_task_map[related_task.id] = related_task
    return api_response(
        data={
            **_serialize_quality_task(task, production_task, inspections, inbound, disposition, quality_cost, related_task_map, sample=sample),
            "inspections": [_serialize_inspection(item) for item in inspections],
            "inbound_application": {
                "id": inbound.id,
                "warehouse_id": inbound.warehouse_id,
                "status": inbound.status,
                "remark": inbound.remark,
                "approved_at": inbound.approved_at.isoformat() if inbound.approved_at else None,
            } if inbound else None,
            "disposition": _serialize_disposition(disposition, related_task_map),
            "quality_cost_record": {
                "id": quality_cost.id,
                "estimated_amount": float(quality_cost.estimated_amount),
                "recognized_amount": float(quality_cost.recognized_amount),
                "status": quality_cost.status,
                "status_label": status_label(quality_cost.status),
                "remark": quality_cost.remark,
                "recognized_at": quality_cost.recognized_at.isoformat() if quality_cost.recognized_at else None,
            } if quality_cost else None,
        }
    )


@router.post("/inspections")
def create_inspection(
    payload: InspectionCreate,
    current_user: User = Depends(require_roles("quality", "quality_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    task = db.execute(select(QualityTask).where(QualityTask.id == payload.quality_task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.status in {"completed", "rejected", "scrapped", "rework_created"}:
        raise HTTPException(status_code=409, detail="当前质检任务状态不允许继续录入")
    sample = db.scalar(select(SupplierSample).where(SupplierSample.quality_task_id == task.id))
    if sample:
        if sample.status != "pending_quality":
            raise HTTPException(status_code=409, detail="样品尚未收样或已完成质检")
        if current_user.role.code == "quality":
            scoped = get_inventory_scope_warehouse_ids(db, current_user)
            warehouse = db.get(Warehouse, sample.warehouse_id)
            if task.assigned_inspector_id != current_user.id and sample.warehouse_id not in (scoped or []) and (not warehouse or warehouse.qc_user_id != current_user.id):
                raise HTTPException(status_code=403, detail="仅目标仓库质检员可处理此样品")
        if Decimal(payload.inspected_quantity) > Decimal(sample.received_quantity or sample.quantity):
            raise HTTPException(status_code=409, detail="检验数量不能大于收样数量")
        if Decimal(payload.sample_consumption_qty) > Decimal(sample.received_quantity or sample.quantity):
            raise HTTPException(status_code=409, detail="样品耗用不能大于收样数量")
    if task.stock_transfer_id:
        if task.status != "pending":
            raise HTTPException(status_code=409, detail="移库质检已处理")
        if Decimal(payload.sample_consumption_qty or 0) != 0:
            raise HTTPException(status_code=409, detail="移库质检不能直接消耗样品；请另行处理库存异常")
        if current_user.role.code == "quality" and task.assigned_inspector_id != current_user.id:
            scoped = get_inventory_scope_warehouse_ids(db, current_user)
            if scoped is None or task.target_warehouse_id not in scoped:
                raise HTTPException(status_code=403, detail="仅目标仓库的质检员可以处理此移库质检任务")
    # 需求:该质检任务只能由指定质检员处理;超级质检员与管理层可代为处理。
    # 普通质检员越权录入他人任务时明确报错,而不是静默通过。
    role_code = current_user.role.code if current_user.role else None
    if (
        task.assigned_inspector_id
        and task.assigned_inspector_id != current_user.id
        and role_code not in {"quality_super", "manager", "chairman", "system_admin"}
    ):
        assigned_user = db.execute(select(User).where(User.id == task.assigned_inspector_id)).scalar_one_or_none()
        assigned_name = assigned_user.full_name if assigned_user else f"#{task.assigned_inspector_id}"
        raise HTTPException(status_code=403, detail=f"该质检任务已指定由 {assigned_name} 处理,不能越权录入")
    duplicate = db.execute(
        select(QualityInspection).where(
            QualityInspection.quality_task_id == task.id,
            QualityInspection.sequence_no == payload.sequence_no,
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="该序号的质检记录已存在")
    production_task = db.execute(
        select(ProductionTask).options(joinedload(ProductionTask.product)).where(ProductionTask.id == task.production_task_id)
    ).scalar_one_or_none()
    passed = payload.is_passed and not _is_failed_grade(payload.grade)
    inspection = QualityInspection(
        quality_task_id=task.id,
        inspector_user_id=current_user.id,
        sequence_no=payload.sequence_no,
        result_text=payload.result_text,
        grade=payload.grade,
        is_passed=passed,
        inspected_quantity=payload.inspected_quantity,
        sample_consumption_qty=payload.sample_consumption_qty,
        report_summary=payload.report_summary.strip() if payload.report_summary else None,
    )
    db.add(inspection)
    db.flush()
    if sample:
        task.status = "completed"
        sample.status = "completed"
        complete_todos(db, business_type="quality_task", business_id=task.id)
        create_notification(
            db, title="供应商样品质检已完成",
            content=f"{sample.sample_no} 质检结果：{'合格' if passed else '不合格'}（{inspection.grade}）。请到供应商名录查看报告。",
            module="quality", recipient_user_ids=[sample.requested_by_id],
            created_by_id=current_user.id, category="success" if passed else "warning",
        )
        write_operation_log(db, user_id=current_user.id, module="quality", action="inspect_supplier_sample",
                            content=f"供应商样品质检 {sample.sample_no}", after_data={"sample_id": sample.id, "passed": passed, "inspection_id": inspection.id})
        db.commit()
        return api_response(data={"id": inspection.id, "status": task.status})
    if task.stock_transfer_id:
        sync_transfer_inspection(
            db, task, current_user,
            passed=passed,
            reason=payload.report_summary or payload.result_text,
        )
        write_operation_log(db, user_id=current_user.id, module="quality", action="inspect_stock_transfer", content=f"移库质检 {task.quality_no}", after_data={"stock_transfer_id": task.stock_transfer_id, "passed": passed})
        db.commit()
        return api_response(data={"id": inspection.id, "status": task.status})
    total_count = db.execute(select(QualityInspection).where(QualityInspection.quality_task_id == task.id)).scalars().all()
    if len(total_count) >= task.required_tests:
        task.status = "pending_disposition" if any(_is_failed_grade(item.grade) or not item.is_passed for item in total_count) else "ready_for_inbound"
    else:
        task.status = "inspecting"
    _sync_return_reinspection_status(db, task=task, production_task=production_task, inspections=total_count, current_user=current_user)
    watcher_role_codes = _quality_recipient_role_codes(task)
    watcher_ids = _role_user_ids(db, *watcher_role_codes)
    if watcher_ids:
        create_notification(
            db,
            title="质检结果已更新",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text="已录入新的质检结果",
                inspections=total_count,
                inspected_sequence=payload.sequence_no,
            ),
            module="quality",
            recipient_user_ids=watcher_ids,
            created_by_id=current_user.id,
            category="warning" if task.status == "pending_disposition" else "info",
            is_popup=False,
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="create_inspection",
        content=f"录入质检结果 {task.quality_no}",
        after_data={
            "quality_task_id": task.id,
            "quality_no": task.quality_no,
            "sequence_no": payload.sequence_no,
            "status": task.status,
            "sample_consumption_qty": float(payload.sample_consumption_qty or 0),
            "report_summary": inspection.report_summary,
        },
    )
    db.commit()
    return api_response(data={"id": inspection.id, "status": task.status})


@router.post("/tasks/{task_id}/rework")
def create_rework_task(
    task_id: int,
    payload: QualityReworkCreate,
    current_user: User = Depends(require_roles("quality", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    task = db.execute(select(QualityTask).where(QualityTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.source_category == "supplier_sample":
        raise HTTPException(status_code=409, detail="供应商样品质检不参与生产返工流程")
    inspections = db.execute(select(QualityInspection).where(QualityInspection.quality_task_id == task.id)).scalars().all()
    _ensure_failed_task(task, inspections)
    existing = db.execute(select(QualityDisposition).where(QualityDisposition.quality_task_id == task.id)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="当前质检任务已做返工/报废处理")

    production_task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == task.production_task_id)
    ).unique().scalar_one_or_none()
    if production_task is None:
        raise HTTPException(status_code=404, detail="来源生产任务不存在")
    if Decimal(payload.quantity) > Decimal(production_task.produced_quantity or 0):
        raise HTTPException(status_code=409, detail="返工数量不能大于原任务产出数量")

    rework_task = ProductionTask(
        task_no=next_business_no(db, "SC"),
        source_type="rework",
        product_id=production_task.product_id,
        parent_task_id=production_task.id,
        planned_quantity=payload.quantity,
        fluctuation_ratio=production_task.fluctuation_ratio,
        status="pending_material",
        created_by_id=current_user.id,
        notes=f"返工来源：{task.quality_no}；原因：{payload.reason}",
    )
    db.add(rework_task)
    db.flush()

    bom = db.execute(select(BOM).options(joinedload(BOM.items)).where(BOM.product_id == production_task.product_id)).unique().scalar_one_or_none()
    if bom:
        for item in bom.items:
            planned_quantity = Decimal(payload.quantity) * Decimal(item.standard_quantity)
            db.add(
                ProductionMaterialUsage(
                    production_task_id=rework_task.id,
                    material_id=item.material_id,
                    planned_quantity=planned_quantity,
                    requested_quantity=planned_quantity,
                    approved_quantity=planned_quantity,
                )
            )

    disposition = QualityDisposition(
        quality_task_id=task.id,
        disposition_type="rework",
        quantity=payload.quantity,
        reason=payload.reason,
        related_production_task_id=rework_task.id,
        created_by_id=current_user.id,
    )
    db.add(disposition)
    db.flush()
    estimated_cost = _estimate_rework_cost(bom, Decimal(payload.quantity))
    cost_record = QualityCostRecord(
        quality_disposition_id=disposition.id,
        estimated_amount=estimated_cost,
        recognized_amount=0,
        status="pending",
        remark="待财务归集返工成本",
    )
    db.add(cost_record)
    task.status = "rework_created"
    production_task.status = "rework_pending"
    complete_todos(db, business_type="quality_task", business_id=task.id)

    production_user_ids = _role_user_ids(db, "production", "manager", "chairman")
    production_notice_user_ids = _role_user_ids(db, "production", "inventory", "manager", "chairman")
    for user_id in production_user_ids:
        create_todo(
            db,
            title=f"待处理返工任务 {rework_task.task_no}",
            description=f"质检任务 {task.quality_no} 已发起返工。",
            module="production",
            assigned_user_id=user_id,
            business_type="production_task",
            business_id=rework_task.id,
        )
    if production_notice_user_ids:
        create_notification(
            db,
            title="新增返工任务",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text=f"已生成返工任务 {rework_task.task_no}",
                inspections=inspections,
                disposition=disposition,
            ),
            module="production",
            recipient_user_ids=production_notice_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )

    finance_user_ids = _role_user_ids(db, "finance", "manager", "chairman")
    for user_id in finance_user_ids:
        create_todo(
            db,
            title=f"待归集返工成本 {task.quality_no}",
            description=f"返工任务 {rework_task.task_no} 已生成，预计成本 {estimated_cost}。",
            module="finance",
            assigned_user_id=user_id,
            business_type="quality_cost_record",
            business_id=cost_record.id,
        )
    if finance_user_ids:
        create_notification(
            db,
            title="新增返工成本待确认",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text=f"已发起返工，预计成本 {estimated_cost}",
                inspections=inspections,
                disposition=disposition,
            ),
            module="finance",
            recipient_user_ids=finance_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="create_rework_task",
        content=f"为质检任务 {task.quality_no} 发起返工",
        after_data={
            "quality_no": task.quality_no,
            "rework_task_no": rework_task.task_no,
            "quantity": float(payload.quantity),
            "quality_cost_record_id": cost_record.id,
            "estimated_cost": float(estimated_cost),
        },
    )
    db.commit()
    return api_response(data={"rework_task_id": rework_task.id, "rework_task_no": rework_task.task_no, "quality_cost_record_id": cost_record.id})


@router.post("/tasks/{task_id}/scrap")
def create_scrap_record(
    task_id: int,
    payload: QualityScrapCreate,
    current_user: User = Depends(require_roles("quality", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    task = db.execute(select(QualityTask).where(QualityTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.source_category == "supplier_sample":
        raise HTTPException(status_code=409, detail="供应商样品质检不参与库存报废流程")
    inspections = db.execute(select(QualityInspection).where(QualityInspection.quality_task_id == task.id)).scalars().all()
    _ensure_failed_task(task, inspections)
    existing = db.execute(select(QualityDisposition).where(QualityDisposition.quality_task_id == task.id)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="当前质检任务已做返工/报废处理")

    production_task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == task.production_task_id)
    ).unique().scalar_one_or_none()
    if production_task is None:
        raise HTTPException(status_code=404, detail="来源生产任务不存在")
    if Decimal(payload.quantity) > Decimal(production_task.produced_quantity or 0):
        raise HTTPException(status_code=409, detail="报废数量不能大于原任务产出数量")

    disposition = QualityDisposition(
        quality_task_id=task.id,
        disposition_type="scrap",
        quantity=payload.quantity,
        reason=payload.reason,
        created_by_id=current_user.id,
    )
    db.add(disposition)
    db.flush()
    estimated_cost = _estimate_scrap_cost(production_task, Decimal(payload.quantity))
    cost_record = QualityCostRecord(
        quality_disposition_id=disposition.id,
        estimated_amount=estimated_cost,
        recognized_amount=0,
        status="pending",
        remark="待财务确认报废损失",
    )
    db.add(cost_record)
    task.status = "scrapped"
    production_task.scrap_quantity = Decimal(production_task.scrap_quantity or 0) + Decimal(payload.quantity)
    production_task.status = "scrapped" if Decimal(production_task.scrap_quantity or 0) >= Decimal(production_task.produced_quantity or 0) else "partially_scrapped"
    production_task.notes = f"{production_task.notes or ''}\n[报废] 数量：{payload.quantity}；原因：{payload.reason}".strip()
    complete_todos(db, business_type="quality_task", business_id=task.id)

    finance_user_ids = _role_user_ids(db, "finance", "manager", "chairman")
    for user_id in finance_user_ids:
        create_todo(
            db,
            title=f"待处理报废成本 {task.quality_no}",
            description=f"质检不合格已报废，数量 {payload.quantity}，请关注成本影响。",
            module="finance",
            assigned_user_id=user_id,
            business_type="quality_cost_record",
            business_id=cost_record.id,
        )
    if finance_user_ids:
        create_notification(
            db,
            title="新增报废记录",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text=f"已登记报废，预计损失 {estimated_cost}",
                inspections=inspections,
                disposition=disposition,
            ),
            module="finance",
            recipient_user_ids=finance_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    production_inventory_user_ids = _role_user_ids(db, "production", "inventory", "manager", "chairman")
    if production_inventory_user_ids:
        create_notification(
            db,
            title="质检任务已报废",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text="已登记报废，请同步处理后续生产、库存与财务动作",
                inspections=inspections,
                disposition=disposition,
            ),
            module="quality",
            recipient_user_ids=production_inventory_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="create_scrap_record",
        content=f"为质检任务 {task.quality_no} 登记报废",
        after_data={
            "quality_no": task.quality_no,
            "quantity": float(payload.quantity),
            "reason": payload.reason,
            "quality_cost_record_id": cost_record.id,
            "estimated_cost": float(estimated_cost),
        },
    )
    db.commit()
    return api_response(data={"quality_task_id": task.id, "scrap_quantity": float(payload.quantity), "quality_cost_record_id": cost_record.id})


@router.post("/tasks/{task_id}/allow-inbound")
def allow_inbound(
    task_id: int,
    payload: QualityAllowInbound,
    current_user: User = Depends(require_roles("quality", "quality_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    task = db.execute(select(QualityTask).where(QualityTask.id == task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.source_category == "supplier_sample":
        raise HTTPException(status_code=409, detail="供应商样品不计入库存，不能准许入库")
    if task.status != "ready_for_inbound":
        raise HTTPException(status_code=409, detail="当前质检任务未达到准许入库条件")

    production_task = db.execute(
        select(ProductionTask).options(joinedload(ProductionTask.product)).where(ProductionTask.id == task.production_task_id)
    ).scalar_one_or_none()
    if production_task is None:
        raise HTTPException(status_code=404, detail="来源任务不存在")

    if production_task.source_type == "purchase_quality" or task.source_category == "purchase":
        production_task, receipt, receipt_item, order, order_item = resolve_purchase_quality_context(db, task)
        inbound_quantity = Decimal(receipt_item.received_quantity or 0) - quality_sample_consumption(db, task.id)
        if inbound_quantity <= 0:
            raise HTTPException(status_code=409, detail="当前可入库数量不足，已被取样耗用完毕")

        task.status = "completed"
        complete_todos(db, business_type="quality_task", business_id=task.id)
        create_inventory_operation_request(
            db,
            operation_type="inbound",
            request_category="purchase_quality_inbound",
            source_module="quality",
            source_type="purchase_receipt",
            source_id=receipt.id,
            source_no=receipt.receipt_no,
            warehouse_id=(
                receipt.warehouse_id
                if order_item.packaging_config_id and not order_item.product_id and not order_item.material_id
                else _purchase_quality_target_warehouse_id(db, order_item)
            ),
            requested_by_id=current_user.id,
            items=[
                {
                    "product_id": order_item.product_id,
                    "material_id": order_item.material_id,
                    "packaging_config_id": order_item.packaging_config_id if not order_item.product_id and not order_item.material_id else None,
                    "purchase_receipt_item_id": receipt_item.id,
                    "batch_no": receipt_item.batch_no,
                    "quantity": float(inbound_quantity),
                    "unit_cost": float(Decimal(receipt_item.unit_cost or 0)),
                }
            ],
            remark=payload.remark,
            recipient_user_ids=_role_user_ids(db, "inventory"),
            title=f"待执行来料质检入库 {receipt.receipt_no}",
            description=f"采购到货单 {receipt.receipt_no} 已质检通过，请库管执行手工入库。",
        )

        purchase_quality_tasks = db.execute(select(QualityTask).where(QualityTask.source_category == "purchase")).scalars().all()
        purchase_task_ids = [row.production_task_id for row in purchase_quality_tasks]
        purchase_task_map = {
            row.id: row for row in db.execute(select(ProductionTask).where(ProductionTask.id.in_(purchase_task_ids))).scalars().all()
        } if purchase_task_ids else {}
        receipt_task_statuses: list[str] = []
        for row in purchase_quality_tasks:
            meta = parse_purchase_quality_meta(purchase_task_map.get(row.production_task_id))
            if meta.get("purchase_receipt_id") == receipt.id:
                receipt_task_statuses.append(row.status)
        if receipt_task_statuses and all(status == "completed" for status in receipt_task_statuses):
            # 质检结束后仍须由库管执行入库作业，不能提前标记为已入库。
            receipt.status = "pending_inbound"
            receipt.quality_status = "approved"
        else:
            receipt.status = "pending_quality"
            receipt.quality_status = "pending"

        recipient_user_ids = list(dict.fromkeys([order.purchaser_user_id, *_role_user_ids(db, "inventory", "finance", "manager", "chairman")]))
        inspections = db.execute(
            select(QualityInspection).where(QualityInspection.quality_task_id == task.id).order_by(QualityInspection.sequence_no.asc())
        ).scalars().all()
        create_notification(
            db,
            title="采购来料质检已准许入库",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text="已完成来料质检并生成库管入库作业",
                inspections=inspections,
                inbound_quantity=inbound_quantity,
                remark=payload.remark,
            ),
            module="quality",
            recipient_user_ids=recipient_user_ids,
            created_by_id=current_user.id,
            category="success",
        )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="quality",
            action="allow_quality_inbound",
            content=f"准许采购质检任务 {task.quality_no} 入库",
            after_data={
                "quality_no": task.quality_no,
                "purchase_order_no": order.order_no,
                "purchase_receipt_no": receipt.receipt_no,
                "inbound_quantity": float(inbound_quantity),
                "status": task.status,
            },
        )
        db.commit()
        return api_response(
            data={
                "id": task.id,
                "status": task.status,
                "purchase_receipt_id": receipt.id,
                "purchase_receipt_no": receipt.receipt_no,
                "inbound_quantity": float(inbound_quantity),
            }
        )

    return_meta = parse_return_quality_meta(production_task)
    if production_task.source_type == "sales_return_reinspection" and return_meta.get("business_type") == "sales_return":
        sales_return_id = return_meta.get("business_id")
        sales_return = db.execute(
            select(SalesReturn).where(SalesReturn.id == int(sales_return_id)).with_for_update()
        ).scalar_one_or_none() if sales_return_id else None
        if sales_return is None:
            raise HTTPException(status_code=404, detail="销售退换货质检关联单据不存在")
        if sales_return.status not in {"approved", "refunded"}:
            raise HTTPException(status_code=409, detail="销售退换货单尚未审批通过，不能生成回库作业")
        if sales_return.reinspection_status != "approved":
            raise HTTPException(status_code=409, detail="销售退换货复检尚未通过，不能生成回库作业")

        inbound_request, inventory_user_ids = create_sales_return_inbound_request(
            db,
            sales_return,
            requested_by_id=current_user.id,
        )
        task.status = "completed"
        production_task.status = "completed"
        complete_todos(db, business_type="quality_task", business_id=task.id)
        create_notification(
            db,
            title="销售退换货质检已放行",
            content=(
                f"销售退换货单 {sales_return.return_no} 已通过质检，"
                f"已生成库存入库作业 {inbound_request.request_no}。"
            ),
            module="quality",
            recipient_user_ids=list(dict.fromkeys([
                *sales_return_notice_user_ids(db, sales_return),
                *inventory_user_ids,
            ])),
            created_by_id=current_user.id,
            category="success",
        )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="quality",
            action="allow_sales_return_inbound",
            content=f"准许销售退换货复检任务 {task.quality_no} 入库",
            after_data={
                "quality_no": task.quality_no,
                "sales_return_no": sales_return.return_no,
                "inventory_request_no": inbound_request.request_no,
                "remark": payload.remark,
            },
        )
        db.commit()
        return api_response(
            data={
                "id": task.id,
                "status": task.status,
                "sales_return_id": sales_return.id,
                "inventory_request_id": inbound_request.id,
                "inventory_request_no": inbound_request.request_no,
            }
        )

    task.status = "completed"
    complete_todos(db, business_type="quality_task", business_id=task.id)
    inspections = db.execute(
        select(QualityInspection).where(QualityInspection.quality_task_id == task.id).order_by(QualityInspection.sequence_no.asc())
    ).scalars().all()

    production_user_ids = _role_user_ids(db, "production")
    production_notice_user_ids = _role_user_ids(db, "production", "inventory", "finance", "manager", "chairman")
    for user_id in production_user_ids:
        create_todo(
            db,
            title=f"待申请入库 {task.quality_no}",
            description=f"质检任务 {task.quality_no} 已准许入库，请在生产侧继续处理。",
            module="production",
            assigned_user_id=user_id,
            business_type="production_task",
            business_id=production_task.id,
            priority="high",
        )
    if production_notice_user_ids:
        create_notification(
            db,
            title="质检已准许入库",
            content=_build_quality_sync_content(
                task,
                production_task,
                action_text="已完成质检并准许入库，请生产侧继续发起入库",
                inspections=inspections,
                remark=payload.remark,
            ),
            module="production",
            recipient_user_ids=production_notice_user_ids,
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="allow_quality_inbound",
        content=f"准许质检任务 {task.quality_no} 入库",
        after_data={
            "quality_no": task.quality_no,
            "product_name": production_task.product.name if production_task.product else None,
            "remark": payload.remark,
            "status": task.status,
        },
    )
    db.commit()
    return api_response(data={"id": task.id, "status": task.status})


@router.post("/inbounds")
def create_inbound_application(
    payload: QualityInboundCreate,
    current_user: User = Depends(require_roles("quality", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    task = db.execute(select(QualityTask).options(joinedload(QualityTask.inspections)).where(QualityTask.id == payload.quality_task_id)).unique().scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    if task.source_category == "supplier_sample":
        raise HTTPException(status_code=409, detail="供应商样品不计入库存，不能申请入库")
    production_task = db.execute(select(ProductionTask).where(ProductionTask.id == task.production_task_id)).scalar_one_or_none()
    if production_task and (production_task.source_type == "purchase_quality" or task.source_category == "purchase"):
        raise HTTPException(status_code=409, detail="采购来料质检请直接执行“准许入库”，无需再发起入库申请")
    if production_task and production_task.source_type in {"sales_return_reinspection", "purchase_return_reinspection"}:
        raise HTTPException(status_code=409, detail="退换货复检请直接执行“准许入库”，系统将生成对应仓库作业")
    if task.status == "pending_disposition":
        raise HTTPException(status_code=409, detail="当前质检任务存在不合格结果，请先处理返工或报废")
    if len(task.inspections) < task.required_tests:
        raise HTTPException(status_code=409, detail="质检次数未达标")
    if any(inspection.grade == "不合格" for inspection in task.inspections):
        raise HTTPException(status_code=409, detail="存在不合格结果，不能提交入库申请")
    existing = db.execute(
        select(QualityInboundApplication).where(QualityInboundApplication.quality_task_id == task.id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="该质检任务已存在入库申请")
    inbound = QualityInboundApplication(
        quality_task_id=task.id,
        warehouse_id=payload.warehouse_id,
        requested_by_id=current_user.id,
        status="pending",
        remark=payload.remark,
    )
    db.add(inbound)
    db.flush()
    task.status = "inbound_pending"
    complete_todos(db, business_type="quality_task", business_id=task.id)
    approver_user_ids = _role_user_ids(db, "inventory", "manager", "chairman")
    for user_id in approver_user_ids:
        create_todo(
            db,
            title=f"待审批质检入库 {task.quality_no}",
            description=f"批次 {task.batch_no} 已完成质检，请审批入库申请。",
            module="quality",
            assigned_user_id=user_id,
            business_type="quality_inbound",
            business_id=inbound.id,
            priority="high",
        )
    if approver_user_ids:
        create_notification(
            db,
            title="新增质检入库申请",
            content=f"质检任务 {task.quality_no} 已提交入库申请，请及时审批。",
            module="quality",
            recipient_user_ids=approver_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="create_inbound_application",
        content=f"提交质检入库申请 {task.quality_no}",
        after_data={"quality_task_id": task.id, "inbound_application_id": inbound.id},
    )
    db.commit()
    return api_response(data={"id": inbound.id})


@router.get("/inbounds")
def list_inbound_applications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(QualityInboundApplication).order_by(QualityInboundApplication.created_at.desc())
    if current_user.role.code == "quality":
        query = query.where(QualityInboundApplication.requested_by_id == current_user.id)
    # 超级质检员:查看所有仓库的入库申请(不额外过滤)
    rows = db.execute(query).scalars().all()
    task_ids = [row.quality_task_id for row in rows]
    warehouse_ids = [row.warehouse_id for row in rows]
    tasks = {
        task.id: task
        for task in db.execute(select(QualityTask).where(QualityTask.id.in_(task_ids))).scalars().all()
    } if task_ids else {}
    warehouses = {
        warehouse.id: warehouse
        for warehouse in db.execute(select(Warehouse).where(Warehouse.id.in_(warehouse_ids))).scalars().all()
    } if warehouse_ids else {}
    return api_response(
        data=[
            {
                "id": row.id,
                "quality_task_id": row.quality_task_id,
                "quality_no": tasks[row.quality_task_id].quality_no if row.quality_task_id in tasks else None,
                "batch_no": tasks[row.quality_task_id].batch_no if row.quality_task_id in tasks else None,
                "warehouse_id": row.warehouse_id,
                "warehouse_name": warehouses[row.warehouse_id].name if row.warehouse_id in warehouses else None,
                "status": row.status,
                "status_label": status_label(row.status),
                "remark": row.remark,
                "requested_by_id": row.requested_by_id,
                "can_approve": current_user.role.code in {"inventory", "manager", "chairman", "system_admin"} and row.status == "pending",
                "readonly": current_user.role.code == "quality",
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@router.get("/inbounds/{application_id}")
def get_inbound_application_detail(application_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    application = db.execute(select(QualityInboundApplication).where(QualityInboundApplication.id == application_id)).scalar_one_or_none()
    if application is None:
        raise HTTPException(status_code=404, detail="入库申请不存在")

    task = db.execute(select(QualityTask).where(QualityTask.id == application.quality_task_id)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="质检任务不存在")
    production_task = db.execute(
        select(ProductionTask).options(joinedload(ProductionTask.product)).where(ProductionTask.id == task.production_task_id)
    ).scalar_one_or_none()
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == application.warehouse_id)).scalar_one_or_none()
    inspections = db.execute(
        select(QualityInspection).where(QualityInspection.quality_task_id == task.id).order_by(QualityInspection.sequence_no.asc())
    ).scalars().all()
    user_ids = [user_id for user_id in {application.requested_by_id, application.approved_by_id, task.submitted_by_id} if user_id]
    user_map = {
        row.id: row for row in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
    } if user_ids else {}
    transactions = db.execute(
        select(InventoryTransaction)
        .where(
            InventoryTransaction.transaction_type == "quality_inbound",
            or_(
                and_(InventoryTransaction.related_type == "quality_inbound", InventoryTransaction.related_id == application.id),
                and_(InventoryTransaction.related_type == "production_task", InventoryTransaction.related_id == task.production_task_id),
            ),
        )
        .order_by(InventoryTransaction.created_at.asc())
    ).scalars().all()
    return api_response(
        data={
            "id": application.id,
            "quality_task_id": task.id,
            "quality_no": task.quality_no,
            "batch_no": task.batch_no,
            "product_name": production_task.product.name if production_task and production_task.product else None,
            "production_task_id": production_task.id if production_task else None,
            "production_task_no": production_task.task_no if production_task else None,
            "warehouse_id": application.warehouse_id,
            "warehouse_name": warehouse.name if warehouse else None,
            "status": application.status,
            "status_label": status_label(application.status),
            "remark": application.remark,
            "requested_by_name": user_map[application.requested_by_id].full_name if application.requested_by_id in user_map else None,
            "approved_by_name": user_map[application.approved_by_id].full_name if application.approved_by_id in user_map else None,
            "submitted_by_name": user_map[task.submitted_by_id].full_name if task.submitted_by_id in user_map else None,
            "required_tests": task.required_tests,
            "completed_tests": len(inspections),
            "passed_tests": sum(1 for row in inspections if row.is_passed),
            "latest_grade": inspections[-1].grade if inspections else None,
            "created_at": application.created_at.isoformat() if application.created_at else None,
            "approved_at": application.approved_at.isoformat() if application.approved_at else None,
            "inspections": [_serialize_inspection(row) for row in inspections],
            "inventory_transactions": [
                {
                    "id": row.id,
                    "transaction_no": row.transaction_no,
                    "quantity": float(row.quantity),
                    "unit_cost": float(row.unit_cost or 0),
                    "total_cost": float(row.total_cost or 0),
                    "batch_no": row.batch_no,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in transactions
            ],
        }
    )


@router.post("/inbounds/{application_id}/approve")
def approve_inbound(
    application_id: int,
    payload: QualityInboundApproval,
    current_user: User = Depends(require_roles("inventory", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    application = db.execute(select(QualityInboundApplication).where(QualityInboundApplication.id == application_id)).scalar_one_or_none()
    if application is None:
        raise HTTPException(status_code=404, detail="入库申请不存在")
    if application.status != "pending":
        raise HTTPException(status_code=409, detail="入库申请已处理")
    quality_task = db.execute(select(QualityTask).where(QualityTask.id == application.quality_task_id)).scalar_one()
    production_task = db.execute(
        select(ProductionTask)
        .options(joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material))
        .where(ProductionTask.id == quality_task.production_task_id)
    ).unique().scalar_one()
    parent_task = db.execute(select(ProductionTask).where(ProductionTask.id == production_task.parent_task_id)).scalar_one_or_none() if production_task.parent_task_id else None
    if payload.decision == "approved":
        # 真实材料领用成本在库管执行入库时，从已过账的领料出库流水计算。
        # 不再使用原料主数据的参考价格作为生产成本。
        create_inventory_operation_request(
            db,
            operation_type="inbound",
            request_category="production_quality_inbound",
            source_module="quality",
            source_type="quality_inbound",
            source_id=production_task.id,
            source_no=quality_task.quality_no,
            warehouse_id=application.warehouse_id,
            requested_by_id=current_user.id,
            items=[
                {
                    "product_id": production_task.product_id,
                    "material_id": None,
                    "batch_no": quality_task.batch_no,
                    "quantity": float(production_task.produced_quantity),
                    "unit_cost": 0,
                }
            ],
            remark=payload.remark,
            recipient_user_ids=_role_user_ids(db, "inventory"),
            title=f"待执行质检通过入库 {quality_task.quality_no}",
            description=f"质检任务 {quality_task.quality_no} 已审批通过，请库管执行手工入库。",
        )
        application.status = "approved"
        application.approved_by_id = current_user.id
        application.approved_at = now_local()
        quality_task.status = "completed"
        production_task.status = "ready_for_inbound"
        if production_task.source_type == "rework" and parent_task and parent_task.status == "rework_pending":
            parent_task.status = "reworked"
            parent_task.notes = (
                f"{parent_task.notes or ''}\n[返工完成] 返工任务 {production_task.task_no} 已完成，返工批次号 {quality_task.batch_no}"
            ).strip()
    else:
        application.status = "rejected"
        quality_task.status = "rejected"
    application.remark = payload.remark
    complete_todos(db, business_type="quality_inbound", business_id=application.id)
    recipient_user_ids = list(dict.fromkeys([user_id for user_id in [application.requested_by_id, production_task.created_by_id] if user_id]))
    if recipient_user_ids:
        create_notification(
            db,
            title="质检入库申请已处理",
            content=(
                f"质检任务 {quality_task.quality_no} 的入库申请已"
                f"{'通过' if payload.decision == 'approved' else '驳回'}。"
            ),
            module="quality",
            recipient_user_ids=recipient_user_ids,
            created_by_id=current_user.id,
            category="success" if payload.decision == "approved" else "warning",
        )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="quality",
        action="approve_inbound_application",
        content=f"处理质检入库申请 {quality_task.quality_no}",
        after_data={"application_id": application.id, "decision": payload.decision, "status": application.status},
    )
    db.commit()
    return api_response(msg="入库申请已处理")
