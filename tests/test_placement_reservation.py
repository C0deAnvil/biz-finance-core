"""置货与占用库存的回归用例(锁死本次修复的核心缺陷)。

改造前的实测缺陷:
1. 库存 100 / 订单 100 → 财务审批后 预计可用 = -100、置货状态 = red,
   充足库存的订单反而被判完全缺货、无法置货(pending_sales 与 reserved 重复扣减)。
2. 库存 200 / 订单 100 → 审批占用 100,置货又占用 100,同一订单占用两次。
3. 驳回后占用不释放,库存被未生效订单永久锁死。
"""

from helpers import create_trade_product, uniq, warehouse_map


def _create_customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("回归客户"), "contact_name": "张三", "phone": "13900000000", "address": "测试地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _stock_product(client, login, *, warehouse_id: int, product_id: int, quantity: float) -> None:
    """用手工入库铺实有库存，并补足默认包装，避免包装缺口干扰成品占用断言。"""
    login("inventory_super01")
    packaging_response = client.get("/api/master-data/packaging-configs?entity_type=global&active_only=true")
    assert packaging_response.status_code == 200, packaging_response.text
    packaging_items = [
        {
            "packaging_config_id": row["id"],
            "batch_no": uniq("BZ"),
            "quantity": max(quantity, 1),
            "unit_cost": 1,
        }
        for row in packaging_response.json().get("data") or []
    ]
    resp = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": warehouse_id,
            "operation_type": "inbound",
            "remark": "pytest 铺库存",
            "items": [
                {"product_id": product_id, "batch_no": uniq("PC"), "quantity": quantity, "unit_cost": 30},
                *packaging_items,
            ],
        },
    )
    assert resp.status_code == 200, resp.text


def _stock_row(client, login, *, warehouse_id: int, product_id: int) -> dict:
    login("inventory_super01")
    resp = client.get("/api/inventory/stocks", params=[("warehouse_id", warehouse_id), ("product_id", product_id)])
    assert resp.status_code == 200, resp.text
    for row in resp.json().get("data") or []:
        if row.get("item_type") == "product" and int(row.get("item_id") or 0) == product_id:
            return row
    raise AssertionError(f"未找到产品 #{product_id} 的库存行")


def _create_approved_order(client, login, *, product_id: int, quantity: float) -> int:
    """建单 → 提交 → 财务审批通过(货到付款,不受定金/收款约束)。"""
    customer_id = _create_customer(client, login)
    login("sales01")
    resp = client.post(
        "/api/sales/orders",
        json={
            "customer_id": customer_id,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "测试地址",
            "payment_type": "货到付款",
            "order_type": "normal",
            "operator_user_id": None,
            "items": [{"product_id": product_id, "quantity": quantity, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    login("finance01")
    resp = client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest"})
    assert resp.status_code == 200, resp.text
    return order_id


def _placement_queue(client, login, order_id: int) -> dict:
    login("inventory_super01")
    resp = client.get("/api/inventory/queues")
    assert resp.status_code == 200, resp.text
    placement = (resp.json().get("data") or {}).get("placement") or []
    target = next((q for q in placement if q.get("order_id") == order_id), None)
    assert target is not None, f"待置货列表未找到订单 {order_id}"
    return target


def test_sufficient_stock_order_is_green_and_placeable(client, login):
    """库存刚好等于订单量:应判绿色且可一次性置完(改造前为 red + 置货被拒)。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _stock_product(client, login, warehouse_id=wh, product_id=product_id, quantity=100)

    order_id = _create_approved_order(client, login, product_id=product_id, quantity=100)

    # 审批后:实有 100、占用 100、可置货 0(都被本单锁定)、补货可用 0(无需补货)
    row = _stock_row(client, login, warehouse_id=wh, product_id=product_id)
    assert float(row["quantity"]) == 100.0
    assert float(row["reserved_quantity"]) == 100.0, f"应恰好占用 100, 实际 {row['reserved_quantity']}"
    assert float(row["expected_available_quantity"]) == 0.0, (
        f"补货可用量应为 0(不应为负数): 实际 {row['expected_available_quantity']}"
    )

    queue = _placement_queue(client, login, order_id)
    assert queue["stock_status"] == "green", f"库存充足应为绿色, 实际 {queue['stock_status']}"
    assert not queue["blocked_reason"], f"不应被阻止: {queue['blocked_reason']}"

    item = queue["items"][0]
    assert float(item["available_quantity"]) == 100.0, "本单可置量应为 100(自己锁定的货)"
    assert float(item["shortage_quantity"]) == 0.0, "不应有缺口"

    # 置货 100 应成功
    resp = client.post(
        f"/api/inventory/queues/{queue['queue_id']}/placement",
        json={"action": "placed", "items": [{"sales_order_item_id": item["sales_order_item_id"], "placed_quantity": 100}]},
    )
    body = resp.json()
    assert body.get("code") in (0, 200), f"库存充足应可置货, 实际: {body}"


def test_placement_only_uses_the_dispatch_warehouse(client, login):
    """纸质作业单绑定成品仓，其他仓库库存不能跨仓直接置货。"""
    warehouses = warehouse_map(client, login)
    product_id = create_trade_product(client, login)
    _stock_product(client, login, warehouse_id=warehouses["raw"], product_id=product_id, quantity=1)

    order_id = _create_approved_order(client, login, product_id=product_id, quantity=0.2)
    queue = _placement_queue(client, login, order_id)

    assert queue["warehouse_id"] == warehouses["finished"]
    assert queue["stock_status"] == "red", queue
    item = queue["items"][0]
    assert float(item["available_quantity"]) == 0
    response = client.post(
        f"/api/inventory/queues/{queue['queue_id']}/placement",
        json={"action": "placed", "items": [{"sales_order_item_id": item["sales_order_item_id"], "placed_quantity": 0.2}]},
    )
    assert response.json().get("code") == 409, response.text


def test_reservation_not_doubled_by_placement(client, login):
    """同一订单不应被占用两次:审批占用后,置货只记数量、不再重复占用。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _stock_product(client, login, warehouse_id=wh, product_id=product_id, quantity=200)

    order_id = _create_approved_order(client, login, product_id=product_id, quantity=100)

    row = _stock_row(client, login, warehouse_id=wh, product_id=product_id)
    assert float(row["reserved_quantity"]) == 100.0, "审批后应占用 100"

    queue = _placement_queue(client, login, order_id)
    item = queue["items"][0]
    resp = client.post(
        f"/api/inventory/queues/{queue['queue_id']}/placement",
        json={"action": "placed", "items": [{"sales_order_item_id": item["sales_order_item_id"], "placed_quantity": 100}]},
    )
    assert resp.json().get("code") in (0, 200), resp.text

    row = _stock_row(client, login, warehouse_id=wh, product_id=product_id)
    assert float(row["reserved_quantity"]) == 100.0, (
        f"置货不应重复占用(订单量 100),实际占用 {row['reserved_quantity']}"
    )


def test_rejection_releases_reservation(client, login):
    """财务驳回后必须释放占用,否则库存被未生效订单永久锁死。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _stock_product(client, login, warehouse_id=wh, product_id=product_id, quantity=50)

    customer_id = _create_customer(client, login)
    login("sales01")
    resp = client.post(
        "/api/sales/orders",
        json={
            "customer_id": customer_id,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "测试地址",
            "payment_type": "货到付款",
            "order_type": "normal",
            "operator_user_id": None,
            "items": [{"product_id": product_id, "quantity": 30, "unit_price": 100}],
        },
    )
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200

    login("finance01")
    resp = client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "rejected", "note": "pytest 驳回"})
    assert resp.status_code == 200, resp.text

    row = _stock_row(client, login, warehouse_id=wh, product_id=product_id)
    assert float(row["reserved_quantity"]) == 0.0, (
        f"驳回后占用应释放为 0, 实际 {row['reserved_quantity']}"
    )
    assert float(row["available_quantity"]) == 50.0, "驳回后全部库存应重新可用"


def test_deposit_order_can_be_approved_before_payment(client, login):
    """财务审批与收款确认解耦:定金单未收款也应能通过审批。

    改造前审批要求定金先到账,形成"未收款不能审批、不审批不能置货"的死锁。
    收款仍是独立操作,故审批后置货环节应因"定金未付"被拦(该校验位置才正确)。
    """
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _stock_product(client, login, warehouse_id=wh, product_id=product_id, quantity=100)

    customer_id = _create_customer(client, login)
    login("sales01")
    resp = client.post(
        "/api/sales/orders",
        json={
            "customer_id": customer_id,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "测试地址",
            "payment_type": "先付定金后付尾款",
            "deposit_amount": 500,
            "order_type": "normal",
            "operator_user_id": None,
            "items": [{"product_id": product_id, "quantity": 50, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200

    # 不传任何到账金额 → 仍应审批通过(改造前会 409「定金到账金额不足」)
    login("finance01")
    resp = client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest 未收款审批"})
    assert resp.status_code == 200, f"未收定金也应允许审批, 实际: {resp.text}"

    # 审批通过 → 已占用库存并进入待置货
    row = _stock_row(client, login, warehouse_id=wh, product_id=product_id)
    assert float(row["reserved_quantity"]) == 50.0, "审批通过应占用 50"

    # 但置货应被定金未付拦住(校验保留在置货环节)
    queue = _placement_queue(client, login, order_id)
    assert queue["blocked_reason"], "定金未付应在置货环节被拦"
    assert "定金" in queue["blocked_reason"], f"阻止原因应指向定金, 实际: {queue['blocked_reason']}"


def test_partial_stock_order_is_yellow(client, login):
    """库存部分满足:应判黄色(可部分置货),并给出准确缺口。"""
    warehouses = warehouse_map(client, login)
    wh = warehouses["finished"]
    product_id = create_trade_product(client, login)
    _stock_product(client, login, warehouse_id=wh, product_id=product_id, quantity=40)

    order_id = _create_approved_order(client, login, product_id=product_id, quantity=100)

    queue = _placement_queue(client, login, order_id)
    assert queue["stock_status"] == "yellow", f"部分缺货应为黄色, 实际 {queue['stock_status']}"
    item = queue["items"][0]
    assert float(item["available_quantity"]) == 40.0, f"可置量应为 40, 实际 {item['available_quantity']}"
    assert float(item["shortage_quantity"]) == 60.0, f"缺口应为 60, 实际 {item['shortage_quantity']}"


def test_no_stock_order_is_red(client, login):
    """完全无库存:应判红色并阻止置货,提示先提补货需求。"""
    warehouses = warehouse_map(client, login)
    product_id = create_trade_product(client, login)
    order_id = _create_approved_order(client, login, product_id=product_id, quantity=25)

    queue = _placement_queue(client, login, order_id)
    assert queue["stock_status"] == "red", f"完全缺货应为红色, 实际 {queue['stock_status']}"
    assert queue["blocked_reason"], "完全缺货应给出阻止原因"
    item = queue["items"][0]
    assert float(item["shortage_quantity"]) == 25.0
