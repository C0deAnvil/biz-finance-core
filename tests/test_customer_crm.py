from helpers import uniq


def test_sales_customer_crm_follow_up_and_lost_state(client, login):
    login("sales01")
    created = client.post("/api/master-data/customers", json={
        "name": uniq("CRM客户"),
        "contact_name": "联系人",
        "phone": "13800000000",
        "address": "测试地址",
    })
    assert created.status_code == 200, created.text
    customer_id = created.json()["data"]["id"]

    listed = client.get("/api/master-data/customer-crm")
    assert listed.status_code == 200, listed.text
    assert customer_id in {row["id"] for row in listed.json()["data"]["items"]}

    followed = client.post(f"/api/master-data/customers/{customer_id}/follow-ups", json={
        "follow_type": "call",
        "content": "电话沟通采购计划",
        "next_follow_at": "2026-11-10T09:00:00",
    })
    assert followed.status_code == 200, followed.text

    lost = client.post(f"/api/master-data/customers/{customer_id}/crm-status", json={
        "status": "lost",
        "reason": "客户暂时停止采购",
    })
    assert lost.status_code == 200, lost.text
    history = client.get(f"/api/master-data/customers/{customer_id}/crm-history")
    assert history.status_code == 200, history.text
    assert history.json()["data"]["follow_ups"][0]["content"] == "电话沟通采购计划"


def test_finance_can_create_account_with_only_display_name(client, login):
    login("finance01")
    response = client.post("/api/finance/accounts", json={
        "owner_type": "company",
        "account_name": uniq("现金账户"),
    })
    assert response.status_code == 200, response.text
    row = response.json()["data"]
    assert row["bank_name"] is None
    assert row["account_number"] is None
