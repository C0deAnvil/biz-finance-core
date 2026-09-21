from sqlalchemy import select

from database import SessionLocal
from helpers import uniq
from models import Customer, Product, PackagingConfig, SalesOrder, SalesOrderItem, SalesOrderPriceChangeLog


def setup_order(login):
    with SessionLocal() as db:
        customer = Customer(code=uniq("SAMPLE-C"), name=uniq("样品客户"), contact_name="测试", phone="13000000000", address="测试地址")
        product = Product(product_code=uniq("SAMPLE-P"), name="样品产品", price=100, fulfillment_mode="trade")
        packaging = PackagingConfig(entity_type="global", entity_id=0, packaging_name=uniq("免费样品包装"), kg_per_bag=1, unit_price=0)
        db.add_all([customer, product, packaging])
        db.commit()
        payload = {"customer_id": customer.id, "consignee": "测试", "consignee_phone": "13000000000",
                   "delivery_address": "测试地址", "payment_type": "货到付款", "order_type": "sample",
                   "items": [{"product_id": product.id, "packaging_config_id": packaging.id,
                              "quantity": 1, "unit_price": 0, "price_change_remark": "样品赠送"}]}
    login("sales01")
    return payload


def test_zero_price_create_submit_and_higher_price_edit(client, login):
    payload = setup_order(login)
    response = client.post("/api/sales/orders", json=payload)
    assert response.status_code == 200, response.text
    oid = response.json()["data"]["id"]
    with SessionLocal() as db:
        assert db.get(SalesOrder, oid).total_amount == 0
        assert db.scalar(select(SalesOrderItem.unit_price).where(SalesOrderItem.sales_order_id == oid)) is None
    response = client.post(f"/api/sales/orders/{oid}/submit")
    assert response.status_code == 200, response.text
    # 另建草稿，检验改价时从标准价改为高价无需备注。
    payload["items"][0].update(unit_price=100, price_change_remark="")
    response = client.post("/api/sales/orders", json=payload)
    assert response.status_code == 200, response.text
    oid = response.json()["data"]["id"]
    payload["items"][0]["unit_price"] = 120
    response = client.put(f"/api/sales/orders/{oid}", json=payload)
    assert response.status_code == 200, response.text
    response = client.post(f"/api/sales/orders/{oid}/submit")
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        order = db.get(SalesOrder, oid)
        assert order.total_amount == 120
        assert order.price_approval_status == "pending"
        assert db.scalar(select(SalesOrderPriceChangeLog.remark).where(SalesOrderPriceChangeLog.sales_order_id == oid)) == ""
    # 高价也可直接新建，不仅限于编辑。
    response = client.post("/api/sales/orders", json=payload)
    assert response.status_code == 200, response.text


def test_negative_prices_rejected_and_discount_remark_required(client, login):
    payload = setup_order(login)
    for price, expected in [(-1, 422), (0, 409), (90, 409)]:
        payload["items"][0].update(unit_price=price, price_change_remark="")
        response = client.post("/api/sales/orders", json=payload)
        assert response.status_code == 200, response.text
        order_id = response.json()["data"]["id"]
        response = client.post(f"/api/sales/orders/{order_id}/submit")
        assert response.status_code == expected, response.text


def test_sales_product_options_exclude_disabled_products(client, login):
    with SessionLocal() as db:
        enabled = Product(product_code=uniq("ACTIVE-P"), name="启用产品", price=10, is_active=True)
        disabled = Product(product_code=uniq("DISABLED-P"), name="停用产品", price=10, is_active=False)
        db.add_all([enabled, disabled])
        db.commit()
        enabled_id, disabled_id = enabled.id, disabled.id

    login("sales01")
    response = client.get("/api/master-data/products?active_only=true")
    assert response.status_code == 200, response.text
    ids = {row["id"] for row in response.json()["data"]}
    assert enabled_id in ids
    assert disabled_id not in ids

    all_response = client.get("/api/master-data/products")
    assert all_response.status_code == 200, all_response.text
    assert disabled_id in {row["id"] for row in all_response.json()["data"]}
