from sqlalchemy import select

from database import SessionLocal
from helpers import create_bom, create_material, create_production_product, uniq
from models import SalesOrder, User
from services.sales_service import _create_fulfillment_demands


def test_sales_demand_requires_explicit_material_confirmation(client, login):
    material_id = create_material(client, login)
    product_id = create_production_product(client, login)
    create_bom(client, login, product_id=product_id, material_id=material_id, standard_quantity=2)
    login("sales01")
    response = client.post("/api/master-data/customers", json={
        "name": uniq("原料确认客户"), "contact_name": "测试", "phone": "13900000000", "address": "测试",
    })
    assert response.status_code == 200, response.text
    customer_id = response.json()["data"]["id"]
    response = client.post("/api/sales/orders", json={
        "customer_id": customer_id, "consignee": "测试", "consignee_phone": "13900000000",
        "delivery_address": "测试", "payment_type": "货到付款", "order_type": "normal",
        "items": [{"product_id": product_id, "quantity": 40, "unit_price": 200}],
    })
    assert response.status_code == 200, response.text
    order_id = response.json()["data"]["id"]
    with SessionLocal() as db:
        order = db.get(SalesOrder, order_id)
        user = db.scalar(select(User).where(User.username == "sales01"))
        demands = _create_fulfillment_demands(
            db, order=order, current_user=user,
        )
        db.commit()
        demand_id = demands[0]["id"]
        # 重复处理同一成品需求也不能自动提交原料采购。
        _create_fulfillment_demands(db, order=order, current_user=user)
        db.commit()
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
