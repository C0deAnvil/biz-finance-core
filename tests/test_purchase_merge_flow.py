"""采购合并建单测试:多条采购补货需求(fulfillment_demand)可合并生成一个采购订单。

覆盖接口:
- POST /api/master-data/suppliers、/products(贸易型)、PUT /suppliers/{id}/catalog
- POST /api/inventory/fulfillment-demands(手动补货需求)
- POST /api/purchases/orders(携带 replenishment_sources 合并建单)
- GET /api/purchases/orders/{id}、GET /api/inventory/fulfillment-demands(校验需求已转)
"""

from helpers import (
    create_supplier,
    create_trade_product,
    set_supplier_catalog,
    uniq,
)


def _create_demand(client, login, product_id: int, shortage: float) -> int:
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": shortage, "remark": "pytest 合并建单需求"},
    )
    assert resp.status_code == 200, f"创建补货需求失败: {resp.text}"
    return resp.json()["data"]["id"]


def _demand_status(client, login, demand_id: int) -> str:
    login("inventory01")
    resp = client.get("/api/inventory/fulfillment-demands")
    assert resp.status_code == 200, f"补货需求列表失败: {resp.text}"
    for row in resp.json().get("data") or []:
        if int(row.get("id") or 0) == demand_id:
            return row.get("status") or ""
    raise AssertionError(f"未找到补货需求 #{demand_id}")


def test_merge_multiple_demands_into_one_purchase_order(client, login):
    supplier_id = create_supplier(client, login)
    p1 = create_trade_product(client, login)
    p2 = create_trade_product(client, login)
    # 同一供应商同时维护两个产品的供货名录
    login("purchase01")
    resp = client.put(f"/api/master-data/suppliers/{supplier_id}/catalog", json=[{"product_id": p1}, {"product_id": p2}])
    assert resp.status_code == 200, f"维护供应商名录失败: {resp.text}"

    d1 = _create_demand(client, login, p1, shortage=5)
    d2 = _create_demand(client, login, p2, shortage=7)
    assert _demand_status(client, login, d1) == "pending"
    assert _demand_status(client, login, d2) == "pending"

    # 两条需求合并成一个采购订单
    login("purchase01")
    resp = client.post(
        "/api/purchases/orders",
        json={
            "supplier_id": supplier_id,
            "payment_type": "银行转账",
            "items": [
                {"product_id": p1, "quantity": 5, "unit_price": 10},
                {"product_id": p2, "quantity": 7, "unit_price": 12},
            ],
            "replenishment_sources": [
                {"source_type": "fulfillment_demand", "source_id": d1},
                {"source_type": "fulfillment_demand", "source_id": d2},
            ],
        },
    )
    assert resp.status_code == 200, f"合并创建采购订单失败: {resp.text}"
    order_id = resp.json().get("data", {}).get("id")
    assert order_id, resp.text

    # 单订单包含两条明细
    login("purchase01")
    detail_resp = client.get(f"/api/purchases/orders/{order_id}")
    assert detail_resp.status_code == 200, f"查询采购订单失败: {detail_resp.text}"
    detail = detail_resp.json().get("data") or {}
    item_products = sorted(
        int(it.get("product_id") or 0) for it in (detail.get("items") or [])
    )
    assert item_products == sorted([p1, p2]), f"合并订单应包含两条产品明细: {item_products}"

    # 两条需求都转为已处理
    assert _demand_status(client, login, d1) == "converted", "需求1 应转为已处理"
    assert _demand_status(client, login, d2) == "converted", "需求2 应转为已处理"
