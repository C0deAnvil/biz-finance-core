from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_roles
from models import InventoryExceptionRecord, User
from responses import api_response
from schemas.inventory import InventoryExceptionCreate, InventoryExceptionDecision
from services.inventory_exception_service import (
    approve_inventory_exception,
    create_inventory_exception,
    reject_inventory_exception,
    serialize_inventory_exception,
    _load_record,
)


router = APIRouter(prefix="/inventory/exceptions", tags=["inventory"])


@router.get("")
def list_inventory_exceptions(
    status: str | None = Query(default=None),
    current_user: User = Depends(require_roles("inventory", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    query = select(InventoryExceptionRecord).order_by(InventoryExceptionRecord.created_at.desc(), InventoryExceptionRecord.id.desc())
    if status:
        query = query.where(InventoryExceptionRecord.status == status)
    rows = db.execute(query).scalars().all()
    return api_response(data=[serialize_inventory_exception(_load_record(db, row.id)) for row in rows])


@router.get("/{record_id}")
def get_inventory_exception(
    record_id: int,
    current_user: User = Depends(require_roles("inventory", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return api_response(data=serialize_inventory_exception(_load_record(db, record_id)))


@router.post("")
def submit_inventory_exception(
    payload: InventoryExceptionCreate,
    current_user: User = Depends(require_roles("inventory", "system_admin")),
    db: Session = Depends(get_db),
):
    return create_inventory_exception(payload, current_user, db)


@router.post("/{record_id}/approve")
def approve_exception(
    record_id: int,
    payload: InventoryExceptionDecision,
    current_user: User = Depends(require_roles("manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return approve_inventory_exception(record_id, payload.note, current_user, db)


@router.post("/{record_id}/reject")
def reject_exception(
    record_id: int,
    payload: InventoryExceptionDecision,
    current_user: User = Depends(require_roles("manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    return reject_inventory_exception(record_id, payload.note, current_user, db)
