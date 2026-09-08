"""销售订单列表页签的回归用例。

改造前 draft 不在任何 tab 过滤器里,而建单必然先落 draft,
导致销售"保存草稿"后订单从列表消失,再也无法编辑/提交/删除——录入链路断掉。
"""

from helpers import create_trade_product, uniq


def _customer(client, login) -> int:
    login("sales01")
    resp = client.post(
        "/api/master-data/customers",
        json={"name": uniq("页签客户"), "contact_name": "张三", "phone": "13900000000", "address": "地址"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _create_draft(client, login) -> tuple[int, str]:
    customer_id = _customer(client, login)
    product_id = create_trade_product(client, login)
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
            "items": [{"product_id": product_id, "quantity": 3, "unit_price": 100}],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    return data["id"], data["order_no"]


def _list(client, login, tab: str) -> dict:
    login("sales01")
    resp = client.get("/api/sales/orders", params=[("tab", tab), ("page", 1), ("page_size", 100)])
    assert resp.status_code == 200, f"tab={tab} 列表失败: {resp.text}"
    return resp.json()["data"]


def test_draft_order_is_visible_in_draft_tab(client, login):
    """刚保存的草稿必须能在 draft 页签里看到(改造前查不到)。"""
    order_id, order_no = _create_draft(client, login)

    data = _list(client, login, "draft")
    ids = [row["id"] for row in data["items"]]
    assert order_id in ids, f"草稿订单 {order_no} 应出现在 draft 页签, 实际返回 {len(ids)} 条且不含它"


def test_draft_order_reachable_via_all_tab(client, login):
    """all 页签同样应包含草稿(兜底路径)。"""
    order_id, _ = _create_draft(client, login)

    data = _list(client, login, "all")
    assert order_id in [row["id"] for row in data["items"]], "all 页签应包含草稿订单"


def test_draft_not_mixed_into_reviewing_tab(client, login):
    """草稿不应混进"审核中"页签——它还没提交,不该出现在待审批列表里。"""
    order_id, _ = _create_draft(client, login)

    data = _list(client, login, "reviewing")
    assert order_id not in [row["id"] for row in data["items"]], "未提交的草稿不应出现在审核中页签"


def test_tab_counts_returned_for_every_tab(client, login):
    """列表接口应返回各页签的单据总数,供前端角标使用。

    改造前前端用"当页行数"充当角标,未选中页签恒显示 0,
    选中页签也只显示当页条数(最多 page_size),数字全是错的。
    """
    order_id, _ = _create_draft(client, login)

    data = _list(client, login, "draft")
    counts = data.get("tab_counts")
    assert counts, "应返回 tab_counts"
    for key in ("draft", "reviewing", "shipping", "completed", "all"):
        assert key in counts, f"tab_counts 缺少 {key}"
        assert isinstance(counts[key], int), f"tab_counts[{key}] 应为整数, 实际 {type(counts[key])}"
    assert counts["draft"] >= 1, "刚建了一张草稿, draft 计数应至少为 1"
    # 角标是总数而非当页条数:total 与 tab_counts 对应页签应一致
    assert counts["draft"] == data["pagination"]["total"], "draft 计数应等于该页签的 total"


def test_submitted_order_moves_out_of_draft_tab(client, login):
    """提交后订单应离开 draft 页签、进入审核中页签。"""
    order_id, _ = _create_draft(client, login)
    login("sales01")
    assert client.post(f"/api/sales/orders/{order_id}/submit").status_code == 200

    draft_ids = [row["id"] for row in _list(client, login, "draft")["items"]]
    assert order_id not in draft_ids, "已提交订单不应再留在 draft 页签"

    reviewing_ids = [row["id"] for row in _list(client, login, "reviewing")["items"]]
    assert order_id in reviewing_ids, "已提交订单应出现在审核中页签"
