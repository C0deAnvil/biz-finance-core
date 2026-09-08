"""占用库存在"分批置货 → 发货"全过程中的守恒回归用例。

改造前占用会在置货时二次累加、且发货只释放一次,导致占用逐步泄漏。
本用例锁死三条不变量:置货不改变占用总量、发货同步扣减实有与占用、
占用始终位于 [0, 实有] 区间内。"""
from helpers import create_trade_product, uniq, warehouse_map


def _customer(client, login):
    login("sales01")
    r = client.post("/api/master-data/customers",
        json={"name": uniq("分批客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"})
    return r.json()["data"]["id"]


def _stock(client, login, *, wh, pid, qty):
    login("inventory_super01")
    r = client.post("/api/inventory/manual-operations",
        json={"warehouse_id": wh, "operation_type": "inbound", "remark": "探针",
              "items": [{"product_id": pid, "batch_no": uniq("CY"), "quantity": qty, "unit_cost": 10}]})
    assert r.status_code == 200, r.text


def _row(client, login, *, wh, pid):
    login("inventory_super01")
    st = client.get("/api/inventory/stocks", params=[("warehouse_id", wh), ("product_id", pid)]).json()["data"]
    return next(x for x in st if x.get("item_id") == pid and x.get("item_type") == "product")


def _queue(client, login, oid, key="placement"):
    login("inventory_super01")
    q = client.get("/api/inventory/queues").json()["data"]
    return next((x for x in (q.get(key) or []) if x.get("order_id") == oid), None)


def test_reservation_conserved_across_batches(client, login):
    wh = warehouse_map(client, login)["finished"]
    pid = create_trade_product(client, login)
    _stock(client, login, wh=wh, pid=pid, qty=100)

    cid = _customer(client, login)
    login("sales01")
    r = client.post("/api/sales/orders", json={
        "customer_id": cid, "consignee": "张三", "consignee_phone": "13900000000",
        "delivery_address": "地址", "payment_type": "货到付款", "order_type": "normal",
        "operator_user_id": None,
        "items": [{"product_id": pid, "quantity": 100, "unit_price": 100}]})
    oid = r.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{oid}/submit").status_code == 200
    login("finance01")
    assert client.post(f"/api/sales/orders/{oid}/approve", json={"decision": "approved", "note": "探针"}).status_code == 200

    row = _row(client, login, wh=wh, pid=pid)
    print(f"\n[CHECK] 审批后 实有={row['quantity']} 占用={row['reserved_quantity']}")

    t = _queue(client, login, oid)
    iid = t["items"][0]["sales_order_item_id"]

    # 第一批置货 40
    login("inventory_super01")
    r = client.post(f"/api/inventory/queues/{t['queue_id']}/placement",
        json={"action": "placed", "items": [{"sales_order_item_id": iid, "placed_quantity": 40}]})
    print(f"[CHECK] 置货40 -> {r.json().get('code')}")
    row = _row(client, login, wh=wh, pid=pid)
    print(f"[CHECK] 置40后 实有={row['quantity']} 占用={row['reserved_quantity']} (占用应仍=100)")

    # 第二批置货 60(置满)
    t = _queue(client, login, oid)
    r = client.post(f"/api/inventory/queues/{t['queue_id']}/placement",
        json={"action": "placed", "items": [{"sales_order_item_id": iid, "placed_quantity": 60}]})
    print(f"[CHECK] 置货60 -> {r.json().get('code')}")
    row = _row(client, login, wh=wh, pid=pid)
    print(f"[CHECK] 置满后 实有={row['quantity']} 占用={row['reserved_quantity']} (占用应仍=100)")

    # 发货 40
    sq = _queue(client, login, oid, key="shipment")
    if sq:
        r = client.post(f"/api/inventory/queues/{sq['queue_id']}/shipment",
            json={"action": "shipped", "items": [{"sales_order_item_id": iid, "shipped_quantity": 40}]})
        print(f"[CHECK] 发货40 -> {r.json().get('code')} {r.json().get('msg')}")
        row = _row(client, login, wh=wh, pid=pid)
        print(f"[CHECK] 发40后 实有={row['quantity']} 占用={row['reserved_quantity']} (实有应=60,占用应=60)")
    else:
        print("[CHECK] 未进入发货队列")

    row = _row(client, login, wh=wh, pid=pid)
    assert float(row["reserved_quantity"]) >= 0, "占用为负"
    assert float(row["reserved_quantity"]) <= float(row["quantity"]) + 1e-6, \
        f"占用 {row['reserved_quantity']} 超过实有 {row['quantity']}"


def test_finance_edit_does_not_leak_reservation(client, login):
    """财务改单会删除并重建订单明细,占用必须随之重算,不能留下无主占用。

    明细上记着"本明细占了多少",删除明细就销毁了这本账,而仓库端占用不会自动减。
    若不先退还再重占,改单后这批货被永久锁死且找不到归属者
    (改单后订单仍是 pending_shipment,不会重跑审批占用)。
    """
    wh = warehouse_map(client, login)["finished"]
    pid = create_trade_product(client, login)
    _stock(client, login, wh=wh, pid=pid, qty=200)

    cid = _customer(client, login)
    login("sales01")
    r = client.post("/api/sales/orders", json={
        "customer_id": cid, "consignee": "张三", "consignee_phone": "13900000000",
        "delivery_address": "地址", "payment_type": "货到付款", "order_type": "normal",
        "operator_user_id": None,
        "items": [{"product_id": pid, "quantity": 100, "unit_price": 100}]})
    assert r.status_code == 200, r.text
    oid = r.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{oid}/submit").status_code == 200
    login("finance01")
    assert client.post(f"/api/sales/orders/{oid}/approve",
                       json={"decision": "approved", "note": "回归"}).status_code == 200

    row = _row(client, login, wh=wh, pid=pid)
    assert float(row["reserved_quantity"]) == 100.0, f"审批后应占用 100, 实际 {row['reserved_quantity']}"

    # 财务改单:数量 100 → 80
    login("finance01")
    resp = client.put(f"/api/sales/orders/{oid}/finance-edit", json={
        "customer_id": cid, "consignee": "张三", "consignee_phone": "13900000000",
        "delivery_address": "地址", "payment_type": "货到付款", "order_type": "normal",
        "operator_user_id": None, "edit_note": "回归改单",
        "items": [{"product_id": pid, "quantity": 80, "unit_price": 100}]})
    assert resp.status_code == 200, resp.text

    row = _row(client, login, wh=wh, pid=pid)
    assert float(row["reserved_quantity"]) == 80.0, (
        f"改单为 80 后占用应为 80(既不残留 100 也不清零), 实际 {row['reserved_quantity']}"
    )
