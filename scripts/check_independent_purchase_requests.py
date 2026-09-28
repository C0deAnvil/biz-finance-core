"""SQLite-only checks for production task creation and independent purchase requests."""

from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.purchases import (
    create_department_purchase_request,
    list_department_purchase_requests,
    process_department_purchase_request,
)
from models import Base, BOM, BOMItem, Material, Product, ProductionPlan, PurchaseOrder, PurchaseOrderItem, PurchaseRequest, Role, User
from schemas.production import ProductionTaskCreate
from schemas.purchases import DepartmentPurchaseRequestCreate, DepartmentPurchaseRequestProcess
from services.production_service import convert_plan_to_task, create_task


engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
with Session(engine) as db:
    roles = {}
    users = {}
    for code in ("production", "quality", "purchase"):
        role = Role(code=code, name=code, home_route="/")
        db.add(role)
        db.flush()
        roles[code] = role
        user = User(username=code, full_name=code, hashed_password="x", role_id=role.id)
        db.add(user)
        db.flush()
        users[code] = user
    product = Product(product_code="CP-TEST", name="测试产品", fulfillment_mode="production")
    material = Material(material_code="YL-TEST", name="测试原料")
    db.add_all([product, material])
    db.flush()
    bom = BOM(product_id=product.id, version_no="v1")
    db.add(bom)
    db.flush()
    db.add(BOMItem(bom_id=bom.id, material_id=material.id, standard_quantity=Decimal("1")))
    db.commit()

    # No inventory stock exists, but creating a task must still succeed.
    result = create_task(
        ProductionTaskCreate(product_id=product.id, planned_quantity=Decimal("5")),
        users["production"], db,
    )
    assert result["data"]["task_no"]

    plan = ProductionPlan(
        plan_no="SCJH-TEST", product_id=product.id, planned_quantity=Decimal("3"),
        created_by_id=users["production"].id,
    )
    db.add(plan)
    db.commit()
    converted = convert_plan_to_task(plan.id, users["production"], db)
    assert converted["data"]["production_task_id"]

    raw = create_department_purchase_request(
        DepartmentPurchaseRequestCreate(
            item_type="material", material_id=material.id, quantity="1.2345", unit="kg", reason="自主采购原料",
        ), users["production"], db,
    )
    other = create_department_purchase_request(
        DepartmentPurchaseRequestCreate(
            item_type="other", item_name="质检手套", quantity="10", unit="副", reason="质检耗材采购",
        ), users["quality"], db,
    )
    assert len(list_department_purchase_requests(users["production"], db)["data"]) == 1
    assert len(list_department_purchase_requests(users["quality"], db)["data"]) == 1
    assert len(list_department_purchase_requests(users["purchase"], db)["data"]) == 2
    order = PurchaseOrder(
        order_no="CG-TEST", supplier_id=1, purchaser_user_id=users["purchase"].id,
        operator_user_id=users["purchase"].id, payment_type="先付款后到货", status="draft",
    )
    db.add(order)
    db.flush()
    db.add(PurchaseOrderItem(
        purchase_order_id=order.id, material_id=material.id, quantity=Decimal("1.2345"),
        unit_price=Decimal("1"), total_amount=Decimal("1.2345"),
    ))
    db.commit()
    process_department_purchase_request(
        raw["data"]["id"], DepartmentPurchaseRequestProcess(purchase_order_id=order.id), users["purchase"], db,
    )
    process_department_purchase_request(
        other["data"]["id"], DepartmentPurchaseRequestProcess(process_note="已线下采购并登记"), users["purchase"], db,
    )
    assert db.get(PurchaseRequest, other["data"]["id"]).status == "closed"
    assert db.get(PurchaseRequest, raw["data"]["id"]).status == "ordered"
    assert db.get(PurchaseRequest, raw["data"]["id"]).quantity == Decimal("1.2345")

engine.dispose()
print("production task and independent purchase request checks passed")
