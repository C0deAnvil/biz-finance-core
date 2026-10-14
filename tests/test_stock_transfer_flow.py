"""移库全过程的查询锁应作用于 SQL 语句，而不是 Result 对象。"""

from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.routers.inventory.transfers import (
    confirm_transfer_inbound,
    confirm_transfer_outbound,
    create_stock_transfer,
    list_stock_transfers,
    submit_transfer_quality,
)
from models import Base, Inventory, Material, OperationLog, PackagingConfig, Product, Role, StockTransfer, User, Warehouse
from schemas.inventory import StockTransferCreateRequest, TransferQualityRequest


def test_stock_transfer_create_outbound_quality_inbound():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="system_admin", name="系统管理员", home_route="/", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        actor = User(username="transfer_admin", full_name="测试管理员", hashed_password="x", role_id=role.id)
        source = Warehouse(code="TRANSFER_SOURCE", name="移库源仓", warehouse_type="finished")
        target = Warehouse(code="TRANSFER_TARGET", name="移库目标仓", warehouse_type="finished")
        product = Product(product_code="TRANSFER-PRODUCT", name="移库测试产品")
        db.add_all([actor, source, target, product])
        db.flush()
        db.add(Inventory(
            warehouse_id=source.id, product_id=product.id,
            quantity=Decimal("10"), reserved_quantity=Decimal("0"), avg_unit_cost=Decimal("2"),
        ))
        db.commit()

        created = create_stock_transfer(
            StockTransferCreateRequest(
                source_warehouse_id=source.id, target_warehouse_id=target.id,
                product_id=product.id, quantity=3,
            ),
            current_user=actor, db=db,
        )
        transfer_id = created["data"]["id"]
        assert db.get(StockTransfer, transfer_id).status == "pending_outbound"
        assert db.scalar(select(OperationLog.id).where(OperationLog.action == "create_stock_transfer")) is not None

        confirm_transfer_outbound(transfer_id, current_user=actor, db=db)
        assert db.get(StockTransfer, transfer_id).status == "pending_quality"
        submit_transfer_quality(
            transfer_id, TransferQualityRequest(result="passed"), current_user=actor, db=db,
        )
        assert db.get(StockTransfer, transfer_id).status == "pending_inbound"
        confirm_transfer_inbound(transfer_id, current_user=actor, db=db)
        assert db.get(StockTransfer, transfer_id).status == "completed"
        source_stock = db.scalar(select(Inventory).where(Inventory.warehouse_id == source.id))
        target_stock = db.scalar(select(Inventory).where(Inventory.warehouse_id == target.id))
        assert source_stock.quantity == Decimal("7")
        assert target_stock.quantity == Decimal("3")

        returned = create_stock_transfer(
            StockTransferCreateRequest(
                source_warehouse_id=source.id, target_warehouse_id=target.id,
                product_id=product.id, quantity=2,
            ),
            current_user=actor, db=db,
        )
        returned_id = returned["data"]["id"]
        confirm_transfer_outbound(returned_id, current_user=actor, db=db)
        submit_transfer_quality(
            returned_id,
            TransferQualityRequest(result="failed", reject_reason="质检测试退回"),
            current_user=actor, db=db,
        )
        assert db.get(StockTransfer, returned_id).status == "returned"
        db.refresh(source_stock)
        assert source_stock.quantity == Decimal("7")


def test_material_and_packaging_transfer_can_be_submitted_and_listed():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="system_admin", name="移库测试管理员", home_route="/", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        actor = User(username="transfer_material_admin", full_name="测试管理员", hashed_password="x", role_id=role.id)
        source = Warehouse(code="LANGFANG_RAW_TEST", name="廊坊原料测试仓", warehouse_type="raw_material")
        target = Warehouse(code="CANGZHOU_RAW_TEST", name="沧州测试仓", warehouse_type="finished")
        material = Material(material_code="YL-TRANSFER-TEST", name="球尿素")
        packaging = PackagingConfig(entity_type="global", entity_id=0, packaging_name="测试包装", kg_per_bag=Decimal("1"))
        db.add_all([actor, source, target, material, packaging])
        db.flush()
        db.add_all([
            Inventory(warehouse_id=source.id, material_id=material.id, quantity=Decimal("10")),
            Inventory(warehouse_id=source.id, packaging_config_id=packaging.id, quantity=Decimal("10")),
        ])
        db.commit()

        for item_field, item_id in (("material_id", material.id), ("packaging_config_id", packaging.id)):
            response = create_stock_transfer(
                StockTransferCreateRequest(
                    source_warehouse_id=source.id, target_warehouse_id=target.id,
                    quantity=1, **{item_field: item_id},
                ),
                current_user=actor, db=db,
            )
            assert response["data"]["id"]

        listed = list_stock_transfers(current_user=actor, db=db)["data"]
        assert {row["item_name"] for row in listed} == {"球尿素", "测试包装"}
