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

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    BOM,
    BOMItem,
    FulfillmentDemand,
    Inventory,
    MaterialReplenishmentDemand,
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


def stock_key(
    *,
    product_id: int | None = None,
    material_id: int | None = None,
    packaging_config_id: int | None = None,
) -> StockKey:
    """产品、原料、包装物的统一字典键。"""
    if product_id:
        return ("product", product_id)
    if material_id:
        return ("material", material_id)
    return ("packaging", packaging_config_id)


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

def material_production_commitments(
    db: Session,
    *,
    material_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    exclude_fulfillment_demand_id: int | None = None,
) -> dict[int, Decimal]:
    """按原料汇总尚未领出的生产占用。

    占用包含两部分：
    1. 已建立生产任务的实际申请领料量(requested_quantity - actual_quantity)；
    2. 尚未转任务的生产补货需求。后者以人工已提交的原料需求量为准，
       未人工调整时才使用 BOM 自动计算量。

    fulfillment_demand 转成任务时 shortage_quantity 会同步减少，而任务领料量
    会同步增加，因此两部分不会对同一批数量重复占用。
    """
    ids = {int(value) for value in (material_ids or []) if value}
    result: dict[int, Decimal] = defaultdict(Decimal)

    task_query = (
        select(
            ProductionMaterialUsage.material_id,
            ProductionMaterialUsage.requested_quantity,
            ProductionMaterialUsage.actual_quantity,
        )
        .join(ProductionTask, ProductionTask.id == ProductionMaterialUsage.production_task_id)
        .where(
            ProductionTask.status.not_in(INACTIVE_TASK_STATUSES),
        )
    )
    if ids:
        task_query = task_query.where(ProductionMaterialUsage.material_id.in_(ids))
    for material_id, requested, actual in db.execute(task_query):
        result[material_id] += max(Decimal(requested or 0) - Decimal(actual or 0), Decimal("0"))

    demand_query = select(FulfillmentDemand).where(
        FulfillmentDemand.fulfillment_mode == "production",
        FulfillmentDemand.status.in_(("pending", "pending_material")),
        FulfillmentDemand.shortage_quantity > 0,
    )
    if exclude_fulfillment_demand_id is not None:
        demand_query = demand_query.where(FulfillmentDemand.id != exclude_fulfillment_demand_id)
    demands = db.scalars(demand_query).all()
    if not demands:
        return dict(result)

    demand_ids = [row.id for row in demands]
    product_shortages = {row.product_id: [] for row in demands}
    for row in demands:
        product_shortages[row.product_id].append((row.id, Decimal(row.shortage_quantity or 0)))

    system_required: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    bom_query = (
        select(BOM.product_id, BOMItem.material_id, BOMItem.standard_quantity)
        .join(BOMItem, BOMItem.bom_id == BOM.id)
        .where(BOM.product_id.in_(list(product_shortages) or [-1]))
    )
    if ids:
        bom_query = bom_query.where(BOMItem.material_id.in_(ids))
    for product_id, material_id, standard_quantity in db.execute(bom_query):
        for demand_id, shortage_quantity in product_shortages.get(product_id, []):
            system_required[demand_id, material_id] += shortage_quantity * Decimal(standard_quantity or 0)

    manually_submitted: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    explicitly_required: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    for fulfillment_demand_id, items_json in db.execute(
        select(MaterialReplenishmentDemand.fulfillment_demand_id, MaterialReplenishmentDemand.items_json)
        .where(MaterialReplenishmentDemand.fulfillment_demand_id.in_(demand_ids))
    ):
        for item in items_json or []:
            material_id = int(item.get("material_id") or 0)
            if not material_id or (ids and material_id not in ids):
                continue
            manually_submitted[fulfillment_demand_id, material_id] += Decimal(str(item.get("shortage_quantity") or 0))
            explicitly_required[fulfillment_demand_id, material_id] = max(
                explicitly_required[fulfillment_demand_id, material_id],
                Decimal(str(item.get("actual_required_quantity") or 0)),
            )

    for key in set(system_required) | set(manually_submitted) | set(explicitly_required):
        _demand_id, material_id = key
        result[material_id] += max(system_required[key], manually_submitted[key], explicitly_required[key])
    return dict(result)


def material_production_commitment(
    db: Session,
    *,
    material_id: int,
    exclude_fulfillment_demand_id: int | None = None,
) -> Decimal:
    """返回单种原料的生产占用；保留单项接口供既有调用方使用。"""
    return material_production_commitments(
        db,
        material_ids=[material_id],
        exclude_fulfillment_demand_id=exclude_fulfillment_demand_id,
    ).get(material_id, Decimal("0"))


def purchase_in_transit_map(db: Session) -> dict[StockKey, Decimal]:
    """采购在途 = 活跃采购单的 已下单量 - 已收货量,按产品/原料聚合。"""
    result: dict[StockKey, Decimal] = {}
    for product_id, material_id, packaging_config_id, ordered_quantity, received_quantity in db.execute(
        select(
            PurchaseOrderItem.product_id,
            PurchaseOrderItem.material_id,
            PurchaseOrderItem.packaging_config_id,
            func.coalesce(func.sum(PurchaseOrderItem.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderItem.received_quantity), 0),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.status.in_(ACTIVE_PURCHASE_STATUSES))
        .group_by(PurchaseOrderItem.product_id, PurchaseOrderItem.material_id, PurchaseOrderItem.packaging_config_id)
    ).all():
        result[stock_key(product_id=product_id, material_id=material_id, packaging_config_id=packaging_config_id)] = max(
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
