"""财务事务指派人校验的回归用例。

需求:提交客户订单审批、收款申请、报销、采购付款申请等财务相关事务时应选择一个财务人员。

改造前采购付款申请直接采信入参 assigned_finance_user_id,不校验该用户是否为财务,
可把付款申请指派给销售等任意角色(销售/报销路径则已有校验)。
"""

from helpers import create_purchase_order, create_supplier, create_trade_product, set_supplier_catalog, uniq, supplier_account_for_order


def _user_id(username: str) -> int:
    from database import SessionLocal
    from models import User
    from sqlalchemy import select

    db = SessionLocal()
    try:
        return db.execute(select(User).where(User.username == username)).scalar_one().id
    finally:
        db.close()


def _stored_assignee(request_id: int):
    """返回 (assigned_finance_user_id, 该用户角色code)。"""
    from database import SessionLocal
    from models import PurchasePaymentRequest, User
    from sqlalchemy import select

    db = SessionLocal()
    try:
        row = db.execute(
            select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
        ).scalar_one()
        assignee_id = row.assigned_finance_user_id
        if not assignee_id:
            return None, None
        user = db.execute(select(User).where(User.id == assignee_id)).scalar_one_or_none()
        return assignee_id, (user.role.code if user and user.role else None)
    finally:
        db.close()


def _make_payable_order(client, login) -> tuple[int, float]:
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)
    created = create_purchase_order(
        client, login, supplier_id=supplier_id, product_id=product_id, quantity=8, unit_price=12
    )
    total = float(sum(float(i["quantity"]) * float(i["unit_price"]) for i in created["items"]))
    return created["id"], total


def _submit_payment_request(client, login, *, order_id: int, total: float, assignee_id: int | None):
    login("purchase01")
    payload = {
        "purchase_order_id": order_id,
        "counterparty_account_id": supplier_account_for_order(client, login, order_id),
        "requested_amount": total,
        "order_amount": total,
        "other_fee_amount": 0,
        "payment_detail_remark": "pytest 付款申请",
    }
    if assignee_id is not None:
        payload["assigned_finance_user_id"] = assignee_id
    return client.post("/api/purchases/settlements/payment-requests", json=payload)


def test_non_finance_assignee_is_rejected(client, login):
    """把采购付款申请指派给非财务角色(销售)应被拒绝。"""
    order_id, total = _make_payable_order(client, login)
    sales_id = _user_id("sales01")

    resp = _submit_payment_request(client, login, order_id=order_id, total=total, assignee_id=sales_id)
    assert resp.status_code == 409, f"指派非财务应被拒绝, 实际 {resp.status_code}: {resp.text}"


def test_finance_assignee_is_honoured(client, login):
    """指定合法财务人员时应按指定落库,而不是被轮询覆盖。"""
    order_id, total = _make_payable_order(client, login)
    finance_id = _user_id("finance01")

    resp = _submit_payment_request(client, login, order_id=order_id, total=total, assignee_id=finance_id)
    assert resp.status_code == 200, resp.text

    stored_id, role_code = _stored_assignee(resp.json()["data"]["id"])
    assert stored_id == finance_id, f"应按指定的财务落库, 实际 {stored_id}"
    assert role_code in {"finance", "finance_super"}, f"落库的处理人角色异常: {role_code}"


def test_unassigned_falls_back_to_finance(client, login):
    """不指定时应自动轮询到财务角色,不能留空导致无人处理。"""
    order_id, total = _make_payable_order(client, login)

    resp = _submit_payment_request(client, login, order_id=order_id, total=total, assignee_id=None)
    assert resp.status_code == 200, resp.text

    stored_id, role_code = _stored_assignee(resp.json()["data"]["id"])
    assert stored_id, "未指定时应轮询分配一个财务"
    assert role_code in {"finance", "finance_super"}, f"轮询到的应是财务角色, 实际 {role_code}"


def test_assignable_users_endpoint_lists_only_finance(client, login):
    """/api/finance/assignable-users 只返回财务人员,且发起方角色(销售)也能访问。"""
    login("sales01")
    resp = client.get("/api/finance/assignable-users")
    assert resp.status_code == 200, f"发起方应能读取财务候选列表: {resp.text}"
    rows = resp.json()["data"]
    assert rows, "财务候选列表不应为空"
    assert all(r["role_code"] in {"finance", "finance_super"} for r in rows), (
        f"只应返回财务角色, 实际: {[r['role_code'] for r in rows]}"
    )
    assert all(set(r.keys()) == {"id", "full_name", "role_code", "is_super"} for r in rows), (
        "不应暴露除 id/姓名/角色 之外的用户信息"
    )


def _create_customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("财务指派客户"), "contact_name": "张三", "phone": "13900000000", "address": "测试地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _create_draft_order(client, login) -> int:
    customer_id = _create_customer(client, login)
    product_id = create_trade_product(client, login)
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
            "items": [{"product_id": product_id, "quantity": 3, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_sales_order_submit_records_assignee(client, login):
    """提交客户订单给财务审批时选择的财务应被记录。"""
    order_id = _create_draft_order(client, login)
    finance_id = _user_id("finance01")

    login("sales01")
    resp = client.post(
        f"/api/sales/orders/{order_id}/submit",
        json={"assigned_finance_user_id": finance_id},
    )
    assert resp.status_code == 200, resp.text

    from database import SessionLocal
    from models import SalesOrder
    from sqlalchemy import select

    db = SessionLocal()
    try:
        row = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one()
        assert row.assigned_finance_user_id == finance_id, (
            f"应记录指定的审批财务, 实际 {row.assigned_finance_user_id}"
        )
    finally:
        db.close()


def test_sales_order_submit_rejects_non_finance(client, login):
    """提交客户订单时指派非财务角色(库管)应被拒绝。"""
    order_id = _create_draft_order(client, login)
    inventory_id = _user_id("inventory01")

    login("sales01")
    resp = client.post(
        f"/api/sales/orders/{order_id}/submit",
        json={"assigned_finance_user_id": inventory_id},
    )
    assert resp.status_code == 409, f"指派非财务应被拒绝, 实际 {resp.status_code}: {resp.text}"
