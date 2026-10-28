"""Expired device history is removed without discarding work that still needs attention."""

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.printing import purge_old_completed_print_jobs, purge_old_print_access_logs
from api.routers.warehouse_scanning import purge_old_scan_access_logs, purge_old_scan_events
from models import (
    Base, InventoryDispatchQueue, PlacementPrintAccessLog, PlacementPrintJob,
    Role, SalesOrder, User, Warehouse, WarehouseScanAccessLog, WarehouseScanDevice,
    WarehouseScanEvent,
)
from services.time_utils import now_local


def test_device_records_expire_after_three_days_but_open_print_jobs_remain():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    old = now_local() - timedelta(days=4)
    recent = now_local() - timedelta(days=1)
    with Session(engine) as db:
        role = Role(code="inventory", name="库管", home_route="/")
        warehouse = Warehouse(code="WH-RETENTION", name="测试仓", warehouse_type="finished")
        db.add_all([role, warehouse])
        db.flush()
        keeper = User(username="retention_keeper", full_name="库管", hashed_password="x", role_id=role.id)
        db.add(keeper)
        db.flush()
        device = WarehouseScanDevice(
            name="测试扫码电脑", token_hash="scan-test-token-hash", warehouse_id=warehouse.id,
            operator_user_id=keeper.id, created_by_id=keeper.id,
        )
        db.add(device)
        db.flush()

        for number, status, printed_at in (
            ("OLD", "printed", old), ("RECENT", "manual_printed", recent),
            ("FAILED", "failed", None), ("SUBMITTED", "submitted", None),
        ):
            order = SalesOrder(
                order_no=f"XS-RETENTION-{number}", sales_user_id=keeper.id,
                operator_user_id=keeper.id, consignee="甲", consignee_phone="1",
                delivery_address="测试", payment_type="full", status="pending_shipment",
            )
            db.add(order)
            db.flush()
            queue = InventoryDispatchQueue(sales_order_id=order.id, placement_status="pending")
            db.add(queue)
            db.flush()
            db.add(PlacementPrintJob(
                queue_id=queue.id, status=status, printed_at=printed_at, created_at=old,
            ))

        db.add_all([
            PlacementPrintAccessLog(method="POST", path="/old", result="authorized", created_at=old),
            PlacementPrintAccessLog(method="POST", path="/recent", result="authorized", created_at=recent),
            WarehouseScanAccessLog(method="POST", path="/old", result="authorized", created_at=old),
            WarehouseScanAccessLog(method="POST", path="/recent", result="authorized", created_at=recent),
            WarehouseScanEvent(
                device_id=device.id, warehouse_id=warehouse.id, operator_user_id=keeper.id,
                action="placement", idempotency_key="old", result="success", created_at=old,
            ),
            WarehouseScanEvent(
                device_id=device.id, warehouse_id=warehouse.id, operator_user_id=keeper.id,
                action="shipment", idempotency_key="recent", result="success", created_at=recent,
            ),
        ])
        db.commit()

        assert purge_old_print_access_logs(db) == 1
        assert purge_old_scan_access_logs(db) == 1
        assert purge_old_scan_events(db) == 1
        assert purge_old_completed_print_jobs(db) == 1
        assert {row.status for row in db.scalars(select(PlacementPrintJob)).all()} == {
            "manual_printed", "failed", "submitted",
        }
        assert [row.idempotency_key for row in db.scalars(select(WarehouseScanEvent)).all()] == ["recent"]
        assert db.scalars(select(SalesOrder)).all()
    engine.dispose()
