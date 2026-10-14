from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, FulfillmentDemand, InventoryDispatchQueue, Product, Role, SalesOrder, SalesOrderItem, User, Warehouse
from services.sales_service import _create_fulfillment_demands
from services.workflow_repair import repair_sales_dispatch_workflows


def test_startup_repair_does_not_create_demand_but_keeper_can_submit():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sales_role = Role(code="sales", name="销售", home_route="/", navigation_json=[], scope_json=[])
        inventory_role = Role(code="inventory", name="库管", home_route="/", navigation_json=[], scope_json=[])
        purchase_role = Role(code="purchase", name="采购", home_route="/", navigation_json=[], scope_json=[])
        db.add_all([sales_role, inventory_role, purchase_role])
        db.flush()
        seller = User(username="seller_demand_test", full_name="销售测试", hashed_password="x", role_id=sales_role.id)
        keeper = User(username="keeper_demand_test", full_name="库管测试", hashed_password="x", role_id=inventory_role.id)
        purchaser = User(username="purchaser_demand_test", full_name="采购测试", hashed_password="x", role_id=purchase_role.id)
        product = Product(product_code="CP-DEMAND-TEST", name="贸易产品", fulfillment_mode="trade")
        warehouse = Warehouse(code="CANGZHOU_FINISHED", name="沧州仓", warehouse_type="finished")
        db.add_all([seller, keeper, purchaser, product, warehouse])
        db.flush()
        order = SalesOrder(
            order_no="XS-DEMAND-TEST", sales_user_id=seller.id, operator_user_id=seller.id,
            consignee="测试", consignee_phone="13900000000", delivery_address="测试地址",
            payment_type="货到付款", status="pending_shipment", total_amount=Decimal("100"),
        )
        db.add(order)
        db.flush()
        db.add(SalesOrderItem(
            sales_order_id=order.id, product_id=product.id, quantity=Decimal("10"),
            unit_price=Decimal("10"), total_amount=Decimal("100"),
        ))
        db.flush()

        repair_sales_dispatch_workflows(db)
        db.flush()
        assert db.scalar(select(FulfillmentDemand.id).where(FulfillmentDemand.sales_order_id == order.id)) is None
        assert db.scalar(select(InventoryDispatchQueue.id).where(InventoryDispatchQueue.sales_order_id == order.id)) is not None

        created = _create_fulfillment_demands(
            db, order=order, current_user=keeper,
            shortage_override={product.id: Decimal("10")},
        )
        assert len(created) == 1
        demand = db.get(FulfillmentDemand, created[0]["id"])
        assert demand.created_by_id == keeper.id
        assert demand.sales_order_id == order.id
