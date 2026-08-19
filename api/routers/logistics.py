from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import get_current_user, require_roles
from models import LogisticsRecord, PurchaseOrder, PurchaseOrderItem, SalesOrder, SalesOrderItem, User, Warehouse
from responses import api_response
from schemas.logistics import LogisticsRecordCreate, LogisticsRecordUpdate
from services.core import next_business_no, write_operation_log
from services.organization import (
    get_scoped_user_ids,
    is_master_data_admin,
    is_system_admin,
    user_in_scope,
    warehouse_in_scope,
)


router = APIRouter(prefix="/logistics", tags=["logistics"])


MODULE_LABELS = {
    "sales": "销售物流",
    "purchase": "采购物流",
    "inventory": "库存物流",
}

BUSINESS_TYPE_LABELS = {
    "sales_order": "客户订单",
    "purchase_order": "采购订单",
}

LOGISTICS_MODE_LABELS = {
    "road": "公路运输",
    "rail": "铁路运输",
    "sea": "水运",
    "air": "空运",
    "pickup": "自提",
}


def _load_sales_order(db: Session, business_id: int) -> SalesOrder | None:
    return db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.sales_user),
            joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
        )
        .where(SalesOrder.id == business_id)
    ).unique().scalar_one_or_none()


def _load_purchase_order(db: Session, business_id: int) -> PurchaseOrder | None:
    return db.execute(
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.purchaser),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
        )
        .where(PurchaseOrder.id == business_id)
    ).unique().scalar_one_or_none()


def _load_business(db: Session, business_type: str, business_id: int):
    if business_type == "sales_order":
        order = _load_sales_order(db, business_id)
        if order is None:
            raise HTTPException(status_code=404, detail="客户订单不存在")
        return order
    if business_type == "purchase_order":
        order = _load_purchase_order(db, business_id)
        if order is None:
            raise HTTPException(status_code=404, detail="采购订单不存在")
        return order
    raise HTTPException(status_code=404, detail="物流关联业务不存在")


def _business_owner_id(row) -> int | None:
    if isinstance(row, SalesOrder):
        return row.sales_user_id
    if isinstance(row, PurchaseOrder):
        return row.purchaser_user_id
    return None


def _business_related_no(row) -> str | None:
    return getattr(row, "order_no", None)


def _business_summary(row) -> dict:
    if isinstance(row, SalesOrder):
        return {
            "business_no": row.order_no,
            "customer_name": row.customer.name if row.customer else None,
            "owner_name": row.sales_user.full_name if row.sales_user else None,
            "status": row.status,
        }
    if isinstance(row, PurchaseOrder):
        return {
            "business_no": row.order_no,
            "supplier_name": row.supplier.name if row.supplier else None,
            "owner_name": row.purchaser.full_name if row.purchaser else None,
            "status": row.status,
        }
    return {}


def _business_goods_info(row) -> list[dict]:
    if isinstance(row, SalesOrder):
        return [
            {
                "item_name": item.product.name if item.product else None,
                "item_code": item.product.product_code if item.product else None,
                "quantity": float(item.quantity or 0),
                "specification": item.display_specification,
            }
            for item in row.items
        ]
    if isinstance(row, PurchaseOrder):
        return [
            {
                "item_name": item.product.name if item.product else (item.material.name if item.material else None),
                "item_code": item.product.product_code if item.product else (item.material.material_code if item.material else None),
                "quantity": float(item.quantity or 0),
                "specification": item.display_specification,
            }
            for item in row.items
        ]
    return []


def _can_view_record(db: Session, current_user: User, row: LogisticsRecord, business_row) -> bool:
    if is_system_admin(current_user) or is_master_data_admin(current_user):
        return True
    role_code = current_user.role.code if current_user.role else ""
    if role_code in {"manager", "chairman"}:
        return True
    if role_code == "sales":
        return row.business_type == "sales_order" and user_in_scope(
            db, current_user, business_role_code="sales", owner_user_id=_business_owner_id(business_row)
        )
    if role_code == "purchase":
        return row.business_type == "purchase_order" and user_in_scope(
            db, current_user, business_role_code="purchase", owner_user_id=_business_owner_id(business_row)
        )
    if role_code == "inventory":
        if row.module == "inventory":
            return warehouse_in_scope(db, current_user, row.warehouse_id)
        return warehouse_in_scope(db, current_user, row.warehouse_id)
    return False


def _assert_manage_permission(db: Session, current_user: User, *, module: str, business_type: str, business_row, warehouse_id: int | None):
    if is_system_admin(current_user) or is_master_data_admin(current_user):
        return
    role_code = current_user.role.code if current_user.role else ""
    if role_code == "sales":
        if module != "sales" or business_type != "sales_order":
            raise HTTPException(status_code=403, detail="销售仅可维护客户订单物流")
        if not user_in_scope(db, current_user, business_role_code="sales", owner_user_id=_business_owner_id(business_row)):
            raise HTTPException(status_code=403, detail="仅可维护权限范围内的客户订单物流")
        return
    if role_code == "purchase":
        if module != "purchase" or business_type != "purchase_order":
            raise HTTPException(status_code=403, detail="采购仅可维护采购订单物流")
        if not user_in_scope(db, current_user, business_role_code="purchase", owner_user_id=_business_owner_id(business_row)):
            raise HTTPException(status_code=403, detail="仅可维护权限范围内的采购订单物流")
        return
    if role_code == "inventory":
        if module != "inventory":
            raise HTTPException(status_code=403, detail="库管仅可维护库存物流")
        if not warehouse_in_scope(db, current_user, warehouse_id):
            raise HTTPException(status_code=403, detail="仅可维护所属仓库范围内的物流记录")
        return
    raise HTTPException(status_code=403, detail="当前角色无权维护物流记录")


def _serialize_logistics(row: LogisticsRecord, business_row, warehouse: Warehouse | None, creator: User | None) -> dict:
    return {
        "id": row.id,
        "logistics_no": row.logistics_no,
        "module": row.module,
        "module_label": MODULE_LABELS.get(row.module, row.module),
        "business_type": row.business_type,
        "business_type_label": BUSINESS_TYPE_LABELS.get(row.business_type, row.business_type),
        "business_id": row.business_id,
        "related_no": row.related_no,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "logistics_mode": row.logistics_mode,
        "logistics_mode_label": LOGISTICS_MODE_LABELS.get(row.logistics_mode, row.logistics_mode),
        "carrier_name": row.carrier_name,
        "driver_name": row.driver_name,
        "driver_phone": row.driver_phone,
        "vehicle_no": row.vehicle_no,
        "tracking_no": row.tracking_no,
        "bill_date": row.bill_date.isoformat() if row.bill_date else None,
        "origin_station": row.origin_station,
        "destination_station": row.destination_station,
        "transfer_station": row.transfer_station,
        "consignor_name": row.consignor_name,
        "consignor_phone": row.consignor_phone,
        "consignor_address": row.consignor_address,
        "consignee_name": row.consignee_name,
        "consignee_phone": row.consignee_phone,
        "consignee_address": row.consignee_address,
        "goods_info": row.goods_info_json or [],
        "delivery_fee": float(row.delivery_fee or 0),
        "freight_payment_type": row.freight_payment_type,
        "handover_type": row.handover_type,
        "document_attachment_id": row.document_attachment_id,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
        "arrived_at": row.arrived_at.isoformat() if row.arrived_at else None,
        "remark": row.remark,
        "created_by_id": row.created_by_id,
        "created_by_name": creator.full_name if creator else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "business_summary": _business_summary(business_row),
    }


@router.get("/options")
def logistics_options(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sales_query = select(SalesOrder).options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user)).order_by(SalesOrder.created_at.desc())
    purchase_query = select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.purchaser)).order_by(PurchaseOrder.created_at.desc())

    sales_scope = get_scoped_user_ids(db, current_user, "sales")
    if sales_scope is not None:
        sales_query = sales_query.where(SalesOrder.sales_user_id.in_(sales_scope or [-1]))
    purchase_scope = get_scoped_user_ids(db, current_user, "purchase")
    if purchase_scope is not None:
        purchase_query = purchase_query.where(PurchaseOrder.purchaser_user_id.in_(purchase_scope or [-1]))

    sales_orders = db.execute(sales_query).scalars().all()
    purchase_orders = db.execute(purchase_query).scalars().all()
    warehouses = db.execute(select(Warehouse).order_by(Warehouse.id.asc())).scalars().all()

    return api_response(
        data={
            "modules": [{"value": key, "label": value} for key, value in MODULE_LABELS.items()],
            "business_types": [{"value": key, "label": value} for key, value in BUSINESS_TYPE_LABELS.items()],
            "logistics_modes": [{"value": key, "label": value} for key, value in LOGISTICS_MODE_LABELS.items()],
            "sales_orders": [
                {
                    "id": row.id,
                    "order_no": row.order_no,
                    "customer_name": row.customer.name if row.customer else None,
                    "owner_name": row.sales_user.full_name if row.sales_user else None,
                    "status": row.status,
                }
                for row in sales_orders
            ],
            "purchase_orders": [
                {
                    "id": row.id,
                    "order_no": row.order_no,
                    "supplier_name": row.supplier.name if row.supplier else None,
                    "owner_name": row.purchaser.full_name if row.purchaser else None,
                    "status": row.status,
                }
                for row in purchase_orders
            ],
            "warehouses": [{"id": row.id, "name": row.name, "type": row.warehouse_type} for row in warehouses],
        }
    )


@router.get("")
def list_logistics(
    module: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(LogisticsRecord).order_by(LogisticsRecord.created_at.desc(), LogisticsRecord.id.desc())
    if module:
        query = query.where(LogisticsRecord.module == module)
    if business_type:
        query = query.where(LogisticsRecord.business_type == business_type)
    if business_id is not None:
        query = query.where(LogisticsRecord.business_id == business_id)
    if date_from is not None:
        query = query.where(LogisticsRecord.bill_date >= date_from)
    if date_to is not None:
        query = query.where(LogisticsRecord.bill_date <= date_to)

    rows = db.execute(query).scalars().all()
    if not rows:
        return api_response(data=[])

    warehouse_map = {
        row.id: row for row in db.execute(select(Warehouse).where(Warehouse.id.in_({item.warehouse_id for item in rows if item.warehouse_id}))).scalars().all()
    }
    creator_map = {
        row.id: row for row in db.execute(select(User).where(User.id.in_({item.created_by_id for item in rows if item.created_by_id}))).scalars().all()
    }

    sales_business_ids = [row.business_id for row in rows if row.business_type == "sales_order"]
    purchase_business_ids = [row.business_id for row in rows if row.business_type == "purchase_order"]
    sales_map = {
        row.id: row for row in db.execute(
            select(SalesOrder).options(joinedload(SalesOrder.customer), joinedload(SalesOrder.sales_user)).where(SalesOrder.id.in_(sales_business_ids))
        ).scalars().all()
    } if sales_business_ids else {}
    purchase_map = {
        row.id: row for row in db.execute(
            select(PurchaseOrder).options(joinedload(PurchaseOrder.supplier), joinedload(PurchaseOrder.purchaser)).where(PurchaseOrder.id.in_(purchase_business_ids))
        ).scalars().all()
    } if purchase_business_ids else {}

    data = []
    for row in rows:
        business_row = sales_map.get(row.business_id) if row.business_type == "sales_order" else purchase_map.get(row.business_id)
        if business_row is None:
            continue
        if not _can_view_record(db, current_user, row, business_row):
            continue
        data.append(_serialize_logistics(row, business_row, warehouse_map.get(row.warehouse_id), creator_map.get(row.created_by_id)))
    return api_response(data=data)


@router.get("/{record_id}")
def get_logistics_detail(record_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.execute(select(LogisticsRecord).where(LogisticsRecord.id == record_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="物流记录不存在")
    business_row = _load_business(db, row.business_type, row.business_id)
    if not _can_view_record(db, current_user, row, business_row):
        raise HTTPException(status_code=403, detail="无权查看该物流记录")
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == row.warehouse_id)).scalar_one_or_none() if row.warehouse_id else None
    creator = db.execute(select(User).where(User.id == row.created_by_id)).scalar_one_or_none() if row.created_by_id else None
    return api_response(data=_serialize_logistics(row, business_row, warehouse, creator))


@router.post("")
def create_logistics(
    payload: LogisticsRecordCreate,
    current_user: User = Depends(require_roles("sales", "purchase", "inventory", "system_admin", "master_data_admin")),
    db: Session = Depends(get_db),
):
    business_row = _load_business(db, payload.business_type, payload.business_id)
    _assert_manage_permission(
        db,
        current_user,
        module=payload.module,
        business_type=payload.business_type,
        business_row=business_row,
        warehouse_id=payload.warehouse_id,
    )

    warehouse = None
    if payload.warehouse_id is not None:
        warehouse = db.execute(select(Warehouse).where(Warehouse.id == payload.warehouse_id)).scalar_one_or_none()
        if warehouse is None:
            raise HTTPException(status_code=404, detail="物流关联仓库不存在")

    row = LogisticsRecord(
        logistics_no=next_business_no(db, "WL"),
        module=payload.module,
        business_type=payload.business_type,
        business_id=payload.business_id,
        related_no=_business_related_no(business_row),
        warehouse_id=payload.warehouse_id,
        logistics_mode=payload.logistics_mode,
        carrier_name=payload.carrier_name,
        driver_name=payload.driver_name,
        driver_phone=payload.driver_phone,
        vehicle_no=payload.vehicle_no,
        tracking_no=payload.tracking_no,
        bill_date=payload.bill_date or date.today(),
        origin_station=payload.origin_station,
        destination_station=payload.destination_station,
        transfer_station=payload.transfer_station,
        consignor_name=payload.consignor_name,
        consignor_phone=payload.consignor_phone,
        consignor_address=payload.consignor_address,
        consignee_name=payload.consignee_name,
        consignee_phone=payload.consignee_phone,
        consignee_address=payload.consignee_address,
        goods_info_json=_business_goods_info(business_row),
        delivery_fee=Decimal(payload.delivery_fee or 0),
        freight_payment_type=payload.freight_payment_type,
        handover_type=payload.handover_type,
        document_attachment_id=payload.document_attachment_id,
        dispatched_at=payload.dispatched_at,
        arrived_at=payload.arrived_at,
        remark=payload.remark,
        created_by_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    write_operation_log(
        db,
        user_id=current_user.id,
        module=payload.module,
        action="create_logistics_record",
        content=f"创建物流记录 {row.logistics_no}",
        after_data={"business_type": payload.business_type, "business_id": payload.business_id, "related_no": row.related_no},
    )
    db.commit()
    return api_response(data=_serialize_logistics(row, business_row, warehouse, current_user))


@router.put("/{record_id}")
def update_logistics(
    record_id: int,
    payload: LogisticsRecordUpdate,
    current_user: User = Depends(require_roles("sales", "purchase", "inventory", "system_admin", "master_data_admin")),
    db: Session = Depends(get_db),
):
    row = db.execute(select(LogisticsRecord).where(LogisticsRecord.id == record_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="物流记录不存在")
    business_row = _load_business(db, row.business_type, row.business_id)
    _assert_manage_permission(
        db,
        current_user,
        module=row.module,
        business_type=row.business_type,
        business_row=business_row,
        warehouse_id=row.warehouse_id,
    )

    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    row.bill_date = row.bill_date or date.today()
    row.goods_info_json = row.goods_info_json or _business_goods_info(business_row)

    db.commit()
    db.refresh(row)
    warehouse = db.execute(select(Warehouse).where(Warehouse.id == row.warehouse_id)).scalar_one_or_none() if row.warehouse_id else None
    creator = db.execute(select(User).where(User.id == row.created_by_id)).scalar_one_or_none() if row.created_by_id else None
    return api_response(data=_serialize_logistics(row, business_row, warehouse, creator))
