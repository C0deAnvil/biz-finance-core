from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.production import create_demand_raw_material_demands
from models import Base, BOM, BOMItem, FulfillmentDemand, Material, MaterialReplenishmentDemand, Product, Role, User, Warehouse
from schemas.production import DemandRawMaterialDemandCreateRequest, RawMaterialDemandInput


def test_production_replenishment_submission_creates_demand():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        production_role = Role(code="production", name="生产", home_route="/", navigation_json=[], scope_json=[])
        purchase_role = Role(code="purchase", name="采购", home_route="/", navigation_json=[], scope_json=[])
        db.add_all([production_role, purchase_role])
        db.flush()
        producer = User(username="production_submit_test", full_name="生产测试", hashed_password="x", role_id=production_role.id)
        purchaser = User(username="purchase_submit_test", full_name="采购测试", hashed_password="x", role_id=purchase_role.id)
        product = Product(product_code="CP-SUBMIT-TEST", name="测试产品", fulfillment_mode="production")
        material = Material(material_code="YL-SUBMIT-TEST", name="测试原料")
        warehouse = Warehouse(code="LANGFANG_RAW", name="廊坊工厂仓", warehouse_type="raw")
        db.add_all([producer, purchaser, product, material, warehouse])
        db.flush()
        bom = BOM(product_id=product.id)
        db.add(bom)
        db.flush()
        db.add(BOMItem(bom_id=bom.id, material_id=material.id, standard_quantity=Decimal("2")))
        demand = FulfillmentDemand(
            demand_no="BH-SUBMIT-TEST", product_id=product.id, fulfillment_mode="production",
            shortage_quantity=Decimal("10"), required_quantity=Decimal("10"), status="pending",
            created_by_id=producer.id,
        )
        db.add(demand)
        db.flush()

        result = create_demand_raw_material_demands(
            demand.id,
            DemandRawMaterialDemandCreateRequest(items=[
                RawMaterialDemandInput(material_id=material.id, shortage_quantity=Decimal("20")),
            ]),
            producer,
            db,
        )
        assert result["code"] == 200
        assert len(result["data"]["raw_demand_nos"]) == 1
        batch = db.scalar(select(MaterialReplenishmentDemand))
        assert batch is not None
        assert batch.items_json[0]["shortage_quantity"] == 20
        assert db.get(FulfillmentDemand, demand.id).status == "pending_material"
        with pytest.raises(HTTPException) as error:
            create_demand_raw_material_demands(
                demand.id,
                DemandRawMaterialDemandCreateRequest(items=[
                    RawMaterialDemandInput(material_id=material.id, shortage_quantity=Decimal("1")),
                ]),
                producer,
                db,
            )
        assert error.value.status_code == 409
        assert "剩余缺口" in str(error.value.detail)
