"""质检指定质检员的回归用例。

需求:申请质检时需选择一个质检员,由该指定质检员或超级质检员处理。
改造前 QualityTask 没有指定质检员字段,任何质检员都能录入任何任务的检验结果。
"""

from helpers import create_production_product, uniq, warehouse_map


SECOND_INSPECTOR = "quality02"


def _user_id(username: str) -> int:
    """直接查库取用户 id(测试环境无对外的按用户名查询接口)。"""
    from database import SessionLocal
    from models import User
    from sqlalchemy import select

    db = SessionLocal()
    try:
        row = db.execute(select(User).where(User.username == username)).scalar_one()
        return row.id
    finally:
        db.close()


def _ensure_second_inspector() -> int:
    """确保存在第二个普通质检员(seed 只有一个,越权场景需要两个)。"""
    from database import SessionLocal
    from models import Role, User
    from security import hash_password
    from sqlalchemy import select

    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.username == SECOND_INSPECTOR)).scalar_one_or_none()
        if existing is not None:
            return existing.id
        role = db.execute(select(Role).where(Role.code == "quality")).scalar_one()
        template = db.execute(select(User).where(User.username == "quality01")).scalar_one()
        user = User(
            username=SECOND_INSPECTOR,
            hashed_password=hash_password("ChangeMe123!"),
            full_name="质检员2",
            role_id=role.id,
            department_id=template.department_id,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


def _create_manual_task(client, login, *, product_id: int, warehouse_id: int, inspector_id: int | None) -> dict:
    """手工建质检任务(绕开整条生产链,聚焦指派与鉴权)。"""
    login("quality01")
    payload = {
        "product_id": product_id,
        "quantity": 10,
        "batch_no": uniq("PZ"),
        "required_tests": 1,
        "remark": "pytest 指派质检",
        "target_warehouse_id": warehouse_id,
    }
    if inspector_id is not None:
        payload["assigned_inspector_id"] = inspector_id
    resp = client.post("/api/quality/tasks/manual", json=payload)
    assert resp.status_code == 200, f"创建质检任务失败: {resp.text}"
    return resp.json()["data"]


def _find_task(client, login, username: str, quality_no: str) -> dict | None:
    login(username)
    resp = client.get("/api/quality/tasks")
    assert resp.status_code == 200, resp.text
    for row in resp.json().get("data") or []:
        if row.get("quality_no") == quality_no:
            return row
    return None


def _submit_inspection(client, login, username: str, *, quality_task_id: int):
    login(username)
    return client.post(
        "/api/quality/inspections",
        json={
            "quality_task_id": quality_task_id,
            "sequence_no": 1,
            "result_text": "pytest 检验",
            "grade": "A",
            "is_passed": True,
            "inspected_quantity": 10,
            "sample_consumption_qty": 0,
        },
    )


def test_assigned_inspector_is_recorded(client, login):
    """申请质检时指定的质检员应被记录在任务上。"""
    warehouses = warehouse_map(client, login)
    product_id = create_production_product(client, login)
    inspector_id = _user_id("quality01")

    data = _create_manual_task(
        client, login, product_id=product_id, warehouse_id=warehouses["finished"], inspector_id=inspector_id
    )
    quality_no = data.get("quality_no")
    assert quality_no, f"未返回质检单号: {data}"

    row = _find_task(client, login, "quality01", quality_no)
    assert row is not None, "指定质检员应能看到派给自己的任务"
    assert row.get("assigned_inspector_id") == inspector_id, (
        f"应记录指定质检员 {inspector_id}, 实际 {row.get('assigned_inspector_id')}"
    )


def test_unassigned_task_stays_open_to_all_inspectors(client, login):
    """不指定质检员时任务保持"未锁定":任何质检员都能领取并录入。

    刻意不做自动分配——指定意味着独占(他人 403),
    若未指定也自动派人,等于凭空把谁都能领的任务锁死在一个人身上。
    """
    warehouses = warehouse_map(client, login)
    product_id = create_production_product(client, login)

    data = _create_manual_task(
        client, login, product_id=product_id, warehouse_id=warehouses["finished"], inspector_id=None
    )
    row = _find_task(client, login, "quality_super01", data["quality_no"])
    assert row is not None, "超级质检员应能看到所有任务"
    assert not row.get("assigned_inspector_id"), "未指定时不应自动锁定给某人"

    # 未指定的任务,任何普通质检员都能录入(不被 403 拦)
    resp = _submit_inspection(client, login, "quality01", quality_task_id=data["id"])
    assert resp.status_code == 200, f"未指定的任务应允许质检员领取, 实际 {resp.status_code}: {resp.text}"


def test_other_inspector_cannot_submit_inspection(client, login):
    """非指定质检员不能越权录入检验结果;超级质检员可以代为处理。"""
    warehouses = warehouse_map(client, login)
    product_id = create_production_product(client, login)
    assigned_id = _user_id("quality01")

    data = _create_manual_task(
        client, login, product_id=product_id, warehouse_id=warehouses["finished"], inspector_id=assigned_id
    )
    task_id = data["id"]

    # 另一个普通质检员越权 → 403
    _ensure_second_inspector()
    resp = _submit_inspection(client, login, SECOND_INSPECTOR, quality_task_id=task_id)
    assert resp.status_code == 403, f"非指定质检员应被拒绝, 实际 {resp.status_code}: {resp.text}"

    # 超级质检员可代为处理
    resp = _submit_inspection(client, login, "quality_super01", quality_task_id=task_id)
    assert resp.status_code == 200, f"超级质检员应可代处理, 实际 {resp.status_code}: {resp.text}"


def test_assigned_inspector_can_submit_inspection(client, login):
    """指定质检员本人可以正常录入检验结果。"""
    warehouses = warehouse_map(client, login)
    product_id = create_production_product(client, login)
    assigned_id = _user_id("quality01")

    data = _create_manual_task(
        client, login, product_id=product_id, warehouse_id=warehouses["finished"], inspector_id=assigned_id
    )
    resp = _submit_inspection(client, login, "quality01", quality_task_id=data["id"])
    assert resp.status_code == 200, f"指定质检员应可录入, 实际 {resp.status_code}: {resp.text}"


def test_invalid_inspector_is_rejected(client, login):
    """指定一个非质检员(如销售)应被拒绝,避免把质检派给无资质的人。"""
    warehouses = warehouse_map(client, login)
    product_id = create_production_product(client, login)
    sales_id = _user_id("sales01")

    login("quality01")
    resp = client.post(
        "/api/quality/tasks/manual",
        json={
            "product_id": product_id,
            "quantity": 10,
            "batch_no": uniq("PZ"),
            "required_tests": 1,
            "target_warehouse_id": warehouses["finished"],
            "assigned_inspector_id": sales_id,
        },
    )
    assert resp.status_code == 409, f"指定非质检员应被拒绝, 实际 {resp.status_code}: {resp.text}"
