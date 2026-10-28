from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, Inventory, InventoryCostAdjustment, InventoryOperationRequest, InventoryTransaction, ProductionMaterialUsage, ProductionTask, SalesOrder, User, Warehouse
from api.routers.inventory.stocks import InventoryCostAdjustmentPayload, adjust_inventory_cost
from schemas.inventory import InventoryOperationHandleRequest
from services import core
from services.inventory_cost import sync_sales_order_cost
from services import workflow


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(core, "_sync_low_stock_alert_for_inventory", lambda *args, **kwargs: None)
    with Session(engine) as session:
        session.add(Warehouse(id=1, code="W1", name="测试仓", warehouse_type="raw"))
        session.flush()
        yield session
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize("item_key", ["product_id", "material_id", "packaging_config_id"])
def test_first_receipt_weighted_average_and_final_issue(db, item_key):
    identity = {item_key: 101}
    stock, first = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("2"), unit_cost=Decimal("1.2345"),
        transaction_type="purchase_inbound", **identity,
    )
    assert stock.cost_balance == Decimal("2.4690")
    assert stock.avg_unit_cost == Decimal("1.23450000")
    assert stock.cost_status == "actual"
    assert first.total_cost == Decimal("2.4690")

    stock, second = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("3"), unit_cost=Decimal("2.0000"),
        transaction_type="purchase_inbound", **identity,
    )
    assert stock.cost_balance == Decimal("8.4690")
    assert stock.avg_unit_cost == Decimal("1.69380000")
    assert second.total_cost == Decimal("6.0000")

    stock, issued = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("-1"), unit_cost=Decimal("999"),
        transaction_type="sales_outbound", **identity,
    )
    assert issued.total_cost == Decimal("-1.6938")
    assert stock.cost_balance == Decimal("6.7752")
    stock, final_issue = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("-4"),
        transaction_type="sales_outbound", **identity,
    )
    assert final_issue.total_cost == Decimal("-6.7752")
    assert stock.quantity == 0
    assert stock.cost_balance == 0
    assert stock.avg_unit_cost == 0
    assert stock.cost_status == "unpriced"
    assert len(db.execute(select(InventoryTransaction)).scalars().all()) == 4


def test_existing_estimate_is_weighted_not_overwritten(db):
    db.add(Inventory(
        warehouse_id=1, product_id=102, quantity=Decimal("10"),
        avg_unit_cost=Decimal("5"), cost_balance=Decimal("50"), cost_status="estimated",
    ))
    db.flush()
    stock, transaction = core.apply_inventory_change(
        db, warehouse_id=1, product_id=102, quantity=Decimal("10"),
        unit_cost=Decimal("7"), transaction_type="purchase_inbound",
    )
    assert stock.cost_balance == Decimal("120.0000")
    assert stock.avg_unit_cost == Decimal("6.00000000")
    assert stock.cost_status == "estimated"
    assert transaction.cost_status == "actual"


@pytest.mark.parametrize("item_key", ["product_id", "material_id", "packaging_config_id"])
def test_first_real_purchase_replaces_remaining_opening_estimate_once(db, item_key):
    identity = {item_key: 107}
    db.add(Inventory(
        warehouse_id=1, quantity=Decimal("10"), avg_unit_cost=Decimal("5"),
        cost_balance=Decimal("50"), cost_status="estimated", opening_cost_pending=True,
        **identity,
    ))
    db.flush()
    stock, _ = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("-2"), transaction_type="manual_outbound", **identity,
    )
    assert stock.cost_balance == Decimal("40")
    stock, receipt = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("2"), unit_cost=Decimal("7"),
        transaction_type="manual_inbound", replace_opening_estimate=True, **identity,
    )
    assert receipt.total_cost == Decimal("14.0000")
    assert stock.cost_balance == Decimal("70.0000")
    assert stock.avg_unit_cost == Decimal("7.00000000")
    assert stock.cost_status == "actual"
    assert stock.opening_cost_pending is False
    audit = db.execute(select(InventoryCostAdjustment).where(InventoryCostAdjustment.inventory_id == stock.id)).scalar_one()
    assert audit.old_cost_balance == Decimal("40")
    assert audit.new_cost_balance == Decimal("56.0000")
    stock, _ = core.apply_inventory_change(
        db, warehouse_id=1, quantity=Decimal("2"), unit_cost=Decimal("9"),
        transaction_type="manual_inbound", replace_opening_estimate=True, **identity,
    )
    assert stock.cost_balance == Decimal("88.0000")
    assert stock.avg_unit_cost == Decimal("7.33333333")
    assert db.execute(select(InventoryCostAdjustment).where(InventoryCostAdjustment.inventory_id == stock.id)).scalars().all() == [audit]


def test_unpriced_existing_stock_stays_flagged_after_receipt(db):
    db.add(Inventory(
        warehouse_id=1, material_id=103, quantity=Decimal("10"),
        avg_unit_cost=0, cost_balance=0, cost_status="unpriced",
    ))
    db.flush()
    stock, _ = core.apply_inventory_change(
        db, warehouse_id=1, material_id=103, quantity=Decimal("10"),
        unit_cost=Decimal("4"), transaction_type="purchase_inbound",
    )
    assert stock.avg_unit_cost == Decimal("2.00000000")
    assert stock.cost_status == "unpriced"


def test_finance_cost_adjustment_preserves_quantity_and_audit(db):
    user = User(id=1, username="finance", full_name="财务", hashed_password="unused", role_id=1)
    stock = Inventory(warehouse_id=1, product_id=104, quantity=Decimal("10"), avg_unit_cost=0, cost_balance=0, cost_status="unpriced")
    db.add_all([user, stock])
    db.flush()
    result = adjust_inventory_cost(
        stock.id, InventoryCostAdjustmentPayload(unit_cost=Decimal("5.12345678"), reason="期初盘点暂估"),
        current_user=user, db=db,
    )
    assert result["data"]["inventory_amount"] == 51.2346
    db.refresh(stock)
    assert stock.quantity == Decimal("10")
    assert stock.cost_balance == Decimal("51.2346")
    assert stock.cost_status == "estimated"
    audit = db.execute(select(InventoryCostAdjustment).where(InventoryCostAdjustment.inventory_id == stock.id)).scalar_one()
    assert audit.reason == "期初盘点暂估"


def test_sales_profit_uses_posted_cost_and_refuses_unpriced(db):
    order = SalesOrder(
        id=500, order_no="S500", customer_id=1, sales_user_id=1, operator_user_id=1,
        consignee="收货人", consignee_phone="123", delivery_address="地址", payment_type="full",
        status="shipped", total_amount=Decimal("100"), transport_fee=Decimal("5"), packaging_fee=Decimal("2"),
    )
    db.add(order)
    db.add(InventoryTransaction(
        transaction_no="TX500", transaction_type="sales_outbound", warehouse_id=1,
        product_id=105, quantity=Decimal("-2"), unit_cost=Decimal("20"),
        total_cost=Decimal("-40"), cost_status="actual", related_type="sales_order", related_id=500,
    ))
    db.add(InventoryTransaction(
        transaction_no="TX500P", transaction_type="sales_packaging_outbound", warehouse_id=1,
        packaging_config_id=106, quantity=Decimal("-1"), unit_cost=Decimal("2"),
        total_cost=Decimal("-2"), cost_status="actual", related_type="sales_order", related_id=500,
    ))
    db.flush()
    sync_sales_order_cost(db, order)
    assert order.cost_amount == Decimal("42")
    assert order.profit_amount == Decimal("53")
    assert order.cost_status == "actual"
    db.add(InventoryTransaction(
        transaction_no="TX501", transaction_type="sales_packaging_outbound", warehouse_id=1,
        packaging_config_id=106, quantity=Decimal("-1"), unit_cost=0,
        total_cost=0, cost_status="unpriced", related_type="sales_order", related_id=500,
    ))
    sync_sales_order_cost(db, order)
    assert order.cost_status == "unpriced"
    assert order.profit_amount == 0


def test_production_inbound_uses_posted_material_plus_labor_and_overhead(db, monkeypatch):
    monkeypatch.setattr(workflow, "_sync_inventory_request_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "create_notification", lambda *args, **kwargs: None)
    actor = User(id=2, username="keeper", full_name="库管", hashed_password="unused", role_id=1)
    task = ProductionTask(
        id=600, task_no="SC600", product_id=201, planned_quantity=Decimal("10"),
        produced_quantity=Decimal("8"), scrap_quantity=Decimal("2"), created_by_id=2,
        direct_labor_cost=Decimal("10"), manufacturing_overhead_cost=Decimal("5"),
    )
    usage = ProductionMaterialUsage(production_task_id=600, material_id=202, planned_quantity=Decimal("20"))
    issue = InventoryOperationRequest(
        id=601, request_no="CK601", operation_type="outbound", request_category="production_material_issue",
        source_module="production", source_type="production_task", source_id=600, warehouse_id=1,
        requested_by_id=2, status="completed", original_items_json=[], handled_items_json=[],
    )
    inbound = InventoryOperationRequest(
        id=602, request_no="RK602", operation_type="inbound", request_category="production_finished_inbound",
        source_module="production", source_type="production_task", source_id=600, warehouse_id=1,
        requested_by_id=2, status="pending",
        original_items_json=[{"product_id": 201, "material_id": None, "packaging_config_id": None, "batch_no": "B1", "quantity": 8, "unit_cost": 0}],
        handled_items_json=[],
    )
    db.add_all([actor, task, usage, issue, inbound, InventoryTransaction(
        transaction_no="TX601", transaction_type="manual_outbound", warehouse_id=1,
        material_id=202, quantity=Decimal("-20"), unit_cost=Decimal("2"), total_cost=Decimal("-40"),
        cost_status="actual", related_type="inventory_operation_request", related_id=601,
    )])
    db.commit()
    workflow.complete_inventory_operation_request(
        inbound.id, InventoryOperationHandleRequest(items=inbound.original_items_json), actor, db,
    )
    stock = db.execute(select(Inventory).where(Inventory.product_id == 201)).scalar_one()
    assert stock.quantity == Decimal("8")
    assert stock.cost_balance == Decimal("55")
    assert stock.avg_unit_cost == Decimal("6.87500000")
    assert stock.cost_status == "estimated"

    duplicate = InventoryOperationRequest(
        request_no="RK603", operation_type="inbound", request_category="production_finished_inbound",
        source_module="production", source_type="production_task", source_id=600, warehouse_id=1,
        requested_by_id=2, status="pending", original_items_json=inbound.original_items_json, handled_items_json=[],
    )
    db.add(duplicate)
    db.commit()
    with pytest.raises(HTTPException, match="重复结转"):
        workflow.complete_inventory_operation_request(
            duplicate.id, InventoryOperationHandleRequest(items=duplicate.original_items_json), actor, db,
        )
