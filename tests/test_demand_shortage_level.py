"""补货需求"原料状况"红黄绿判定的回归用例。

改造前 api/routers/production.py 用 `max_producible_map.get(row.id)` 取值,
而该 map 的键是 **product_id**(见 _batch_max_producible_map 入参)。
两套 ID 几乎永不相等,于是恒取到默认 0 →
"最大可产"永远显示 0、"原料状况"永远是红,即便原料充足。

而转建任务走 raw_material_shortage_detail 另一条路径,算得出真实缺口、允许转,
于是出现"列表显示原料不足、却能正常转任务"的矛盾。

口径(用户确认):原料足→绿,部分足→黄,完全不足→红。
"""

from helpers import (
    create_bom,
    create_material,
    create_production_product,
    raw_material_inbound,
    warehouse_map,
)


def _demand(client, login, *, product_id: int, shortage: float) -> int:
    login("inventory01")
    resp = client.post(
        "/api/inventory/fulfillment-demands",
        json={"product_id": product_id, "shortage_quantity": shortage, "remark": "pytest"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _demand_row(client, login, demand_id: int) -> dict:
    login("production01")
    resp = client.get("/api/production/fulfillment-demands", params={"page": 1, "page_size": 100})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    rows = data["items"] if isinstance(data, dict) else data
    row = next((r for r in rows if r["id"] == demand_id), None)
    assert row is not None, f"补货需求 {demand_id} 应出现在生产侧列表"
    return row


def _setup(client, login, *, standard_quantity: float, material_stock: float):
    """建生产型产品 + BOM(单耗 standard_quantity) + 铺原料库存。"""
    material_id = create_material(client, login)
    product_id = create_production_product(client, login)
    create_bom(client, login, product_id=product_id, material_id=material_id, standard_quantity=standard_quantity)
    if material_stock > 0:
        raw_warehouse_id = warehouse_map(client, login)["raw"]
        raw_material_inbound(
            client, login, material_id=material_id, quantity=material_stock, warehouse_id=raw_warehouse_id
        )
    return product_id, material_id


def test_sufficient_material_shows_green(client, login):
    """原料充足 → 绿。改造前恒为红,这是本次修复的核心。"""
    # 单耗 2,库存 1000 → 最大可产 500;需求 100 < 500 → 绿
    product_id, _ = _setup(client, login, standard_quantity=2, material_stock=1000)
    demand_id = _demand(client, login, product_id=product_id, shortage=100)

    row = _demand_row(client, login, demand_id)
    assert row["max_producible_quantity"] > 0, (
        f"最大可产不应为 0(改造前的 bug), 实际 {row['max_producible_quantity']}"
    )
    assert row["shortage_level"] == "green", (
        f"原料充足应为绿, 实际 {row['shortage_level']} (最大可产={row['max_producible_quantity']})"
    )


def test_partial_material_shows_yellow(client, login):
    """原料部分不足 → 黄。"""
    # 单耗 2,库存 400 → 最大可产 200;需求 300 > 200 但 >0 → 黄
    product_id, _ = _setup(client, login, standard_quantity=2, material_stock=400)
    demand_id = _demand(client, login, product_id=product_id, shortage=300)

    row = _demand_row(client, login, demand_id)
    assert row["shortage_level"] == "yellow", (
        f"部分不足应为黄, 实际 {row['shortage_level']} (最大可产={row['max_producible_quantity']})"
    )


def test_no_material_shows_red(client, login):
    """原料完全不足 → 红(红色本身是对的,不能因修复而失效)。"""
    product_id, _ = _setup(client, login, standard_quantity=2, material_stock=0)
    demand_id = _demand(client, login, product_id=product_id, shortage=50)

    row = _demand_row(client, login, demand_id)
    assert row["max_producible_quantity"] == 0
    assert row["shortage_level"] == "red", f"完全无原料应为红, 实际 {row['shortage_level']}"


def test_max_producible_respects_bom_ratio(client, login):
    """最大可产必须按 BOM 单耗折算,不能拿产品数直接比原料数。

    这正是"需求600/库存775"看起来矛盾的原因:600 是原料需要量
    (300 个产品 × 单耗 2),不是产品数。
    """
    # 单耗 2,库存 600 → 最大可产 300(而不是 600)
    product_id, _ = _setup(client, login, standard_quantity=2, material_stock=600)
    demand_id = _demand(client, login, product_id=product_id, shortage=300)

    row = _demand_row(client, login, demand_id)
    assert abs(row["max_producible_quantity"] - 300) < 0.01, (
        f"单耗 2、库存 600 时最大可产应为 300, 实际 {row['max_producible_quantity']}"
    )
    # 需求 300 == 可产 300 → 刚好够 → 绿
    assert row["shortage_level"] == "green"


def test_shortage_level_matches_convert_dialog(client, login):
    """列表的"原料状况"必须与转任务弹窗的缺口判定一致。

    改造前两者矛盾:列表说原料不足(红),弹窗算出缺口 0 并允许转任务。
    """
    product_id, _ = _setup(client, login, standard_quantity=2, material_stock=1000)
    shortage = 300
    demand_id = _demand(client, login, product_id=product_id, shortage=shortage)

    row = _demand_row(client, login, demand_id)

    # 转任务弹窗那条路径
    login("production01")
    resp = client.get(
        "/api/production/raw-material-shortage",
        params={"product_id": product_id, "planned_quantity": shortage},
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    dialog_total_shortage = sum(float(r["shortage_quantity"]) for r in detail)

    if dialog_total_shortage <= 0:
        assert row["shortage_level"] == "green", (
            f"弹窗判定无缺口(可正常转任务),列表却是 {row['shortage_level']} —— 两处口径矛盾"
        )
    else:
        assert row["shortage_level"] in {"yellow", "red"}, (
            f"弹窗判定有缺口({dialog_total_shortage}),列表却是 {row['shortage_level']}"
        )
