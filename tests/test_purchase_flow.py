"""采购流程测试:建单 → 审批(阈值) → 到货 → 质检 → 入库 → 付款申请。

覆盖接口:
- POST /api/purchases/orders、GET /orders、GET /orders/{id}
- POST /orders/{id}/approve、POST /orders/{id}/confirm
- POST /api/purchases/receipts、GET /receipts/{id}
- POST /api/quality/inspections、POST /api/quality/tasks/{id}/allow-inbound
- POST /api/inventory/operation-requests/{id}/approve|complete
- POST /api/purchases/settlements/payment-requests 及审批/完成
"""

from helpers import (
    supplier_account_for_order,
    create_company_account,
    allow_quality_inbound,
    approve_purchase_order,
    complete_operation_request,
    confirm_purchase_order,
    create_purchase_order,
    create_purchase_receipt,
    create_supplier,
    create_trade_product,
    find_operation_request,
    find_quality_task_by_batch,
    request_operation_items,
    set_supplier_catalog,
    stock_of,
    submit_inspection,
    uniq,
    upload_attachment,
    warehouse_map,
)


def _get_order(client, login, order_id: int) -> dict:
    login("purchase01")
    resp = client.get(f"/api/purchases/orders/{order_id}")
    assert resp.status_code == 200, f"查询采购订单失败: {resp.text}"
    return resp.json().get("data") or {}


def _get_receipt(client, login, receipt_id: int) -> dict:
    login("purchase01")
    resp = client.get(f"/api/purchases/receipts/{receipt_id}")
    assert resp.status_code == 200, f"查询到货单失败: {resp.text}"
    return resp.json().get("data") or {}


def test_purchase_order_create_and_list(client, login):
    """采购建单(未达阈值直接进入采购中)并可从列表/详情查到。"""
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)

    created = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=5, unit_price=10)
    order_id = created["id"]
    assert created["status"] == "purchasing", f"小额采购应直接采购中,实际: {created}"

    detail = _get_order(client, login, order_id)
    assert detail.get("order_no"), detail
    assert detail.get("status") == "purchasing"
    items = detail.get("items") or []
    assert len(items) == 1
    assert float(items[0]["quantity"]) == 5
    assert float(items[0]["unit_price"]) == 10

    login("purchase01")
    resp = client.get("/api/purchases/orders")
    assert resp.status_code == 200
    rows = (resp.json().get("data") or {}).get("items") or []
    assert any(int((row or {}).get("id") or 0) == order_id for row in rows), "新建采购单应出现在列表"


def test_purchase_fulfillment_demands_list_loads(client, login):
    """采购页必须能加载补货需求,即使暂时没有待处理需求。"""
    login("purchase01")
    resp = client.get("/api/purchases/fulfillment-demands")
    assert resp.status_code == 200, f"采购补货需求列表加载失败: {resp.text}"
    assert isinstance(resp.json().get("data"), list)


def test_purchase_order_threshold_approval_flow(client, login):
    """达到采购审批阈值(数量阈值)的订单需要经理审批并采购员确认。"""
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login, threshold_qty=3)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)

    # quantity >= threshold(1) 触发数量阈值审批
    created = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=5, unit_price=10)
    order_id = created["id"]
    assert created["status"] == "pending_approval", f"超阈值应进入待审批,实际: {created}"

    approve_purchase_order(client, login, order_id=order_id, decision="approved")
    detail = _get_order(client, login, order_id)
    assert detail["status"] == "pending_purchase_confirm", detail

    confirm_purchase_order(client, login, order_id=order_id)
    detail = _get_order(client, login, order_id)
    assert detail["status"] == "purchasing", detail

    # 驳回路径:审批拒绝后应停在 rejected
    created2 = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=5, unit_price=10)
    approve_purchase_order(client, login, order_id=created2["id"], decision="rejected")
    detail2 = _get_order(client, login, created2["id"])
    assert detail2["status"] == "rejected", detail2


def test_purchase_receipt_quality_inbound_full_chain(client, login):
    """采购到货(需质检) → 自动创建来料质检 → 质检合格准许入库 → 经理审批 → 库管入库 → 库存增加。"""
    warehouses = warehouse_map(client, login)
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)
    created = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=10, unit_price=10)
    order_id = created["id"]
    order_item_id = created["items"][0]["id"]
    if created["status"] == "pending_approval":
        approve_purchase_order(client, login, order_id=order_id)
        confirm_purchase_order(client, login, order_id=order_id)

    # 到货(需质检)
    receipt = create_purchase_receipt(
        client,
        login,
        purchase_order_id=order_id,
        order_item_id=order_item_id,
        warehouse_id=warehouses["finished"],
        received_quantity=10,
        requires_quality=True,
    )
    receipt_id = receipt["id"]
    assert receipt.get("status") == "pending_quality", f"到货应进入待质检,实际: {receipt}"

    # 自动生成来料质检任务(单明细订单批次号 = 订单号-ZJ)
    quality_task = find_quality_task_by_batch(client, login, batch_no=f"{created['order_no']}-ZJ", source_type="purchase_quality")
    task_id = quality_task["id"]

    # 质检合格 → 质检任务准许入库
    submit_inspection(client, login, quality_task_id=task_id, sequence_no=1, grade="A", quantity=10, passed=True)
    login("quality01")
    resp = client.get(f"/api/quality/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ready_for_inbound", resp.json()

    allow_quality_inbound(client, login, quality_task_id=task_id)
    receipt_detail = _get_receipt(client, login, receipt_id)
    assert receipt_detail.get("quality_status") == "approved", receipt_detail

    # 生成库管入库作业(入库不再需要经理审批) → 库管直接执行
    op = find_operation_request(client, login, category="purchase_quality_inbound", source_id=receipt_id, statuses={"pending"})
    complete_operation_request(client, login, request_id=op["id"], items=request_operation_items(op))

    # 成品仓库存 +10
    stock = stock_of(client, login, warehouse_id=warehouses["finished"], product_id=product_id)
    assert stock >= 10, f"入库后成品库存应 >= 10,实际: {stock}"


def test_purchase_payment_request_flow(client, login):
    """采购付款申请:提交 → 董事长审批 → 采购员确认完成。"""
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)
    created = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=8, unit_price=12)
    order_id = created["id"]
    total_amount = float(sum(float((item or {}).get("quantity") or 0) * float((item or {}).get("unit_price") or 0) for item in created.get("items") or []))
    assert total_amount > 0

    login("finance01")
    finance_id = client.get("/api/auth/me").json()["data"]["id"]
    login("purchase01")
    resp = client.post(
        "/api/purchases/settlements/payment-requests",
        json={
            "purchase_order_id": order_id,
            "counterparty_account_id": supplier_account_for_order(client, login, order_id),
            "assigned_finance_user_id": finance_id,
            "requested_amount": total_amount,
            "order_amount": total_amount,
            "other_fee_amount": 0,
            "payment_detail_remark": "pytest 付款申请",
        },
    )
    assert resp.status_code == 200, f"提交付款申请失败: {resp.text}"
    request_data = resp.json().get("data") or {}
    request_id = request_data["id"]

    # 未达审批阈值(默认 50000)的付款申请直接进入财务出账环节,不再经董事长审批。
    # 原先所有付款一律先派给董事长,小额付款也要等审批,流程无谓变长;
    # 且董事长审批后会再产生一张待付款申请,存在重复付款风险。
    assert request_data.get("status") == "pending_finance_payment", (
        f"小额付款申请应直接待财务出账, 实际 {request_data.get('status')}"
    )
    login("chairman01")
    resp = client.post(
        f"/api/purchases/settlements/payment-requests/{request_id}/approve",
        json={"decision": "approved", "actual_amount": total_amount, "manager_remark": "pytest 审批通过"},
    )
    assert resp.status_code == 409, f"未达阈值的付款申请不应可被董事长审批: {resp.text}"

    # 财务登记出账(需先上传银行回单)
    receipt_attachment_id = upload_attachment(
        client, login, business_type="purchase_payment_request", business_id=request_id, attachment_type="bank_receipt"
    )
    login("finance01")
    resp = client.post(
        f"/api/finance/payables/payment-requests/{request_id}/register",
        json={
            "purchase_payment_request_id": request_id,
            "company_account_id": create_company_account(client, login),
            "actual_amount": total_amount,
            "remark": "pytest 出账",
            "payment_method": "银行转账",
            "bank_receipt_attachment_id": receipt_attachment_id,
        },
    )
    assert resp.status_code == 200, f"财务出账登记失败: {resp.text}"

    # 采购员确认完成
    login("purchase01")
    resp = client.post(f"/api/purchases/settlements/payment-requests/{request_id}/complete", json={"completion_remark": "pytest 款已付"})
    assert resp.status_code == 200, f"付款完成失败: {resp.text}"

    # 结算页应能查到该订单(订单/应付/付款申请任一处出现即可)
    login("purchase01")
    resp = client.get("/api/purchases/settlements")
    assert resp.status_code == 200
    payload = resp.json().get("data") or {}
    assert payload, "结算页数据为空"

    def _contains(obj, needle: int) -> bool:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {"id", "order_id", "purchase_order_id", "request_id", "payment_request_id"} and value == needle:
                    return True
                if _contains(value, needle):
                    return True
        elif isinstance(obj, list):
            return any(_contains(item, needle) for item in obj)
        return False

    assert _contains(payload, order_id), "付款后结算页应包含该订单"


def test_purchase_order_validation(client, login):
    """采购校验:未维护名录的供应商不能建单;已停用供应商不能建单。"""
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)

    # 供应商无名录 → 409
    login("purchase01")
    resp = client.post(
        "/api/purchases/orders",
        json={
            "supplier_id": supplier_id,
            "payment_type": "银行转账",
            "items": [{"product_id": product_id, "quantity": 1, "unit_price": 10}],
        },
    )
    assert resp.status_code == 409
    assert "名录" in resp.json().get("msg", "")

    # 名录配置后建单成功
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)
    created = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=1, unit_price=10)
    assert created["status"] == "purchasing"


def test_purchase_order_unique_flow_records(client, login):
    """采购订单详情应含核心业务字段(前端依赖)。"""
    supplier_id = create_supplier(client, login)
    product_id = create_trade_product(client, login)
    set_supplier_catalog(client, login, supplier_id=supplier_id, product_id=product_id)
    created = create_purchase_order(client, login, supplier_id=supplier_id, product_id=product_id, quantity=3, unit_price=50)
    detail = _get_order(client, login, created["id"])
    for key in ("order_no", "supplier_id", "supplier_name", "items", "status", "status_label", "payment_type", "arrival_status", "flow_steps"):
        assert key in detail, f"采购订单详情缺少字段 {key}"
    assert uniq("X") != uniq("Y")
