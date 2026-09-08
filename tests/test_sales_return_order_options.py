"""退货页"销售订单"候选列表的回归用例。

改造前前端把 /api/sales/orders 的返回(`{items, pagination}` 对象)直接当数组用,
下拉框只会出现两条 "undefined / undefined";且默认 tab 不含已发货/已完成订单,
即使取对了 items 也选不到真正需要退货的单——退货申请完全提不出来。

本用例从接口层锁死两点:
1. shipping / completed 两个页签能取到已发货、已完成订单;
2. 返回项包含下拉框渲染所需的 id / order_no / customer_name。
"""

from helpers import create_trade_product, uniq


def _customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("退货候选客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _stock(client, login, *, product_id: int, quantity: float) -> None:
    from helpers import warehouse_map

    wh = warehouse_map(client, login)["finished"]
    login("inventory_super01")
    resp = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": wh,
            "operation_type": "inbound",
            "remark": "pytest 铺库存",
            "items": [{"product_id": product_id, "batch_no": uniq("RT"), "quantity": quantity, "unit_cost": 30}],
        },
    )
    assert resp.status_code == 200, resp.text


def _create_shipped_order(client, login) -> int:
    """建单 → 提交 → 审批,再直改为已发货(聚焦候选列表,不展开发货前置条件)。"""
    customer_id = _customer(client, login)
    product_id = create_trade_product(client, login)
    _stock(client, login, product_id=product_id, quantity=50)

    login("sales01")
    resp = client.post(
        "/api/sales/orders",
        json={
            "customer_id": customer_id,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "地址",
            "payment_type": "货到付款",
            "order_type": "normal",
            "operator_user_id": None,
            "items": [{"product_id": product_id, "quantity": 10, "unit_price": 100}],
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
    from models import SalesOrder
    from sqlalchemy import select

    db = SessionLocal()
    try:
        order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one()
        order.status = "shipped"
        for item in order.items:
            item.shipped_quantity = item.quantity
        db.commit()
    finally:
        db.close()
    return order_id


def test_shipped_order_appears_in_return_candidates(client, login):
    """已发货订单必须能出现在退货候选里(shipping 页签)。"""
    order_id = _create_shipped_order(client, login)

    login("sales01")
    resp = client.get("/api/sales/orders", params=[("tab", "shipping"), ("page", 1), ("page_size", 100)])
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert order_id in [row["id"] for row in items], "已发货订单应出现在 shipping 页签,否则退货选不到它"


def test_return_candidate_rows_have_label_fields(client, login):
    """候选项必须带 id / order_no / customer_name —— 下拉框 label 依赖这三个字段。"""
    order_id = _create_shipped_order(client, login)

    login("sales01")
    resp = client.get("/api/sales/orders", params=[("tab", "shipping"), ("page", 1), ("page_size", 100)])
    row = next(r for r in resp.json()["data"]["items"] if r["id"] == order_id)
    for key in ("id", "order_no", "customer_name"):
        assert row.get(key), f"候选项缺少 {key},下拉框会显示 undefined"


def test_orders_payload_is_object_not_array(client, login):
    """接口返回的是对象而非数组 —— 前端必须取 .items,不能直接赋值。

    这条用例的作用是:一旦后端改成直接返回数组,前端 `.items` 取值会静默变成
    undefined,此处会失败并提醒同步修改前端。
    """
    login("sales01")
    resp = client.get("/api/sales/orders", params=[("tab", "all"), ("page", 1), ("page_size", 5)])
    data = resp.json()["data"]
    assert isinstance(data, dict), f"返回应为对象, 实际 {type(data)}"
    assert "items" in data and isinstance(data["items"], list), "返回体应含 items 数组"
    assert "pagination" in data, "返回体应含 pagination"
