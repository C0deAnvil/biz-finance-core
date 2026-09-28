from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Warehouse


def _find_warehouse(db: Session, *, warehouse_type: str, codes: tuple[str, ...], error_message: str) -> Warehouse:
    warehouse = db.execute(select(Warehouse).where(Warehouse.code.in_(codes))).scalars().first()
    if warehouse is None:
        warehouse = db.execute(select(Warehouse).where(Warehouse.warehouse_type == warehouse_type).order_by(Warehouse.id.asc())).scalars().first()
    if warehouse is None:
        raise HTTPException(status_code=500, detail=error_message)
    return warehouse


def get_finished_warehouse(db: Session) -> Warehouse:
    return _find_warehouse(
        db,
        warehouse_type="finished",
        codes=("CANGZHOU_FINISHED", "WH-FG"),
        error_message="成品仓未初始化，请先在主数据中创建 warehouse_type=finished 的成品仓库",
    )


def get_raw_warehouse(db: Session) -> Warehouse:
    return _find_warehouse(
        db,
        warehouse_type="raw",
        codes=("LANGFANG_RAW", "WH-RM"),
        error_message="原料仓未初始化，请先在主数据中创建 warehouse_type=raw 的原料仓库",
    )


def get_factory_warehouse(db: Session) -> Warehouse:
    """廊坊工厂仓库；当前主数据将它归类为 raw，但也接收生产成品。"""
    return get_raw_warehouse(db)
