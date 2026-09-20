"""个人中心：员工可维护自己的资料和密码，但不能修改权限字段。"""


def test_employee_can_update_profile_and_password(client, login):
    login("sales01")
    before = client.get("/api/auth/me")
    assert before.status_code == 200
    original = before.json()["data"]

    response = client.patch("/api/auth/me", json={
        "full_name": "销售测试员",
        "phone": "13900000001",
        "email": "sales-profile@example.com",
        "job_title": "销售专员",
    })
    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["full_name"] == "销售测试员"
    assert updated["phone"] == "13900000001"
    assert updated["role_code"] == original["role_code"]

    password = client.post("/api/auth/change-password", json={
        "current_password": "ChangeMe123!",
        "new_password": "ChangeMe456!",
    })
    assert password.status_code == 200, password.text

    login("sales01", "ChangeMe456!")
    restored = client.patch("/api/auth/me", json={
        "full_name": original["full_name"],
        "phone": original.get("phone"),
        "email": original.get("email"),
        "job_title": original.get("job_title"),
    })
    assert restored.status_code == 200, restored.text
    reset = client.post("/api/auth/change-password", json={
        "current_password": "ChangeMe456!",
        "new_password": "ChangeMe123!",
    })
    assert reset.status_code == 200, reset.text
