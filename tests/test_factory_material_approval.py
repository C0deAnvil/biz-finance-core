from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.approvals import _pending_material_requests, _pending_special_requests, approve_item, get_approval_detail
from api.routers.dashboard import dashboard
from api.routers.inventory.stocks import list_operation_requests
from api.routers.production import list_special_requests
from models import (
    Base, Department, Inventory, InventoryOperationRequest, Material, Product,
    ProductionMaterialUsage, ProductionTask, Role, TodoItem, User, Warehouse,
)
from schemas.approvals import ApprovalDecisionRequest
from schemas.production import MaterialRequestCreate, MaterialUsageInput
from services.production_service import can_review_factory_material, submit_material_request


def test_only_factory_keeper_sees_and_approves_material_request():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        inventory_role = Role(code="inventory", name="库管", home_route="/", navigation_json=[], scope_json=[])
        production_role = Role(code="production", name="生产", home_route="/", navigation_json=[], scope_json=[])
        raw_team = Department(code="raw_team", name="工厂仓组", warehouse_scope="raw")
        finished_team = Department(code="finished_team", name="成品仓组", warehouse_scope="finished")
        db.add_all([inventory_role, production_role, raw_team, finished_team])
        db.flush()
        factory_keeper = User(username="factory_keeper", full_name="工厂库管", hashed_password="x", role_id=inventory_role.id, department_id=raw_team.id)
        finished_keeper = User(username="finished_keeper", full_name="成品库管", hashed_password="x", role_id=inventory_role.id, department_id=finished_team.id)
        producer = User(username="producer", full_name="生产", hashed_password="x", role_id=production_role.id)
        db.add_all([factory_keeper, finished_keeper, producer])
        db.flush()
        factory = Warehouse(code="LANGFANG_RAW", name="廊坊工厂仓", warehouse_type="raw", manager_user_id=factory_keeper.id)
        finished = Warehouse(code="CANGZHOU_FINISHED", name="沧州成品仓", warehouse_type="finished", manager_user_id=finished_keeper.id)
        product = Product(product_code="CP-TEST", name="生产产品", fulfillment_mode="production")
        material = Material(material_code="YL-TEST", name="测试原料")
        db.add_all([factory, finished, product, material])
        db.flush()
        db.add(Inventory(warehouse_id=factory.id, material_id=material.id, quantity=Decimal("10")))
        task = ProductionTask(task_no="SC-TEST", product_id=product.id, planned_quantity=Decimal("1"), created_by_id=producer.id, warehouse_id=factory.id, status="pending_material")
        db.add(task)
        db.flush()
        usage = ProductionMaterialUsage(
            production_task_id=task.id, material_id=material.id,
            planned_quantity=Decimal("2"),
        )
        db.add(usage)
        db.flush()

        submit_material_request(
            task.id,
            MaterialRequestCreate(material_usages=[MaterialUsageInput(material_id=material.id, requested_quantity=Decimal("2"))]),
            producer,
            db,
        )
        approval_todo_users = set(db.scalars(select(TodoItem.assigned_user_id).where(
            TodoItem.business_type == "production_material_request", TodoItem.business_id == usage.id,
        )).all())
        assert approval_todo_users == {factory_keeper.id}
        db.add(TodoItem(
            title="历史误派领料待办", module="production", status="pending",
            assigned_user_id=finished_keeper.id,
            business_type="production_material_request", business_id=usage.id,
        ))
        db.flush()
        assert all(
            item["business_type"] != "production_material_request"
            for item in dashboard(current_user=finished_keeper, db=db)["data"]["todos"]
        )

        assert can_review_factory_material(db, factory_keeper)
        assert not can_review_factory_material(db, finished_keeper)
        assert len(_pending_material_requests(factory_keeper, db)) == 1
        assert _pending_material_requests(finished_keeper, db) == []
        assert len(list_special_requests(current_user=factory_keeper, db=db)["data"]) == 1
        assert list_special_requests(current_user=finished_keeper, db=db)["data"] == []
        with pytest.raises(HTTPException) as exc:
            get_approval_detail(f"production_material_request:{usage.id}", current_user=finished_keeper, db=db)
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            approve_item(f"production_material_request:{usage.id}", ApprovalDecisionRequest(), finished_keeper, db)
        assert exc.value.status_code == 403

        result = approve_item(f"production_material_request:{usage.id}", ApprovalDecisionRequest(), factory_keeper, db)
        assert result["code"] == 200
        db.refresh(usage)
        assert usage.inventory_approval_status == "approved"
        issue = db.scalar(select(InventoryOperationRequest).where(InventoryOperationRequest.request_category == "production_material_issue"))
        assert issue is not None and issue.warehouse_id == factory.id
        todo_user_ids = set(db.scalars(select(TodoItem.assigned_user_id).where(
            TodoItem.business_type == "inventory_operation_request", TodoItem.business_id == issue.id,
        )).all())
        assert todo_user_ids == {factory_keeper.id}
        db.add(TodoItem(
            title="历史误派出库待办", module="inventory", status="pending",
            assigned_user_id=finished_keeper.id,
            business_type="inventory_operation_request", business_id=issue.id,
        ))
        db.flush()
        assert all(
            item["business_type"] != "inventory_operation_request"
            for item in dashboard(current_user=finished_keeper, db=db)["data"]["todos"]
        )
        assert issue.id in {row["id"] for row in list_operation_requests(current_user=factory_keeper, db=db)["data"]}
        assert issue.id not in {row["id"] for row in list_operation_requests(current_user=finished_keeper, db=db)["data"]}

        special_task = ProductionTask(task_no="SC-SPECIAL", product_id=product.id, planned_quantity=Decimal("1"), created_by_id=producer.id, warehouse_id=factory.id, status="pending_special_approval")
        db.add(special_task)
        db.flush()
        special_usage = ProductionMaterialUsage(
            production_task_id=special_task.id, material_id=material.id,
            planned_quantity=Decimal("2"), requested_quantity=Decimal("3"),
            inventory_approval_status="pending_approval", manager_approval_status="pending_approval",
            special_request_status="pending_approval",
        )
        db.add(special_usage)
        db.flush()
        assert len(_pending_special_requests(factory_keeper, db)) == 1
        assert _pending_special_requests(finished_keeper, db) == []
        assert special_usage.id in {row["id"] for row in list_special_requests(current_user=factory_keeper, db=db)["data"]}
        assert list_special_requests(current_user=finished_keeper, db=db)["data"] == []
        with pytest.raises(HTTPException) as exc:
            get_approval_detail(f"production_special_request:{special_usage.id}", current_user=finished_keeper, db=db)
        assert exc.value.status_code == 403
