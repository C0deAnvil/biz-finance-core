"""质检流程测试:任务管理、手动质检、合格/不合格判定、返工、权限。

覆盖接口:
- GET /api/quality/tasks、GET /api/quality/tasks/{id}
- POST /api/quality/tasks/manual
- POST /api/quality/inspections(多轮次判定)
- POST /api/quality/tasks/{id}/rework
- 非质检角色录入结果的权限拦截
"""

from helpers import (
    create_production_product,
    submit_inspection,
    uniq,
)


def _create_manual_quality_task(client, login, *, product_id: int | None = None) -> dict:
    login("quality01")
    payload = {
        "product_id": product_id,
        "quantity": 10,
        "batch_no": uniq("ZJZ"),
        "required_tests": 1,
        "remark": "pytest 手动质检",
    }
    if product_id is None:
        payload.pop("product_id")
    resp = client.post("/api/quality/tasks/manual", json=payload)
    assert resp.status_code == 200, f"创建手动质检任务失败: {resp.text}"
    task_id = resp.json().get("data", {}).get("id")
    # 创建接口只返回 id/quality_no,其余字段从详情取
    login("quality01")
    detail_resp = client.get(f"/api/quality/tasks/{task_id}")
    assert detail_resp.status_code == 200, f"查询质检任务失败: {detail_resp.text}"
    return detail_resp.json().get("data") or {}


def test_quality_tasks_list_and_detail(client, login):
    """质检任务列表/详情可访问且含前端依赖字段。"""
    login("quality01")
    resp = client.get("/api/quality/tasks")
    assert resp.status_code == 200
    rows = resp.json().get("data") or []
    assert isinstance(rows, list)
    if rows:
        first = rows[0]
        for key in ("id", "quality_no", "status", "status_label", "source_type", "source_label", "batch_no", "can_record_inspection"):
            assert key in first, f"质检任务列表缺少字段 {key}"

    # 详情接口对存在的任务应可访问
    for row in rows[:1]:
        login("quality01")
        resp = client.get(f"/api/quality/tasks/{row['id']}")
        assert resp.status_code == 200
        assert resp.json().get("data", {}).get("id") == row["id"]


def test_manual_quality_task_pass_flow(client, login):
    """手动质检:创建 → 录入合格结果 → 达到轮次后 ready_for_inbound。"""
    product_id = create_production_product(client, login)
    task = _create_manual_quality_task(client, login, product_id=product_id)
    task_id = task["id"]
    assert task.get("status") in {"pending", "inspecting"}, task

    result = submit_inspection(client, login, quality_task_id=task_id, sequence_no=1, grade="A", quantity=10, passed=True)
    assert result.get("status") == "ready_for_inbound", f"合格结果应达到准许入库,实际: {result}"

    login("quality01")
    resp = client.get(f"/api/quality/tasks/{task_id}")
    assert resp.status_code == 200
    detail = resp.json().get("data") or {}
    assert detail["status"] == "ready_for_inbound", detail


def test_quality_fail_grade_sets_pending_disposition(client, login):
    """不合格样品/记录应使质检任务进入待处理,为返工/报废留出空间。"""
    product_id = create_production_product(client, login)
    task = _create_manual_quality_task(client, login, product_id=product_id)
    task_id = task["id"]

    result = submit_inspection(client, login, quality_task_id=task_id, sequence_no=1, grade="不合格", quantity=10, passed=False)
    assert result.get("status") == "pending_disposition", f"不合格记录应进入待处理,实际: {result}"

    # 待处理任务可以发起返工
    login("quality01")
    resp = client.post(f"/api/quality/tasks/{task_id}/rework", json={"quantity": 5, "reason": "pytest 需要返工"})
    assert resp.status_code == 200, f"发起返工失败: {resp.text}"
    data = resp.json().get("data") or {}
    assert data.get("rework_task_id") and data.get("rework_task_no"), data


def test_quality_rework_disallowed_when_passed(client, login):
    """已合格(ready_for_inbound)的质检任务不允许返工。"""
    product_id = create_production_product(client, login)
    task = _create_manual_quality_task(client, login, product_id=product_id)
    task_id = task["id"]
    submit_inspection(client, login, quality_task_id=task_id, sequence_no=1, grade="合格", quantity=10, passed=True)

    login("quality01")
    resp = client.post(f"/api/quality/tasks/{task_id}/rework", json={"quantity": 5, "reason": "pytest 重复返工"})
    assert resp.status_code in (409, 422), f"合格的质检任务不应允许返工,实际: {resp.status_code} {resp.text}"


def test_quality_duplicate_sequence_rejected(client, login):
    """同一轮次(sequence_no)重复录入应被拒绝。"""
    product_id = create_production_product(client, login)
    task = _create_manual_quality_task(client, login, product_id=product_id)
    task_id = task["id"]
    submit_inspection(client, login, quality_task_id=task_id, sequence_no=1, grade="A", quantity=10, passed=True)

    login("quality01")
    resp = client.post(
        "/api/quality/inspections",
        json={
            "quality_task_id": task_id,
            "sequence_no": 1,
            "result_text": "pytest 重复记录",
            "grade": "A",
            "is_passed": True,
            "inspected_quantity": 10,
            "sample_consumption_qty": 0,
        },
    )
    assert resp.status_code == 409, f"重复轮次应被拒绝,实际: {resp.status_code} {resp.text}"


def test_quality_inspection_permission(client, login):
    """非质检角色(采购)不能录入质检结果。"""
    product_id = create_production_product(client, login)
    task = _create_manual_quality_task(client, login, product_id=product_id)

    login("purchase01")
    resp = client.post(
        "/api/quality/inspections",
        json={
            "quality_task_id": task["id"],
            "sequence_no": 1,
            "result_text": "pytest 越权记录",
            "grade": "A",
            "is_passed": True,
            "inspected_quantity": 10,
            "sample_consumption_qty": 0,
        },
    )
    assert resp.status_code in (401, 403), f"采购不应有质检录入权限,实际: {resp.status_code} {resp.text}"