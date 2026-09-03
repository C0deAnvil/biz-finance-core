"""销售退货在途测试:退货提交 → 增加在途;退货入库 → 扣在途 + 加实有。

覆盖接口:
- POST /api/master-data/customers、/api/master-data/products(贸易型)
- POST /api/inventory/manual-operations(成品入库铺库存)
- POST /api/sales/orders、POST /orders/{id}/submit、POST /orders/{id}/approve
- POST /api/sales/returns、POST /api/sales/returns/{id}/approve
- POST /api/inventory/operation-requests/{id}/complete(退货入库)
- GET /api/inventory/stocks(校验 quantity / in_transit / expected_available_quantity)
"""

from helpers import (
    complete_operation_request,
    create_trade_product,
    find_operation_request,
    request_operation_items,
    uniq,
    warehouse_map,
)


def _create_customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("测试客户"), "contact_name": "张三", "phone": "13900000000", "address": "测试地址"},
    )
    assert resp.status_code == 200, f"创建客户失败: {resp.text}"
    return resp.json()["data"]["id"]


def _finish_product_stock(client, login, *, warehouse_id: int, product_id: int, quantity: float) -> None:
    login("inventory_super01")
    resp = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": warehouse_id,
            "operation_type": "inbound",
            "remark": "pytest 成品入库",
            "items": [{"product_id": product_id, "batch_no": uniq("PC"), "quantity": quantity, "unit_cost": 30}],
        },
    )
    assert resp.status_code == 200, f"成品手工入库失败: {resp.text}"


def _stock_row(client, login, *, warehouse_id: int, product_id: int) -> dict:
    login("inventory_super01")
    resp = client.get("/api/inventory/stocks", params=[("warehouse_id", warehouse_id), ("product_id", product_id)])
    assert resp.status_code == 200, f"查询库存失败: {resp.text}"
    rows = resp.json().get("data") or []
    for row in rows:
        if row.get("item_type") == "product" and int(row.get("item_id") or 0) == product_id:
            return row
    raise AssertionError(f"未找到产品 #{product_id} 的库存行")


def _create_shipped_order(client, login, *, product_id: int, quantity: float) -> tuple[int, int]:
    """建单 → 提交 → 财务审批,再直改状态为已发货(聚焦退货在途,不展开发货前置)。"""
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
    assert resp.status_code == 200, f"创建销售订单失败: {resp.text}"
    order_id = resp.json()["data"]["id"]

    resp = client.post(f"/api/sales/orders/{order_id}/submit")
    assert resp.status_code == 200, f"提交销售订单失败: {resp.text}"

    login("finance01")
    resp = client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest 审批"})
    assert resp.status_code == 200, f"财务审批失败: {resp.text}"

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
        item_id = order.items[0].id
    finally:
        db.close()
    return order_id, item_id


def test_sales_return_in_transit_flow(client, login):
    """退货提交 → 在途增加;审批 → 生成入库作业;入库完成 → 扣在途 + 加实有。"""
    warehouses = warehouse_map(client, login)
    product_id = create_trade_product(client, login)
    _finish_product_stock(client, login, warehouse_id=warehouses["finished"], product_id=product_id, quantity=10)
    order_id, item_id = _create_shipped_order(client, login, product_id=product_id, quantity=10)

    # 提交退货(10) → 成品仓该产品在途 +10
    login("sales01")
    resp = client.post(
        "/api/sales/returns",
        json={
            "sales_order_id": order_id,
            "return_type": "return",
            "reason": "pytest 退货",
            "refund_amount": 0,
            "requires_reinspection": False,
            "items": [{"sales_order_item_id": item_id, "quantity": 10}],
        },
    )
    assert resp.status_code == 200, f"提交退货失败: {resp.text}"
    return_id = resp.json()["data"]["id"]
    row = _stock_row(client, login, warehouse_id=warehouses["finished"], product_id=product_id)
    assert float(row.get("in_transit") or 0) == 10.0, f"退货提交后应在途 10, 实际: {row.get('in_transit')}"
    # 预计可用应把退货在途计入(实有10 - 占用10 + 在途10 = 10)
    assert float(row.get("expected_available_quantity") or 0) == 10.0, (
        f"预计可用应计入退货在途(10-10+10=10), 实际: {row.get('expected_available_quantity')}"
    )

    # 经理审批通过 → 生成退货入库作业
    login("manager01")
    resp = client.post(f"/api/sales/returns/{return_id}/approve", json={"decision": "approved", "note": "pytest 通过"})
    assert resp.status_code == 200, f"退货审批失败: {resp.text}"
    op = find_operation_request(client, login, category="sales_return_inbound", source_id=return_id, statuses={"pending"})

    # 库管执行入库 → 实有 +10, 在途清零
    complete_operation_request(client, login, request_id=op["id"], items=request_operation_items(op))
    row = _stock_row(client, login, warehouse_id=warehouses["finished"], product_id=product_id)
    assert float(row.get("quantity") or 0) == 20.0, f"入库后实有应 20, 实际: {row.get('quantity')}"
    assert float(row.get("in_transit") or 0) == 0.0, f"入库后在途应清零, 实际: {row.get('in_transit')}"
