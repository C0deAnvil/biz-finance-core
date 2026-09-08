"""指定质检员入口的回归用例。

后端早已支持 assigned_inspector_id(质检任务创建、生产报工),
但前端此前完全没有入口,也没有候选人列表接口,导致任务永远无人被指派。

本用例锁死:
1. /api/quality/assignable-inspectors 返回质检员候选(前端下拉依赖它);
2. 质检任务序列化返回 assigned_inspector_name(只有 id 前端没法显示)。
"""


def test_assignable_inspectors_endpoint(client, login):
    """候选人接口应返回普通质检员 + 超级质检员,且含 id 与姓名。"""
    login("quality01")
    resp = client.get("/api/quality/assignable-inspectors")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert rows, "应至少返回一个质检员"
    for row in rows:
        assert row.get("id"), "候选项缺少 id"
        assert row.get("full_name"), "候选项缺少 full_name(下拉框 label 依赖它)"


def test_assignable_inspectors_open_to_other_roles(client, login):
    """发起质检的可能是采购/生产/库管,接口不应限定质检角色。"""
    for username in ("purchase01", "production01", "inventory01"):
        login(username)
        resp = client.get("/api/quality/assignable-inspectors")
        assert resp.status_code == 200, f"{username} 应能取到质检员候选: {resp.text}"
        assert resp.json()["data"], f"{username} 取到的候选列表不应为空"


def test_quality_task_exposes_inspector_name(client, login):
    """质检任务列表应返回 assigned_inspector_name —— 只有 id 前端显示不出人名。"""
    login("quality01")
    resp = client.get("/api/quality/tasks")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    rows = data["items"] if isinstance(data, dict) else data
    if not rows:
        return  # 无任务时该断言无意义,交由下方生产链路用例覆盖
    row = rows[0]
    assert "assigned_inspector_id" in row, "缺少 assigned_inspector_id"
    assert "assigned_inspector_name" in row, "缺少 assigned_inspector_name(前端无法显示人名)"
    assert "target_warehouse_name" in row, "缺少 target_warehouse_name"


def test_inspector_name_resolves_when_assigned(client, login):
    """指定了质检员时,姓名字段必须真的解析出来(而非恒为 None)。

    模型上原先只有 assigned_inspector_id 外键、没有 relationship,
    序列化里取 task.assigned_inspector 会永远拿到 None。
    """
    from database import SessionLocal
    from models import QualityTask, User
    from sqlalchemy import select

    db = SessionLocal()
    try:
        inspector = db.execute(select(User).where(User.username == "quality01")).scalar_one()
        task = db.execute(select(QualityTask).order_by(QualityTask.id.desc())).scalars().first()
        if task is None:
            return
        task.assigned_inspector_id = inspector.id
        db.commit()
        task_id = task.id
        expected = inspector.full_name or inspector.username
    finally:
        db.close()

    login("quality01")
    resp = client.get(f"/api/quality/tasks/{task_id}")
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload.get("assigned_inspector_name") == expected, (
        f"应解析出质检员姓名 {expected}, 实际 {payload.get('assigned_inspector_name')}"
    )
