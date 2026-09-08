"""分批生产的补货需求记账回归用例。

改造前的缺陷:
1. shortage_quantity 被原地递减,原始需求量丢失,"需求100/已产10/待产90"无法还原(E)。
2. ProductionTask.fulfillment_demand_id 从未被赋值,生产型补货需求的进度恒为空(F)。
3. 提原料补货需求时无条件把状态覆盖为 pending_material,
   即使成品缺口已全部转成任务(已 converted)也会被打回(G)。
"""

from helpers import (
    create_bom,
    create_material,
    create_production_product,
    raw_material_inbound,
    warehouse_map,
)


def _setup_production_product(client, login):
    """建原料 + 生产型成品 + BOM,并铺足原料库存(避免原料不足干扰本用例)。"""
    warehouses = warehouse_map(client, login)
    material_id = create_material(client, login)
    product_id = create_production_product(client, login)
    create_bom(client, login, product_id=product_id, material_id=material_id, standard_quantity=2)
    raw_material_inbound(client, login, material_id=material_id, quantity=1000, warehouse_id=warehouses["raw"])
    return product_id


def _create_demand(client, login, *, product_id: int, shortage: float) -> int:
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": shortage, "remark": "pytest 分批补货"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _find_demand(client, login, demand_id: int) -> dict:
    login("inventory01")
    resp = client.get("/api/inventory/fulfillment-demands")
    assert resp.status_code == 200, resp.text
    for row in resp.json().get("data") or []:
        if int(row.get("id") or 0) == demand_id:
            return row
    raise AssertionError(f"未找到补货需求 #{demand_id}")


def _create_task(client, login, *, product_id: int, demand_id: int, planned_quantity: float) -> int:
    login("production01")
    resp = client.post(
        "/api/production/tasks",
        json={
            "product_id": product_id,
            "planned_quantity": planned_quantity,
            "warehouse_id": None,
            "notes": "pytest 分批生产",
            "fulfillment_demand_id": demand_id,
        },
    )
    assert resp.status_code == 200, f"创建生产任务失败: {resp.text}"
    return resp.json()["data"]["id"]


def test_partial_production_keeps_demand_open_with_progress(client, login):
    """需求 100 只安排 10 → 需求保持未完成,且待补/已补记账正确。"""
    product_id = _setup_production_product(client, login)
    demand_id = _create_demand(client, login, product_id=product_id, shortage=100)

    row = _find_demand(client, login, demand_id)
    assert float(row.get("required_quantity") or 0) == 100.0, (
        f"原始需求量应为 100, 实际 {row.get('required_quantity')}"
    )
    assert float(row.get("fulfilled_quantity") or 0) == 0.0, "尚未补货,已补量应为 0"

    # 第一批:只生产 10
    _create_task(client, login, product_id=product_id, demand_id=demand_id, planned_quantity=10)

    row = _find_demand(client, login, demand_id)
    assert row["status"] != "converted", f"仅补 10/100,需求不应完成: {row['status']}"
    assert float(row["required_quantity"]) == 100.0, "原始需求量必须保持 100(不能被递减覆盖)"
    assert float(row["fulfilled_quantity"]) == 10.0, f"已补量应为 10, 实际 {row['fulfilled_quantity']}"
    assert float(row["pending_quantity"]) == 90.0, f"待补量应为 90, 实际 {row['pending_quantity']}"
    # 生产型需求的进度必须能推导出来(依赖 fulfillment_demand_id 已回填)
    assert row.get("related_no"), "应能关联到生产任务号(fulfillment_demand_id 已回填)"

    # 第二批:补齐剩余 90
    _create_task(client, login, product_id=product_id, demand_id=demand_id, planned_quantity=90)

    row = _find_demand(client, login, demand_id)
    assert float(row["fulfilled_quantity"]) == 100.0, "累计已补应为 100"
    assert float(row["pending_quantity"]) == 0.0, "待补量应归零"
    assert row["status"] == "converted", f"全部补齐后需求应完成, 实际 {row['status']}"


def test_demand_progress_links_production_task(client, login):
    """生产型补货需求应能实时反映所关联生产任务的状态(改造前恒为空)。"""
    product_id = _setup_production_product(client, login)
    demand_id = _create_demand(client, login, product_id=product_id, shortage=20)
    _create_task(client, login, product_id=product_id, demand_id=demand_id, planned_quantity=20)

    row = _find_demand(client, login, demand_id)
    assert row.get("related_no"), "补货需求应关联到生产任务号"
    assert row.get("progress_status"), "应能推导出进度状态"
    # 任务刚建处于待领料,进度应体现为原料/生产环节而非"待处理"
    assert row["progress_status"] != "pending", (
        f"已转生产任务后进度不应仍为待处理, 实际 {row['progress_status']}"
    )
