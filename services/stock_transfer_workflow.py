"""移库任务编排。实际库存变动只由库存作业请求完成。"""

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Inventory, InventoryOperationRequest, QualityTask, Role, StockTransfer, User, Warehouse
from services.core import complete_todos, create_notification, create_todo, next_business_no
from services.organization import get_inventory_scope_warehouse_ids
from services.time_utils import now_local
from services.workflow import create_inventory_operation_request


def _warehouse_workers(db: Session, warehouse_id: int, role_code: str, preferred_id: int | None) -> list[int]:
    users = db.execute(
        select(User).join(Role, User.role_id == Role.id).where(
            Role.code == role_code, User.is_active.is_(True)
        )
    ).scalars().all()
    if preferred_id and any(user.id == preferred_id for user in users):
        return [preferred_id]
    return [
        user.id for user in users
        if (scope := get_inventory_scope_warehouse_ids(db, user)) is not None and warehouse_id in scope
    ]


def _items(transfer: StockTransfer, *, quantity: Decimal | None = None, batch_no: str | None = None, unit_cost: Decimal | None = None) -> list[dict]:
    return [{
        "product_id": transfer.product_id,
        "material_id": transfer.material_id,
        "packaging_config_id": transfer.packaging_config_id,
        "quantity": float(quantity if quantity is not None else transfer.quantity),
        "batch_no": batch_no,
        "unit_cost": float(unit_cost or 0),
    }]


def create_transfer_operation(db: Session, transfer: StockTransfer, operation_type: str, requested_by_id: int, *, items: list[dict] | None = None) -> InventoryOperationRequest:
    outbound = operation_type == "outbound"
    warehouse_id = transfer.source_warehouse_id if outbound else transfer.target_warehouse_id
    warehouse = db.get(Warehouse, warehouse_id)
    recipients = _warehouse_workers(db, warehouse_id, "inventory", warehouse.manager_user_id if warehouse else None)
    if not recipients:
        raise HTTPException(status_code=409, detail=f"{warehouse.name if warehouse else '目标'}仓库未配置在岗库管，无法生成库存作业请求")
    if outbound and items is None:
        stock = db.execute(select(Inventory).where(
            Inventory.warehouse_id == transfer.source_warehouse_id,
            Inventory.product_id == transfer.product_id,
            Inventory.material_id == transfer.material_id,
            Inventory.packaging_config_id == transfer.packaging_config_id,
        )).scalar_one_or_none()
        items = _items(transfer, unit_cost=Decimal(stock.avg_unit_cost or 0) if stock else None)
    return create_inventory_operation_request(
        db,
        operation_type=operation_type,
        request_category="stock_transfer_outbound" if outbound else "stock_transfer_inbound",
        source_module="inventory",
        source_type="stock_transfer",
        source_id=transfer.id,
        source_no=transfer.transfer_no,
        warehouse_id=warehouse_id,
        requested_by_id=requested_by_id,
        items=items if items is not None else _items(transfer),
        remark=f"移库 {transfer.transfer_no}：{transfer.remark or '请核对实物数量'}",
        recipient_user_ids=recipients,
        title=f"待执行移库{'出库' if outbound else '入库'} {transfer.transfer_no}",
        description=f"移库单 {transfer.transfer_no}，请在库存状态完成{'源仓出库' if outbound else '目标仓入库'}。",
    )


def create_transfer_quality_task(db: Session, transfer: StockTransfer, requested_by_id: int) -> QualityTask:
    existing = db.execute(select(QualityTask).where(QualityTask.stock_transfer_id == transfer.id)).scalar_one_or_none()
    if existing:
        return existing
    warehouse = db.get(Warehouse, transfer.target_warehouse_id)
    recipients = _warehouse_workers(db, transfer.target_warehouse_id, "quality", warehouse.qc_user_id if warehouse else None)
    if not recipients:
        raise HTTPException(status_code=409, detail=f"{warehouse.name if warehouse else '目标'}仓库未配置在岗质检员，无法生成质检任务")
    task = QualityTask(
        quality_no=next_business_no(db, "ZJY"),
        production_task_id=None,
        stock_transfer_id=transfer.id,
        batch_no=transfer.transfer_no,
        source_category="stock_transfer",
        required_tests=1,
        status="pending",
        submitted_by_id=requested_by_id,
        assigned_inspector_id=warehouse.qc_user_id if warehouse and warehouse.qc_user_id in recipients else None,
        assigned_by_id=requested_by_id if warehouse and warehouse.qc_user_id in recipients else None,
        assigned_at=now_local() if warehouse and warehouse.qc_user_id in recipients else None,
        target_warehouse_id=transfer.target_warehouse_id,
    )
    db.add(task)
    db.flush()
    for user_id in recipients:
        create_todo(db, title=f"待质检移库单 {transfer.transfer_no}", description="目标仓库移库质检，通过后自动生成入库作业。", module="quality", assigned_user_id=user_id, business_type="quality_task", business_id=task.id, priority="high")
    create_notification(db, title=f"待质检移库单 {transfer.transfer_no}", content="请在质检任务完成移库质检。", module="quality", recipient_user_ids=recipients, created_by_id=requested_by_id, category="info")
    return task


def sync_transfer_operation(db: Session, request: InventoryOperationRequest, current_user: User) -> None:
    transfer = db.execute(select(StockTransfer).where(StockTransfer.id == request.source_id).with_for_update()).scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status_code=404, detail="移库单不存在")
    handled = request.handled_items_json or []
    if len(handled) != 1 or any(
        handled[0].get(key) != getattr(transfer, key) for key in ("product_id", "material_id", "packaging_config_id")
    ):
        raise HTTPException(status_code=409, detail="移库作业不允许更换货品；请核对作业明细")
    quantity = Decimal(str(handled[0]["quantity"]))
    if request.request_category == "stock_transfer_outbound":
        if transfer.status != "pending_outbound":
            raise HTTPException(status_code=409, detail="移库单已出库或状态不允许出库")
        transfer.quantity = quantity
        transfer.outbound_by_id = current_user.id
        transfer.outbound_at = now_local()
        if transfer.requires_quality:
            transfer.status = "pending_quality"
            create_transfer_quality_task(db, transfer, current_user.id)
        else:
            transfer.status = "pending_inbound"
            create_transfer_operation(db, transfer, "inbound", current_user.id, items=handled)
    elif request.request_category == "stock_transfer_inbound":
        if transfer.status != "pending_inbound":
            raise HTTPException(status_code=409, detail="移库单尚未到入库阶段")
        transfer.status = "completed"
        transfer.completed_by_id = current_user.id
        transfer.completed_at = now_local()


def sync_transfer_inspection(db: Session, task: QualityTask, current_user: User, *, passed: bool, reason: str | None = None) -> None:
    transfer = db.execute(select(StockTransfer).where(StockTransfer.id == task.stock_transfer_id).with_for_update()).scalar_one_or_none()
    if transfer is None or transfer.status != "pending_quality":
        raise HTTPException(status_code=409, detail="移库单已不在待质检状态")
    task.status = "completed" if passed else "rejected"
    transfer.quality_by_id = current_user.id
    transfer.quality_at = now_local()
    transfer.quality_result = "passed" if passed else "failed"
    complete_todos(db, business_type="quality_task", business_id=task.id)
    if passed:
        transfer.status = "pending_inbound"
        outbound = db.execute(select(InventoryOperationRequest).where(
            InventoryOperationRequest.source_type == "stock_transfer",
            InventoryOperationRequest.source_id == transfer.id,
            InventoryOperationRequest.request_category == "stock_transfer_outbound",
            InventoryOperationRequest.status == "completed",
        )).scalar_one_or_none()
        create_transfer_operation(db, transfer, "inbound", current_user.id, items=outbound.handled_items_json if outbound else None)
    else:
        transfer.status = "quality_failed"
        transfer.reject_reason = reason or "移库质检不通过，货物仍处于在途/待处置状态"
        create_notification(db, title=f"移库质检不通过 {transfer.transfer_no}", content=f"{transfer.reject_reason}。系统未自动退回源仓或生成入库，请人工处置实物及库存。", module="inventory", recipient_user_ids=[transfer.created_by_id], created_by_id=current_user.id, category="warning")


def repair_pending_transfer_jobs(db: Session) -> int:
    """为升级前已在途的移库补齐作业/质检任务，不重复创建已存在任务。"""
    transfers = db.execute(select(StockTransfer).where(
        StockTransfer.status.in_(["pending_outbound", "pending_quality", "pending_inbound"])
    )).scalars().all()
    repaired = 0
    for transfer in transfers:
        try:
            with db.begin_nested():
                if transfer.status == "pending_quality":
                    if db.execute(select(QualityTask.id).where(QualityTask.stock_transfer_id == transfer.id)).scalar_one_or_none() is None:
                        create_transfer_quality_task(db, transfer, transfer.outbound_by_id or transfer.created_by_id)
                        repaired += 1
                else:
                    category = "stock_transfer_outbound" if transfer.status == "pending_outbound" else "stock_transfer_inbound"
                    exists = db.execute(select(InventoryOperationRequest.id).where(
                        InventoryOperationRequest.source_type == "stock_transfer",
                        InventoryOperationRequest.source_id == transfer.id,
                        InventoryOperationRequest.request_category == category,
                    ).limit(1)).scalar_one_or_none()
                    if exists is None:
                        create_transfer_operation(db, transfer, "outbound" if category.endswith("outbound") else "inbound", transfer.created_by_id)
                        repaired += 1
        except HTTPException:
            # 配置缺失的历史单不应导致整个移库列表不可访问；配置后刷新可重试。
            continue
    if repaired:
        db.commit()
    return repaired
