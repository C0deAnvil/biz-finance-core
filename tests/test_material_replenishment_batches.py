from decimal import Decimal
from sqlalchemy import select

from database import SessionLocal
from helpers import (create_bom, create_material, create_production_product, create_supplier,
                     set_supplier_catalog, raw_material_inbound, warehouse_map, uniq)
from models import (Inventory, InventoryOperationRequest, MaterialReplenishmentDemand, PurchaseOrder,
                    PurchaseOrderItem, PurchaseReceipt, ProductionTask, TodoItem, User)
from services.material_replenishment_service import SOURCE_TYPE


def setup_material(client, login):
    mid = create_material(client, login)
    pid = create_production_product(client, login)
    create_bom(client, login, product_id=pid, material_id=mid, standard_quantity=2)
    return mid, pid


def demand(client, login, pid, qty=50):
    login("inventory01")
    response = client.post("/api/inventory/fulfillment-demands", json={"product_id": pid, "shortage_quantity": qty})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def progress(client, login, did):
    login("production01")
    response = client.get(f"/api/production/fulfillment-demands/{did}/material-progress")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def submit(client, login, did, mid, qty, expected=200):
    login("production01")
    response = client.post(f"/api/production/fulfillment-demands/{did}/raw-material-demands",
                           json={"items": [{"material_id": mid, "shortage_quantity": qty}]})
    assert response.status_code == expected, response.text
    return response.json()


def purchase(client, login, sid, mid, batches, qty):
    login("purchase01")
    payload = {"supplier_id": sid, "payment_type": "货到付款", "expected_arrival_date": "2099-01-15", "items": [{"material_id": mid, "quantity": qty, "unit_price": 1}]}
    if len(batches) == 1:
        payload.update(replenishment_source_type=SOURCE_TYPE, replenishment_source_id=batches[0]["id"])
    else:
        payload["replenishment_sources"] = [{"source_type": SOURCE_TYPE, "source_id": batch["id"]} for batch in batches]
    response = client.post("/api/purchases/orders", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_append_after_purchase_and_cumulative_totals(client, login):
    mid, pid = setup_material(client, login)
    did = demand(client, login, pid)
    sid = create_supplier(client, login)
    set_supplier_catalog(client, login, supplier_id=sid, material_id=mid)
    first = submit(client, login, did, mid, 60)["data"]["raw_demand_nos"][0]
    data = progress(client, login, did)
    batch1 = data["replenishment_demands"][0]
    po1 = purchase(client, login, sid, mid, [batch1], 60)
    assert progress(client, login, did)["items"][0]["remaining_replenishment_quantity"] == 40
    second = submit(client, login, did, mid, 40)["data"]["raw_demand_nos"][0]
    assert first != second
    data = progress(client, login, did)
    assert data["items"][0]["submitted_quantity"] == 100
    assert data["items"][0]["remaining_replenishment_quantity"] == 0
    assert data["replenishment_demands"][0]["purchase_order_id"] == po1
    assert data["replenishment_demands"][0]["items"][0]["shortage_quantity"] == 60
    submit(client, login, did, mid, 1, 409)
    po2 = purchase(client, login, sid, mid, [data["replenishment_demands"][1]], 40)
    assert po1 != po2
    other = demand(client, login, pid)
    assert progress(client, login, other)["items"][0]["submitted_quantity"] == 0
    with SessionLocal() as db:
        assert len(db.scalars(select(MaterialReplenishmentDemand).where(MaterialReplenishmentDemand.fulfillment_demand_id == did)).all()) == 2
        assert db.scalar(select(PurchaseOrderItem.quantity).where(PurchaseOrderItem.purchase_order_id == po1)) == 60


def test_decimal_replenishment_accepts_exact_remaining_quantity(client, login):
    mid, pid = setup_material(client, login)
    did = demand(client, login, pid, qty=2.12)
    # BOM 用量为 2，原料总需求 4.24；分两次提交验证小数累计和边界。
    submit(client, login, did, mid, 2.12)
    assert progress(client, login, did)["items"][0]["remaining_replenishment_quantity"] == 2.12
    submit(client, login, did, mid, 2.13, 409)
    submit(client, login, did, mid, 0, 422)
    submit(client, login, did, mid, 2.12)
    data = progress(client, login, did)
    assert data["items"][0]["submitted_quantity"] == 4.24
    assert data["items"][0]["remaining_replenishment_quantity"] == 0
    assert len(data["replenishment_demands"]) == 2
    submit(client, login, did, mid, 0.01, 409)


def test_progress_respects_other_task_commitment(client, login):
    mid, pid = setup_material(client, login)
    wh = warehouse_map(client, login)["raw"]
    raw_material_inbound(client, login, material_id=mid, quantity=80, warehouse_id=wh)
    login("production01")
    response = client.post("/api/production/tasks", json={"product_id": pid, "planned_quantity": 15, "source_type": "manual"})
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]["id"]
    did = demand(client, login, pid)
    data = progress(client, login, did)
    item = data["items"][0]
    assert item["stock_quantity"] == 80
    assert item["production_reserved_quantity"] == 30
    assert item["stock_available_quantity"] == 50
    assert item["remaining_replenishment_quantity"] == 50
    response = client.get("/api/production/raw-material-shortage", params={"product_id": pid, "planned_quantity": 50})
    assert response.json()["data"][0]["available_quantity"] == 50
    # 创建任务的硬校验与弹窗口径一致。
    response = client.post("/api/production/tasks", json={"product_id": pid, "planned_quantity": 26, "source_type": "manual"})
    assert response.status_code == 409
    with SessionLocal() as db:
        db.get(ProductionTask, task_id).status = "cancelled"
        db.commit()
    assert progress(client, login, did)["items"][0]["stock_available_quantity"] == 80


def test_merged_purchase_allocates_receipts_once_and_reopens_all(client, login):
    mid, pid = setup_material(client, login)
    d1, d2 = demand(client, login, pid), demand(client, login, pid)
    sid = create_supplier(client, login)
    set_supplier_catalog(client, login, supplier_id=sid, material_id=mid)
    submit(client, login, d1, mid, 60)
    submit(client, login, d2, mid, 40)
    b1, b2 = progress(client, login, d1)["replenishment_demands"][0], progress(client, login, d2)["replenishment_demands"][0]
    po = purchase(client, login, sid, mid, [b1, b2], 100)
    # 作为合并单次要来源的 YL 也必须被防重校验保护。
    login("purchase01")
    response = client.post("/api/purchases/orders", json={"supplier_id": sid, "payment_type": "货到付款",
        "expected_arrival_date": "2099-01-15",
        "items": [{"material_id": mid, "quantity": 40, "unit_price": 1}],
        "replenishment_source_type": SOURCE_TYPE, "replenishment_source_id": b2["id"]})
    assert response.status_code == 409
    response = client.delete(f"/api/purchases/orders/{po}")
    assert response.status_code == 200, response.text
    for did in (d1, d2):
        assert progress(client, login, did)["replenishment_demands"][0]["purchase_order_id"] is None
    po = purchase(client, login, sid, mid, [b1, b2], 100)
    wh = warehouse_map(client, login)["raw"]
    with SessionLocal() as db:
        buyer = db.scalar(select(User).where(User.username == "purchase01"))
        receipt = PurchaseReceipt(receipt_no=uniq("DH"), purchase_order_id=po, warehouse_id=wh, received_by_id=buyer.id,
                                  requires_quality=True, quality_status="pending", status="pending_quality")
        db.add(receipt)
        db.scalar(select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po)).received_quantity = 100
        db.commit()
        rid, uid = receipt.id, buyer.id
    assert progress(client, login, d1)["items"][0]["outstanding_quantity"] == 60
    assert progress(client, login, d2)["items"][0]["outstanding_quantity"] == 40
    # 模拟质检后完成 70 的实际入库，按需求顺序分配为 60 + 10，不能双方都扣 70。
    with SessionLocal() as db:
        db.add(InventoryOperationRequest(request_no=uniq("RK"), warehouse_id=wh, operation_type="inbound",
            request_category="purchase_quality_inbound", source_module="purchase", source_type="purchase_receipt",
            source_id=rid, requested_by_id=uid, status="completed", original_items_json=[],
            handled_items_json=[{"material_id": mid, "quantity": 70}]))
        db.add(Inventory(warehouse_id=wh, material_id=mid, quantity=70, reserved_quantity=0, avg_unit_cost=1))
        db.commit()
    a, b = progress(client, login, d1)["items"][0], progress(client, login, d2)["items"][0]
    assert (a["submitted_quantity"], b["submitted_quantity"]) == (60, 40)
    assert (a["outstanding_quantity"], b["outstanding_quantity"]) == (0, 30)
    assert (a["remaining_replenishment_quantity"], b["remaining_replenishment_quantity"]) == (30, 0)
