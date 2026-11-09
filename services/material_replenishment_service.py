"""原料可用库存与按生产补货需求汇总的人工补货进度。"""
import json
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from models import (BOM, Inventory, InventoryOperationRequest, Material, MaterialReplenishmentDemand,
                    PurchaseOrder, PurchaseOrderItem, PurchaseReceipt, TodoItem)
from services.inventory_availability import material_production_commitments
from services.material_replenishment_confirmation import confirmed_material_quantities
from services.warehouse_lookup import get_raw_warehouse

SOURCE_TYPE = "production_material_replenishment"
LEGACY_SOURCE_TYPE = "sales_order_material_replenishment"
ZERO = Decimal("0")


def _material_rows(value, *, document_no: str, field_name: str) -> list[dict]:
    """Validate historical JSON rows before calculating material progress.

    A few old replenishment records were written before ``items_json`` had a
    stable structure.  Letting ``None``/dict/string values reach the loops
    below produces TypeError/AttributeError and the UI can only display a
    generic HTTP 500.  Treat this as repairable business data instead and tell
    the operator exactly which document must be checked.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(
            status_code=409,
            detail=f"{document_no} 的{field_name}格式异常，请联系管理员修复该单据后重试",
        )
    rows: list[dict] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=409,
                detail=f"{document_no} 的{field_name}第 {index} 行格式异常，请联系管理员修复该单据后重试",
            )
        rows.append(item)
    return rows


def material_stock_availability(db, material_ids, *, lock=False, exclude_fulfillment_demand_id=None):
    warehouse = get_raw_warehouse(db)
    rows = select(Inventory).where(Inventory.warehouse_id == warehouse.id,
                                   Inventory.material_id.in_(material_ids or [-1]), Inventory.product_id.is_(None))
    if lock:
        rows = rows.with_for_update()
    stocks = {mid: {"quantity": ZERO, "reserved_quantity": ZERO, "production_reserved_quantity": ZERO} for mid in material_ids}
    for row in db.scalars(rows):
        stocks[row.material_id]["quantity"] += Decimal(row.quantity or 0)
        stocks[row.material_id]["reserved_quantity"] += Decimal(row.reserved_quantity or 0)
    commitments = material_production_commitments(
        db,
        material_ids=material_ids,
        exclude_fulfillment_demand_id=exclude_fulfillment_demand_id,
    )
    for mid, quantity in commitments.items():
        stocks[mid]["production_reserved_quantity"] += quantity
    for stock in stocks.values():
        stock["available_quantity"] = max(stock["quantity"] - stock["reserved_quantity"] - stock["production_reserved_quantity"], ZERO)
    return stocks


def _order_material_totals(db, order_ids):
    received, inbound = defaultdict(Decimal), defaultdict(Decimal)
    for pid, mid, quantity in db.execute(
        select(PurchaseOrderItem.purchase_order_id, PurchaseOrderItem.material_id, PurchaseOrderItem.received_quantity)
        .where(PurchaseOrderItem.purchase_order_id.in_(order_ids or [-1]))
    ):
        received[pid, mid] += Decimal(quantity or 0)
    # 已到货不等于已入库；只扣除实际完成的入库作业，防止重复计算库存。
    for pid, handled_items in db.execute(
        select(PurchaseReceipt.purchase_order_id, InventoryOperationRequest.handled_items_json)
        .join(InventoryOperationRequest, InventoryOperationRequest.source_id == PurchaseReceipt.id)
        .where(PurchaseReceipt.purchase_order_id.in_(order_ids or [-1]),
               InventoryOperationRequest.request_category.in_(["purchase_receipt_inbound", "purchase_quality_inbound"]),
               InventoryOperationRequest.status == "completed")
    ):
        for item in _material_rows(
            handled_items,
            document_no=f"采购到货记录#{pid}",
            field_name="入库明细",
        ):
            inbound[pid, item.get("material_id")] += Decimal(str(item.get("quantity") or 0))
    return received, inbound


def material_demand_progress(db, demand, *, lock_stock=False):
    bom = db.execute(select(BOM).options(joinedload(BOM.items)).where(BOM.product_id == demand.product_id)).unique().scalar_one_or_none()
    requirements = {item.material_id: Decimal(demand.shortage_quantity or 0) * Decimal(item.standard_quantity)
                    for item in (bom.items if bom else []) if item.standard_quantity > 0}
    # 旧版同一 BH 的待办是同一份确认量的多接收人副本，不得累加。
    legacy = {}
    for todo in db.scalars(select(TodoItem).where(TodoItem.business_type == LEGACY_SOURCE_TYPE, TodoItem.business_id == demand.id)
                           .order_by(TodoItem.updated_at.desc(), TodoItem.id.desc())):
        try:
            payload = json.loads((todo.description or "").split("|sales_order_material_replenishment:", 1)[1])
        except (IndexError, TypeError, ValueError):
            continue
        if str(payload.get("fulfillment_demand_id")) == str(demand.id):
            for mid, quantity in confirmed_material_quantities(todo.description or "", payload).items():
                legacy.setdefault(mid, quantity)
    batch_query = select(MaterialReplenishmentDemand).where(MaterialReplenishmentDemand.fulfillment_demand_id == demand.id).order_by(MaterialReplenishmentDemand.id)
    if lock_stock:
        batch_query = batch_query.with_for_update()
    batches = db.scalars(batch_query).all()
    legacy_orders = db.scalars(select(PurchaseOrder).where(PurchaseOrder.replenishment_source_type == LEGACY_SOURCE_TYPE,
                                                         PurchaseOrder.replenishment_source_id == demand.id)).all()
    order_ids = {b.purchase_order_id for b in batches if b.purchase_order_id} | {p.id for p in legacy_orders}
    orders = {p.id: p for p in db.scalars(select(PurchaseOrder).where(PurchaseOrder.id.in_(order_ids or [-1]), PurchaseOrder.status != "cancelled"))}
    received, inbound = _order_material_totals(db, list(orders))
    # 合并采购时同一原料的到货/入库量按 YL 顺序分配，不能在多个 BH 下重复全额计入。
    allocations = {}
    for batch in db.scalars(select(MaterialReplenishmentDemand).where(MaterialReplenishmentDemand.purchase_order_id.in_(list(orders) or [-1]))
                            .order_by(MaterialReplenishmentDemand.id)):
        for item in _material_rows(batch.items_json, document_no=batch.demand_no, field_name="原料明细"):
            try:
                mid, qty = int(item["material_id"]), Decimal(str(item["shortage_quantity"]))
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"{batch.demand_no} 的原料编号或数量异常，请联系管理员修复该单据后重试",
                ) from exc
            key = (batch.purchase_order_id, mid)
            arrival, stocked = min(qty, received[key]), min(qty, inbound[key])
            allocations[batch.id, mid] = (arrival, stocked)
            received[key] -= arrival
            inbound[key] -= stocked
    submitted, explicit_required, outstanding, arrived = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    material_orders = defaultdict(list)
    for mid, qty in legacy.items():
        legacy_po = next((p for p in legacy_orders if p.id in orders), None)
        stocked = min(qty, inbound[legacy_po.id, mid]) if legacy_po else ZERO
        submitted[mid] += qty
        outstanding[mid] += max(qty - stocked, ZERO)
        if legacy_po:
            arrived[mid] += min(qty, received[legacy_po.id, mid])
            material_orders[mid].append(legacy_po)
    for batch in batches:
        for item in _material_rows(batch.items_json, document_no=batch.demand_no, field_name="原料明细"):
            try:
                mid, qty = int(item["material_id"]), Decimal(str(item["shortage_quantity"]))
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"{batch.demand_no} 的原料编号或数量异常，请联系管理员修复该单据后重试",
                ) from exc
            explicit_required[mid] = max(
                explicit_required[mid],
                Decimal(str(item.get("actual_required_quantity") or 0)),
            )
            arrival, stocked = allocations.get((batch.id, mid), (ZERO, ZERO))
            submitted[mid] += qty
            outstanding[mid] += max(qty - stocked, ZERO)
            arrived[mid] += arrival
            if batch.purchase_order_id in orders:
                material_orders[mid].append(orders[batch.purchase_order_id])
    ids = sorted(set(requirements) | set(submitted))
    # 当前需求查看自己的可生产量时，只扣除其他需求/任务的占用；否则会把
    # 本需求既作为“需要量”又作为“他人占用”重复扣减。
    stocks = material_stock_availability(
        db,
        ids,
        lock=lock_stock,
        exclude_fulfillment_demand_id=demand.id,
    )
    names = {m.id: m.name for m in db.scalars(select(Material).where(Material.id.in_(ids or [-1])))}
    items = []
    for mid in ids:
        system_required = requirements.get(mid, ZERO)
        # 人工提交量是最终业务需求；BOM 计算量仅作为默认建议。允许生产人员
        # 因损耗、安全余量等原因将 1000 调整为 1800，并贯穿采购与生产领料。
        required = max(system_required, explicit_required[mid], submitted[mid])
        stock = stocks[mid]
        pending = max(required - stock["available_quantity"] - outstanding[mid], ZERO)
        related = {p.id: p for p in material_orders[mid]}
        progress, label = "pending", "待提交补货"
        if required <= stock["available_quantity"]:
            progress, label = "done", "已满足"
        elif related:
            progress, label = ("arrived", "已到货") if arrived[mid] else ("ordered", "已下单")
        elif submitted[mid]:
            label = "待采购处理"
        items.append({"material_id": mid, "material_name": names.get(mid, f"原料#{mid}"),
                      "required_quantity": float(required), "system_required_quantity": float(system_required),
                      "stock_available_quantity": float(stock["available_quantity"]),
                      "stock_quantity": float(stock["quantity"]), "stock_reserved_quantity": float(stock["reserved_quantity"]),
                      "production_reserved_quantity": float(stock["production_reserved_quantity"]),
                      "submitted_quantity": float(submitted[mid]), "outstanding_quantity": float(outstanding[mid]),
                      "received_quantity": float(arrived[mid]), "remaining_replenishment_quantity": float(pending),
                      "progress": progress, "progress_label": label,
                      "related_po_no": "、".join(p.order_no for p in related.values()) or None})
    return {"demand_id": demand.id, "demand_no": demand.demand_no, "status": demand.status,
            "can_produce": bool(requirements) and all(i["stock_available_quantity"] >= i["required_quantity"] for i in items),
            "can_edit_replenishment": demand.status in {"pending", "pending_material"}, "items": items,
            "replenishment_demands": [{"id": b.id, "demand_no": b.demand_no,
                                        "items": _material_rows(b.items_json, document_no=b.demand_no, field_name="原料明细"),
                                        "purchase_order_id": b.purchase_order_id,
                                        "purchase_order_no": orders[b.purchase_order_id].order_no if b.purchase_order_id in orders else None}
                                       for b in batches]}
