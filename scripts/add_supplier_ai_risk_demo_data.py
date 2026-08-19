from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal
from models import (
    CashFlowRecord,
    LogisticsRecord,
    Material,
    PayableRecord,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderApproval,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    QualityCostRecord,
    QualityDisposition,
    QualityInspection,
    QualityInboundApplication,
    QualityTask,
    Role,
    Supplier,
    User,
    Warehouse,
)
from services.supplier_ai_service import refresh_supplier_ai_results


def dec(value: int | float | str) -> Decimal:
    return Decimal(str(value))


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def user_by_role(db: Session, role_code: str) -> User:
    user = (
        db.execute(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(Role.code == role_code, User.is_active.is_(True))
            .order_by(User.id)
        )
        .scalars()
        .first()
    )
    if user is None:
        raise RuntimeError(f"缺少角色账号：{role_code}")
    return user


def one_by_code(db: Session, model, code_field: str, code: str):
    row = db.execute(select(model).where(getattr(model, code_field) == code)).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"缺少基础数据：{model.__name__}.{code_field}={code}")
    return row


def delete_existing_case(db: Session) -> None:
    orders = db.execute(select(PurchaseOrder).where(PurchaseOrder.order_no.like("AIRISK%"))).scalars().all()
    if not orders:
        return
    order_ids = [row.id for row in orders]
    receipt_ids = [row.id for row in db.execute(select(PurchaseReceipt).where(PurchaseReceipt.purchase_order_id.in_(order_ids))).scalars().all()]
    task_ids = [
        row.id
        for row in db.execute(select(ProductionTask).where(ProductionTask.task_no.like("AIQCSHADOW%"))).scalars().all()
    ]
    quality_ids = [row.id for row in db.execute(select(QualityTask).where(QualityTask.production_task_id.in_(task_ids))).scalars().all()] if task_ids else []
    if quality_ids:
        disposition_ids = [
            row.id
            for row in db.execute(select(QualityDisposition).where(QualityDisposition.quality_task_id.in_(quality_ids))).scalars().all()
        ]
        if disposition_ids:
            for row in db.execute(select(QualityCostRecord).where(QualityCostRecord.quality_disposition_id.in_(disposition_ids))).scalars().all():
                db.delete(row)
            for row in db.execute(select(QualityDisposition).where(QualityDisposition.id.in_(disposition_ids))).scalars().all():
                db.delete(row)
        for row in db.execute(select(QualityInboundApplication).where(QualityInboundApplication.quality_task_id.in_(quality_ids))).scalars().all():
            db.delete(row)
        for row in db.execute(select(QualityInspection).where(QualityInspection.quality_task_id.in_(quality_ids))).scalars().all():
            db.delete(row)
        db.flush()
        for row in db.execute(select(QualityTask).where(QualityTask.id.in_(quality_ids))).scalars().all():
            db.delete(row)
        db.flush()
    if task_ids:
        for row in db.execute(select(ProductionTask).where(ProductionTask.id.in_(task_ids))).scalars().all():
            db.delete(row)
        db.flush()
    if receipt_ids:
        for row in db.execute(select(PurchaseReceiptItem).where(PurchaseReceiptItem.purchase_receipt_id.in_(receipt_ids))).scalars().all():
            db.delete(row)
        db.flush()
        for row in db.execute(select(PurchaseReceipt).where(PurchaseReceipt.id.in_(receipt_ids))).scalars().all():
            db.delete(row)
        db.flush()
    for model in [PayableRecord, PurchaseOrderApproval, PurchaseOrderItem, LogisticsRecord, CashFlowRecord]:
        for row in db.execute(select(model).where(getattr(model, "purchase_order_id", None).in_(order_ids)) if hasattr(model, "purchase_order_id") else select(model).where(model.related_no.like("AIRISK%"))).scalars().all():
            db.delete(row)
    db.flush()
    for row in orders:
        db.delete(row)
    db.flush()


def add_order(
    db: Session,
    *,
    order_no: str,
    supplier: Supplier,
    material: Material,
    warehouse: Warehouse,
    users: dict[str, User],
    order_date: str,
    quantity: str,
    unit_price: str,
    receipt_delay_days: int | None,
    received_ratio: str,
    quality_status: str,
    paid_ratio: str,
    logistics_carrier: str,
) -> None:
    order_dt = dt(order_date)
    qty = dec(quantity)
    price = dec(unit_price)
    total = qty * price
    received_qty = (qty * dec(received_ratio)).quantize(dec("0.0001"))
    status = "fully_received" if received_qty >= qty else "partial_received" if received_qty > 0 else "purchasing"
    order = PurchaseOrder(
        order_no=order_no,
        supplier_id=supplier.id,
        purchaser_user_id=users["purchase"].id,
        payment_type="先到货后付款",
        status=status,
        total_amount=total,
        threshold_triggered=total >= dec("10000"),
        approved_by_id=users["manager"].id if total >= dec("10000") else None,
        approved_at=order_dt + timedelta(days=1) if total >= dec("10000") else None,
        contract_received=True,
        created_at=order_dt,
        updated_at=order_dt,
    )
    db.add(order)
    db.flush()

    item = PurchaseOrderItem(
        purchase_order_id=order.id,
        material_id=material.id,
        quantity=qty,
        unit_price=price,
        total_amount=total,
        kg_per_bag=dec("25"),
        bag_count=qty / dec("25"),
        display_specification="25kg/袋",
        received_quantity=received_qty,
        returned_quantity=0,
        created_at=order_dt,
        updated_at=order_dt,
    )
    db.add(item)
    db.flush()

    if order.threshold_triggered:
        db.add(
            PurchaseOrderApproval(
                purchase_order_id=order.id,
                approver_user_id=users["manager"].id,
                decision="approved",
                note="供应商智能分析样本：采购阈值审批通过",
                approved_at=order_dt + timedelta(days=1),
                created_at=order_dt + timedelta(days=1),
                updated_at=order_dt + timedelta(days=1),
            )
        )

    if received_qty > 0 and receipt_delay_days is not None:
        receipt_dt = order_dt + timedelta(days=receipt_delay_days)
        receipt = PurchaseReceipt(
            receipt_no=f"DH-{order_no}",
            purchase_order_id=order.id,
            warehouse_id=warehouse.id,
            received_by_id=users["inventory"].id,
            requires_quality=True,
            quality_status=quality_status,
            status="completed" if quality_status in {"approved", "not_required"} else "pending_quality",
            remark="供应商智能分析样本：采购到货记录",
            created_at=receipt_dt,
            updated_at=receipt_dt,
        )
        db.add(receipt)
        db.flush()
        db.add(
            PurchaseReceiptItem(
                purchase_receipt_id=receipt.id,
                purchase_order_item_id=item.id,
                received_quantity=received_qty,
                batch_no=f"B-{order_no}",
                unit_cost=price,
                created_at=receipt_dt,
                updated_at=receipt_dt,
            )
        )

        shadow = ProductionTask(
            task_no=f"AIQCSHADOW-{order_no[-6:]}",
            source_type="purchase_quality",
            product_id=one_by_code(db, __import__("models").Product, "product_code", "SALT-01").id,
            planned_quantity=received_qty,
            produced_quantity=received_qty,
            requires_quality=True,
            status="completed",
            batch_no=f"B-{order_no}",
            created_by_id=users["quality"].id,
            started_at=receipt_dt,
            completed_at=receipt_dt + timedelta(hours=4),
            notes=f"供应商智能分析样本质检：{order_no}",
            created_at=receipt_dt,
            updated_at=receipt_dt,
        )
        db.add(shadow)
        db.flush()
        quality = QualityTask(
            quality_no=f"AIQ-{order_no[-6:]}",
            production_task_id=shadow.id,
            batch_no=f"B-{order_no}",
            source_category="purchase",
            required_tests=1,
            status="completed",
            submitted_by_id=users["inventory"].id,
            created_at=receipt_dt,
            updated_at=receipt_dt,
        )
        db.add(quality)
        db.flush()
        passed = quality_status == "approved"
        db.add(
            QualityInspection(
                quality_task_id=quality.id,
                inspector_user_id=users["quality"].id,
                sequence_no=1,
                result_text="抽检合格，允许入库。" if passed else "抽检发现含水率或杂质指标波动，需复核供应商批次稳定性。",
                grade="A" if passed else "C",
                is_passed=passed,
                inspected_quantity=received_qty,
                sample_consumption_qty=dec("2"),
                report_summary="采购来料质检通过" if passed else "采购来料质检异常",
                inspected_at=receipt_dt + timedelta(hours=4),
                created_at=receipt_dt + timedelta(hours=4),
                updated_at=receipt_dt + timedelta(hours=4),
            )
        )

    paid_amount = (total * dec(paid_ratio)).quantize(dec("0.01"))
    if paid_amount > 0:
        pay_dt = order_dt + timedelta(days=receipt_delay_days + 1 if receipt_delay_days is not None else 5)
        db.add(
            PayableRecord(
                purchase_order_id=order.id,
                amount=paid_amount,
                order_amount=paid_amount,
                other_fee_amount=0,
                paid_at=pay_dt,
                paid_by_id=users["manager"].id,
                status="partial_settled" if paid_amount < total else "settled",
                remark="供应商智能分析样本：采购付款",
                created_at=pay_dt,
                updated_at=pay_dt,
            )
        )
        db.add(
            CashFlowRecord(
                record_no=f"ZJ-AIRISK-{order_no[-6:]}",
                flow_type="outgoing",
                business_type="purchase_order",
                business_id=order.id,
                related_no=order.order_no,
                amount=paid_amount,
                happened_at=pay_dt,
                remark="供应商智能分析样本采购付款",
                created_at=pay_dt,
                updated_at=pay_dt,
            )
        )

    db.add(
        LogisticsRecord(
            logistics_no=f"WL-{order_no}",
            module="purchase",
            business_type="purchase_order",
            business_id=order.id,
            related_no=order.order_no,
            warehouse_id=warehouse.id,
            logistics_mode="road",
            carrier_name=logistics_carrier,
            driver_name="王师傅",
            driver_phone="13900007777",
            vehicle_no=f"鲁A{order_no[-4:]}",
            tracking_no=f"AI{order_no[-8:]}",
            dispatched_at=order_dt + timedelta(days=1),
            arrived_at=order_dt + timedelta(days=receipt_delay_days) if receipt_delay_days is not None else None,
            remark="供应商智能分析样本物流",
            created_by_id=users["purchase"].id,
            created_at=order_dt + timedelta(days=1),
            updated_at=order_dt + timedelta(days=1),
        )
    )


def add_supplier_risk_data() -> None:
    with SessionLocal() as db:
        delete_existing_case(db)
        users = {
            "purchase": user_by_role(db, "purchase"),
            "inventory": user_by_role(db, "inventory"),
            "quality": user_by_role(db, "quality"),
            "manager": user_by_role(db, "manager"),
        }
        warehouse = one_by_code(db, Warehouse, "code", "WH-RM")
        suppliers = {row.code: row for row in db.execute(select(Supplier)).scalars().all()}
        materials = {row.material_code: row for row in db.execute(select(Material)).scalars().all()}
        cases = [
            ("AIRISK20260101", "S002", "RM-002", "2026-01-08", "38000", "0.30", 7, "1.00", "approved", "1.00", "鲁北干线物流有限公司"),
            ("AIRISK20260201", "S002", "RM-002", "2026-02-10", "42000", "0.31", 8, "0.78", "failed", "0.60", "鲁北干线物流有限公司"),
            ("AIRISK20260301", "S002", "RM-002", "2026-03-12", "36000", "0.32", 6, "1.00", "approved", "1.00", "鲁北干线物流有限公司"),
            ("AIRISK20260401", "S002", "RM-002", "2026-04-14", "40000", "0.33", 9, "0.52", "failed", "0.35", "鲁北干线物流有限公司"),
            ("AIRISK20260102", "S007", "RM-008", "2026-01-18", "6500", "3.15", 5, "1.00", "approved", "1.00", "豫中快运有限公司"),
            ("AIRISK20260202", "S007", "RM-008", "2026-02-20", "7200", "3.18", 7, "0.86", "approved", "0.80", "豫中快运有限公司"),
            ("AIRISK20260302", "S007", "RM-008", "2026-03-23", "7600", "3.22", 8, "0.70", "failed", "0.50", "豫中快运有限公司"),
            ("AIRISK20260103", "S008", "RM-005", "2026-01-22", "9000", "0.64", 6, "1.00", "failed", "1.00", "赣江化工物流有限公司"),
            ("AIRISK20260203", "S008", "RM-005", "2026-02-25", "11000", "0.66", 10, "0.63", "failed", "0.40", "赣江化工物流有限公司"),
            ("AIRISK20260303", "S008", "RM-005", "2026-03-29", "10500", "0.67", 9, "0.58", "failed", "0.30", "赣江化工物流有限公司"),
            ("AIRISK20260403", "S008", "RM-005", "2026-04-24", "12000", "0.69", None, "0", "not_required", "0", "赣江化工物流有限公司"),
            ("AIRISK20260404", "S009", "RM-001", "2026-04-05", "16000", "0.19", 7, "0.70", "approved", "0.50", "辽河油田物流有限公司"),
            ("AIRISK20260405", "S009", "RM-001", "2026-04-26", "18000", "0.20", None, "0", "not_required", "0", "辽河油田物流有限公司"),
        ]
        for case in cases:
            order_no, supplier_code, material_code, order_date, qty, price, delay, received_ratio, quality_status, paid_ratio, carrier = case
            add_order(
                db,
                order_no=order_no,
                supplier=suppliers[supplier_code],
                material=materials[material_code],
                warehouse=warehouse,
                users=users,
                order_date=order_date,
                quantity=qty,
                unit_price=price,
                receipt_delay_days=delay,
                received_ratio=received_ratio,
                quality_status=quality_status,
                paid_ratio=paid_ratio,
                logistics_carrier=carrier,
            )
        db.flush()
        refresh_supplier_ai_results(db, users["manager"])
        db.commit()
        print("供应商智能分析风险样本数据已追加并刷新模型结果")


if __name__ == "__main__":
    add_supplier_risk_data()
