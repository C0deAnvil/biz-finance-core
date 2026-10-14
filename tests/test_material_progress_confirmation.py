from decimal import Decimal

from helpers import create_bom, create_material, create_production_product
from services.material_replenishment_confirmation import confirmed_material_quantities


def test_production_demand_requires_explicit_material_confirmation(client, login):
    material_id = create_material(client, login)
    product_id = create_production_product(client, login)
    create_bom(client, login, product_id=product_id, material_id=material_id, standard_quantity=2)
    login("inventory01")
    response = client.post("/api/inventory/fulfillment-demands", json={
        "product_id": product_id, "shortage_quantity": 40,
    })
    assert response.status_code == 200, response.text
    demand_id = response.json()["data"]["id"]
    login("production01")
    url = f"/api/production/fulfillment-demands/{demand_id}"
    response = client.get(f"{url}/material-progress")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["can_produce"] is False
    item = next(item for item in data["items"] if item["material_id"] == material_id)
    assert item["required_quantity"] == 80
    assert item["submitted_quantity"] == 0
    assert item["progress_label"] == "待提交补货"
    response = client.post(f"{url}/raw-material-demands", json={
        "items": [{"material_id": material_id, "shortage_quantity": 80}],
    })
    assert response.status_code == 200, response.text
    data = client.get(f"{url}/material-progress").json()["data"]
    assert data["items"][0]["submitted_quantity"] == 80
    assert data["can_produce"] is False


def test_confirmation_origin_and_partial_confirmation():
    payload = {"items": [{"material_id": 1, "shortage_quantity": 4427.12}]}
    assert confirmed_material_quantities("客户订单 XS 原料缺口", payload) == {}
    assert confirmed_material_quantities("生产任务 待创建 原料不足", payload) == {1: Decimal("4427.12")}
    payload["confirmed_material_quantities"] = {"2": 9000}
    assert confirmed_material_quantities("生产任务 待创建 原料不足", payload) == {2: Decimal("9000")}
