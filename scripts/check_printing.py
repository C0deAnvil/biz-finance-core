"""Small isolated smoke test for print job generation, auth and log expiry."""

from datetime import timedelta
from hashlib import sha256

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.printing import _agent, _sync_new_jobs, purge_old_print_access_logs
from models import Base, InventoryDispatchQueue, PlacementPrintAccessLog, PlacementPrintAgent, PlacementPrintJob, Role, SalesOrder, User
from services.time_utils import now_local


engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
with Session(engine) as db:
    role = Role(code="system_admin", name="管理员", home_route="/")
    db.add(role)
    db.flush()
    user = User(username="test_admin", full_name="测试管理员", hashed_password="x", role_id=role.id)
    db.add(user)
    db.flush()

    def add_order(no, created_at):
        order = SalesOrder(
            order_no=no, sales_user_id=user.id, operator_user_id=user.id,
            consignee="收货人", consignee_phone="123", delivery_address="地址",
            payment_type="full_payment", status="pending_confirmation",
        )
        db.add(order)
        db.flush()
        queue = InventoryDispatchQueue(sales_order_id=order.id, created_at=created_at)
        db.add(queue)
        db.flush()
        return queue

    old_queue = add_order("XS-OLD", now_local() - timedelta(hours=2))
    token = "test-secret"
    agent = PlacementPrintAgent(
        name="仓库设备", token_hash=sha256(token.encode()).hexdigest(),
        created_by_id=user.id, created_at=now_local() - timedelta(hours=1),
    )
    db.add(agent)
    db.flush()
    new_queue = add_order("XS-NEW", now_local())
    db.commit()

    _sync_new_jobs(db, agent)
    jobs = db.scalars(select(PlacementPrintJob)).all()
    assert len(jobs) == 1 and jobs[0].queue_id == new_queue.id
    assert jobs[0].queue_id != old_queue.id
    _sync_new_jobs(db, agent)
    assert len(db.scalars(select(PlacementPrintJob)).all()) == 1

    scope = {
        "type": "http", "method": "POST", "path": "/api/printing/agent/jobs/claim",
        "headers": [(b"user-agent", b"test-printer")], "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    assert _agent(db, token, request).id == agent.id
    try:
        _agent(db, "wrong", request)
        raise AssertionError("invalid token was accepted")
    except HTTPException as exc:
        assert exc.status_code == 401
    logs = db.scalars(select(PlacementPrintAccessLog).order_by(PlacementPrintAccessLog.id)).all()
    assert [row.result for row in logs] == ["authorized", "rejected"]
    assert all(row.ip_address == "127.0.0.1" for row in logs)

    db.add(PlacementPrintAccessLog(method="POST", path="/old", result="rejected", created_at=now_local() - timedelta(days=4)))
    db.commit()
    assert purge_old_print_access_logs(db) == 1
    assert len(db.scalars(select(PlacementPrintAccessLog)).all()) == 2

print("printing smoke test passed")
