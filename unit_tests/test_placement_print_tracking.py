"""Placement printing is durable across outages and records manual/automatic success."""

from datetime import timedelta
from hashlib import sha256

from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.printing import (
    JobClaim,
    JobResult,
    PrintMark,
    _sync_new_jobs,
    claim_job,
    complete_job,
    mark_placement_order_printed,
)
from models import Base, InventoryDispatchQueue, PlacementPrintAgent, PlacementPrintJob, Role, SalesOrder, User
from services.time_utils import now_local


def _request():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/printing/agent/jobs/claim",
        "headers": [(b"user-agent", b"print-tracking-test")],
        "client": ("127.0.0.1", 12345),
    })


def test_print_backlog_and_manual_automatic_tracking():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="inventory", name="库管", home_route="/", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        keeper = User(username="print_keeper", full_name="打印库管", hashed_password="x", role_id=role.id, is_active=True)
        db.add(keeper)
        db.flush()

        def add_queue(number):
            order = SalesOrder(
                order_no=number,
                sales_user_id=keeper.id,
                operator_user_id=keeper.id,
                consignee="收货人",
                consignee_phone="13800000000",
                delivery_address="测试地址",
                payment_type="full_payment",
                status="pending_confirmation",
            )
            db.add(order)
            db.flush()
            queue = InventoryDispatchQueue(sales_order_id=order.id, placement_status="pending")
            db.add(queue)
            db.flush()
            return order, queue

        token = "print-agent-secret"
        agent = PlacementPrintAgent(
            name="仓库打印设备",
            token_hash=sha256(token.encode()).hexdigest(),
            created_by_id=keeper.id,
        )
        db.add(agent)
        db.flush()
        old_order, old_queue = add_queue("XS-PRINT-OLD")
        manual_order, manual_queue = add_queue("XS-PRINT-MANUAL")
        confirm_order, confirm_queue = add_queue("XS-PRINT-CONFIRM")
        db.commit()

        # All unprinted queues created after device activation are durable backlog work.
        _sync_new_jobs(db, agent)
        jobs = {job.queue_id: job for job in db.scalars(select(PlacementPrintJob)).all()}
        assert set(jobs) == {old_queue.id, manual_queue.id, confirm_queue.id}

        lease_token = "lease-secret"
        automatic_job = jobs[old_queue.id]
        automatic_job.status = "leased"
        automatic_job.claimed_by_agent_id = agent.id
        automatic_job.lease_token_hash = sha256(lease_token.encode()).hexdigest()
        automatic_job.lease_until = now_local() + timedelta(minutes=5)
        db.commit()
        complete_job(
            automatic_job.id,
            JobResult(lease_token=lease_token, success=True, verification="spooler_completed"),
            _request(),
            x_print_agent_token=token,
            db=db,
        )
        db.refresh(old_queue)
        assert old_queue.placement_printed_at is not None
        assert old_queue.placement_print_method == "automatic"
        assert old_queue.placement_print_count == 1

        # A successful command without spooler evidence is not falsely marked printed.
        confirm_lease = "confirm-lease"
        confirmation_job = jobs[confirm_queue.id]
        confirmation_job.status = "leased"
        confirmation_job.claimed_by_agent_id = agent.id
        confirmation_job.lease_token_hash = sha256(confirm_lease.encode()).hexdigest()
        confirmation_job.lease_until = now_local() + timedelta(minutes=5)
        db.commit()
        complete_job(
            confirmation_job.id,
            JobResult(lease_token=confirm_lease, success=True, verification="submitted_only"),
            _request(),
            x_print_agent_token=token,
            db=db,
        )
        db.refresh(confirm_queue)
        assert confirm_queue.placement_printed_at is None
        assert confirm_queue.placement_print_status == "submitted"
        mark_placement_order_printed(
            confirm_order.id,
            PrintMark(source="automatic_confirmed"),
            current_user=keeper,
            db=db,
        )
        db.refresh(confirm_queue)
        assert confirm_queue.placement_print_status == "printed"
        assert confirm_queue.placement_print_method == "automatic_confirmed"

        mark_placement_order_printed(manual_order.id, PrintMark(source="manual"), current_user=keeper, db=db)
        db.refresh(manual_queue)
        assert manual_queue.placement_printed_at is not None
        assert manual_queue.placement_print_method == "manual"
        assert manual_queue.placement_printed_by_id == keeper.id
        assert manual_queue.placement_print_count == 1
        assert db.scalar(select(PlacementPrintJob.status).where(PlacementPrintJob.queue_id == manual_queue.id)) == "manual_printed"

        # A failed unprinted job is recovered once when the local process starts again.
        failed_order, failed_queue = add_queue("XS-PRINT-FAILED")
        db.commit()
        _sync_new_jobs(db, agent)
        failed_job = db.scalar(select(PlacementPrintJob).where(PlacementPrintJob.queue_id == failed_queue.id))
        failed_job.status = "failed"
        failed_job.attempts = 5
        failed_job.last_error = "打印机离线"
        db.commit()
        claimed = claim_job(
            JobClaim(recover_failed=True),
            _request(),
            x_print_agent_token=token,
            db=db,
        )["data"]
        assert claimed["id"] == failed_job.id
        db.refresh(failed_job)
        assert failed_job.status == "leased"
        assert failed_job.attempts == 1
