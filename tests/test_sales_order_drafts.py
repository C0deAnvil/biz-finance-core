"""销售订单不完整草稿：保存不校验，提交时才校验。"""


def test_incomplete_sales_order_can_be_saved_but_not_submitted(client, login):
    login("sales01")
    response = client.post("/api/sales/orders", json={"items": [{}]})
    assert response.status_code == 200, response.text
    order_id = response.json()["data"]["id"]

    detail = client.get(f"/api/sales/orders/{order_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["draft_payload"]["customer_id"] is None

    response = client.post(f"/api/sales/orders/{order_id}/submit")
    assert response.status_code == 422
