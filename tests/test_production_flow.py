"""生产流程测试:补货需求 → 生产任务 → 领料审批/出库 → 完工 → 质检 → 准许入库 → 成品入库。

覆盖接口:
- POST /api/inventory/fulfillment-demands(手动补货需求,按产品类型路由)
- POST /api/production/tasks、GET /tasks/{id}
- POST /api/production/tasks/{id}/material-request
- POST /api/production/special-requests/{usage_id}/approve(库存领料审批)
- POST /api/inventory/operation-requests/{id}/complete(原料出库)
- POST /api/production/tasks/{id}/complete(完工,自动生成质检)
- POST /api/quality/inspections、POST /api/quality/tasks/{id}/allow-inbound
- POST /api/production/tasks/{id}/inbound(提交入库作业)
- POST /api/inventory/operation-requests/{id}/approve|complete(成品入库)
"""

from helpers import (
    allow_quality_inbound,
    complete_operation_request,
    create_bom,
    create_material,
    create_production_product,
    find_operation_request,
    find_quality_task_by_batch,
    raw_material_inbound,
    request_operation_items,
    stock_of,
    submit_inspection,
    uniq,
    warehouse_map,
)


def _create_production_setup(client, login, *, planned_quantity: float = 10, material_usage_qty: float = 20):
    """创建原料/成品/BOM/原料库存,返回 (product_id, material_id, warehouses)。"""
    warehouses = warehouse_map(client, login)
    material_id = create_material(client, login)
    product_id = create_production_product(client, login)
    create_bom(client, login, product_id=product_id, material_id=material_id, standard_quantity=2)
    raw_material_inbound(client, login, material_id=material_id, quantity=material_usage_qty * 10, warehouse_id=warehouses["raw"])
    return product_id, material_id, warehouses


def _create_demand(client, login, product_id: int, shortage: float) -> int:
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": shortage, "remark": "pytest 补货需求"},
    )
    assert resp.status_code == 200, f"创建补货需求失败: {resp.text}"
    return resp.json()["data"]["id"]


def _find_demand(client, login, demand_id: int) -> dict:
    """补货需求只有列表接口,按 id 过滤。"""
    login("inventory01")
    resp = client.get("/api/inventory/fulfillment-demands")
    assert resp.status_code == 200, f"补货需求列表失败: {resp.text}"
    for row in resp.json().get("data") or []:
        if int(row.get("id") or 0) == demand_id:
            return row
    raise AssertionError(f"未找到补货需求 #{demand_id}")


def _create_task(client, login, *, product_id: int, demand_id: int | None = None, planned_quantity: float) -> int:
    login("production01")
    payload = {
        "product_id": product_id,
        "planned_quantity": planned_quantity,
        "warehouse_id": None,
        "notes": "pytest 生产任务",
    }
    if demand_id:
        payload["fulfillment_demand_id"] = demand_id
    resp = client.post("/api/production/tasks", json=payload)
    assert resp.status_code == 200, f"创建生产任务失败: {resp.text}"
    return resp.json()["data"]["id"]


def _task_detail(client, login, task_id: int) -> dict:
    login("production01")
    resp = client.get(f"/api/production/tasks/{task_id}")
    assert resp.status_code == 200, f"查询生产任务失败: {resp.text}"
    return resp.json().get("data") or {}


def _submit_material_request(client, login, task_id: int, material_id: int, quantity: float) -> None:
    login("production01")
    resp = client.post(
        f"/api/production/tasks/{task_id}/material-request",
        json={"material_usages": [{"material_id": material_id, "requested_quantity": quantity, "reason": "pytest 领料"}]},
    )
    assert resp.status_code == 200, f"提交领料申请失败: {resp.text}"


def _approve_material_usage(client, login, usage_id: int) -> None:
    login("inventory01")
    resp = client.post(
        f"/api/production/special-requests/{usage_id}/approve",
        json={"decision": "approved", "remark": "pytest 库存审批"},
    )
    assert resp.status_code == 200, f"领料审批失败: {resp.text}"


def _complete_task(client, login, task_id: int, produced: float) -> dict:
    login("production01")
    resp = client.post(
        f"/api/production/tasks/{task_id}/complete",
        json={"produced_quantity": produced, "scrap_quantity": 0, "requires_quality": True, "preliminary_acceptance_note": "pytest 完工初验"},
    )
    assert resp.status_code == 200, f"完工登记失败: {resp.text}"
    return resp.json().get("data") or {}


def _production_material_issue_full(client, login, *, task_id: int) -> None:
    """走完领料流程:提交领料 → 库存审批 → 库管执行原料出库。"""
    detail = _task_detail(client, login, task_id)
    usages = detail.get("usages") or []
    assert usages, f"任务应自动生成 BOM 领料明细: {detail}"
    usage = usages[0]
    _submit_material_request(client, login, task_id, usage["material_id"], float(usage["planned_quantity"]))
    _approve_material_usage(client, login, usage["id"])

    # 审批后触发原料出库作业(production_material_issue)
    op = find_operation_request(client, login, category="production_material_issue", source_id=task_id, statuses={"pending"})
    complete_operation_request(client, login, request_id=op["id"], items=request_operation_items(op))

    detail = _task_detail(client, login, task_id)
    assert detail["status"] == "material_issued", f"出库后任务应为已领料,实际: {detail['status']}"


def test_manual_demand_routes_production(client, login):
    """生产型产品手动补货需求应路由到生产(fulfillment_mode=production, 状态 pending)。"""
    product_id, _, _ = _create_production_setup(client, login)
    demand_id = _create_demand(client, login, product_id, shortage=10)
    demand = _find_demand(client, login, demand_id)
    assert demand.get("fulfillment_mode") == "production", demand
    assert demand.get("status") == "pending", demand


def test_production_task_full_chain(client, login):
    """生产全链:需求 → 任务 → 领料 → 完工 → 质检合格 → 准许入库 → 成品入库 → 库存增加。"""
    product_id, material_id, warehouses = _create_production_setup(client, login, planned_quantity=10)
    demand_id = _create_demand(client, login, product_id, shortage=10)

    task_id = _create_task(client, login, product_id=product_id, demand_id=demand_id, planned_quantity=10)
    detail = _task_detail(client, login, task_id)
    assert detail["status"] == "pending_material", detail
    assert detail.get("task_no"), detail

    # 需求转任务后剩余为 0 → 需求应转已处理
    demand = _find_demand(client, login, demand_id)
    assert demand.get("status") == "converted"

    _production_material_issue_full(client, login, task_id=task_id)

    _complete_task(client, login, task_id, produced=10)
    detail = _task_detail(client, login, task_id)
    assert detail["status"] == "waiting_quality", detail
    assert detail.get("batch_no"), "完工后应生成批次号"

    # 质检合格(生产质检任务批次号 = 任务批次号)
    quality_task = find_quality_task_by_batch(client, login, batch_no=detail["batch_no"])
    submit_inspection(client, login, quality_task_id=quality_task["id"], sequence_no=1, grade="A", quantity=10, passed=True)
    allow_quality_inbound(client, login, quality_task_id=quality_task["id"])

    # 提交入库 → 库管直接入库(入库不再需要经理审批)
    login("production01")
    resp = client.post(f"/api/production/tasks/{task_id}/inbound", json={"remark": "pytest 成品入库"})
    assert resp.status_code == 200, f"提交成品入库失败: {resp.text}"

    op = find_operation_request(client, login, category="production_quality_inbound", source_id=task_id, statuses={"pending"})
    complete_operation_request(client, login, request_id=op["id"], items=request_operation_items(op))

    detail = _task_detail(client, login, task_id)
    assert detail["status"] == "completed", f"入库后任务应为已完成,实际: {detail['status']}"
    stock = stock_of(client, login, warehouse_id=warehouses["finished"], product_id=product_id)
    assert stock >= 10, f"入库后成品库存应 >= 10,实际: {stock}"