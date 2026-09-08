"""安全库存告警与正式补货需求的去重回归用例。

改造前:同一个缺口会同时产生两份工作项——正式补货需求(FulfillmentDemand)
和安全库存告警待办(TodoItem),两者之间无任何关联或去重,采购/生产会收到
两份内容相同、来源不同的任务。

现在:正式单据优先。已有未完成补货需求时不再发安全库存告警,
并关闭已存在的重复告警;正式需求结束后告警会重新触发(自愈特性保留)。
"""

from helpers import create_trade_product, uniq, warehouse_map

LOW_STOCK_BUSINESS_TYPE = "low_stock_replenishment"


def _set_safety_stock(product_id: int, qty: float) -> None:
    from database import SessionLocal
    from models import Product
    from sqlalchemy import select

    db = SessionLocal()
    try:
        product = db.execute(select(Product).where(Product.id == product_id)).scalar_one()
        product.safety_stock_qty = qty
        db.commit()
    finally:
        db.close()


def _open_low_stock_todo_count(product_id: int) -> int:
    """该产品未完成的安全库存告警待办数。"""
    from database import SessionLocal
    from models import Inventory, TodoItem
    from sqlalchemy import select

    db = SessionLocal()
    try:
        inv_ids = [
            row for row in db.execute(
                select(Inventory.id).where(Inventory.product_id == product_id)
            ).scalars().all()
        ]
        if not inv_ids:
            return 0
        rows = db.execute(
            select(TodoItem.id).where(
                TodoItem.business_type == LOW_STOCK_BUSINESS_TYPE,
                TodoItem.business_id.in_(inv_ids),
                TodoItem.status != "completed",
            )
        ).scalars().all()
        return len(rows)
    finally:
        db.close()


def _manual_inbound(client, login, *, warehouse_id: int, product_id: int, quantity: float) -> None:
    login("inventory_super01")
    resp = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": warehouse_id,
            "operation_type": "inbound",
            "remark": "pytest 触发库存变动",
            "items": [{"product_id": product_id, "batch_no": uniq("LS"), "quantity": quantity, "unit_cost": 10}],
        },
    )
    assert resp.status_code == 200, resp.text


def test_low_stock_alert_suppressed_when_demand_exists(client, login):
    """已有正式补货需求时,不应再发安全库存告警(避免双份派工)。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _set_safety_stock(product_id, 100)

    # 入库 10(远低于安全库存 100)→ 应产生安全库存告警
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=10)
    assert _open_low_stock_todo_count(product_id) > 0, "库存低于安全库存时应产生告警待办"

    # 提交一张正式补货需求
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": 90, "remark": "pytest 正式补货"},
    )
    assert resp.status_code == 200, resp.text

    # 再次触发库存变动 → 正式单据已接管,告警应被关闭
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=1)
    assert _open_low_stock_todo_count(product_id) == 0, (
        "已有正式补货需求时,重复的安全库存告警应被关闭"
    )


def test_low_stock_alert_still_fires_without_demand(client, login):
    """没有正式补货需求时,安全库存告警仍应正常工作(不能因去重把功能关掉)。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _set_safety_stock(product_id, 50)

    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=5)
    assert _open_low_stock_todo_count(product_id) > 0, "无正式需求时告警应照常触发"


def test_low_stock_alert_clears_when_stock_recovers(client, login):
    """库存回补到安全线以上时告警自动消除(自愈特性,不应被本次改动破坏)。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _set_safety_stock(product_id, 30)

    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=5)
    assert _open_low_stock_todo_count(product_id) > 0, "低于安全库存应告警"

    # 补到 60 > 安全库存 30
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=55)
    assert _open_low_stock_todo_count(product_id) == 0, "库存恢复后告警应自动消除"


def _open_material_alert_count(product_id: int) -> int:
    """该产品派生的"原料补货"安全库存告警未完成数。"""
    from database import SessionLocal
    from models import Inventory, TodoItem
    from sqlalchemy import select

    db = SessionLocal()
    try:
        inv_ids = db.execute(select(Inventory.id).where(Inventory.product_id == product_id)).scalars().all()
        if not inv_ids:
            return 0
        rows = db.execute(
            select(TodoItem.id).where(
                TodoItem.business_type == "low_stock_product_material_replenishment",
                TodoItem.business_id.in_(inv_ids),
                TodoItem.status != "completed",
            )
        ).scalars().all()
        return len(rows)
    finally:
        db.close()


def test_material_alerts_also_cleared_when_demand_exists(client, login):
    """生产型产品:正式补货需求接管后,派生的"原料补货"告警也必须一并关闭。

    否则主告警关了、原料告警却一直挂着——库存回升分支才有的清理逻辑
    在正式需求未结束前走不到,该告警会永久残留。
    """
    from helpers import create_bom, create_material, create_production_product

    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    material_id = create_material(client, login)
    product_id = create_production_product(client, login)
    create_bom(client, login, product_id=product_id, material_id=material_id, standard_quantity=2)
    _set_safety_stock(product_id, 100)

    # 低于安全库存 → 主告警 + 原料告警
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=10)
    assert _open_low_stock_todo_count(product_id) > 0, "应产生主告警"

    # 提正式补货需求 → 接管该缺口
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": 90, "remark": "pytest 正式补货"},
    )
    assert resp.status_code == 200, resp.text

    # 再触发库存变动 → 主告警与原料告警都应被关闭
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=1)
    assert _open_low_stock_todo_count(product_id) == 0, "主告警应被关闭"
    assert _open_material_alert_count(product_id) == 0, "派生的原料补货告警也应被关闭"


def _force_demand_converted(product_id: int) -> None:
    """把该产品的补货需求置为 converted(模拟已转采购单/生产任务)。"""
    from database import SessionLocal
    from models import FulfillmentDemand
    from sqlalchemy import select

    db = SessionLocal()
    try:
        rows = db.execute(
            select(FulfillmentDemand).where(FulfillmentDemand.product_id == product_id)
        ).scalars().all()
        for row in rows:
            row.status = "converted"
        db.commit()
    finally:
        db.close()


def test_converted_demand_does_not_permanently_suppress_alert(client, login):
    """补货需求转单(converted)后,安全库存告警必须能重新触发。

    converted 在本系统里是终态(没有代码把它推进到 completed)。
    若把它算作"未完成需求"用于去重,该产品只要历史上转过一次补货需求,
    告警就被永久压制——库存跌到零也不会再报警。
    """
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _set_safety_stock(product_id, 100)

    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=10)
    assert _open_low_stock_todo_count(product_id) > 0, "应先产生告警"

    # 提正式需求 → 压制告警
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": 90, "remark": "pytest"},
    )
    assert resp.status_code == 200, resp.text
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=1)
    assert _open_low_stock_todo_count(product_id) == 0, "有待处理需求时应压制告警"

    # 需求转单(终态)→ 库存仍远低于安全库存,告警应重新触发
    _force_demand_converted(product_id)
    _manual_inbound(client, login, warehouse_id=wh, product_id=product_id, quantity=1)
    assert _open_low_stock_todo_count(product_id) > 0, (
        "需求已转单(converted 为终态),库存仍不足时告警必须能重新触发"
    )
