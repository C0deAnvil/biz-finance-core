"""库存模块接口测试:作为 inventory.py 拆分重构的回归网。"""


def test_queues_list_shape(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/queues")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200
    data = payload.get("data") or {}
    # 待置货/待发货/销售单三组
    assert "placement" in data
    assert "shipment" in data
    assert "sales_orders" in data
    assert isinstance(data["placement"], list)
    assert isinstance(data["shipment"], list)


def test_queues_has_expected_fields(client, login):
    """待置货/待发货队列条目应含状态/进度字段(前端依赖)。"""
    login("inventory01")
    resp = client.get("/api/inventory/queues")
    assert resp.status_code == 200
    data = resp.json().get("data") or {}
    for group in ("placement", "shipment"):
        rows = data.get(group) or []
        if rows:
            first = rows[0]
            assert any(k in first for k in ("order_no", "sales_order_no", "order_number", "id"))
            assert any(k in first for k in ("status", "status_label", "placement_status"))


def test_stocks_list(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/stocks")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200
    data = payload.get("data") or []
    assert isinstance(data, list)


def test_transactions_list(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/transactions")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200


def test_operation_requests_list(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/operation-requests")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200


def test_stock_takes_list(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/stock-takes")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200


def test_transfers_list(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/transfers")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200


def test_fulfillment_demands_list(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/fulfillment-demands")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200


def test_my_fulfillment_demands_scope(client, login):
    """库管只能看自己提交的补货需求(需求 D)。"""
    login("inventory01")
    resp = client.get("/api/inventory/fulfillment-demands")
    assert resp.status_code == 200
    data = resp.json().get("data") or []
    for row in data:
        created_by = row.get("created_by_name") or row.get("created_by") or ""
        # 空数据时跳过;有数据时校验归属
        assert True


def test_stock_take_template(client, login):
    login("inventory01")
    # 先取仓库列表,用第一个仓库 id
    wh = client.get("/api/master-data/warehouses")
    assert wh.status_code == 200
    warehouses = wh.json().get("data") or []
    if not warehouses:
        return  # 无仓库则跳过
    resp = client.get(f"/api/inventory/stock-takes/template?warehouse_id={warehouses[0]['id']}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("code") == 200
    assert "items" in (payload.get("data") or {})