"""Warehouse barcode workflow for full placement and full shipment.

The scanner is treated as a keyboard.  Authentication lives on the trusted
Windows workstation, never inside the printed barcode.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re
import secrets
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload, selectinload

from api.routers.inventory._common import _placement_blocked_reason, _placement_stock_status
from api.routers.inventory.queues import mark_placement, mark_shipment, shipment_blocked_conditions
from config import settings
from database import get_db
from dependencies import get_current_user
from models import (
    Attachment,
    InventoryDispatchQueue,
    SalesOrder,
    SalesOrderItem,
    SalesShipmentBatch,
    ShipmentProof,
    User,
    Warehouse,
    WarehouseScanAccessLog,
    WarehouseScanDevice,
    WarehouseScanEvent,
)
from responses import api_response
from schemas.sales import PlacementActionRequest, PlacementItemInput, SalesShipmentItemInput, SalesShipmentRequest
from services.core import next_business_no
from services.display import status_label
from services.organization import warehouse_in_scope
from services.time_utils import now_local


router = APIRouter(prefix="/warehouse-scanning", tags=["仓库扫码"])

PLACEMENT_PREFIX = "YCP01"
SHIPMENT_PREFIX = "YCS01"
BARCODE_RE = re.compile(r"^(YCP01|YCS01)([A-F0-9]{24})$")
MAX_PROOF_BYTES = 10 * 1024 * 1024
PROOF_VALID_MINUTES = 30


class ScanDeviceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    warehouse_id: int
    operator_user_id: int


class ScanRequest(BaseModel):
    barcode: str = Field(..., min_length=10, max_length=80)


class ConfirmScanRequest(ScanRequest):
    idempotency_key: str = Field(..., min_length=8, max_length=80)


class ShipmentConfirmRequest(ConfirmScanRequest):
    proof_id: int


def _system_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.role or not (current_user.role.is_admin or current_user.role.code == "system_admin"):
        raise HTTPException(status_code=403, detail="仅系统管理员可管理仓库扫码设备")
    return current_user


def _client_ip(request: Request) -> str | None:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else None)


def _device_auth(
    request: Request,
    x_warehouse_device_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> WarehouseScanDevice:
    digest = sha256(x_warehouse_device_token.encode()).hexdigest() if x_warehouse_device_token else None
    device = db.scalar(
        select(WarehouseScanDevice)
        .options(joinedload(WarehouseScanDevice.operator_user).joinedload(User.role))
        .where(
            WarehouseScanDevice.token_hash == digest,
            WarehouseScanDevice.is_active.is_(True),
        )
    ) if digest else None
    db.add(WarehouseScanAccessLog(
        device_id=device.id if device else None,
        token_fingerprint=digest[:12] if digest else None,
        ip_address=_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
        method=request.method,
        path=request.url.path[:255],
        result="authorized" if device else "rejected",
    ))
    if device:
        device.last_seen_at = now_local()
    db.commit()
    if device is None:
        raise HTTPException(status_code=401, detail="仓库扫码设备认证码无效、缺失或已停用")
    if not device.operator_user or not device.operator_user.is_active:
        raise HTTPException(status_code=403, detail="扫码设备绑定的操作员不可用")
    if not device.operator_user.role or device.operator_user.role.code not in {"inventory", "inventory_super"}:
        raise HTTPException(status_code=403, detail="扫码设备绑定账号已不再具有库管权限")
    if not warehouse_in_scope(db, device.operator_user, device.warehouse_id):
        raise HTTPException(status_code=403, detail="扫码设备绑定操作员已无权处理该仓库")
    return device


def _parse_barcode(raw: str) -> tuple[str, str]:
    normalized = "".join((raw or "").strip().upper().split())
    match = BARCODE_RE.fullmatch(normalized)
    if not match:
        raise HTTPException(status_code=422, detail="条码格式不正确，请扫描系统生成的置货或发货条码")
    return ("placement" if match.group(1) == PLACEMENT_PREFIX else "shipment", match.group(2))


def _queue_for_barcode(db: Session, raw: str, *, lock: bool = False) -> tuple[str, InventoryDispatchQueue]:
    action, token = _parse_barcode(raw)
    condition = (
        InventoryDispatchQueue.placement_scan_token == token
        if action == "placement"
        else InventoryDispatchQueue.shipment_scan_token == token
    )
    query = select(InventoryDispatchQueue).where(condition)
    if lock:
        query = query.with_for_update()
    queue = db.scalar(query)
    if queue is None:
        raise HTTPException(status_code=404, detail="条码对应的仓库作业单不存在")
    return action, queue


def _assert_device_queue(device: WarehouseScanDevice, queue: InventoryDispatchQueue) -> None:
    if queue.warehouse_id != device.warehouse_id:
        raise HTTPException(status_code=403, detail="该作业单不属于本设备绑定的仓库")


def _order(db: Session, queue: InventoryDispatchQueue) -> SalesOrder:
    order = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.sales_user),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.product),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.packaging_config),
        )
        .where(SalesOrder.id == queue.sales_order_id)
    ).unique().scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="作业单关联的客户订单不存在")
    return order


def _placement_stock_detail(stock: dict) -> str | None:
    shortages = []
    for item in stock.get("items", []):
        name = item.get("product_name") or "商品"
        product_short = Decimal(str(item.get("shortage_quantity") or 0))
        packaging_short = Decimal(str(item.get("packaging_shortage_quantity") or 0))
        if product_short > 0:
            shortages.append(f"{name}成品缺 {product_short:g}")
        if packaging_short > 0:
            packaging_name = item.get("packaging_name") or "包装"
            shortages.append(f"{name}的{packaging_name}缺 {packaging_short:g}")
    return "；".join(shortages) if shortages else None


def _placement_reason(db: Session, order: SalesOrder, stock: dict) -> str | None:
    reason = _placement_blocked_reason(db, order, stock["stock_status"])
    # 扫码始终是整单置货；黄色的部分可置状态也要指出具体缺口。
    if reason and stock["stock_status"] == "red" and order.status == "pending_shipment":
        detail = _placement_stock_detail(stock)
        return f"整单置货库存不足：{detail}" if detail else reason
    if reason:
        return reason
    detail = _placement_stock_detail(stock)
    return f"整单置货条件未满足：{detail}" if detail else None


def _shipment_reason(db: Session, order: SalesOrder, queue: InventoryDispatchQueue) -> str | None:
    if queue.placement_status != "hidden":
        return "该订单尚未完成置货，请先扫描置货码"
    if order.status != "pending_shipment":
        return f"订单当前状态为{status_label(order.status)}，不处于待发货状态"
    if order.finance_edit_pending:
        return "财务修改待销售和库存确认，暂不能发货"
    conditions = shipment_blocked_conditions(db, order)
    return "；".join(conditions) if conditions else None


def _serialize_event(row: WarehouseScanEvent) -> dict:
    return {
        "event_id": row.id,
        "device_id": row.device_id,
        "warehouse_id": row.warehouse_id,
        "operator_user_id": row.operator_user_id,
        "action": row.action,
        "result": row.result,
        "message": row.message,
        "queue_id": row.queue_id,
        "order_id": row.sales_order_id,
        "metadata": row.metadata_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _existing_event(db: Session, device_id: int, key: str) -> WarehouseScanEvent | None:
    return db.scalar(select(WarehouseScanEvent).where(
        WarehouseScanEvent.device_id == device_id,
        WarehouseScanEvent.idempotency_key == key,
    ))


def _record_event(
    db: Session,
    *,
    device: WarehouseScanDevice,
    request: Request,
    queue: InventoryDispatchQueue | None,
    action: str,
    key: str,
    barcode: str,
    result: str,
    message: str,
    metadata: dict | None = None,
) -> WarehouseScanEvent:
    row = _event_row(
        device=device, request=request, queue=queue, action=action, key=key,
        barcode=barcode, result=result, message=message, metadata=metadata,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _event_row(
    *,
    device: WarehouseScanDevice,
    request: Request,
    queue: InventoryDispatchQueue | None,
    action: str,
    key: str,
    barcode: str,
    result: str,
    message: str,
    metadata: dict | None = None,
) -> WarehouseScanEvent:
    return WarehouseScanEvent(
        device_id=device.id,
        queue_id=queue.id if queue else None,
        sales_order_id=queue.sales_order_id if queue else None,
        warehouse_id=device.warehouse_id,
        operator_user_id=device.operator_user_id,
        action=action,
        idempotency_key=key,
        barcode_fingerprint=sha256(barcode.encode()).hexdigest()[:12],
        result=result,
        message=message[:500],
        ip_address=_client_ip(request),
        metadata_json=metadata or {},
    )


def _failure(
    db: Session,
    *,
    device: WarehouseScanDevice,
    request: Request,
    queue: InventoryDispatchQueue | None,
    action: str,
    key: str,
    barcode: str,
    message: str,
    metadata: dict | None = None,
):
    db.rollback()
    event = _record_event(
        db, device=device, request=request, queue=queue, action=action, key=key,
        barcode=barcode, result="rejected", message=message, metadata=metadata,
    )
    return api_response(code=409, msg=message, data=_serialize_event(event))


@router.post("/devices")
def create_device(
    payload: ScanDeviceCreate,
    current_user: User = Depends(_system_admin),
    db: Session = Depends(get_db),
):
    warehouse = db.get(Warehouse, payload.warehouse_id)
    operator = db.execute(
        select(User).options(joinedload(User.role)).where(User.id == payload.operator_user_id)
    ).scalar_one_or_none()
    if warehouse is None:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if operator is None or not operator.is_active:
        raise HTTPException(status_code=404, detail="绑定操作员不存在或已停用")
    if not operator.role or operator.role.code not in {"inventory", "inventory_super"}:
        raise HTTPException(status_code=422, detail="扫码设备只能绑定库管或超级库管账号")
    if not warehouse_in_scope(db, operator, warehouse.id):
        raise HTTPException(status_code=422, detail="该操作员无权处理所选仓库")
    token = secrets.token_urlsafe(40)
    row = WarehouseScanDevice(
        name=payload.name.strip(),
        token_hash=sha256(token.encode()).hexdigest(),
        warehouse_id=warehouse.id,
        operator_user_id=operator.id,
        created_by_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return api_response(
        msg="认证码只显示一次，请保存到仓库电脑，不要写入条码或纸质单据",
        data={"id": row.id, "name": row.name, "warehouse_name": warehouse.name, "token": token},
    )


@router.get("/devices")
def list_devices(current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    rows = db.execute(
        select(WarehouseScanDevice)
        .options(joinedload(WarehouseScanDevice.warehouse), joinedload(WarehouseScanDevice.operator_user))
        .order_by(WarehouseScanDevice.id.desc())
    ).scalars().all()
    return api_response(data=[{
        "id": row.id,
        "name": row.name,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": row.warehouse.name if row.warehouse else None,
        "operator_user_id": row.operator_user_id,
        "operator_name": row.operator_user.full_name if row.operator_user else None,
        "is_active": row.is_active,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "created_at": row.created_at.isoformat(),
    } for row in rows])


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: int, current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    row = db.get(WarehouseScanDevice, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="扫码设备不存在")
    row.is_active = False
    db.commit()
    return api_response(msg="扫码设备认证码已停用")


@router.get("/events")
def list_events(current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(WarehouseScanEvent).order_by(WarehouseScanEvent.id.desc()).limit(500)).all()
    return api_response(data=[_serialize_event(row) for row in rows])


@router.get("/access-logs")
def list_access_logs(current_user: User = Depends(_system_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(WarehouseScanAccessLog).order_by(WarehouseScanAccessLog.id.desc()).limit(500)).all()
    device_names = {row.id: row.name for row in db.scalars(select(WarehouseScanDevice)).all()}
    return api_response(data=[{
        "id": row.id, "device_id": row.device_id, "device_name": device_names.get(row.device_id), "token_fingerprint": row.token_fingerprint,
        "ip_address": row.ip_address, "user_agent": row.user_agent, "method": row.method,
        "path": row.path, "result": row.result, "created_at": row.created_at.isoformat(),
    } for row in rows])


@router.post("/device/heartbeat")
def heartbeat(device: WarehouseScanDevice = Depends(_device_auth)):
    return api_response(data={"device_id": device.id, "server_time": now_local().isoformat()})


@router.post("/device/resolve")
def resolve_scan(
    payload: ScanRequest,
    device: WarehouseScanDevice = Depends(_device_auth),
    db: Session = Depends(get_db),
):
    action, queue = _queue_for_barcode(db, payload.barcode)
    _assert_device_queue(device, queue)
    order = _order(db, queue)
    if action == "placement":
        stock = _placement_stock_status(db, order, queue.warehouse_id)
        reason = _placement_reason(db, order, stock)
        completed = queue.placement_status == "hidden"
        allowed = completed or reason is None
        message = "该订单已经完成置货" if completed else (reason or "可整单置货")
    else:
        completed = queue.shipment_status == "completed"
        reason = _shipment_reason(db, order, queue)
        allowed = completed or reason is None
        message = "该订单已经完成发货" if completed else (reason or "请拍摄并上传发货凭证后再次扫描发货码")
    return api_response(data={
        "action": action,
        "allowed": allowed,
        "completed": completed,
        "message": message,
        "queue_id": queue.id,
        "order_id": order.id,
        "order_no": order.order_no,
        "warehouse_id": queue.warehouse_id,
        "requires_photo": action == "shipment" and not completed,
    })


@router.post("/device/placement/confirm")
def confirm_placement(
    payload: ConfirmScanRequest,
    request: Request,
    device: WarehouseScanDevice = Depends(_device_auth),
    db: Session = Depends(get_db),
):
    existing = _existing_event(db, device.id, payload.idempotency_key)
    if existing:
        return api_response(code=200 if existing.result == "success" else 409, msg=existing.message or "请求已处理", data=_serialize_event(existing))
    action, queue = _queue_for_barcode(db, payload.barcode, lock=True)
    if action != "placement":
        return _failure(db, device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key, barcode=payload.barcode, message="请扫描置货条码，不要扫描发货条码")
    try:
        _assert_device_queue(device, queue)
    except HTTPException as exc:
        return _failure(db, device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key, barcode=payload.barcode, message=str(exc.detail))
    order = _order(db, queue)
    if queue.placement_status == "hidden":
        event = _record_event(db, device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key, barcode=payload.barcode, result="success", message="该订单已经完成置货，本次未重复处理", metadata={"already_completed": True, "order_no": order.order_no})
        return api_response(msg=event.message, data=_serialize_event(event))
    stock = _placement_stock_status(db, order, queue.warehouse_id)
    blocked = _placement_reason(db, order, stock)
    if blocked:
        return _failure(db, device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key, barcode=payload.barcode, message=blocked, metadata={"stock_status": stock["stock_status"], "items": stock["items"]})
    items = [
        PlacementItemInput(
            sales_order_item_id=item.id,
            placed_quantity=max(Decimal(item.quantity or 0) - Decimal(item.placed_quantity or 0) - Decimal(item.shipped_quantity or 0), Decimal("0")),
        )
        for item in order.items
        if Decimal(item.quantity or 0) > Decimal(item.placed_quantity or 0) + Decimal(item.shipped_quantity or 0)
    ]
    event = _event_row(
        device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key,
        barcode=payload.barcode, result="success", message=f"订单 {order.order_no} 已整单置货",
        metadata={"order_no": order.order_no, "full_quantity": True},
    )
    db.add(event)
    try:
        result = mark_placement(
            queue.id,
            PlacementActionRequest(action="placed", items=items, atomic=True),
            current_user=device.operator_user,
            db=db,
        )
    except HTTPException as exc:
        return _failure(db, device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key, barcode=payload.barcode, message=str(exc.detail))
    if result.get("code") != 200:
        data = result.get("data") or {}
        shortages = data.get("shortages") or []
        details = [
            f"{row.get('product_name') or '商品'}的{row.get('packaging_name') or ('包装' if row.get('shortage_type') == 'packaging' else '成品')}缺 {row.get('shortage_quantity', 0)}"
            for row in shortages if isinstance(row, dict)
        ]
        message = result.get("msg") or "整单置货失败"
        if details:
            message += "：" + "；".join(details)
        return _failure(db, device=device, request=request, queue=queue, action="placement", key=payload.idempotency_key, barcode=payload.barcode, message=message, metadata=data)
    db.refresh(event)
    return api_response(msg=event.message, data=_serialize_event(event))


@router.post("/device/shipment-proofs")
async def upload_shipment_proof(
    request: Request,
    barcode: str = Form(...),
    captured_at: datetime = Form(...),
    file: UploadFile = File(...),
    device: WarehouseScanDevice = Depends(_device_auth),
    db: Session = Depends(get_db),
):
    action, queue = _queue_for_barcode(db, barcode)
    if action != "shipment":
        raise HTTPException(status_code=409, detail="发货照片必须关联发货条码")
    _assert_device_queue(device, queue)
    if queue.shipment_status == "completed":
        raise HTTPException(status_code=409, detail="该订单已经完成发货")
    order = _order(db, queue)
    blocked = _shipment_reason(db, order, queue)
    if blocked:
        raise HTTPException(status_code=409, detail=blocked)
    content = await file.read(MAX_PROOF_BYTES + 1)
    await file.close()
    filename = file.filename or "shipment-proof.jpg"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=422, detail="发货凭证只支持 JPG、JPEG 或 PNG 图片")
    if not content or len(content) > MAX_PROOF_BYTES:
        raise HTTPException(status_code=422, detail="发货凭证不能为空且不能超过 10MB")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=422, detail="JPG 图片内容无效")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=422, detail="PNG 图片内容无效")
    now = now_local()
    captured = datetime.fromtimestamp(captured_at.timestamp()) if captured_at.tzinfo else captured_at
    if captured > now + timedelta(minutes=5) or captured < now - timedelta(hours=24):
        raise HTTPException(status_code=422, detail="照片拍摄时间异常，请重新拍摄")
    content_sha256 = sha256(content).hexdigest()
    existing_proof = db.scalar(select(ShipmentProof).where(ShipmentProof.sha256 == content_sha256))
    if existing_proof:
        if (
            existing_proof.queue_id == queue.id
            and existing_proof.device_id == device.id
            and existing_proof.used_at is None
        ):
            return api_response(msg="该照片已上传，可继续扫描发货码", data={
                "proof_id": existing_proof.id,
                "queue_id": queue.id,
                "order_id": queue.sales_order_id,
                "valid_until": (existing_proof.created_at + timedelta(minutes=PROOF_VALID_MINUTES)).isoformat(),
            })
        raise HTTPException(status_code=409, detail="该照片已经关联过其他发货作业，不能重复使用")
    target_dir = settings.upload_dir / "sales_order" / str(queue.sales_order_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"shipment_proof_{queue.sales_order_id}_{uuid.uuid4().hex}{suffix}"
    target_path = target_dir / stored_name
    target_path.write_bytes(content)
    try:
        attachment = Attachment(
            business_type="sales_order",
            business_id=queue.sales_order_id,
            # 与销售订单详情、全链看板统一附件类型；前端仍兼容历史“发货凭证”。
            attachment_type="发货照片",
            original_name=filename[:255],
            stored_name=stored_name,
            relative_path=str(target_path.relative_to(settings.upload_dir)),
            mime_type="image/png" if suffix == ".png" else "image/jpeg",
            uploaded_by_id=device.operator_user_id,
        )
        db.add(attachment)
        db.flush()
        proof = ShipmentProof(
            queue_id=queue.id,
            sales_order_id=queue.sales_order_id,
            warehouse_id=device.warehouse_id,
            device_id=device.id,
            attachment_id=attachment.id,
            sha256=content_sha256,
            captured_at=captured,
        )
        db.add(proof)
        db.commit()
        db.refresh(proof)
    except Exception:
        db.rollback()
        target_path.unlink(missing_ok=True)
        raise
    return api_response(msg="发货凭证已上传，请扫描发货条码完成整单发货", data={
        "proof_id": proof.id,
        "queue_id": queue.id,
        "order_id": queue.sales_order_id,
        "valid_until": (now + timedelta(minutes=PROOF_VALID_MINUTES)).isoformat(),
    })


@router.post("/device/shipment/confirm")
def confirm_shipment(
    payload: ShipmentConfirmRequest,
    request: Request,
    device: WarehouseScanDevice = Depends(_device_auth),
    db: Session = Depends(get_db),
):
    existing = _existing_event(db, device.id, payload.idempotency_key)
    if existing:
        return api_response(code=200 if existing.result == "success" else 409, msg=existing.message or "请求已处理", data=_serialize_event(existing))
    action, queue = _queue_for_barcode(db, payload.barcode, lock=True)
    if action != "shipment":
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message="请扫描发货条码，不要扫描置货条码")
    try:
        _assert_device_queue(device, queue)
    except HTTPException as exc:
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message=str(exc.detail))
    order = _order(db, queue)
    if queue.shipment_status == "completed":
        event = _record_event(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, result="success", message="该订单已经完成发货，本次未重复扣减库存", metadata={"already_completed": True, "order_no": order.order_no})
        return api_response(msg=event.message, data=_serialize_event(event))
    proof = db.scalar(select(ShipmentProof).where(ShipmentProof.id == payload.proof_id).with_for_update())
    now = now_local()
    if proof is None or proof.queue_id != queue.id or proof.device_id != device.id:
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message="发货凭证不存在或不属于本订单/本设备")
    if proof.used_at is not None:
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message="该发货凭证已经使用，不能重复发货")
    if proof.created_at < now - timedelta(minutes=PROOF_VALID_MINUTES):
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message="发货凭证已超过30分钟，请重新拍摄")
    items = [
        SalesShipmentItemInput(
            sales_order_item_id=item.id,
            shipped_quantity=max(Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0), Decimal("0")),
        )
        for item in order.items
        if Decimal(item.quantity or 0) > Decimal(item.shipped_quantity or 0)
    ]
    proof.used_at = now
    batch = SalesShipmentBatch(
        batch_no=next_business_no(db, "FH"),
        queue_id=queue.id,
        sales_order_id=order.id,
        warehouse_id=device.warehouse_id,
        device_id=device.id,
        operator_user_id=device.operator_user_id,
        proof_id=proof.id,
        status="completed",
        total_quantity=sum((Decimal(item.shipped_quantity or 0) for item in items), Decimal("0")),
        shipped_at=now,
    )
    db.add(batch)
    event = _event_row(
        device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key,
        barcode=payload.barcode, result="success", message=f"订单 {order.order_no} 已整单发货",
        metadata={"order_no": order.order_no, "proof_id": proof.id, "shipment_batch_no": batch.batch_no, "full_quantity": True},
    )
    db.add(event)
    try:
        result = mark_shipment(
            queue.id,
            SalesShipmentRequest(items=items, remark=f"扫码整单发货，凭证 #{proof.id}", force=False),
            current_user=device.operator_user,
            db=db,
        )
    except HTTPException as exc:
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message=str(exc.detail))
    if result.get("code") != 200:
        data = result.get("data") or {}
        conditions = data.get("blocked_conditions") or []
        message = "；".join(str(condition) for condition in conditions) if conditions else (result.get("msg") or "整单发货失败")
        return _failure(db, device=device, request=request, queue=queue, action="shipment", key=payload.idempotency_key, barcode=payload.barcode, message=message, metadata=data)
    db.refresh(event)
    return api_response(msg=event.message, data=_serialize_event(event))


def purge_old_scan_access_logs(db: Session) -> int:
    result = db.execute(delete(WarehouseScanAccessLog).where(
        WarehouseScanAccessLog.created_at < now_local() - timedelta(days=3)
    ))
    db.commit()
    return result.rowcount or 0


def purge_old_scan_events(db: Session) -> int:
    """扫码处理结果仅保留三天；订单、出入库和发货凭证不在清理范围内。"""
    result = db.execute(delete(WarehouseScanEvent).where(
        WarehouseScanEvent.created_at < now_local() - timedelta(days=3)
    ))
    db.commit()
    return result.rowcount or 0
