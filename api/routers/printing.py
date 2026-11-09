"""Authenticated PDF downloads and a lease based warehouse print queue."""

from datetime import timedelta
from hashlib import sha256
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from database import get_db
from dependencies import get_current_user, require_roles
from models import FulfillmentDemand, InventoryDispatchQueue, PlacementPrintAccessLog, PlacementPrintAgent, PlacementPrintJob, SalesOrder, SalesOrderItem, StockTransfer, User
from responses import api_response
from services.order_printing import render_order_pdf, render_production_demand_pdf
from services.stock_transfer_printing import render_stock_transfer_pdf
from services.organization import user_in_scope
from services.time_utils import now_local


router = APIRouter(prefix="/printing", tags=["打印"])


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)


class JobResult(BaseModel):
    lease_token: str
    success: bool
    error: str | None = Field(default=None, max_length=500)
    verification: str | None = Field(default=None, max_length=40)


class JobClaim(BaseModel):
    recover_failed: bool = False


class PrintMark(BaseModel):
    source: str = Field(default="manual", pattern="^(manual|automatic_confirmed)$")


def _order(db: Session, order_id: int) -> SalesOrder:
    row = db.execute(
        select(SalesOrder).options(
            joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user),
            joinedload(SalesOrder.operator_user), joinedload(SalesOrder.receipt_account),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.product),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        ).where(SalesOrder.id == order_id)
    ).unique().scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if row.status in {"draft", "cancelled"}:
        raise HTTPException(status_code=409, detail="草稿或已作废订单不能打印正式单据")
    return row


def _pdf_response(
    order: SalesOrder,
    kind: str,
    *,
    queue: InventoryDispatchQueue | None = None,
    disposition: str = "inline",
):
    filename = f"{kind}-{order.order_no}.pdf"
    render_kwargs = {}
    if kind in {"placement", "shipment"}:
        if queue is None:
            raise HTTPException(status_code=404, detail="该订单没有库存作业需求")
        render_kwargs = {"warehouse_name": queue.warehouse.name if queue.warehouse else None}
        render_kwargs["placement_token" if kind == "placement" else "shipment_token"] = (
            queue.placement_scan_token if kind == "placement" else queue.shipment_scan_token
        )
    return Response(
        content=render_order_pdf(order, kind=kind, **render_kwargs),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/sales-orders/{order_id}.pdf")
def sales_order_pdf(
    order_id: int,
    current_user: User = Depends(require_roles("sales", "finance", "finance_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    order = _order(db, order_id)
    if not user_in_scope(db, current_user, business_role_code="sales", owner_user_id=order.sales_user_id):
        raise HTTPException(status_code=403, detail="无权查看该销售单")
    return _pdf_response(order, "sales")


@router.get("/placement-orders/{order_id}.pdf")
def placement_order_pdf(
    order_id: int,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    order = _order(db, order_id)
    queue = db.scalar(
        select(InventoryDispatchQueue)
        .options(joinedload(InventoryDispatchQueue.warehouse))
        .where(InventoryDispatchQueue.sales_order_id == order_id)
    )
    if queue is None:
        raise HTTPException(status_code=404, detail="该订单没有置货需求")
    return _pdf_response(order, "placement", queue=queue)


@router.get("/shipment-orders/{order_id}.pdf")
def shipment_order_pdf(
    order_id: int,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    order = _order(db, order_id)
    queue = db.scalar(
        select(InventoryDispatchQueue)
        .options(joinedload(InventoryDispatchQueue.warehouse))
        .where(InventoryDispatchQueue.sales_order_id == order_id)
    )
    if queue is None:
        raise HTTPException(status_code=404, detail="该订单没有发货需求")
    if order.status not in {"pending_shipment", "shipped", "completed"}:
        raise HTTPException(status_code=409, detail="财务尚未审批通过，不能生成发货单")
    return _pdf_response(order, "shipment", queue=queue)


@router.get("/production-demands/{demand_id}.pdf")
def production_demand_pdf(
    demand_id: int,
    current_user: User = Depends(require_roles("production", "inventory", "purchase", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    demand = db.execute(
        select(FulfillmentDemand)
        .options(
            joinedload(FulfillmentDemand.product),
            joinedload(FulfillmentDemand.sales_order),
            joinedload(FulfillmentDemand.created_by),
            joinedload(FulfillmentDemand.assigned_user),
        )
        .where(FulfillmentDemand.id == demand_id, FulfillmentDemand.fulfillment_mode == "production")
    ).unique().scalar_one_or_none()
    if demand is None:
        raise HTTPException(status_code=404, detail="生产补货需求不存在")
    from services.material_replenishment_service import material_demand_progress
    content = render_production_demand_pdf(demand, material_demand_progress(db, demand))
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="production-demand-{demand.demand_no}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/stock-transfers/{transfer_id}/{document_type}.pdf")
def stock_transfer_pdf(
    transfer_id: int,
    document_type: str,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "quality", "quality_super", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    if document_type not in {"outbound", "inbound"}:
        raise HTTPException(status_code=404, detail="不支持的移库单据类型")
    transfer = db.execute(
        select(StockTransfer).options(
            joinedload(StockTransfer.source_warehouse), joinedload(StockTransfer.target_warehouse),
            joinedload(StockTransfer.product), joinedload(StockTransfer.material), joinedload(StockTransfer.packaging_config),
        ).where(StockTransfer.id == transfer_id)
    ).unique().scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status_code=404, detail="移库单不存在")
    name = "outbound" if document_type == "outbound" else "inbound"
    return Response(
        content=render_stock_transfer_pdf(transfer, document_type), media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="stock-transfer-{name}-{transfer.transfer_no}.pdf"', "Cache-Control": "no-store"},
    )


def _system_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.role or current_user.role.code != "system_admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可管理打印设备")
    return current_user


def _agent(db: Session, raw_token: str | None, request: Request) -> PlacementPrintAgent:
    digest = sha256(raw_token.encode()).hexdigest() if raw_token else None
    row = db.scalar(select(PlacementPrintAgent).where(
        PlacementPrintAgent.token_hash == digest,
        PlacementPrintAgent.is_active.is_(True),
    )) if digest else None
    db.add(PlacementPrintAccessLog(
        agent_id=row.id if row else None,
        token_fingerprint=digest[:12] if digest else None,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:255],
        method=request.method,
        path=request.url.path[:255],
        result="authorized" if row else "rejected",
    ))
    db.commit()
    if row is None:
        raise HTTPException(status_code=401, detail="打印设备密钥无效、缺失或已停用")
    return row


@router.post("/agents")
def create_agent(payload: AgentCreate, current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="设备名称至少需要两个字符")
    token = secrets.token_urlsafe(40)
    row = PlacementPrintAgent(name=name, token_hash=sha256(token.encode()).hexdigest(), created_by_id=current_user.id)
    db.add(row)
    db.commit()
    return api_response(data={"id": row.id, "name": row.name, "token": token}, msg="设备密钥仅在此时显示一次，请妥善保存")


@router.get("/agents")
def list_agents(current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(PlacementPrintAgent).order_by(PlacementPrintAgent.id.desc())).all()
    return api_response(data=[{
        "id": row.id,
        "name": row.name,
        "token_fingerprint": row.token_hash[:12],
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat(),
    } for row in rows])


@router.post("/agents/{agent_id}/revoke")
def revoke_agent(agent_id: int, current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    row = db.get(PlacementPrintAgent, agent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="打印设备不存在")
    row.is_active = False
    db.commit()
    return api_response(msg="打印设备密钥已停用")


def _sync_new_jobs(db: Session, agent: PlacementPrintAgent) -> None:
    # The first registered device is the safe activation boundary: recover every
    # unprinted queue after activation, but never mass-print unknown legacy work.
    activated_at = db.scalar(select(func.min(PlacementPrintAgent.created_at))) or agent.created_at
    job_specs = (
        (
            "placement",
            InventoryDispatchQueue.created_at >= activated_at,
            InventoryDispatchQueue.placement_printed_at.is_(None),
            InventoryDispatchQueue.placement_status == "pending",
            SalesOrder.status.in_(["pending_confirmation", "pending_shipment"]),
        ),
        (
            "shipment",
            SalesOrder.approved_at >= activated_at,
            InventoryDispatchQueue.shipment_printed_at.is_(None),
            SalesOrder.status == "pending_shipment",
        ),
    )
    for document_kind, *conditions in job_specs:
        queue_ids = db.scalars(
            select(InventoryDispatchQueue.id)
            .join(SalesOrder, SalesOrder.id == InventoryDispatchQueue.sales_order_id)
            .where(
                *conditions,
                ~InventoryDispatchQueue.id.in_(
                    select(PlacementPrintJob.queue_id).where(
                        PlacementPrintJob.document_kind == document_kind
                    )
                ),
            )
            .order_by(InventoryDispatchQueue.id)
            .limit(100)
        ).all()
        for queue_id in queue_ids:
            try:
                with db.begin_nested():
                    db.add(PlacementPrintJob(
                        queue_id=queue_id,
                        document_kind=document_kind,
                        status="pending",
                    ))
                    db.flush()
            except IntegrityError:
                # Another polling device created the same durable job first.
                pass
    db.commit()


def _ready_print_job_clause():
    return or_(
        and_(
            PlacementPrintJob.document_kind == "placement",
            InventoryDispatchQueue.placement_printed_at.is_(None),
            InventoryDispatchQueue.placement_status == "pending",
            SalesOrder.status.in_(["pending_confirmation", "pending_shipment"]),
        ),
        and_(
            PlacementPrintJob.document_kind == "shipment",
            InventoryDispatchQueue.shipment_printed_at.is_(None),
            SalesOrder.status == "pending_shipment",
        ),
    )


def _set_queue_print_state(queue: InventoryDispatchQueue, document_kind: str, *, status: str, method: str | None = None, printed_at=None, printed_by_id=None, increment=False):
    prefix = "shipment" if document_kind == "shipment" else "placement"
    setattr(queue, f"{prefix}_print_status", status)
    setattr(queue, f"{prefix}_print_method", method)
    setattr(queue, f"{prefix}_printed_at", printed_at)
    setattr(queue, f"{prefix}_printed_by_id", printed_by_id)
    if increment:
        count_field = f"{prefix}_print_count"
        setattr(queue, count_field, int(getattr(queue, count_field) or 0) + 1)


@router.post("/agent/jobs/claim")
def claim_job(payload: JobClaim, request: Request, x_print_agent_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _agent(db, x_print_agent_token, request)
    _sync_new_jobs(db, agent)
    if payload.recover_failed:
        failed_jobs = db.scalars(
            select(PlacementPrintJob)
            .join(InventoryDispatchQueue, InventoryDispatchQueue.id == PlacementPrintJob.queue_id)
            .join(SalesOrder, SalesOrder.id == InventoryDispatchQueue.sales_order_id)
            .where(
                PlacementPrintJob.status == "failed",
                _ready_print_job_clause(),
            )
        ).all()
        for failed_job in failed_jobs:
            failed_job.status = "pending"
            failed_job.attempts = 0
            failed_job.last_error = None
            failed_job.lease_token_hash = None
            failed_job.lease_until = None
        db.commit()
    now = now_local()
    job = db.execute(
        select(PlacementPrintJob)
        .join(InventoryDispatchQueue, InventoryDispatchQueue.id == PlacementPrintJob.queue_id)
        .join(SalesOrder, SalesOrder.id == InventoryDispatchQueue.sales_order_id)
        .where(
            _ready_print_job_clause(),
            PlacementPrintJob.attempts < 5,
            (PlacementPrintJob.status == "pending") |
            ((PlacementPrintJob.status == "leased") & (PlacementPrintJob.lease_until < now)),
        )
        .order_by(PlacementPrintJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return api_response(data=None)
    lease_token = secrets.token_urlsafe(32)
    job.status = "leased"
    job.attempts += 1
    job.claimed_by_agent_id = agent.id
    job.lease_token_hash = sha256(lease_token.encode()).hexdigest()
    job.lease_until = now + timedelta(minutes=5)
    _set_queue_print_state(job.queue, job.document_kind, status="printing", method="automatic")
    db.commit()
    order_id = db.scalar(select(InventoryDispatchQueue.sales_order_id).where(InventoryDispatchQueue.id == job.queue_id))
    return api_response(data={
        "id": job.id,
        "order_id": order_id,
        "document_kind": job.document_kind,
        "lease_token": lease_token,
        "pdf_path": f"/api/printing/agent/jobs/{job.id}/pdf",
    })


def _leased_job(db: Session, job_id: int, agent: PlacementPrintAgent, lease_token: str) -> PlacementPrintJob:
    job = db.get(PlacementPrintJob, job_id)
    if (job is None or job.status != "leased" or job.claimed_by_agent_id != agent.id
            or not secrets.compare_digest(job.lease_token_hash or "", sha256(lease_token.encode()).hexdigest())):
        raise HTTPException(status_code=409, detail="打印任务租约已失效，请重新领取")
    return job


@router.get("/agent/jobs/{job_id}/pdf")
def agent_job_pdf(job_id: int, request: Request, x_print_agent_token: str | None = Header(default=None), x_print_lease_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _agent(db, x_print_agent_token, request)
    job = _leased_job(db, job_id, agent, x_print_lease_token or "")
    queue = db.scalar(
        select(InventoryDispatchQueue)
        .options(joinedload(InventoryDispatchQueue.warehouse))
        .where(InventoryDispatchQueue.id == job.queue_id)
    )
    return _pdf_response(_order(db, job.queue.sales_order_id), job.document_kind, queue=queue)


@router.post("/agent/jobs/{job_id}/complete")
def complete_job(job_id: int, payload: JobResult, request: Request, x_print_agent_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _agent(db, x_print_agent_token, request)
    job = _leased_job(db, job_id, agent, payload.lease_token)
    verified = payload.success and payload.verification == "spooler_completed"
    job.status = "printed" if verified else "submitted" if payload.success else "failed" if job.attempts >= 5 else "pending"
    job.printed_at = now_local() if verified else None
    job.last_error = None if payload.success else (payload.error or "打印机返回失败")[:500]
    job.lease_token_hash = None
    job.lease_until = None
    if verified:
        _set_queue_print_state(
            job.queue,
            job.document_kind,
            status="printed",
            method="automatic",
            printed_at=job.printed_at,
            increment=True,
        )
    elif payload.success:
        # Some drivers acknowledge spooling but expose no reliable completion state.
        # Never call this "printed" until a human confirms actual paper output.
        _set_queue_print_state(job.queue, job.document_kind, status="submitted", method="automatic")
    else:
        _set_queue_print_state(
            job.queue,
            job.document_kind,
            status="failed" if job.status == "failed" else "unprinted",
            method="automatic",
        )
    db.commit()
    return api_response(data={"id": job.id, "status": job.status})


@router.post("/placement-orders/{order_id}/mark-printed")
def mark_placement_order_printed(
    order_id: int,
    payload: PrintMark,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    queue = db.scalar(
        select(InventoryDispatchQueue)
        .where(InventoryDispatchQueue.sales_order_id == order_id)
        .with_for_update()
    )
    if queue is None:
        raise HTTPException(status_code=404, detail="该订单没有置货需求")
    now = now_local()
    job = db.scalar(select(PlacementPrintJob).where(
        PlacementPrintJob.queue_id == queue.id,
        PlacementPrintJob.document_kind == "placement",
    ).with_for_update())
    if job and job.status == "leased" and job.lease_until and job.lease_until > now:
        raise HTTPException(status_code=409, detail="自动打印设备正在处理这张置货单，请稍后刷新状态")
    if job is None:
        job = PlacementPrintJob(
            queue_id=queue.id,
            document_kind="placement",
            status="printed" if payload.source == "automatic_confirmed" else "manual_printed",
            printed_at=now,
        )
        db.add(job)
    else:
        job.status = "printed" if payload.source == "automatic_confirmed" else "manual_printed"
        job.printed_at = now
        job.last_error = None
        job.lease_token_hash = None
        job.lease_until = None
    queue.placement_printed_at = now
    queue.placement_print_status = "printed"
    queue.placement_print_method = payload.source
    queue.placement_printed_by_id = current_user.id
    queue.placement_print_count = int(queue.placement_print_count or 0) + 1
    db.commit()
    return api_response(data={
        "printed": True,
        "printed_at": now.isoformat(),
        "print_method": payload.source,
        "print_count": queue.placement_print_count,
    }, msg="已确认实际出纸" if payload.source == "automatic_confirmed" else "已记录为手动打印")


@router.post("/shipment-orders/{order_id}/mark-printed")
def mark_shipment_order_printed(
    order_id: int,
    payload: PrintMark,
    current_user: User = Depends(require_roles("inventory", "inventory_super", "manager", "chairman")),
    db: Session = Depends(get_db),
):
    order = db.get(SalesOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status not in {"pending_shipment", "shipped", "completed"}:
        raise HTTPException(status_code=409, detail="财务尚未审批通过，不能确认发货单打印")
    queue = db.scalar(
        select(InventoryDispatchQueue)
        .where(InventoryDispatchQueue.sales_order_id == order_id)
        .with_for_update()
    )
    if queue is None:
        raise HTTPException(status_code=404, detail="该订单没有发货需求")
    now = now_local()
    job = db.scalar(select(PlacementPrintJob).where(
        PlacementPrintJob.queue_id == queue.id,
        PlacementPrintJob.document_kind == "shipment",
    ).with_for_update())
    if job and job.status == "leased" and job.lease_until and job.lease_until > now:
        raise HTTPException(status_code=409, detail="自动打印设备正在处理这张发货单，请稍后刷新状态")
    if job is None:
        job = PlacementPrintJob(
            queue_id=queue.id,
            document_kind="shipment",
            status="printed" if payload.source == "automatic_confirmed" else "manual_printed",
            printed_at=now,
        )
        db.add(job)
    else:
        job.status = "printed" if payload.source == "automatic_confirmed" else "manual_printed"
        job.printed_at = now
        job.last_error = None
        job.lease_token_hash = None
        job.lease_until = None
    _set_queue_print_state(
        queue,
        "shipment",
        status="printed",
        method=payload.source,
        printed_at=now,
        printed_by_id=current_user.id,
        increment=True,
    )
    db.commit()
    return api_response(data={
        "printed": True,
        "printed_at": now.isoformat(),
        "print_method": payload.source,
        "print_count": queue.shipment_print_count,
    }, msg="已确认发货单实际出纸")


@router.get("/jobs")
def list_jobs(current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(PlacementPrintJob).order_by(PlacementPrintJob.id.desc()).limit(100)).all()
    return api_response(data=[{
        "id": row.id,
        "order_id": row.queue.sales_order_id,
        "document_kind": row.document_kind,
        "status": row.status,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat(),
    } for row in rows])


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    row = db.get(PlacementPrintJob, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="打印任务不存在")
    if row.status == "leased" and row.lease_until and row.lease_until > now_local():
        raise HTTPException(status_code=409, detail="打印任务仍由设备处理")
    if row.status not in {"failed", "submitted"}:
        raise HTTPException(status_code=409, detail="仅打印异常或已提交待确认的任务可以重新排队")
    row.status = "pending"
    row.attempts = 0
    row.lease_token_hash = None
    row.lease_until = None
    _set_queue_print_state(row.queue, row.document_kind, status="unprinted")
    db.commit()
    return api_response(msg="打印任务已重新排队")


@router.get("/access-logs")
def access_logs(current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(PlacementPrintAccessLog).order_by(PlacementPrintAccessLog.id.desc()).limit(500)).all()
    agents = {row.id: row.name for row in db.scalars(select(PlacementPrintAgent)).all()}
    return api_response(data=[{
        "id": row.id, "agent_name": agents.get(row.agent_id),
        "token_fingerprint": row.token_fingerprint, "ip_address": row.ip_address,
        "user_agent": row.user_agent, "method": row.method, "path": row.path,
        "result": row.result, "created_at": row.created_at.isoformat(),
    } for row in rows])


def purge_old_print_access_logs(db: Session) -> int:
    result = db.execute(delete(PlacementPrintAccessLog).where(
        PlacementPrintAccessLog.created_at < now_local() - timedelta(days=3)
    ))
    db.commit()
    return result.rowcount or 0


def purge_old_completed_print_jobs(db: Session) -> int:
    """清除过期打印历史，保留未确认、失败和待重试任务以防漏单。"""
    cutoff = now_local() - timedelta(days=3)
    result = db.execute(delete(PlacementPrintJob).where(
        PlacementPrintJob.status.in_(("printed", "manual_printed")),
        PlacementPrintJob.printed_at < cutoff,
    ))
    db.commit()
    return result.rowcount or 0
