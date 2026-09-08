"""财务驳回 → 销售修改 → 重新提交 闭环的回归用例。

财务不再直接改单(原"财务改单"入口已改为"审批"),有问题一律驳回、
由销售改后重新提交。这条路径因此成为唯一的纠错通道,必须走得通。

改造前它是断的:inventory_dispatch_queues.sales_order_id 上有唯一约束,
而提交审批路径无条件 insert 置货队列行。首次提交已建过行,
驳回后重新提交必然撞 1062 Duplicate entry → 500。
"""

from helpers import create_trade_product, uniq


def _customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("重提客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _order_payload(customer_id: int, product_id: int, *, unit_price: float, price_note: str | None = None) -> dict:
    item = {"product_id": product_id, "quantity": 5, "unit_price": unit_price}
    if price_note:
        item["price_change_remark"] = price_note
    return {
        "customer_id": customer_id,
        "consignee": "张三",
        "consignee_phone": "13900000000",
        "delivery_address": "地址",
        "payment_type": "货到付款",
        "order_type": "normal",
        "operator_user_id": None,
        "items": [item],
    }


def _submitted_order(client, login) -> tuple[int, int, int]:
    """返回 (order_id, customer_id, product_id),状态为已提交待审批。"""
    customer_id = _customer(client, login)
    product_id = create_trade_product(client, login)
    login("sales01")
    resp = client.post("/api/sales/orders", json=_order_payload(customer_id, product_id, unit_price=100))
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    return order_id, customer_id, product_id


def _status(order_id: int) -> str:
    from database import SessionLocal
    from models import SalesOrder
    from sqlalchemy import select

    db = SessionLocal()
    try:
        return db.execute(select(SalesOrder.status).where(SalesOrder.id == order_id)).scalar_one()
    finally:
        db.close()


def _queue_row_count(order_id: int) -> int:
    from database import SessionLocal
    from models import InventoryDispatchQueue
    from sqlalchemy import func, select

    db = SessionLocal()
    try:
        return db.execute(
            select(func.count())
            .select_from(InventoryDispatchQueue)
            .where(InventoryDispatchQueue.sales_order_id == order_id)
        ).scalar_one()
    finally:
        db.close()


def test_resubmit_after_rejection_succeeds(client, login):
    """驳回后重新提交必须成功(改造前是 500 —— 唯一约束冲突)。"""
    order_id, customer_id, product_id = _submitted_order(client, login)

    login("finance01")
    assert client.post(
        f"/api/sales/orders/{order_id}/approve",
        json={"decision": "rejected", "note": "金额有误，请修改"},
    ).status_code == 200
    assert _status(order_id) == "rejected", "驳回后应为 rejected,销售才能修改"

    login("sales01")
    assert client.put(
        f"/api/sales/orders/{order_id}",
        json=_order_payload(customer_id, product_id, unit_price=100),
    ).status_code == 200

    resp = client.post(f"/api/sales/orders/{order_id}/submit")
    assert resp.status_code == 200, f"驳回后重新提交应成功, 实际 {resp.status_code}: {resp.text}"
    assert _status(order_id) == "pending_confirmation", "重提后应回到待审批"


def test_resubmit_does_not_duplicate_dispatch_queue(client, login):
    """反复驳回/重提不应产生多行置货队列(唯一约束的根因)。"""
    order_id, customer_id, product_id = _submitted_order(client, login)

    for round_no in range(2):
        login("finance01")
        assert client.post(
            f"/api/sales/orders/{order_id}/approve",
            json={"decision": "rejected", "note": f"第{round_no + 1}次驳回"},
        ).status_code == 200
        login("sales01")
        resp = client.post(f"/api/sales/orders/{order_id}/submit")
        assert resp.status_code == 200, f"第{round_no + 1}轮重提失败: {resp.text}"

    assert _queue_row_count(order_id) == 1, (
        f"置货队列应恒为 1 行, 实际 {_queue_row_count(order_id)} 行"
    )


def test_queue_status_reset_on_resubmit(client, login):
    """重提时已存在的队列行应被重置回初始态,而不是留着上一轮的状态。"""
    order_id, _, _ = _submitted_order(client, login)

    from database import SessionLocal
    from models import InventoryDispatchQueue
    from sqlalchemy import select

    db = SessionLocal()
    try:
        row = db.execute(
            select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order_id)
        ).scalar_one()
        row.placement_status = "placed"
        row.shipment_status = "ready"
        db.commit()
    finally:
        db.close()

    login("finance01")
    assert client.post(
        f"/api/sales/orders/{order_id}/approve", json={"decision": "rejected", "note": "退回"}
    ).status_code == 200
    login("sales01")
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200

    db = SessionLocal()
    try:
        row = db.execute(
            select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order_id)
        ).scalar_one()
        assert row.placement_status == "pending", f"置货状态应重置为 pending, 实际 {row.placement_status}"
        assert row.shipment_status == "waiting_approval", (
            f"发货状态应重置为 waiting_approval, 实际 {row.shipment_status}"
        )
    finally:
        db.close()


def test_approval_after_resubmit_completes(client, login):
    """重提后财务再次审批应能正常通过,推进到待发货。"""
    order_id, customer_id, product_id = _submitted_order(client, login)

    login("finance01")
    assert client.post(
        f"/api/sales/orders/{order_id}/approve", json={"decision": "rejected", "note": "请修改"}
    ).status_code == 200
    login("sales01")
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200

    login("finance01")
    resp = client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "通过"})
    assert resp.status_code == 200, f"重提后审批应成功: {resp.text}"
    assert _status(order_id) == "pending_shipment", "审批通过后应进入待发货"
