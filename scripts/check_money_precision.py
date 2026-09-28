"""Isolated regression checks; never touches the configured application database."""

from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.finance import list_my_receivables
from models import Base, FinancialAccountTransaction, Product, PurchaseOrder, ReceivablePlan, Role, SalesOrder, SalesOrderItem, User
from schemas.purchases import PurchaseOrderItemInput
from schemas.sales import SalesOrderItemInput
from services.money import money
from services.purchase_service._common import _build_order_items as build_purchase_items
from services.sales_service._common import _build_order_items as build_sales_items


assert money(Decimal("1.23455")) == Decimal("1.2346")
try:
    SalesOrderItemInput(product_id=1, quantity="1", unit_price="1.23456")
    raise AssertionError("A fifth unit-price decimal was silently accepted")
except ValidationError:
    pass

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
with Session(engine) as db:
    role = Role(code="sales", name="销售", home_route="/")
    db.add(role)
    db.flush()
    users = [
        User(username=f"sales{i}", full_name=f"销售{i}", hashed_password="x", role_id=role.id)
        for i in (1, 2)
    ]
    db.add_all(users)
    db.flush()
    product = Product(product_code="CP-TEST", name="测试产品", price=Decimal("1.2345"))
    db.add(product)
    db.flush()

    sales_order = SalesOrder(
        order_no="XS-PRECISION", sales_user_id=users[0].id, operator_user_id=users[0].id,
        consignee="收货人", consignee_phone="123", delivery_address="地址",
        payment_type="先发货后收款", status="pending_shipment",
    )
    db.add(sales_order)
    db.flush()
    sales_total = build_sales_items(db, order_id=sales_order.id, payload_items=[
        SalesOrderItemInput(product_id=product.id, quantity="2.0000", unit_price="1.2345")
    ])
    assert sales_total == Decimal("2.4690")
    sales_order.total_amount = sales_total

    purchase_order = PurchaseOrder(
        order_no="CG-PRECISION", supplier_id=1, purchaser_user_id=users[0].id,
        operator_user_id=users[0].id, payment_type="先付款后到货", status="draft",
    )
    db.add(purchase_order)
    db.flush()
    purchase_total, _ = build_purchase_items(db, purchase_order, [
        PurchaseOrderItemInput(product_id=product.id, quantity="2.0000", unit_price="1.2345")
    ])
    assert purchase_total == Decimal("2.4690")
    purchase_order.total_amount = purchase_total
    db.add(FinancialAccountTransaction(
        account_id=1, transaction_type="adjustment", amount=Decimal("1.2345"),
        reference_no="TEST", created_by_id=users[0].id,
    ))
    db.add(ReceivablePlan(
        sales_order_id=sales_order.id, due_date=__import__("datetime").date.today(),
        planned_amount=Decimal("1.2345"), received_amount=Decimal("0.1234"),
        status="pending", created_by_id=users[0].id,
    ))
    other_order = SalesOrder(
        order_no="XS-OTHER", sales_user_id=users[1].id, operator_user_id=users[1].id,
        consignee="收货人", consignee_phone="123", delivery_address="地址",
        payment_type="先发货后收款", status="pending_shipment", total_amount=Decimal("99.9999"),
    )
    db.add(other_order)
    db.flush()
    db.add(ReceivablePlan(
        sales_order_id=other_order.id, due_date=__import__("datetime").date.today(),
        planned_amount=Decimal("99.9999"), status="pending", created_by_id=users[1].id,
    ))
    db.commit()

    db.expire_all()
    assert db.get(SalesOrderItem, sales_order.items[0].id).total_amount == Decimal("2.4690")
    assert db.get(PurchaseOrder, purchase_order.id).total_amount == Decimal("2.4690")
    assert db.query(FinancialAccountTransaction).first().amount == Decimal("1.2345")
    response = list_my_receivables(
        page=1, page_size=20, keyword=None, status="all", date_from=None, date_to=None,
        current_user=users[0], db=db,
    )["data"]
    assert response["total"] == 1 and len(response["rows"]) == 1
    assert response["rows"][0]["order_no"] == "XS-PRECISION"
    assert Decimal(response["rows"][0]["remaining_amount"]) == Decimal("1.1111")
    assert list_my_receivables(
        page=1, page_size=20, keyword="PRECISION", status="pending", date_from=None, date_to=None,
        current_user=users[0], db=db,
    )["data"]["total"] == 1
    assert list_my_receivables(
        page=1, page_size=20, keyword=None, status="completed", date_from=None, date_to=None,
        current_user=users[0], db=db,
    )["data"]["total"] == 0

print("money precision and sales receivable scope passed")
