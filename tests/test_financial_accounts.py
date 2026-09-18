import pytest
from sqlalchemy import select

from database import SessionLocal
from helpers import uniq
from models import (Attachment, Customer, Supplier, User, SalesOrder, SalesReceiptRequest,
                    PurchaseOrder, PurchasePaymentRequest, ReceivableRecord, PayableRecord,
                    Product, Inventory, Warehouse)


def account(client, login, owner_type="company", **owner):
    login("finance01")
    response = client.post("/api/finance/accounts", json={
        "owner_type": owner_type, "account_name": uniq("账户"), "bank_name": "测试银行",
        "account_number": uniq("TEST"), **owner,
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def documents():
    with SessionLocal() as db:
        sales = db.scalar(select(User).where(User.username == "sales01"))
        buyer = db.scalar(select(User).where(User.username == "purchase01"))
        customer = Customer(code=uniq("AC"), name=uniq("账户客户"), contact_name="测试", phone="1", address="测试", owner_user_id=sales.id)
        supplier = Supplier(code=uniq("AS"), name=uniq("账户供应商"), contact_name="测试", phone="1", address="测试")
        db.add_all([customer, supplier]); db.flush()
        order = SalesOrder(order_no=uniq("XS"), customer_id=customer.id, sales_user_id=sales.id, operator_user_id=sales.id,
                           consignee="测试", consignee_phone="1", delivery_address="测试", payment_type="货到付款/分期付款", total_amount=100, status="pending_confirmation")
        purchase = PurchaseOrder(order_no=uniq("CG"), supplier_id=supplier.id, purchaser_user_id=buyer.id,
                                 operator_user_id=buyer.id, payment_type="银行转账", total_amount=100, status="purchasing")
        attachment = Attachment(business_type="test", business_id=0, attachment_type="回单", original_name="test.png",
                                stored_name=uniq("test.png"), relative_path="test-only.png", uploaded_by_id=sales.id)
        db.add_all([order, purchase, attachment]); db.commit()
        return customer.id, supplier.id, order.id, purchase.id, attachment.id


def test_account_permissions_owner_validation_and_disabled_accounts(client, login):
    customer_id, supplier_id, order_id, _, _ = documents()
    company = account(client, login)
    other = account(client, login, "supplier", supplier_id=supplier_id)
    customer_account = account(client, login, "customer", customer_id=customer_id)
    login("sales01")
    assert client.post("/api/finance/accounts", json={"owner_type": "company", "account_name": "越权", "bank_name": "银行", "account_number": "1"}).status_code == 403
    listed = client.get("/api/finance/accounts").json()["data"]
    assert company in {a["id"] for a in listed} and customer_account in {a["id"] for a in listed}
    assert other not in {a["id"] for a in listed}
    payload = {"sales_order_id": order_id, "requested_amount": 10, "payment_stage": "cod_full"}
    endpoint = "/api/sales/settlements/receipt-requests"
    assert client.post(endpoint, json=payload).status_code == 409
    assert client.post(endpoint, json={**payload, "company_account_id": company, "counterparty_account_id": other}).status_code == 409
    login("finance01")
    assert client.put(f"/api/finance/accounts/{company}", json={"is_active": False}).status_code == 200
    login("sales01")
    assert client.post(endpoint, json={**payload, "company_account_id": company}).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(SalesReceiptRequest.id).where(SalesReceiptRequest.sales_order_id == order_id)) is None


@pytest.mark.parametrize("merged", [False, True])
def test_sales_request_and_finance_posting_preserve_accounts(client, login, merged):
    customer_id, _, order_id, _, attachment_id = documents()
    company = account(client, login)
    customer_account = account(client, login, "customer", customer_id=customer_id)
    accounts = {"company_account_id": company, "counterparty_account_id": customer_account}
    login("sales01")
    payload = {**accounts, "payment_stage": "cod_full", "requested_amount": 10, "sales_order_id": order_id}
    endpoint = "/api/sales/settlements/receipt-requests"
    if merged:
        payload = {**accounts, "payment_stage": "mixed", "customer_id": customer_id, "items": [{"sales_order_id": order_id, "requested_amount": 10}]}
        endpoint += "/merged"
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        request = db.scalar(select(SalesReceiptRequest).where(SalesReceiptRequest.sales_order_id == order_id))
        assert request.company_account_id == company and request.counterparty_account_id == customer_account
        request_id = request.id
    login("finance01")
    listed = client.get("/api/finance/receivables").json()["data"]["receipt_requests"]
    row = next(r for r in listed if r["id"] == request_id)
    assert row["company_account_id"] == company and row["customer_id"] == customer_id
    register = {**accounts, "bank_receipt_attachment_id": attachment_id, "actual_amount": 10, "sales_receipt_request_id": request_id}
    endpoint = f"/api/finance/receivables/receipt-requests/{request_id}/register"
    if merged:
        register["items"] = [{"sales_receipt_request_id": request_id, "actual_amount": 10}]
        endpoint = "/api/finance/receivables/receipt-requests/merged-register"
    response = client.post(endpoint, json=register)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        record = db.scalar(select(ReceivableRecord).where(ReceivableRecord.sales_order_id == order_id))
        assert record.amount == 10 and record.company_account_id == company and record.counterparty_account_id == customer_account


@pytest.mark.parametrize("merged", [False, True])
def test_purchase_registration_preserves_accounts(client, login, merged):
    _, supplier_id, _, order_id, attachment_id = documents()
    company = account(client, login)
    supplier_account = account(client, login, "supplier", supplier_id=supplier_id)
    with SessionLocal() as db:
        buyer_id = db.scalar(select(User.id).where(User.username == "purchase01"))
        finance_id = db.scalar(select(User.id).where(User.username == "finance01"))
        request = PurchasePaymentRequest(request_no=uniq("FK"), purchase_order_id=order_id, requested_by_id=buyer_id,
                                          assigned_finance_user_id=finance_id, requested_amount=10, order_amount=10, status="pending_finance_payment")
        db.add(request); db.commit(); request_id = request.id
    payload = {"company_account_id": company, "counterparty_account_id": supplier_account,
               "bank_receipt_attachment_id": attachment_id, "purchase_payment_request_id": request_id, "actual_amount": 10}
    endpoint = f"/api/finance/payables/payment-requests/{request_id}/register"
    if merged:
        endpoint = "/api/finance/payables/payment-requests/merged-register"
        payload["items"] = [{"purchase_payment_request_id": request_id, "actual_amount": 10}]
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        record = db.scalar(select(PayableRecord).where(PayableRecord.purchase_order_id == order_id))
        assert record.amount == 10 and record.company_account_id == company and record.counterparty_account_id == supplier_account


def test_product_price_and_inventory_share_sources(client, login):
    with SessionLocal() as db:
        product = Product(product_code=uniq("UNIFIED"), name="统一来源产品", price=100)
        warehouse = db.scalar(select(Warehouse).where(Warehouse.warehouse_type == "finished"))
        db.add(product); db.flush()
        db.add(Inventory(warehouse_id=warehouse.id, product_id=product.id, quantity=25, reserved_quantity=7))
        db.commit(); product_id = product.id; warehouse_id = warehouse.id
        assert product.fulfillment_mode == "trade"
    login("sales01")
    response = client.put(f"/api/master-data/standard-prices/{product_id}", json={"price": 123, "remark": "统一价格测试"})
    assert response.status_code == 200, response.text
    products = client.get("/api/master-data/products").json()["data"]
    if isinstance(products, dict): products = products["items"]
    product = next(p for p in products if p["id"] == product_id)
    assert product["price"] == 123 and product["stock"]["available_quantity"] == 18
    standards = client.get("/api/master-data/standard-prices").json()["data"]
    assert next(p for p in standards if p["product_id"] == product_id)["standard_price"] == 123
    login("inventory01")
    rows = client.get(f"/api/inventory/stocks?warehouse_id={warehouse_id}&item_type=product").json()["data"]
    if isinstance(rows, dict): rows = rows["items"]
    assert next(r for r in rows if r["item_id"] == product_id)["available_quantity"] == product["stock"]["available_quantity"]


@pytest.mark.parametrize("direction", ["receivables", "payables"])
def test_direct_registration_requires_and_records_company_account(client, login, direction):
    customer_id, supplier_id, sales_id, purchase_id, attachment_id = documents()
    company = account(client, login)
    payload = {"company_account_id": company, "bank_receipt_attachment_id": attachment_id}
    if direction == "receivables":
        endpoint = "/api/finance/receivables/direct-register"
        payload.update(customer_id=customer_id, items=[{"sales_order_id": sales_id, "amount": 10}])
    else:
        endpoint = "/api/finance/payables/merged-register"
        payload.update(supplier_id=supplier_id, actual_amount=10, items=[{"purchase_order_id": purchase_id, "order_amount": 10}])
    missing = {**payload, "company_account_id": None}
    response = client.post(endpoint, json=missing)
    assert response.status_code == 409, response.text
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        record = db.scalar(select(ReceivableRecord).where(ReceivableRecord.sales_order_id == sales_id)) if direction == "receivables" else db.scalar(select(PayableRecord).where(PayableRecord.purchase_order_id == purchase_id))
        assert record.company_account_id == company and record.amount == 10


def test_account_owner_cannot_be_deleted(client, login):
    with SessionLocal() as db:
        customer = Customer(code=uniq("AC"), name=uniq("账户保护客户"), contact_name="测试", phone="1", address="测试")
        supplier = Supplier(code=uniq("AS"), name=uniq("账户保护供应商"), contact_name="测试", phone="1", address="测试")
        db.add_all([customer, supplier]); db.commit()
        customer_id, supplier_id = customer.id, supplier.id
    account(client, login, "customer", customer_id=customer_id)
    account(client, login, "supplier", supplier_id=supplier_id)
    login("sales01")
    response = client.delete(f"/api/master-data/customers/{customer_id}")
    assert response.status_code == 409, response.text
    login("purchase01")
    response = client.delete(f"/api/master-data/suppliers/{supplier_id}")
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(Supplier, supplier_id).status == "disabled"
