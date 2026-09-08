"""库存可用量的唯一口径(单一事实来源)。

背景:改造前系统里同时存在 4 套"可用量"算法(3 份逐字复制的 _expected_available_maps
+ replenishment_service 的两个变体 + inventory_dimensions 的缓存列),彼此口径不一致,
导致"库存 100 / 订单 100"被判成完全缺货。本模块收敛为两个明确区分的口径。

三个库存维度(均落库在 inventories 表):
  实有(quantity)      —— 仓库里真实存在的货
  占用(reserved)      —— 已被审批通过的客户订单锁定,不能再许诺给别人
  在途(in_transit)    —— 退货在途;采购在途由采购单实时推导,不落库

两个口径,分别回答两个不同的问题:

  1) 可置货/可发货量 = 实有 - 占用
     回答"现在能不能把货交给客户"。在途的货还没到仓库,不能发,故不计入。
     用于:待置货红黄绿判定、置货校验、发货校验。

  2) 补货可用量 = 实有 + 在途 - 占用
     回答"还需不需要再去补货"。已经在采购/生产途中的量要算进来,
     否则会对同一个缺口重复提补货需求。
     用于:补货需求去重、安全库存预警、缺口计算。

注意:两个算式都**不再减去** pending_sales(未发货订单量)。因为财务审批通过时
已经把该订单的需求写进了 reserved,再减一次 pending_sales 就是对同一笔需求重复扣减。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    Inventory,
    ProductionMaterialUsage,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
)

# 采购单处于这些状态时,未收货部分算"在途"(已下单但还没进仓)
ACTIVE_PURCHASE_STATUSES = (
    "purchasing",
    "partial_received",
    "pending_purchase_confirm",
    "fully_received",
)

# 生产任务处于这些终态时,不再占用原料(已完成/已取消/已报废/已返工)
INACTIVE_TASK_STATUSES = (
    "completed",
    "cancelled",
    "scrapped",
    "partially_scrapped",
    "reworked",
    "rework_pending",
)

StockKey = tuple[str, int | None]


def stock_key(*, product_id: int | None = None, material_id: int | None = None) -> StockKey:
    """产品/原料的统一字典键(替代原先散落在 3 个 router 里的 _stock_key 副本)。"""
    return ("product", product_id) if product_id else ("material", material_id)


# ===== 行级(单个 inventories 行)=====

def placeable_of(inventory: Inventory | None) -> Decimal:
    """可置货量 = 实有 - 占用。库存行不存在时为 0。

    不做 max(...,0) 截断的下层保护放在调用方:这里如实反映数据,
    便于发现"占用 > 实有"这类数据异常。
    """
    if inventory is None:
        return Decimal("0")
    return Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0)


def replenishment_available_of(
    inventory: Inventory | None,
    *,
    purchase_in_transit: Decimal | int = 0,
) -> Decimal:
    """补货可用量 = 实有 + 退货在途 + 采购在途 - 占用。

    采购在途由调用方传入(通常来自 purchase_in_transit_map 的批量结果),
    避免在循环里逐行查采购单造成 N+1。
    """
    if inventory is None:
        return Decimal(purchase_in_transit or 0)
    return (
        Decimal(inventory.quantity or 0)
        - Decimal(inventory.reserved_quantity or 0)
        + Decimal(inventory.in_transit or 0)
        + Decimal(purchase_in_transit or 0)
    )


# ===== 批量(列表页用,一次查询算出全部)=====

def material_production_commitment(db: Session, *, material_id: int) -> Decimal:
    """已被在产任务计划领用、但尚未实际领出的原料量。

    这部分原料虽然还躺在仓库里(实有未减),但已经被排产的任务预定了。
    inventories.reserved_quantity 只记录客户订单对成品的占用,不覆盖这种场景,
    所以必须单独扣除,否则同一批原料会被重复许诺给多个生产任务。
    """
    row = db.execute(
        select(
            func.coalesce(func.sum(ProductionMaterialUsage.planned_quantity), 0),
            func.coalesce(func.sum(ProductionMaterialUsage.actual_quantity), 0),
        )
        .join(ProductionTask, ProductionTask.id == ProductionMaterialUsage.production_task_id)
        .where(
            ProductionMaterialUsage.material_id == material_id,
            ProductionTask.status.not_in(INACTIVE_TASK_STATUSES),
        )
    ).one()
    return max(Decimal(row[0] or 0) - Decimal(row[1] or 0), Decimal("0"))


def purchase_in_transit_map(db: Session) -> dict[StockKey, Decimal]:
    """采购在途 = 活跃采购单的 已下单量 - 已收货量,按产品/原料聚合。"""
    result: dict[StockKey, Decimal] = {}
    for product_id, material_id, ordered_quantity, received_quantity in db.execute(
        select(
            PurchaseOrderItem.product_id,
            PurchaseOrderItem.material_id,
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.status.in_(ACTIVE_PURCHASE_STATUSES))
        .group_by(PurchaseOrderItem.product_id, PurchaseOrderItem.material_id)
    ).all():
        result[stock_key(product_id=product_id, material_id=material_id)] = max(
            Decimal(ordered_quantity or 0) - Decimal(received_quantity or 0),
            Decimal("0"),
        )
    return result


# ===== 跨仓库汇总(按物料统计全公司口径)=====

def _sum_inventory(
    db: Session,
    *,
    product_id: int | None = None,
    material_id: int | None = None,
    warehouse_id: int | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """汇总某物料的 (实有, 占用, 退货在途)。warehouse_id 为空则跨全部仓库。"""
    filters = []
    if product_id is not None:
        filters.append(Inventory.product_id == product_id)
        filters.append(Inventory.material_id.is_(None))
    if material_id is not None:
        filters.append(Inventory.material_id == material_id)
        filters.append(Inventory.product_id.is_(None))
    if warehouse_id is not None:
        filters.append(Inventory.warehouse_id == warehouse_id)
    row = db.execute(
        select(
            func.coalesce(func.sum(Inventory.quantity), 0),
            func.coalesce(func.sum(Inventory.reserved_quantity), 0),
            func.coalesce(func.sum(Inventory.in_transit), 0),
        ).where(*filters)
    ).one()
    return Decimal(row[0] or 0), Decimal(row[1] or 0), Decimal(row[2] or 0)


def purchase_in_transit_for(
    db: Session, *, product_id: int | None = None, material_id: int | None = None
) -> Decimal:
    filters = [PurchaseOrder.status.in_(ACTIVE_PURCHASE_STATUSES)]
    if product_id is not None:
        filters.append(PurchaseOrderItem.product_id == product_id)
    if material_id is not None:
        filters.append(PurchaseOrderItem.material_id == material_id)
    row = db.execute(
        select(
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(*filters)
    ).one()
    return max(Decimal(row[0] or 0) - Decimal(row[1] or 0), Decimal("0"))


def placeable_quantity(
    db: Session,
    *,
    product_id: int | None = None,
    material_id: int | None = None,
    warehouse_id: int | None = None,
) -> Decimal:
    """可置货量(实有 - 占用),跨仓库或指定仓库汇总。"""
    quantity, reserved, _ = _sum_inventory(
        db, product_id=product_id, material_id=material_id, warehouse_id=warehouse_id
    )
    return quantity - reserved


def replenishment_available(
    db: Session,
    *,
    product_id: int | None = None,
    material_id: int | None = None,
    warehouse_id: int | None = None,
) -> Decimal:
    """补货可用量(实有 + 在途 - 占用),用于判断是否还需要提补货需求。"""
    quantity, reserved, return_transit = _sum_inventory(
        db, product_id=product_id, material_id=material_id, warehouse_id=warehouse_id
    )
    in_transit = purchase_in_transit_for(db, product_id=product_id, material_id=material_id)
    return quantity - reserved + return_transit + in_transit
