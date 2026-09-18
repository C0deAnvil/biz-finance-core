import pytest
from sqlalchemy import select

from database import SessionLocal
from models import Customer, SalesOrder, SalesReceiptRequest, PurchasePaymentRequest
from test_financial_accounts import account, documents
from test_sales_sample_prices import setup_order


def balance(client, login, account_id):
    login("finance01")
    result = client.get('/api/finance/accounts?active_only=false')
    assert result.status_code == 200, result.text
    return next(row['balance'] for row in result.json()['data'] if row['id'] == account_id)


def test_receipt_uses_sales_account_and_payment_can_make_balance_negative(client, login):
    _, supplier_id, sales_id, purchase_id, attachment_id = documents()
    company_id = account(client, login)
    other_company_id = account(client, login)
    supplier_account = account(client, login, 'supplier', supplier_id=supplier_id)
    other_supplier_account = account(client, login, 'supplier', supplier_id=supplier_id)
    login('sales01')
    response = client.post('/api/sales/settlements/receipt-requests', json={
        'sales_order_id': sales_id, 'requested_amount': 10, 'payment_stage': 'cod_full', 'company_account_id': company_id,
    })
    assert response.status_code == 200, response.text
    request_id = response.json()['data']['id']
    assert balance(client, login, company_id) == 0
    receipt_url = f'/api/finance/receivables/receipt-requests/{request_id}/register'
    receipt_payload = {'sales_receipt_request_id': request_id, 'actual_amount': 10, 'bank_receipt_attachment_id': attachment_id}
    changed = client.post(receipt_url, json={**receipt_payload, 'company_account_id': other_company_id})
    assert changed.status_code == 409, changed.text
    # 财务无须再次指定账户，使用销售申请中的账户。
    response = client.post(receipt_url, json=receipt_payload)
    assert response.status_code == 200, response.text
    assert balance(client, login, company_id) == 10
    assert client.post(receipt_url, json=receipt_payload).status_code == 409
    assert balance(client, login, company_id) == 10

    login('purchase01')
    client.put('/api/master-data/settings/purchase-approval-threshold', json={'amount': 100000})
    payload = {'purchase_order_id': purchase_id, 'requested_amount': 30, 'order_amount': 30}
    assert client.post('/api/purchases/settlements/payment-requests', json=payload).status_code == 409
    response = client.post('/api/purchases/settlements/payment-requests', json={**payload, 'counterparty_account_id': supplier_account})
    assert response.status_code == 200, response.text
    payment_id = response.json()['data']['id']
    with SessionLocal() as db:
        request = db.get(PurchasePaymentRequest, payment_id)
        assert request.counterparty_account_id == supplier_account
        assert request.company_account_id is None
        # 测试明确指派当前财务，避免测试库轮询分配给其他测试账户。
        from models import User
        request.assigned_finance_user_id = db.scalar(select(User.id).where(User.username == 'finance01'))
        db.commit()
    assert balance(client, login, company_id) == 10
    payment_url = f'/api/finance/payables/payment-requests/{payment_id}/register'
    payment_payload = {'purchase_payment_request_id': payment_id, 'actual_amount': 30,
                       'bank_receipt_attachment_id': attachment_id, 'company_account_id': company_id}
    assert client.post(payment_url, json={**payment_payload, 'counterparty_account_id': other_supplier_account}).status_code == 409
    response = client.post(payment_url, json=payment_payload)
    assert response.status_code == 200, response.text
    assert balance(client, login, company_id) == -20
    assert balance(client, login, other_company_id) == 0
    assert client.post(payment_url, json=payment_payload).status_code == 409
    assert balance(client, login, company_id) == -20


def test_purchase_can_manage_only_supplier_accounts(client, login):
    _, supplier_id, _, _, _ = documents()
    login('purchase01')
    payload = {'owner_type': 'supplier', 'supplier_id': supplier_id, 'account_name': '供应商测试户名',
               'bank_name': '测试开户行', 'account_number': f'00000{supplier_id}'}
    response = client.post('/api/finance/accounts', json=payload)
    assert response.status_code == 200, response.text
    account_id = response.json()['data']['id']
    rows = client.get('/api/master-data/suppliers').json()['data']
    supplier = next(r for r in rows if r['id'] == supplier_id)
    assert supplier['bank_account'] == payload['account_number']
    assert client.post('/api/finance/accounts', json={**payload, 'owner_type': 'company', 'supplier_id': None}).status_code == 403
    company_id = account(client, login)
    login('purchase01')
    assert client.put(f'/api/finance/accounts/{company_id}', json={'is_active': False}).status_code == 403
    assert client.put(f'/api/finance/accounts/{account_id}', json={'is_active': False}).status_code == 200
    data = client.get('/api/finance/accounts').json()['data']
    assert all('balance' not in row for row in data)


def test_merged_purchase_keeps_supplier_account_and_posts_once(client, login):
    from models import PurchaseOrder, User
    _, supplier_id, _, first_id, attachment_id = documents()
    _, _, _, second_id, _ = documents()
    with SessionLocal() as db:
        db.get(PurchaseOrder, second_id).supplier_id = supplier_id
        db.commit()
    supplier_account = account(client, login, 'supplier', supplier_id=supplier_id)
    wrong_account = account(client, login, 'supplier', supplier_id=supplier_id)
    company_id = account(client, login)
    login('purchase01')
    client.put('/api/master-data/settings/purchase-approval-threshold', json={'amount': 100000})
    response = client.post('/api/purchases/settlements/payment-requests/merged', json={
        'supplier_id': supplier_id, 'counterparty_account_id': supplier_account,
        'items': [{'purchase_order_id': oid, 'order_amount': 10} for oid in [first_id, second_id]],
    })
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        rows = db.scalars(select(PurchasePaymentRequest).where(PurchasePaymentRequest.purchase_order_id.in_([first_id, second_id]))).all()
        assert len(rows) == 2 and all(r.counterparty_account_id == supplier_account for r in rows)
        finance_id = db.scalar(select(User.id).where(User.username == 'finance01'))
        for row in rows: row.assigned_finance_user_id = finance_id
        ids = [row.id for row in rows]
        db.commit()
    assert balance(client, login, company_id) == 0
    endpoint = '/api/finance/payables/payment-requests/merged-register'
    payload = {'company_account_id': company_id, 'bank_receipt_attachment_id': attachment_id,
               'items': [{'purchase_payment_request_id': rid, 'actual_amount': 10} for rid in ids]}
    assert client.post(endpoint, json={**payload, 'counterparty_account_id': wrong_account}).status_code == 409
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200, response.text
    assert balance(client, login, company_id) == -20
    assert client.post(endpoint, json=payload).status_code == 409
    assert balance(client, login, company_id) == -20


@pytest.mark.parametrize('sync', [False, True])
@pytest.mark.parametrize('edit_existing', [False, True])
def test_customer_address_updates_only_after_explicit_consent(client, login, sync, edit_existing):
    payload = setup_order(login)
    with SessionLocal() as db:
        original = db.get(Customer, payload['customer_id']).address
    order_id = None
    if edit_existing:
        response = client.post('/api/sales/orders', json=payload)
        assert response.status_code == 200, response.text
        order_id = response.json()['data']['id']
    payload.update(delivery_address='新的收货地址', update_customer_address=sync, customer_address_before=original)
    response = client.put(f'/api/sales/orders/{order_id}', json=payload) if edit_existing else client.post('/api/sales/orders', json=payload)
    assert response.status_code == 200, response.text
    order_id = response.json()['data']['id']
    with SessionLocal() as db:
        assert db.get(SalesOrder, order_id).delivery_address == '新的收货地址'
        assert db.get(Customer, payload['customer_id']).address == ('新的收货地址' if sync else original)


def test_address_conflict_and_failed_order_do_not_overwrite_customer(client, login):
    payload = setup_order(login)
    with SessionLocal() as db:
        original = db.get(Customer, payload['customer_id']).address
    payload.update(delivery_address='新地址', update_customer_address=True, customer_address_before='过期地址')
    response = client.post('/api/sales/orders', json=payload)
    assert response.status_code == 409, response.text
    payload['customer_address_before'] = original
    payload['items'][0]['product_id'] = 99999999
    response = client.post('/api/sales/orders', json=payload)
    assert response.status_code == 404, response.text
    with SessionLocal() as db:
        assert db.get(Customer, payload['customer_id']).address == original
        assert db.scalar(select(SalesOrder.id).where(SalesOrder.customer_id == payload['customer_id'])) is None
