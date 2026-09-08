"""待置货列表提补货需求的回归用例。

对应两个前端缺口:
1. 手动提补货需求弹窗:产品下拉依赖 /api/master-data/products 与本端点的返回体;
2. 待置货行提补货需求:后端 POST /queues/{id}/replenishment-demands 早已存在,
   但前端只在"置货弹窗"内提供入口,而该弹窗要靠"已置货"按钮打开 ——
   库存不足恰恰是该按钮被禁用的原因,越缺货越点不开(死锁)。

同时锁住去重的**提示语义**:复用已有未完成需求时必须能和"新建"区分开,
否则库管重复点击会看到同样的成功提示,分不清到底提没提、有没有提重。
"""

from helpers import create_trade_product, uniq


def _customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("补货客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_manual_demand_returns_demand_no(client, login):
    """手动提补货需求应返回 demand_no —— 前端提示与追溯依赖它。"""
    product_id = create_trade_product(client, login)
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": 30, "remark": "pytest 手动补货"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("demand_no"), "应返回 demand_no"
    assert data.get("fulfillment_mode") == "trade", "贸易型产品应路由到采购"


def test_product_options_available_for_manual_demand(client, login):
    """产品下拉的数据源必须可用且带 fulfillment_mode(用于显示采购/生产)。"""
    create_trade_product(client, login)
    login("inventory01")
    resp = client.get("/api/master-data/products")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert rows, "产品列表不应为空,否则下拉框没有可选项"
    assert "fulfillment_mode" in rows[0], "缺少 fulfillment_mode,下拉 label 无法区分采购/生产"


def _shortage_queue_row(client, login) -> tuple[dict, list[dict]]:
    """造一张必然缺货的已审批订单,返回 (待置货行, 缺口明细)。"""
    customer_id = _customer(client, login)
    product_id = create_trade_product(client, login)  # 不铺库存 -> 必然缺货

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
            "items": [{"product_id": product_id, "quantity": 40, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    order_no = resp.json()["data"]["order_no"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    login("finance01")
    assert client.post(
        f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest"}
    ).status_code == 200

    login("inventory01")
    resp = client.get("/api/inventory/queues")
    assert resp.status_code == 200, resp.text
    placement = resp.json()["data"]["placement"]
    rows = placement["items"] if isinstance(placement, dict) else placement
    row = next((r for r in rows if r.get("order_no") == order_no), None)
    assert row is not None, f"待置货列表应包含 {order_no}"
    shortages = [i for i in (row.get("items") or []) if float(i.get("shortage_quantity") or 0) > 0]
    assert shortages, "该订单无库存,应存在缺口明细(否则前端补货按钮不会显示)"
    return row, shortages


def test_queue_row_has_fields_for_replenishment_button(client, login):
    """待置货行必须带 queue_id 与缺口明细 —— 前端按钮的显示条件。"""
    row, shortages = _shortage_queue_row(client, login)
    assert row.get("queue_id"), "缺少 queue_id,前端会拼出 /queues/undefined/... 的请求"
    for item in shortages:
        assert item.get("product_id"), "缺口明细缺少 product_id"
        assert float(item["shortage_quantity"]) > 0


def test_replenishment_from_queue_row_creates_demand(client, login):
    """从待置货行提补货需求应成功,并明确报告"新建了几条"。"""
    row, shortages = _shortage_queue_row(client, login)
    items = [
        {"product_id": i["product_id"], "shortage_quantity": float(i["shortage_quantity"])}
        for i in shortages
    ]
    login("inventory01")
    resp = client.post(
        f"/api/inventory/queues/{row['queue_id']}/replenishment-demands", json={"items": items}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["new_count"] >= 1, f"应新建至少 1 条需求, 实际 new_count={data['new_count']}"
    assert data["reused_count"] == 0, "首次提交不应有复用"
    assert all(d.get("reused") is False for d in data["demands"]), "首次提交的需求 reused 应为 False"


def test_duplicate_replenishment_is_deduped_and_reported(client, login):
    """重复提交必须被去重,且提示要能和"新建"区分开。

    改造前两次提交都返回"已提 1 条补货需求",库管分不清是提了两单还是被去重。
    """
    row, shortages = _shortage_queue_row(client, login)
    items = [
        {"product_id": i["product_id"], "shortage_quantity": float(i["shortage_quantity"])}
        for i in shortages
    ]
    login("inventory01")
    first = client.post(
        f"/api/inventory/queues/{row['queue_id']}/replenishment-demands", json={"items": items}
    )
    assert first.status_code == 200, first.text
    first_nos = {d["demand_no"] for d in first.json()["data"]["demands"]}

    second = client.post(
        f"/api/inventory/queues/{row['queue_id']}/replenishment-demands", json={"items": items}
    )
    assert second.status_code == 200, second.text
    data = second.json()["data"]
    assert data["new_count"] == 0, f"重复提交不应新建, 实际 new_count={data['new_count']}"
    assert data["reused_count"] >= 1, "重复提交应报告复用了已有需求"
    assert all(d.get("reused") is True for d in data["demands"]), "重复提交的需求 reused 应为 True"
    # 复用的必须是同一批单号,而不是新开的
    assert {d["demand_no"] for d in data["demands"]} == first_nos, "复用的应是首次那批需求"


def test_no_duplicate_demand_rows_in_db(client, login):
    """反复提交后,库里该订单的补货需求不应出现重复行。"""
    row, shortages = _shortage_queue_row(client, login)
    items = [
        {"product_id": i["product_id"], "shortage_quantity": float(i["shortage_quantity"])}
        for i in shortages
    ]
    login("inventory01")
    for _ in range(3):
        assert client.post(
            f"/api/inventory/queues/{row['queue_id']}/replenishment-demands", json={"items": items}
        ).status_code == 200

    from database import SessionLocal
    from models import FulfillmentDemand
    from sqlalchemy import select

    db = SessionLocal()
    try:
        rows = db.execute(
            select(FulfillmentDemand).where(
                FulfillmentDemand.sales_order_id == row["order_id"]
            )
        ).scalars().all()
    finally:
        db.close()
    assert len(rows) == len(shortages), (
        f"3 次提交后应仍只有 {len(shortages)} 条需求, 实际 {len(rows)} 条"
    )
