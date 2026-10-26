from uuid import uuid4


def _new_user_payload(username: str, department_id: int, role_id: int) -> dict:
    return {
        "username": username,
        "full_name": "新增用户测试",
        "department_id": department_id,
        "role_id": role_id,
        "job_title": "测试岗位",
        "data_scope": "self",
        "phone": "13800000000",
        "email": "new-user@example.com",
        "inherit_from_user_id": None,
    }


def test_system_admin_can_create_user_and_duplicate_is_reported(client, login):
    login("sysadmin01")
    username = f"test_{uuid4().hex[:12]}"
    departments = client.get("/api/admin/org-units?flat=true").json()["data"]
    roles = client.get("/api/master-data/roles").json()["data"]
    payload = _new_user_payload(username, departments[0]["id"], roles[0]["id"])

    created = client.post("/api/admin/users", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["data"]["id"] > 0
    assert created.json()["data"]["default_password"]

    duplicate = client.post("/api/admin/users", json=payload)
    assert duplicate.status_code == 409
    assert "用户名已存在" in duplicate.json()["msg"]


def test_create_user_rejects_blank_required_fields(client, login):
    login("sysadmin01")
    departments = client.get("/api/admin/org-units?flat=true").json()["data"]
    roles = client.get("/api/master-data/roles").json()["data"]
    payload = _new_user_payload(f"test_{uuid4().hex[:12]}", departments[0]["id"], roles[0]["id"])
    payload["full_name"] = "   "

    response = client.post("/api/admin/users", json=payload)
    assert response.status_code == 422
    assert "姓名不能为空" in response.json()["msg"]
