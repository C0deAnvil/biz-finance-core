"""核心冒烟测试:登录、角色、基础数据、关键业务规则。"""


def test_login_success(client):
    resp = client.post("/api/auth/login", json={"username": "sysadmin01", "password": "ChangeMe123!"})
    assert resp.status_code == 200
    data = resp.json().get("data") or {}
    assert data.get("user") or data.get("username") or data.get("id") is not None


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "sysadmin01", "password": "WrongPassword123!"})
    assert resp.status_code in (200, 401)
    # 业务错误应带 msg(前端只认 payload.msg)
    payload = resp.json()
    assert "msg" in payload


def test_me_requires_auth(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code in (401, 403)


def test_all_roles_can_login(client, login):
    roles = [
        "sales01",
        "finance01",
        "inventory01",
        "production01",
        "purchase01",
        "quality01",
        "sysadmin01",
        "inventory_super01",
        "quality_super01",
        "finance_super01",
    ]
    for username in roles:
        resp = login(username)
        assert resp.status_code == 200, f"{username} 登录失败"


def test_master_data_warehouses(client, login):
    login("inventory01")
    resp = client.get("/api/master-data/warehouses")
    assert resp.status_code == 200
    data = resp.json().get("data") or []
    assert len(data) >= 2  # 廊坊 + 沧州


def test_products_list(client, login):
    login("sales01")
    resp = client.get("/api/master-data/products")
    assert resp.status_code == 200
    data = resp.json().get("data") or []
    assert isinstance(data, list)


def test_sales_orders_list(client, login):
    login("sales01")
    resp = client.get("/api/sales/orders")
    assert resp.status_code == 200
    payload = resp.json()
    assert "data" in payload


def test_inventory_queues(client, login):
    login("inventory01")
    resp = client.get("/api/inventory/queues")
    assert resp.status_code == 200
    assert "data" in resp.json()


def test_analytics_default_month(client, login):
    """统计接口不传日期应默认本月(后端已实现)。"""
    login("sysadmin01")
    resp = client.get("/api/analytics/metrics")
    assert resp.status_code == 200
    metrics = resp.json().get("data") or []
    assert len(metrics) > 0
    code = metrics[0]["code"]
    detail = client.get(f"/api/analytics/metrics/{code}")
    assert detail.status_code == 200


def test_approvals_history_default_month(client, login):
    """审批历史默认本月。"""
    login("sysadmin01")
    resp = client.get("/api/approvals/history")
    assert resp.status_code == 200
    payload = resp.json().get("data") or {}
    assert "start_date" in payload
    assert "end_date" in payload
    assert "items" in payload