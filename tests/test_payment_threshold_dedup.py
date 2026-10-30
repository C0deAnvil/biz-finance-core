"""采购付款申请:阈值分流 + 重复申请拦截的回归用例。

两个改动都关系资金安全:

1. 阈值分流:只有付款金额达到阈值才需董事长审批,未达阈值直接进入财务出账。
   改造前一律先派给董事长,小额付款也要等审批,且董事长审批通过后又会形成
   一张待付款申请,容易重复付款。

2. 重复拦截:同一采购单不允许挂两张未完成的付款申请。
   改造前查重用 _latest_payment_request(只看最新一张),
   若最新那张已驳回、而更早一张仍在 pending_finance_payment,校验就放行 ——
   真实库里 CG20260907001 就挂上了 FK20260907001(待财务付款) +
   FK20260907002(待完成),两张各 1 元,存在重复付款风险。
"""

from helpers import approve_purchase_order, confirm_purchase_order, create_purchase_order, create_supplier, create_trade_product, set_supplier_catalog, supplier_account_for_order


def _set_threshold(client, login, amount: float) -> None:
    login("purchase01")
    resp = client.put(
        "/api/master-data/settings/purchase-approval-threshold",
        json={"amount": amount},
    )
    assert resp.status_code == 200, resp.text


def _order(client, login, *, quantity: float, unit_price: float) -> tuple[int, float]:
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)
    created = create_purchase_order(
        client, login, supplier_id=supplier_id, product_id=product_id, quantity=quantity, unit_price=unit_price
    )
    total = float(
        sum(
            float((item or {}).get("quantity") or 0) * float((item or {}).get("unit_price") or 0)
            for item in created.get("items") or []
        )
    )
    assert total > 0
    return created["id"], total


def _submit_request(client, login, *, order_id: int, amount: float):
    login("purchase01")
    return client.post(
        "/api/purchases/settlements/payment-requests",
        json={
            "purchase_order_id": order_id,
            "counterparty_account_id": supplier_account_for_order(client, login, order_id),
            "requested_amount": amount,
            "order_amount": amount,
            "other_fee_amount": 0,
            "payment_detail_remark": "pytest 付款申请",
        },
    )


def test_below_threshold_skips_chairman(client, login):
    """未达阈值 → 直接待财务出账,不派董事长审批。"""
    _set_threshold(client, login, 100000)
    order_id, total = _order(client, login, quantity=8, unit_price=12)  # 96,远低于阈值

    resp = _submit_request(client, login, order_id=order_id, amount=total)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "pending_finance_payment", "小额付款应直接待财务出账"


def test_above_threshold_requires_chairman(client, login):
    """达到阈值 → 仍须董事长审批(不能因为改动把审批环节整个去掉)。"""
    _set_threshold(client, login, 100)
    order_id, total = _order(client, login, quantity=100, unit_price=50)  # 5000 >= 100

    resp = _submit_request(client, login, order_id=order_id, amount=total)
    assert resp.status_code == 200, resp.text
    request_data = resp.json()["data"]
    assert request_data["status"] == "pending_chairman_approval", "大额付款必须先经董事长审批"

    login("chairman01")
    resp = client.post(
        f"/api/purchases/settlements/payment-requests/{request_data['id']}/approve",
        json={"decision": "approved", "actual_amount": total, "manager_remark": "pytest 审批通过"},
    )
    assert resp.status_code == 200, f"达阈值的付款申请应可被董事长审批: {resp.text}"


def test_payment_request_requires_effective_purchase_order(client, login):
    """采购单审批和采购确认完成前，不能通过付款申请绕过订单审批。"""
    _set_threshold(client, login, 100)
    order_id, total = _order(client, login, quantity=10, unit_price=20)  # 200 >= 100

    pending = _submit_request(client, login, order_id=order_id, amount=total)
    assert pending.status_code == 409, pending.text
    assert "尚未完成总经理审批" in (pending.json().get("msg") or pending.json().get("detail") or "")

    approve_purchase_order(client, login, order_id=order_id)
    awaiting_confirm = _submit_request(client, login, order_id=order_id, amount=total)
    assert awaiting_confirm.status_code == 409, awaiting_confirm.text
    assert "尚未确认开始采购" in (awaiting_confirm.json().get("msg") or awaiting_confirm.json().get("detail") or "")

    confirm_purchase_order(client, login, order_id=order_id)
    effective = _submit_request(client, login, order_id=order_id, amount=total)
    assert effective.status_code == 200, effective.text


def test_duplicate_payment_request_blocked(client, login):
    """同一采购单已有未完成付款申请时,再提交必须被拦截(防重复付款)。"""
    _set_threshold(client, login, 100000)
    order_id, total = _order(client, login, quantity=8, unit_price=12)

    first = _submit_request(client, login, order_id=order_id, amount=total)
    assert first.status_code == 200, first.text

    second = _submit_request(client, login, order_id=order_id, amount=total)
    assert second.status_code == 409, f"重复提交应被拒绝, 实际 {second.status_code}: {second.text}"
    # 该应用的错误体是 {"code","msg","data"},不是 FastAPI 默认的 detail
    body = second.json()
    assert "未完成的付款申请" in (body.get("msg") or body.get("detail") or ""), second.text


def test_duplicate_blocked_even_when_latest_is_rejected(client, login):
    """最新一张已驳回、但更早一张仍未完成时,也必须拦截。

    这是改造前漏掉的场景:查重只看最新一张,最新是 rejected 就放行,
    于是同一采购单挂上两张有效申请。
    """
    _set_threshold(client, login, 100)
    order_id, total = _order(client, login, quantity=100, unit_price=50)

    # 第一张:达阈值 → pending_chairman_approval
    first = _submit_request(client, login, order_id=order_id, amount=total)
    assert first.status_code == 200, first.text
    first_id = first.json()["data"]["id"]

    # 直接把它推进到 pending_finance_payment(模拟董事长已批、等财务出账)
    login("chairman01")
    resp = client.post(
        f"/api/purchases/settlements/payment-requests/{first_id}/approve",
        json={"decision": "approved", "actual_amount": total, "manager_remark": "pytest"},
    )
    assert resp.status_code == 200, resp.text

    # 再插一张已驳回的申请,让"最新一张"变成 rejected
    from database import SessionLocal
    from models import PurchasePaymentRequest
    from sqlalchemy import select

    db = SessionLocal()
    try:
        row = db.execute(
            select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == first_id)
        ).scalar_one()
        rejected = PurchasePaymentRequest(
            request_no=f"FKTEST{first_id}",
            purchase_order_id=order_id,
            requested_by_id=row.requested_by_id,
            requested_amount=row.requested_amount,
            order_amount=row.order_amount,
            other_fee_amount=0,
            actual_amount=row.actual_amount,
            arrival_status=row.arrival_status,
            status="rejected",
        )
        db.add(rejected)
        db.commit()
    finally:
        db.close()

    # 此时最新一张是 rejected,但 first 仍在 pending_finance_payment → 必须拦截
    third = _submit_request(client, login, order_id=order_id, amount=total)
    assert third.status_code == 409, (
        f"最新一张已驳回但更早一张未完成时仍应拦截, 实际 {third.status_code}: {third.text}"
    )
