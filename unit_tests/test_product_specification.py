from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.master_data import list_products, upsert_packaging_config
from models import Base, PackagingConfig, Product, SalesOrder, SalesOrderItem
from schemas.master_data import PackagingConfigCreate
from services.display import product_specification_text
from services.sales_service._common import _build_order_items


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_legacy_packaging_text_is_not_a_product_grade():
    assert product_specification_text("25 kg/袋") is None
    assert product_specification_text("50kg/包") is None
    assert product_specification_text("GR（优级纯）") == "GR（优级纯）"


def test_product_grade_and_packaging_spec_are_independent(db):
    product = Product(product_code="CP-GRADE", name="测试产品", price=Decimal("10"), specification="工业级")
    db.add(product)
    db.flush()
    db.add(PackagingConfig(
        entity_type="product", entity_id=product.id, packaging_name="25kg编织袋",
        kg_per_bag=Decimal("25"), is_default=True, is_active=True,
    ))
    db.commit()

    result = list_products(page=None, page_size=10, keyword=None, active_only=False, current_user=None, db=db)
    row = next(item for item in result["data"] if item["id"] == product.id)
    assert row["specification"] == "工业级"
    assert row["packaging_specification"] == "25 kg/袋"

    upsert_packaging_config(
        PackagingConfigCreate(
            entity_type="product", entity_id=product.id, packaging_name="50kg纸袋", kg_per_bag=Decimal("50")
        ),
        current_user=None,
        db=db,
    )
    db.refresh(product)
    assert product.specification == "工业级"
    result = list_products(page=None, page_size=10, keyword=None, active_only=False, current_user=None, db=db)
    row = next(item for item in result["data"] if item["id"] == product.id)
    assert row["packaging_specification"] == "50 kg/袋"


def test_new_sales_line_snapshots_product_grade_not_packaging_weight(db):
    product = Product(product_code="CP-SALE-GRADE", name="食品级产品", price=Decimal("10"), specification="食品级")
    packaging = PackagingConfig(
        entity_type="global", entity_id=0, packaging_name="50kg纸袋",
        kg_per_bag=Decimal("50"), is_default=True, is_active=True,
    )
    order = SalesOrder(
        order_no="XS-GRADE", sales_user_id=1, operator_user_id=1, consignee="测试",
        consignee_phone="123", delivery_address="测试地址", payment_type="full",
    )
    db.add_all([product, packaging, order])
    db.flush()

    _build_order_items(
        db, order_id=order.id,
        payload_items=[{"product_id": product.id, "quantity": 100, "unit_price": 10, "packaging_config_id": packaging.id}],
        enforce_price_remark=False, create_price_approval=False,
    )
    item = db.execute(select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order.id)).scalar_one()
    assert item.display_specification == "食品级"
    assert item.kg_per_bag == Decimal("50")
