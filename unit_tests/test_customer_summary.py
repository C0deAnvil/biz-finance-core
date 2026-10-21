"""Customer summary cards must aggregate the full filtered result, not one page."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.master_data import list_customers
from models import Base, Customer, Role, SalesOrder, User


def test_customer_summary_is_stable_across_pages():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="system_admin", name="系统管理员", home_route="/", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        user = User(username="summary_admin", full_name="汇总测试", hashed_password="x", role_id=role.id, is_active=True)
        db.add(user)
        db.flush()

        customers = []
        for index in range(12):
            customer = Customer(
                code=f"KH-SUM-{index:02d}",
                name=f"汇总客户{index:02d}",
                contact_name=f"联系人{index}",
                phone=f"1380000{index:04d}",
                address="测试地址",
                star_level="五星" if index < 4 else None,
                created_by_id=user.id,
                owner_user_id=user.id,
            )
            db.add(customer)
            customers.append(customer)
        db.flush()
        for index in (6, 8, 11):
            db.add(SalesOrder(
                order_no=f"XS-SUM-{index:02d}",
                customer_id=customers[index].id,
                sales_user_id=user.id,
                operator_user_id=user.id,
                consignee="收货人",
                consignee_phone="13800000000",
                delivery_address="收货地址",
                payment_type="full_payment",
                status="pending_confirmation",
            ))
        db.commit()

        query_args = {
            "keyword": None,
            "source_type": None,
            "owner_user_id": None,
            "star_level": None,
            "customer_type": None,
            "region_type": None,
            "region_province": None,
            "churn_repurchase_only": False,
            "tags": None,
            "start_date": None,
            "end_date": None,
            "current_user": user,
            "db": db,
        }
        first_page = list_customers(page=1, page_size=5, **query_args)["data"]
        second_page = list_customers(page=2, page_size=5, **query_args)["data"]

        expected = {
            "customer_count": 12,
            "star_customer_count": 4,
            "active_order_customer_count": 3,
        }
        assert len(first_page["items"]) == 5
        assert len(second_page["items"]) == 5
        assert first_page["summary"] == expected
        assert second_page["summary"] == expected
