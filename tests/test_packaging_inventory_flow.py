"""包装库存随采购、置货和发货同步变化。"""

from helpers import create_global_packaging, create_supplier, create_trade_product, uniq, warehouse_map


def _manual_stock(client, login, *, warehouse_id: int, item: dict, quantity: float, unit_cost: float) -> None:
    login("inventory_super01")
    response = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": warehouse_id,
            "operation_type": "inbound",
            "remark": "pytest 包装库存",
            "items": [{**item, "batch_no": uniq("PK"), "quantity": quantity, "unit_cost": unit_cost}],
        },
    )
    assert response.status_code == 200, response.text


def _stock_row(client, login, *, warehouse_id: int, item_type: str, item_id: int) -> dict:
    login("inventory_super01")
    response = client.get(
        "/api/inventory/stocks",
        params={"warehouse_id": warehouse_id, "item_type": item_type, "page": 1, "page_size": 100},
    )
    assert response.status_code == 200, response.text
    rows = (response.json().get("data") or {}).get("items") or []
    return next(row for row in rows if row.get("item_type") == item_type and row.get("item_id") == item_id)


def _customer(client, login) -> int:
    login("sales01")
    response = client.post(
        "/api/master-data/customers",
        json={"name": uniq("包装客户"), "contact_name": "张三", "phone": "13900000000", "address": "测试地址"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _queue(client, login, order_id: int, group: str) -> dict:
    login("inventory_super01")
    response = client.get("/api/inventory/queues")
    assert response.status_code == 200, response.text
    return next(row for row in response.json()["data"][group] if row["order_id"] == order_id)


def test_sales_placement_and_shipment_reserve_and_consume_packaging(client, login):
    finished_warehouse_id = warehouse_map(client, login)["finished"]
    product_id = create_trade_product(client, login)
    packaging_id = create_global_packaging(client, login)
    _manual_stock(client, login, warehouse_id=finished_warehouse_id, item={"product_id": product_id}, quantity=100, unit_cost=20)
    _manual_stock(client, login, warehouse_id=finished_warehouse_id, item={"packaging_config_id": packaging_id}, quantity=10, unit_cost=5)

    customer_id = _customer(client, login)
    login("sales01")
    response = client.post(
        "/api/sales/orders",
        json={
            "customer_id": customer_id,
            "consignee": "张三",
            "consignee_phone": "13900000000",
            "delivery_address": "测试地址",
            "payment_type": "货到付款",
            "order_type": "normal",
            "items": [{"product_id": product_id, "packaging_config_id": packaging_id, "quantity": 100, "unit_price": 100}],
        },
    )
    assert response.status_code == 200, response.text
    order_id = response.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    login("finance01")
    assert client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest"}).status_code == 200

    placement = _queue(client, login, order_id, "placement")
    item_id = placement["items"][0]["sales_order_item_id"]
    login("inventory_super01")
    response = client.post(
        f"/api/inventory/queues/{placement['queue_id']}/placement",
        json={"action": "placed", "items": [{"sales_order_item_id": item_id, "placed_quantity": 100}]},
    )
    assert response.json().get("code") in (0, 200), response.text
    packaging_stock = _stock_row(client, login, warehouse_id=finished_warehouse_id, item_type="packaging", item_id=packaging_id)
    assert float(packaging_stock["quantity"]) == 10
    assert float(packaging_stock["reserved_quantity"]) == 4

    shipment = _queue(client, login, order_id, "shipment")
    login("inventory_super01")
    response = client.post(
        f"/api/inventory/queues/{shipment['queue_id']}/shipment",
        json={"force": True, "items": [{"sales_order_item_id": item_id, "shipped_quantity": 40}]},
    )
    assert response.json().get("code") in (0, 200), response.text
    packaging_stock = _stock_row(client, login, warehouse_id=finished_warehouse_id, item_type="packaging", item_id=packaging_id)
    assert float(packaging_stock["quantity"]) == 8
    assert float(packaging_stock["reserved_quantity"]) == 2

    shipment = _queue(client, login, order_id, "shipment")
    login("inventory_super01")
    response = client.post(
        f"/api/inventory/queues/{shipment['queue_id']}/shipment",
        json={"force": True, "items": [{"sales_order_item_id": item_id, "shipped_quantity": 60}]},
    )
    assert response.json().get("code") in (0, 200), response.text
    packaging_stock = _stock_row(client, login, warehouse_id=finished_warehouse_id, item_type="packaging", item_id=packaging_id)
    assert float(packaging_stock["quantity"]) == 6
    assert float(packaging_stock["reserved_quantity"]) == 0


def test_packaging_purchase_receipt_creates_packaging_inbound_request(client, login):
    raw_warehouse_id = warehouse_map(client, login)["raw"]
    supplier_id = create_supplier(client, login)
    packaging_id = create_global_packaging(client, login)

    login("purchase01")
    response = client.post(
        "/api/purchases/orders",
        json={
            "supplier_id": supplier_id,
            "payment_type": "先到货后付款",
            "items": [{"packaging_config_id": packaging_id, "quantity": 20, "unit_price": 5}],
        },
    )
    assert response.status_code == 200, response.text
    order_id = response.json()["data"]["id"]
    order = client.get(f"/api/purchases/orders/{order_id}").json()["data"]
    order_item_id = order["items"][0]["id"]
    assert order["items"][0]["packaging_config_id"] == packaging_id

    response = client.post(
        "/api/purchases/receipts",
        json={
            "purchase_order_id": order_id,
            "warehouse_id": raw_warehouse_id,
            "requires_quality": False,
            "items": [{"purchase_order_item_id": order_item_id, "received_quantity": 20, "batch_no": uniq("PK"), "unit_cost": 5}],
        },
    )
    assert response.status_code == 200, response.text

    login("inventory_super01")
    requests = client.get("/api/inventory/operation-requests").json()["data"]
    request = next(
        row
        for row in requests
        if row["request_category"] == "purchase_receipt_inbound"
        and any(item.get("packaging_config_id") == packaging_id for item in row["original_items"])
    )
    response = client.post(
        f"/api/inventory/operation-requests/{request['id']}/complete",
        json={"items": request["original_items"], "handled_remark": "包装入库"},
    )
    assert response.status_code == 200, response.text
    packaging_stock = _stock_row(client, login, warehouse_id=raw_warehouse_id, item_type="packaging", item_id=packaging_id)
    assert float(packaging_stock["quantity"]) == 20
