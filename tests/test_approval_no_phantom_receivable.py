"""财务审批不得产生应收与资金流水的回归用例。

改造前:审批入参带 actual_amount>0 就写一笔应收 + incoming 资金流水,
而前端(销售审批页、审批中心)都会自动预填定金/全款金额,
结果财务点一次"通过"就凭空虚增一笔客户回款——定金单尤其严重,
审批时定金根本还没收到。

现在:审批只表示"这单可以执行",记账统一由收款确认流程负责。
入参金额仅作为审批凭证留痕,不产生账务影响。
"""

from helpers import create_trade_product, uniq


def _customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("审批记账客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _submit_order(client, login, *, deposit: float = 0) -> int:
    customer_id = _customer(client, login)
    product_id = create_trade_product(client, login)
    login("sales01")
    payload = {
        "customer_id": customer_id,
        "consignee": "张三",
        "consignee_phone": "13900000000",
        "delivery_address": "地址",
        "payment_type": "先付定金后付尾款" if deposit else "货到付款",
        "order_type": "normal",
        "operator_user_id": None,
        "items": [{"product_id": product_id, "quantity": 10, "unit_price": 100}],
    }
    if deposit:
        payload["deposit_amount"] = deposit
    resp = client.post("/api/sales/orders", json=payload)
    assert resp.status_code == 200, resp.text
    order_id = resp.json()["data"]["id"]
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200
    return order_id


def _account_rows(order_id: int) -> tuple[int, int]:
    """返回 (应收记录数, 该订单的资金流水数)。"""
    from database import SessionLocal
    from models import CashFlowRecord, ReceivableRecord
    from sqlalchemy import select

    db = SessionLocal()
    try:
        receivables = db.execute(
            select(ReceivableRecord.id).where(ReceivableRecord.sales_order_id == order_id)
        ).scalars().all()
        flows = db.execute(
            select(CashFlowRecord.id).where(
                CashFlowRecord.business_type == "sales_order",
                CashFlowRecord.business_id == order_id,
            )
        ).scalars().all()
        return len(receivables), len(flows)
    finally:
        db.close()


def test_approval_without_amount_records_nothing(client, login):
    """前端现在不传金额:审批后不应有任何应收与资金流水。"""
    order_id = _submit_order(client, login)

    login("finance01")
    resp = client.post(f"/api/sales/orders/{order_id}/approve", json={"decision": "approved", "note": "pytest"})
    assert resp.status_code == 200, resp.text

    receivables, flows = _account_rows(order_id)
    assert receivables == 0, f"审批不应产生应收记录, 实际 {receivables} 条"
    assert flows == 0, f"审批不应产生资金流水, 实际 {flows} 条"


def test_approval_with_amount_still_records_nothing(client, login):
    """即便调用方仍传金额(旧前端/第三方),也不得记账——后端防线。"""
    order_id = _submit_order(client, login)

    login("finance01")
    resp = client.post(
        f"/api/sales/orders/{order_id}/approve",
        json={"decision": "approved", "note": "pytest", "actual_amount": 1000, "voucher_amount": 1000},
    )
    assert resp.status_code == 200, resp.text

    receivables, flows = _account_rows(order_id)
    assert receivables == 0, f"传了金额也不应记应收, 实际 {receivables} 条(会虚增客户回款)"
    assert flows == 0, f"传了金额也不应记资金流水, 实际 {flows} 条"


def test_deposit_order_approval_records_nothing(client, login):
    """定金单最关键:审批时定金还没收到,绝不能先记一笔。"""
    order_id = _submit_order(client, login, deposit=500)

    login("finance01")
    resp = client.post(
        f"/api/sales/orders/{order_id}/approve",
        json={"decision": "approved", "note": "pytest", "actual_amount": 500, "voucher_amount": 500},
    )
    assert resp.status_code == 200, resp.text

    receivables, flows = _account_rows(order_id)
    assert receivables == 0, "定金单审批时定金未到账,不应记应收"
    assert flows == 0, "定金单审批时不应记资金流水"


def test_approval_still_reserves_stock_and_advances_status(client, login):
    """去掉记账不能影响审批本职:仍应占用库存并推进到待发货。"""
    from helpers import warehouse_map

    wh = warehouse_map(client, login)["finished"]
    customer_id = _customer(client, login)
    product_id = create_trade_product(client, login)

    login("inventory_super01")
    assert client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": wh,
            "operation_type": "inbound",
            "remark": "pytest",
            "items": [{"product_id": product_id, "batch_no": uniq("AP"), "quantity": 50, "unit_cost": 30}],
        },
    ).status_code == 200

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
            "items": [{"product_id": product_id, "quantity": 20, "unit_price": 100}],
        },
    )
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
        assert order.status == "pending_shipment", f"审批通过应进入待发货, 实际 {order.status}"
        assert float(order.items[0].reserved_quantity or 0) == 20.0, "审批仍应占用库存 20"
    finally:
        db.close()
