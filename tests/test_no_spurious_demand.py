"""启动修复不得代替库管为客户订单提交补货需求。"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from helpers import create_trade_product, uniq, warehouse_map


def test_sales_actor_cannot_create_order_shortage_demand_directly():
    from services.replenishment_service import create_sales_shortage_demand
    from services.sales_service import _create_fulfillment_demands

    sales = SimpleNamespace(role=SimpleNamespace(code="sales"))
    inventory = SimpleNamespace(role=SimpleNamespace(code="inventory"))
    with pytest.raises(HTTPException) as error:
        _create_fulfillment_demands(None, order=None, current_user=sales, shortage_override={1: Decimal("1")})
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        _create_fulfillment_demands(None, order=None, current_user=inventory)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        create_sales_shortage_demand(
            None, order=None, order_item=None, product=None,
            shortage_quantity=Decimal("1"), current_user=sales,
        )
    assert error.value.status_code == 403


def _customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("假需求客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _stock(client, login, *, wh: int, pid: int, qty: float) -> None:
    login("inventory_super01")
    resp = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": wh,
            "operation_type": "inbound",
            "remark": "pytest 铺库存",
            "items": [{"product_id": pid, "batch_no": uniq("SP"), "quantity": qty, "unit_cost": 10}],
        },
    )
    assert resp.status_code == 200, resp.text


def test_no_demand_when_stock_fully_covers_order(client, login):
    """库存 100 / 订单 100 全额锁定 → 不应生成任何补货需求。"""
    wh = warehouse_map(client, login)["finished"]
    pid = create_trade_product(client, login)
    _stock(client, login, wh=wh, pid=pid, qty=100)

    cid = _customer(client, login)
    login("sales01")
    resp = client.post(
        "/api/sales/orders",
        json={
            "customer_id": cid,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "地址",
            "payment_type": "货到付款",
            "order_type": "normal",
            "operator_user_id": None,
            "items": [{"product_id": pid, "quantity": 100, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    login("finance01")
    assert client.post(
        f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest"}
    ).status_code == 200

    # 启动修复只补齐置货队列/待办，不能生成 BH。
    from database import SessionLocal
    from models import FulfillmentDemand
    from services.workflow_repair import repair_sales_dispatch_workflows
    from sqlalchemy import select

    db = SessionLocal()
    try:
        repair_sales_dispatch_workflows(db)
        db.commit()
        demands = db.execute(
            select(FulfillmentDemand).where(FulfillmentDemand.sales_order_id == order_id)
        ).scalars().all()
        assert not demands, f"库存充足却生成了 {len(demands)} 条假补货需求"
    finally:
        db.close()


def test_shortage_demand_requires_inventory_submission(client, login):
    pid = create_trade_product(client, login)
    cid = _customer(client, login)
    login("sales01")
    resp = client.post(
        "/api/sales/orders",
        json={
            "customer_id": cid,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "地址",
            "payment_type": "货到付款",
            "order_type": "normal",
            "items": [{"product_id": pid, "quantity": 10, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    login("finance01")
    assert client.post(
        f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest"}
    ).status_code == 200

    from database import SessionLocal
    from models import FulfillmentDemand, InventoryDispatchQueue, User
    from services.workflow_repair import repair_sales_dispatch_workflows
    from sqlalchemy import select

    with SessionLocal() as db:
        repair_sales_dispatch_workflows(db)
        db.commit()
        assert db.scalar(select(FulfillmentDemand.id).where(FulfillmentDemand.sales_order_id == order_id)) is None
        queue_id = db.scalar(select(InventoryDispatchQueue.id).where(InventoryDispatchQueue.sales_order_id == order_id))
        inventory_id = db.scalar(select(User.id).where(User.username == "inventory01"))

    login("inventory01")
    resp = client.post(
        f"/api/inventory/queues/{queue_id}/replenishment-demands",
        json={"items": [{"product_id": pid, "shortage_quantity": 10}]},
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        demands = db.scalars(select(FulfillmentDemand).where(FulfillmentDemand.sales_order_id == order_id)).all()
        assert len(demands) == 1
        assert demands[0].created_by_id == inventory_id
