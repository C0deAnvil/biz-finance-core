from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.finance import list_my_receivables
from models import Base, Customer, ReceivablePlan, Role, SalesOrder, User


def test_sales_my_receivables_shows_only_own_converted_orders():
    # 内存库隔离于本地遗留测试库，直接验证查询过滤与四位金额。
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="sales", name="销售", home_route="/", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        sales = User(username="sales_test", full_name="销售", hashed_password="x", role_id=role.id)
        other_sales = User(username="other_sales_test", full_name="其他销售", hashed_password="x", role_id=role.id)
        db.add_all([sales, other_sales])
        db.flush()
        customer = Customer(code="MYRC", name="应收测试客户", contact_name="测试", phone="1", address="测试", owner_user_id=sales.id)
        db.add(customer)
        db.flush()

        def order(number, owner, status):
            return SalesOrder(
                order_no=number, customer_id=customer.id,
                sales_user_id=owner.id, operator_user_id=owner.id,
                consignee="测试", consignee_phone="1", delivery_address="测试",
                payment_type="先发货后收款", total_amount=Decimal("12.3456"), status=status,
            )

        own_order = order("XS-MYRC-own", sales, "pending_shipment")
        draft = order("XS-MYRC-draft", sales, "draft")
        other = order("XS-MYRC-other", other_sales, "pending_shipment")
        db.add_all([own_order, draft, other])
        db.flush()
        db.add_all([
            ReceivablePlan(sales_order_id=own_order.id, due_date=date.today(), planned_amount=Decimal("12.3456"), created_by_id=sales.id),
            ReceivablePlan(sales_order_id=other.id, due_date=date.today(), planned_amount=Decimal("9"), created_by_id=other_sales.id),
        ])
        db.flush()
        result = list_my_receivables(
            page=1, page_size=20, keyword="XS-MYRC", status="all",
            date_from=None, date_to=None, current_user=sales, db=db,
        )["data"]
        assert result["total"] == 1
        assert [row["order_no"] for row in result["rows"]] == ["XS-MYRC-own"]
        assert result["rows"][0]["receivable_amount"] == "12.3456"
