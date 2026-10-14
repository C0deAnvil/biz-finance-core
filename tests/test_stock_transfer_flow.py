"""移库在统一库存作业与可选质检中流转，不能从移库页直接改库存。"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.inventory.transfers import create_stock_transfer, list_stock_transfers, confirm_transfer_outbound
from api.routers.inventory._common import _can_access_operation_request
from api.routers.quality import create_inspection, list_quality_tasks
from models import Base, Inventory, InventoryOperationRequest, InventoryTransaction, Material, PackagingConfig, Product, QualityTask, Role, StockTransfer, TodoItem, User, Warehouse
from schemas.inventory import InventoryOperationHandleRequest, StockTransferCreateRequest
from schemas.quality import InspectionCreate
from services.workflow import complete_inventory_operation_request


def _actor(db, role, name):
    user = User(username=name, full_name=name, hashed_password="x", role_id=role.id, is_active=True)
    db.add(user)
    db.flush()
    return user


def _request(db, transfer_id, category):
    return db.execute(select(InventoryOperationRequest).where(
        InventoryOperationRequest.source_type == "stock_transfer",
        InventoryOperationRequest.source_id == transfer_id,
        InventoryOperationRequest.request_category == category,
    )).scalar_one()


def _complete(db, request, actor):
    return complete_inventory_operation_request(
        request.id,
        InventoryOperationHandleRequest(items=request.original_items_json),
        actor, db,
    )


def test_transfer_without_quality_creates_sequential_warehouse_jobs_and_ledger():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin_role = Role(code="system_admin", name="管理员", home_route="/", navigation_json=[], scope_json=[])
        inventory_role = Role(code="inventory", name="库管", home_route="/", navigation_json=[], scope_json=[])
        db.add_all([admin_role, inventory_role])
        db.flush()
        admin = _actor(db, admin_role, "transfer_admin")
        source_keeper = _actor(db, inventory_role, "source_keeper")
        target_keeper = _actor(db, inventory_role, "target_keeper")
        source = Warehouse(code="SOURCE", name="源仓", warehouse_type="raw_material", manager_user_id=source_keeper.id)
        target = Warehouse(code="TARGET", name="目标仓", warehouse_type="finished", manager_user_id=target_keeper.id)
        product = Product(product_code="P-TRANSFER", name="移库产品")
        db.add_all([source, target, product])
        db.flush()
        db.add(Inventory(warehouse_id=source.id, product_id=product.id, quantity=Decimal("10"), reserved_quantity=Decimal("0"), avg_unit_cost=Decimal("2.5000")))
        db.commit()

        result = create_stock_transfer(StockTransferCreateRequest(source_warehouse_id=source.id, target_warehouse_id=target.id, product_id=product.id, quantity=3), current_user=admin, db=db)
        transfer_id = result["data"]["id"]
        transfer = db.get(StockTransfer, transfer_id)
        assert transfer.status == "pending_outbound"
        assert transfer.requires_quality is False
        assert db.scalar(select(Inventory.quantity).where(Inventory.warehouse_id == source.id)) == Decimal("10")
        outbound = _request(db, transfer_id, "stock_transfer_outbound")
        assert outbound.warehouse_id == source.id
        assert outbound.original_items_json[0]["unit_cost"] == 2.5
        assert {row.assigned_user_id for row in db.scalars(select(TodoItem).where(TodoItem.business_type == "inventory_operation_request", TodoItem.business_id == outbound.id))} == {source_keeper.id}
        assert _can_access_operation_request(db, source_keeper, outbound)
        assert not _can_access_operation_request(db, target_keeper, outbound)
        with pytest.raises(HTTPException) as old_route:
            confirm_transfer_outbound(transfer_id, current_user=admin, db=db)
        assert old_route.value.status_code == 409

        _complete(db, outbound, source_keeper)
        assert transfer.status == "pending_inbound"
        assert db.scalar(select(Inventory.quantity).where(Inventory.warehouse_id == source.id)) == Decimal("7")
        inbound = _request(db, transfer_id, "stock_transfer_inbound")
        assert inbound.warehouse_id == target.id
        assert {row.assigned_user_id for row in db.scalars(select(TodoItem).where(TodoItem.business_type == "inventory_operation_request", TodoItem.business_id == inbound.id))} == {target_keeper.id}
        assert not _can_access_operation_request(db, source_keeper, inbound)
        _complete(db, inbound, target_keeper)
        assert transfer.status == "completed"
        assert db.scalar(select(Inventory.quantity).where(Inventory.warehouse_id == target.id)) == Decimal("3")
        rows = db.scalars(select(InventoryTransaction).where(InventoryTransaction.related_type == "inventory_operation_request").order_by(InventoryTransaction.id)).all()
        assert [(row.transaction_type, row.quantity) for row in rows] == [("stock_transfer_out", Decimal("-3")), ("stock_transfer_in", Decimal("3"))]


def test_transfer_with_quality_routes_to_target_qc_then_inbound():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        roles = {code: Role(code=code, name=code, home_route="/", navigation_json=[], scope_json=[]) for code in ("system_admin", "inventory", "quality")}
        db.add_all(roles.values())
        db.flush()
        admin = _actor(db, roles["system_admin"], "qc_admin")
        source_keeper = _actor(db, roles["inventory"], "qc_source")
        target_keeper = _actor(db, roles["inventory"], "qc_target")
        inspector = _actor(db, roles["quality"], "target_inspector")
        other_inspector = _actor(db, roles["quality"], "other_inspector")
        source = Warehouse(code="QS", name="质检源仓", warehouse_type="raw_material", manager_user_id=source_keeper.id)
        target = Warehouse(code="QT", name="质检目标仓", warehouse_type="finished", manager_user_id=target_keeper.id, qc_user_id=inspector.id)
        material = Material(material_code="M-TRANSFER", name="移库原料")
        db.add_all([source, target, material])
        db.flush()
        db.add(Inventory(warehouse_id=source.id, material_id=material.id, quantity=Decimal("10")))
        db.commit()
        created = create_stock_transfer(StockTransferCreateRequest(source_warehouse_id=source.id, target_warehouse_id=target.id, material_id=material.id, quantity=2, requires_quality=True), current_user=admin, db=db)
        transfer_id = created["data"]["id"]
        _complete(db, _request(db, transfer_id, "stock_transfer_outbound"), source_keeper)
        transfer = db.get(StockTransfer, transfer_id)
        assert transfer.status == "pending_quality"
        task = db.scalar(select(QualityTask).where(QualityTask.stock_transfer_id == transfer_id))
        assert task and task.target_warehouse_id == target.id
        assert {row.assigned_user_id for row in db.scalars(select(TodoItem).where(TodoItem.business_type == "quality_task", TodoItem.business_id == task.id))} == {inspector.id}
        assert any(row["stock_transfer_no"] == transfer.transfer_no for row in list_quality_tasks(current_user=inspector, db=db)["data"])
        assert not any(row["stock_transfer_no"] == transfer.transfer_no for row in list_quality_tasks(current_user=other_inspector, db=db)["data"])
        assert db.scalar(select(InventoryOperationRequest.id).where(InventoryOperationRequest.source_id == transfer_id, InventoryOperationRequest.request_category == "stock_transfer_inbound")) is None
        create_inspection(InspectionCreate(quality_task_id=task.id, sequence_no=1, result_text="合格", grade="A", inspected_quantity=2), current_user=inspector, db=db)
        assert transfer.status == "pending_inbound"
        assert task.status == "completed"
        _complete(db, _request(db, transfer_id, "stock_transfer_inbound"), target_keeper)
        assert transfer.status == "completed"


def test_failed_transfer_quality_does_not_fabricate_return_stock():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        roles = {code: Role(code=code, name=code, home_route="/", navigation_json=[], scope_json=[]) for code in ("system_admin", "inventory", "quality")}
        db.add_all(roles.values())
        db.flush()
        admin = _actor(db, roles["system_admin"], "fail_admin")
        keeper = _actor(db, roles["inventory"], "fail_keeper")
        inspector = _actor(db, roles["quality"], "fail_inspector")
        source = Warehouse(code="FS", name="退检源仓", warehouse_type="raw_material", manager_user_id=keeper.id)
        target = Warehouse(code="FT", name="退检目标仓", warehouse_type="finished", manager_user_id=keeper.id, qc_user_id=inspector.id)
        packaging = PackagingConfig(entity_type="global", entity_id=0, packaging_name="测试包装", kg_per_bag=Decimal("1"))
        db.add_all([source, target, packaging])
        db.flush()
        db.add(Inventory(warehouse_id=source.id, packaging_config_id=packaging.id, quantity=Decimal("5")))
        db.commit()
        created = create_stock_transfer(StockTransferCreateRequest(source_warehouse_id=source.id, target_warehouse_id=target.id, packaging_config_id=packaging.id, quantity=1, requires_quality=True), current_user=admin, db=db)
        transfer_id = created["data"]["id"]
        _complete(db, _request(db, transfer_id, "stock_transfer_outbound"), keeper)
        task = db.scalar(select(QualityTask).where(QualityTask.stock_transfer_id == transfer_id))
        create_inspection(InspectionCreate(quality_task_id=task.id, sequence_no=1, result_text="破损", grade="不合格", is_passed=False, inspected_quantity=1), current_user=inspector, db=db)
        assert db.get(StockTransfer, transfer_id).status == "quality_failed"
        assert db.scalar(select(Inventory.quantity).where(Inventory.warehouse_id == source.id)) == Decimal("4")
        assert db.scalar(select(Inventory.quantity).where(Inventory.warehouse_id == target.id)) is None
        assert db.scalar(select(InventoryOperationRequest.id).where(InventoryOperationRequest.source_id == transfer_id, InventoryOperationRequest.request_category == "stock_transfer_inbound")) is None
