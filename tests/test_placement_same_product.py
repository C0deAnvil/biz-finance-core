"""同一订单多条明细指向同一产品时的占用/可置量回归用例。

这是"本明细自己的占用额度计入可置量"这一算法最容易出错的场景:
若把仓库未占用散量重复加给每一条明细,两条明细的可置量之和会超过实有库存,
库管据此置货就会超卖。"""
from helpers import create_trade_product, uniq, warehouse_map


def _customer(client, login):
    login("sales01")
    r = client.post("/api/master-data/customers",
        json={"name": uniq("重复明细客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _stock(client, login, *, wh, pid, qty):
    login("inventory_super01")
    r = client.post("/api/inventory/manual-operations",
        json={"warehouse_id": wh, "operation_type": "inbound", "remark": "探针",
              "items": [{"product_id": pid, "batch_no": uniq("DP"), "quantity": qty, "unit_cost": 10}]})
    assert r.status_code == 200, r.text


def test_same_product_two_lines_no_double_count(client, login):
    wh = warehouse_map(client, login)["finished"]
    pid = create_trade_product(client, login)
    _stock(client, login, wh=wh, pid=pid, qty=100)   # 只有 100

    cid = _customer(client, login)
    login("sales01")
    # 两条明细,同一产品,各 60 -> 合计 120 > 库存 100
    r = client.post("/api/sales/orders", json={
        "customer_id": cid, "consignee": "张三", "consignee_phone": "13900000000",
        "delivery_address": "地址", "payment_type": "货到付款", "order_type": "normal",
        "operator_user_id": None,
        "items": [
            {"product_id": pid, "quantity": 60, "unit_price": 100},
            {"product_id": pid, "quantity": 60, "unit_price": 100},
        ]})
    assert r.status_code == 200, r.text
    oid = r.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{oid}/submit").status_code == 200
    login("finance01")
    assert client.post(f"/api/sales/orders/{oid}/approve", json={"decision": "approved", "note": "探针"}).status_code == 200

    login("inventory_super01")
    st = client.get("/api/inventory/stocks", params=[("warehouse_id", wh), ("product_id", pid)]).json()["data"]
    row = next(x for x in st if x.get("item_id") == pid and x.get("item_type") == "product")
    print(f"\n[CHECK] 审批后 实有={row['quantity']} 占用={row['reserved_quantity']} (占用不应超过实有100)")

    q = client.get("/api/inventory/queues").json()["data"]
    t = next(x for x in (q.get("placement") or []) if x.get("order_id") == oid)
    print(f"[CHECK] 颜色={t['stock_status']}")
    tot = 0
    for it in t["items"]:
        print(f"[CHECK]   明细 待置={it['remaining_to_place']} 已占={it['reserved_quantity']} 可置={it['available_quantity']}")
        tot += it["available_quantity"]
    print(f"[CHECK] 两条明细可置量合计={tot} (若 >100 则为重复计算)")

    assert float(row["reserved_quantity"]) <= 100.0, f"占用超过实有: {row['reserved_quantity']}"
    assert tot <= 100.0, f"可置量合计 {tot} 超过实有 100 -> 重复计算"


def test_same_product_two_lines_with_free_pool(client, login):
    """库存充裕(仓库仍有未占用散量)时,同产品两条明细也不得重复计算散量。

    散量是全仓共享的:若把整个散量重复加给每条明细,
    "可置合计"会超过实有库存(实有200、两条各报可置140、合计280),
    库管照此置货就会超卖。
    """
    wh = warehouse_map(client, login)["finished"]
    pid = create_trade_product(client, login)
    _stock(client, login, wh=wh, pid=pid, qty=200)   # 充裕:200 > 需求 120

    cid = _customer(client, login)
    login("sales01")
    r = client.post("/api/sales/orders", json={
        "customer_id": cid, "consignee": "张三", "consignee_phone": "13900000000",
        "delivery_address": "地址", "payment_type": "货到付款", "order_type": "normal",
        "operator_user_id": None,
        "items": [
            {"product_id": pid, "quantity": 60, "unit_price": 100},
            {"product_id": pid, "quantity": 60, "unit_price": 100},
        ]})
    assert r.status_code == 200, r.text
    oid = r.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{oid}/submit").status_code == 200
    login("finance01")
    assert client.post(f"/api/sales/orders/{oid}/approve",
                       json={"decision": "approved", "note": "回归"}).status_code == 200

    login("inventory_super01")
    st = client.get("/api/inventory/stocks", params=[("warehouse_id", wh), ("product_id", pid)]).json()["data"]
    row = next(x for x in st if x.get("item_id") == pid and x.get("item_type") == "product")
    q = client.get("/api/inventory/queues").json()["data"]
    t = next(x for x in (q.get("placement") or []) if x.get("order_id") == oid)

    total_available = sum(it["available_quantity"] for it in t["items"])
    assert total_available <= float(row["quantity"]) + 1e-6, (
        f"可置合计 {total_available} 超过实有 {row['quantity']} -> 散量被重复计算"
    )
    # 两条明细各需 60,库存充裕,应各自恰好可置 60(而非各报 140)
    for it in t["items"]:
        assert float(it["available_quantity"]) == 60.0, (
            f"每条明细可置量应为 60, 实际 {it['available_quantity']}"
        )
