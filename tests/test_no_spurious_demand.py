"""库存充足时不得生成"假补货需求"的回归用例。

`_create_fulfillment_demands` 算可用量时若只看"实有 - 总占用",就会把
审批时已为本单锁定的货算成别人的,凭空造出等量缺口。

该函数会在每次应用启动的 repair_workflows 里被调用,一旦算错会持续生成假需求,
而假需求又会压制安全库存告警,连带把置货缺口提示也一并静音。
"""

from helpers import create_trade_product, uniq, warehouse_map


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

    # 直接调用启动修复所走的同一入口
    from database import SessionLocal
    from models import FulfillmentDemand, SalesOrder, User
    from services.sales_service import ensure_sales_order_fulfillment_demands
    from sqlalchemy import select

    db = SessionLocal()
    try:
        order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one()
        user = db.execute(select(User).where(User.username == "sales01")).scalar_one()
        ensure_sales_order_fulfillment_demands(db, order=order, current_user=user)
        db.commit()
        demands = db.execute(
            select(FulfillmentDemand).where(FulfillmentDemand.sales_order_id == order_id)
        ).scalars().all()
        assert not demands, f"库存充足却生成了 {len(demands)} 条假补货需求"
    finally:
        db.close()
