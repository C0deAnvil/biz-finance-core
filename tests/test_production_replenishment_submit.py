from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.production import create_demand_raw_material_demands
from models import Base, BOM, BOMItem, FulfillmentDemand, Material, MaterialReplenishmentDemand, Product, ProductionMaterialUsage, Role, TodoItem, User, Warehouse
from schemas.purchases import PurchaseOrderItemInput
from schemas.production import DemandRawMaterialDemandCreateRequest, MaterialUsageInput, ProductionTaskCreate, RawMaterialDemandInput
from services.inventory_availability import material_production_commitments
from services.material_replenishment_service import material_demand_progress
from services.production_service import create_task
from services.purchase_service._common import _split_legacy_material_batch_for_purchase


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
        material_b = Material(material_code="YL-SUBMIT-TEST-B", name="测试原料B")
        warehouse = Warehouse(code="LANGFANG_RAW", name="廊坊工厂仓", warehouse_type="raw")
        db.add_all([producer, purchaser, product, material, material_b, warehouse])
        db.flush()
        bom = BOM(product_id=product.id)
        db.add(bom)
        db.flush()
        db.add(BOMItem(bom_id=bom.id, material_id=material.id, standard_quantity=Decimal("2")))
        db.add(BOMItem(bom_id=bom.id, material_id=material_b.id, standard_quantity=Decimal("3")))
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
                # BOM 自动建议为 30，但生产可根据实际损耗确认 35；采购必须收到 35。
                RawMaterialDemandInput(material_id=material_b.id, shortage_quantity=Decimal("35")),
            ]),
            producer,
            db,
        )
        assert result["code"] == 200
        assert len(result["data"]["raw_demand_nos"]) == 2
        assert len(set(result["data"]["raw_demand_nos"])) == 2
        batches = db.scalars(select(MaterialReplenishmentDemand).order_by(MaterialReplenishmentDemand.id)).all()
        assert len(batches) == 2
        assert [len(batch.items_json) for batch in batches] == [1, 1]
        assert {batch.items_json[0]["material_id"] for batch in batches} == {material.id, material_b.id}
        material_b_batch = next(batch for batch in batches if batch.items_json[0]["material_id"] == material_b.id)
        assert Decimal(str(material_b_batch.items_json[0]["shortage_quantity"])) == Decimal("35")
        assert Decimal(str(material_b_batch.items_json[0]["system_suggested_quantity"])) == Decimal("30")
        progress = material_demand_progress(db, demand)
        material_b_progress = next(item for item in progress["items"] if item["material_id"] == material_b.id)
        assert material_b_progress["system_required_quantity"] == 30
        assert material_b_progress["required_quantity"] == 35
        assert material_production_commitments(db, material_ids=[material_b.id])[material_b.id] == Decimal("35")
        assert db.get(FulfillmentDemand, demand.id).status == "pending_material"
        # 已按系统建议提交后仍可把实际总需求从 20 上调到 21；系统只追加差额 1。
        correction = create_demand_raw_material_demands(
            demand.id,
            DemandRawMaterialDemandCreateRequest(items=[
                RawMaterialDemandInput(
                    material_id=material.id,
                    shortage_quantity=Decimal("1"),
                    actual_required_quantity=Decimal("21"),
                ),
            ]),
            producer,
            db,
        )
        assert correction["code"] == 200
        corrected_progress = material_demand_progress(db, demand)
        material_a_progress = next(item for item in corrected_progress["items"] if item["material_id"] == material.id)
        assert material_a_progress["required_quantity"] == 21

        task_result = create_task(
            ProductionTaskCreate(
                source_type="manual",
                fulfillment_demand_id=demand.id,
                product_id=product.id,
                planned_quantity=Decimal("10"),
                material_usages=[
                    MaterialUsageInput(material_id=material.id, requested_quantity=Decimal("20")),
                    # 采购确认量可以高于 BOM，但转生产任务的默认领料仍按本批 BOM 量 30。
                    MaterialUsageInput(material_id=material_b.id, requested_quantity=Decimal("30")),
                ],
            ),
            producer,
            db,
        )
        usage_b = db.scalar(
            select(ProductionMaterialUsage).where(
                ProductionMaterialUsage.production_task_id == task_result["data"]["id"],
                ProductionMaterialUsage.material_id == material_b.id,
            )
        )
        assert usage_b.planned_quantity == Decimal("30")
        assert usage_b.requested_quantity == Decimal("30")
        assert material_production_commitments(db, material_ids=[material_b.id])[material_b.id] == Decimal("35")


def test_legacy_multi_material_batch_can_be_split_for_single_material_purchase():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        batch = MaterialReplenishmentDemand(
            demand_no="YL-LEGACY-MULTI",
            fulfillment_demand_id=1,
            created_by_id=1,
            items_json=[
                {"material_id": 101, "material_name": "原料A", "shortage_quantity": 5},
                {"material_id": 102, "material_name": "原料B", "shortage_quantity": 8},
            ],
        )
        db.add(batch)
        db.flush()
        payload = {
            "raw_material_demand_no": batch.demand_no,
            "items": batch.items_json,
        }
        todo = TodoItem(
            title="待采购原料",
            description=f"旧需求|sales_order_material_replenishment:{__import__('json').dumps(payload, ensure_ascii=False)}",
            module="purchase",
            assigned_user_id=1,
            business_type="production_material_replenishment",
            business_id=batch.id,
            status="pending",
            priority="high",
        )
        db.add(todo)
        db.flush()

        selected = _split_legacy_material_batch_for_purchase(
            db,
            batch,
            [PurchaseOrderItemInput(material_id=101, quantity=Decimal("5"), unit_price=Decimal("1"))],
        )

        assert selected.id != batch.id
        assert [item["material_id"] for item in selected.items_json] == [101]
        assert [item["material_id"] for item in batch.items_json] == [102]
        assert '"material_id": 102' in todo.description
        assert '"material_id": 101' not in todo.description
