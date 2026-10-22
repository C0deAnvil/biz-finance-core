"""Supplier samples are received by the target warehouse and inspected off-stock."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.quality import create_inbound_application, create_inspection, list_quality_tasks
from api.routers.supplier_samples import SampleCreate, SampleReceive, create_sample, list_samples, receive_sample
from models import Base, Inventory, Material, PackagingConfig, QualityInspection, QualityTask, Role, Supplier, SupplierCatalogItem, SupplierSample, TodoItem, User, Warehouse
from schemas.quality import InspectionCreate, QualityInboundCreate


def _user(db, role, name):
    row = User(username=name, full_name=name, hashed_password="x", role_id=role.id, is_active=True)
    db.add(row)
    db.flush()
    return row


def test_supplier_sample_receipt_quality_and_no_inventory_movement():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        roles = {code: Role(code=code, name=code, home_route="/", navigation_json=[], scope_json=[])
                 for code in ("purchase", "inventory", "quality", "system_admin")}
        db.add_all(roles.values())
        db.flush()
        purchaser = _user(db, roles["purchase"], "sample_purchase")
        keeper = _user(db, roles["inventory"], "sample_keeper")
        other_keeper = _user(db, roles["inventory"], "other_keeper")
        inspector = _user(db, roles["quality"], "sample_inspector")
        other_inspector = _user(db, roles["quality"], "other_inspector")
        warehouse = Warehouse(code="SAMPLE_RAW", name="样品原料仓", warehouse_type="raw", manager_user_id=keeper.id, qc_user_id=inspector.id)
        supplier = Supplier(code="SAMPLE_SUP", name="样品供应商", contact_name="甲", phone="1", address="地址", status="enabled")
        material = Material(material_code="SAMPLE_MAT", name="样品原料", unit="kg")
        db.add_all([warehouse, supplier, material])
        db.flush()
        packaging = PackagingConfig(entity_type="global", entity_id=0, packaging_name="样品桶", kg_per_bag=25, is_active=True)
        db.add(packaging)
        db.flush()
        db.add_all([
            SupplierCatalogItem(supplier_id=supplier.id, material_id=material.id),
            SupplierCatalogItem(supplier_id=supplier.id, packaging_config_id=packaging.id),
        ])
        db.commit()

        created = create_sample(SampleCreate(supplier_id=supplier.id, material_id=material.id,
                                             warehouse_id=warehouse.id, quantity=Decimal("2.5"), batch_no="B-1"),
                                current_user=purchaser, db=db)
        sample_id = created["data"]["id"]
        sample = db.get(SupplierSample, sample_id)
        assert sample.status == "pending_receipt"
        assert {row.assigned_user_id for row in db.scalars(select(TodoItem).where(TodoItem.business_type == "supplier_sample", TodoItem.business_id == sample_id))} == {keeper.id}
        with pytest.raises(HTTPException) as forbidden:
            receive_sample(sample_id, SampleReceive(received_quantity=Decimal("2.5")), current_user=other_keeper, db=db)
        assert forbidden.value.status_code == 403
        received = receive_sample(sample_id, SampleReceive(received_quantity=Decimal("2.4"), storage_location="样品柜 A1"), current_user=keeper, db=db)
        task_id = received["data"]["quality_task_id"]
        task = db.get(QualityTask, task_id)
        assert task.source_category == "supplier_sample"
        assert {row.assigned_user_id for row in db.scalars(select(TodoItem).where(TodoItem.business_type == "quality_task", TodoItem.business_id == task_id))} == {inspector.id}
        assert any(row["supplier_sample_id"] == sample_id for row in list_quality_tasks(current_user=inspector, db=db)["data"])
        assert not any(row["supplier_sample_id"] == sample_id for row in list_quality_tasks(current_user=other_inspector, db=db)["data"])
        assert {row.assigned_user_id for row in db.scalars(select(TodoItem).where(TodoItem.business_type == "quality_task", TodoItem.business_id == task_id))} == {inspector.id}
        with pytest.raises(HTTPException) as duplicate:
            receive_sample(sample_id, SampleReceive(received_quantity=Decimal("2.4")), current_user=keeper, db=db)
        assert duplicate.value.status_code == 409
        with pytest.raises(HTTPException) as over_quantity:
            create_inspection(InspectionCreate(quality_task_id=task_id, sequence_no=1, result_text="超量", grade="A",
                                               inspected_quantity=Decimal("3")), current_user=inspector, db=db)
        assert over_quantity.value.status_code == 409
        create_inspection(InspectionCreate(quality_task_id=task_id, sequence_no=1, result_text="外观与纯度合格", grade="A",
                                           inspected_quantity=Decimal("2.4"), report_summary="符合样品检验标准"),
                          current_user=inspector, db=db)
        db.refresh(sample)
        assert sample.status == "completed"
        assert task.status == "completed"
        assert db.scalar(select(QualityInspection.id).where(QualityInspection.quality_task_id == task_id)) is not None
        assert db.scalars(select(Inventory)).all() == []
        with pytest.raises(HTTPException) as no_inbound:
            create_inbound_application(QualityInboundCreate(quality_task_id=task_id, warehouse_id=warehouse.id), current_user=inspector, db=db)
        assert no_inbound.value.status_code == 409
        rows = list_samples(supplier_id=supplier.id, current_user=purchaser, db=db)["data"]
        assert rows[0]["is_passed"] is True
        assert rows[0]["report_summary"] == "符合样品检验标准"
        assert rows[0]["received_quantity"] == 2.4
        assert rows[0]["storage_location"] == "样品柜 A1"

        packaging_created = create_sample(
            SampleCreate(
                supplier_id=supplier.id,
                packaging_config_id=packaging.id,
                warehouse_id=warehouse.id,
                quantity=Decimal("2"),
                batch_no="PKG-1",
            ),
            current_user=purchaser,
            db=db,
        )
        packaging_sample = db.get(SupplierSample, packaging_created["data"]["id"])
        assert packaging_sample.packaging_config_id == packaging.id
        assert packaging_sample.unit == "件"
        packaging_task_id = receive_sample(
            packaging_sample.id,
            SampleReceive(received_quantity=Decimal("2")),
            current_user=keeper,
            db=db,
        )["data"]["quality_task_id"]
        packaging_tasks = list_quality_tasks(current_user=inspector, db=db)["data"]
        assert any(
            row["id"] == packaging_task_id
            and row["item_type"] == "packaging"
            and row["item_name"] == "样品桶"
            for row in packaging_tasks
        )

        second = create_sample(SampleCreate(supplier_id=supplier.id, material_id=material.id,
                                            warehouse_id=warehouse.id, quantity=Decimal("1"), batch_no="B-2"),
                               current_user=purchaser, db=db)
        second_id = second["data"]["id"]
        second_task_id = receive_sample(second_id, SampleReceive(received_quantity=Decimal("1")), current_user=keeper, db=db)["data"]["quality_task_id"]
        create_inspection(InspectionCreate(quality_task_id=second_task_id, sequence_no=1, result_text="纯度不达标",
                                           grade="不合格", is_passed=False, inspected_quantity=Decimal("1")),
                          current_user=inspector, db=db)
        latest = list_samples(supplier_id=supplier.id, current_user=purchaser, db=db)["data"][0]
        assert latest["sample_no"] == second["data"]["sample_no"]
        assert latest["status"] == "completed" and latest["is_passed"] is False
        assert db.scalars(select(Inventory)).all() == []

        # 兼容旧仓库数据：未指定专属质检员、质检部门也未维护仓库范围时，
        # 收样不能静默失败，应正常生成任务并通知全部在岗质检人员。
        warehouse.qc_user_id = None
        db.commit()
        fallback_sample = create_sample(
            SampleCreate(
                supplier_id=supplier.id,
                material_id=material.id,
                warehouse_id=warehouse.id,
                quantity=Decimal("0.5"),
                batch_no="B-FALLBACK",
            ),
            current_user=purchaser,
            db=db,
        )
        fallback_task_id = receive_sample(
            fallback_sample["data"]["id"],
            SampleReceive(received_quantity=Decimal("0.5")),
            current_user=keeper,
            db=db,
        )["data"]["quality_task_id"]
        fallback_task = db.get(QualityTask, fallback_task_id)
        assert fallback_task.assigned_inspector_id is None
        assert {
            row.assigned_user_id
            for row in db.scalars(
                select(TodoItem).where(
                    TodoItem.business_type == "quality_task",
                    TodoItem.business_id == fallback_task_id,
                )
            )
        } == {inspector.id, other_inspector.id}
