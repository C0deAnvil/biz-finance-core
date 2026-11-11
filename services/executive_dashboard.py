"""Executive operating cockpit aggregations.

The cockpit deliberately keeps accounting-oriented figures on the same date
basis as the finance overview, while operational cards use their own business
timestamps (shipment, completion, receipt, and scan time).
"""

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from models import (
    Customer,
    FulfillmentDemand,
    Inventory,
    InventoryDispatchQueue,
    ProductionTask,
    PurchaseOrder,
    PurchaseReceipt,
    PurchaseRequest,
    SalesOrder,
    SalesOrderItem,
    User,
)


ZERO = Decimal("0")
FINISHED_STATUSES = {"completed", "fulfilled", "closed", "converted", "received", "shipped"}
PROVINCES = (
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏", "浙江",
    "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川",
    "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "香港", "澳门", "台湾",
)


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min), datetime.combine(date_to, time.max)


def _number(value, places: str = "0.0000") -> str:
    return str(Decimal(value or 0).quantize(Decimal(places)))


def _province(customer: Customer | None, address: str | None) -> str:
    text = " ".join(filter(None, [customer.region_province if customer else None, customer.address if customer else None, address]))
    for province in PROVINCES:
        if province in text:
            return province
    return "其他"


def _duration_hours(started_at: datetime | None, ended_at: datetime | None) -> Decimal | None:
    if not started_at or not ended_at or ended_at < started_at:
        return None
    return Decimal(str((ended_at - started_at).total_seconds() / 3600))


def _average(values: list[Decimal]) -> str:
    return _number(sum(values, ZERO) / len(values), "0.00") if values else "0.00"


def _status_counts(rows) -> list[dict]:
    buckets: dict[str, int] = defaultdict(int)
    for row in rows:
        buckets[str(row.status or "unknown")] += 1
    return [{"status": status, "count": count} for status, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0]))]


def build_executive_dashboard(db: Session, *, date_from: date, date_to: date) -> dict:
    start, end = _bounds(date_from, date_to)

    sales = db.execute(
        select(SalesOrder)
        .options(
            joinedload(SalesOrder.customer),
            joinedload(SalesOrder.sales_user),
            selectinload(SalesOrder.items).joinedload(SalesOrderItem.product),
        )
        .where(
            SalesOrder.status.notin_(("draft", "cancelled")),
            SalesOrder.created_at.between(start, end),
        )
    ).unique().scalars().all()

    # The map and rankings answer "where were orders placed" and therefore use
    # order submission time.  Financial headline sales remain delivery-based.
    shipped_sales = db.execute(
        select(SalesOrder).where(
            SalesOrder.status.in_(("shipped", "completed")),
            func.coalesce(SalesOrder.shipped_at, SalesOrder.completed_at).between(start, end),
        )
    ).scalars().all()

    province_map: dict[str, dict] = {}
    product_map: dict[int, dict] = {}
    seller_map: dict[int, dict] = {}
    sales_amount = sum((Decimal(order.total_amount or 0) for order in shipped_sales), ZERO)
    sales_cost = sum((Decimal(order.cost_amount or 0) for order in shipped_sales), ZERO)
    priced_sales = [order for order in shipped_sales if order.cost_status != "unpriced"]
    sales_profit = sum((Decimal(order.profit_amount or 0) for order in priced_sales), ZERO)
    gross_profit = sum((Decimal(order.total_amount or 0) - Decimal(order.cost_amount or 0) for order in priced_sales), ZERO)
    gross_margin = (gross_profit / sales_amount * Decimal("100")) if sales_amount else ZERO
    for order in sales:
        amount = Decimal(order.total_amount or 0)

        province = _province(order.customer, order.delivery_address)
        region = province_map.setdefault(province, {"province": province, "amount": ZERO, "orders": 0, "quantity": ZERO})
        region["amount"] += amount
        region["orders"] += 1

        seller_id = order.sales_user_id
        seller = seller_map.setdefault(seller_id, {
            "user_id": seller_id,
            "name": order.sales_user.full_name if order.sales_user else "未分配",
            "amount": ZERO,
            "orders": 0,
            "quantity": ZERO,
            "customers": set(),
        })
        seller["amount"] += amount
        seller["orders"] += 1
        if order.customer_id:
            seller["customers"].add(order.customer_id)

        for item in order.items:
            quantity = Decimal(item.shipped_quantity or item.quantity or 0)
            region["quantity"] += quantity
            seller["quantity"] += quantity
            if item.product is None:
                continue
            product = product_map.setdefault(item.product_id, {
                "product_id": item.product_id,
                "code": item.product.product_code,
                "name": item.product.name,
                "specification": item.product.specification or "无规格",
                "amount": ZERO,
                "quantity": ZERO,
                "orders": set(),
            })
            product["amount"] += Decimal(item.total_amount or 0)
            product["quantity"] += quantity
            product["orders"].add(order.id)

    region_sales = [{
        **row,
        "amount": _number(row["amount"]),
        "quantity": _number(row["quantity"]),
    } for row in province_map.values()]
    region_sales.sort(key=lambda row: Decimal(row["amount"]), reverse=True)
    product_sales = [{
        **row,
        "amount": _number(row["amount"]),
        "quantity": _number(row["quantity"]),
        "orders": len(row["orders"]),
    } for row in product_map.values()]
    product_sales.sort(key=lambda row: Decimal(row["amount"]), reverse=True)
    seller_performance = [{
        **row,
        "amount": _number(row["amount"]),
        "quantity": _number(row["quantity"]),
        "customers": len(row["customers"]),
    } for row in seller_map.values()]
    seller_performance.sort(key=lambda row: Decimal(row["amount"]), reverse=True)

    inventories = db.execute(
        select(Inventory)
        .options(
            joinedload(Inventory.warehouse), joinedload(Inventory.product),
            joinedload(Inventory.material), joinedload(Inventory.packaging_config),
        )
    ).scalars().all()
    warehouse_map: dict[int, dict] = {}
    inventory_types = defaultdict(lambda: {"quantity": ZERO, "value": ZERO, "items": 0})
    warning_count = 0
    unpriced_count = 0
    for inventory in inventories:
        quantity = Decimal(inventory.quantity or 0)
        value = Decimal(inventory.cost_balance or 0)
        warehouse = warehouse_map.setdefault(inventory.warehouse_id, {
            "warehouse_id": inventory.warehouse_id,
            "name": inventory.warehouse.name if inventory.warehouse else "未知仓库",
            "location": inventory.warehouse.location if inventory.warehouse else "",
            "quantity": ZERO,
            "reserved": ZERO,
            "value": ZERO,
            "sku_count": 0,
            "warning_count": 0,
        })
        warehouse["quantity"] += quantity
        warehouse["reserved"] += Decimal(inventory.reserved_quantity or 0)
        warehouse["value"] += value
        if quantity > 0:
            warehouse["sku_count"] += 1
        if inventory.product_id:
            item_type = "产品"
            safety_stock = Decimal(inventory.product.safety_stock_qty or 0) if inventory.product else ZERO
        elif inventory.material_id:
            item_type = "原料"
            safety_stock = Decimal(inventory.material.safety_stock_qty or 0) if inventory.material else ZERO
        else:
            item_type = "包装"
            safety_stock = ZERO
        inventory_types[item_type]["quantity"] += quantity
        inventory_types[item_type]["value"] += value
        if quantity > 0:
            inventory_types[item_type]["items"] += 1
        if safety_stock > 0 and quantity - Decimal(inventory.reserved_quantity or 0) < safety_stock:
            warning_count += 1
            warehouse["warning_count"] += 1
        if quantity > 0 and inventory.cost_status == "unpriced":
            unpriced_count += 1

    warehouses = [{
        **row,
        "quantity": _number(row["quantity"]),
        "reserved": _number(row["reserved"]),
        "available": _number(max(row["quantity"] - row["reserved"], ZERO)),
        "value": _number(row["value"]),
    } for row in warehouse_map.values()]
    warehouses.sort(key=lambda row: Decimal(row["quantity"]), reverse=True)
    max_warehouse_qty = max([Decimal(row["quantity"]) for row in warehouses] or [ZERO])
    for row in warehouses:
        row["relative_level"] = round(float(Decimal(row["quantity"]) / max_warehouse_qty * 100), 1) if max_warehouse_qty else 0

    purchase_orders = db.execute(
        select(PurchaseOrder)
        .where(PurchaseOrder.created_at.between(start, end), PurchaseOrder.status != "draft")
    ).scalars().all()
    purchase_amount = sum((Decimal(row.total_amount or 0) for row in purchase_orders), ZERO)
    purchase_receipts = db.execute(
        select(PurchaseReceipt).where(PurchaseReceipt.recorded_at.between(start, end))
    ).scalars().all()
    purchase_requests = db.execute(
        select(PurchaseRequest).where(PurchaseRequest.created_at.between(start, end))
    ).scalars().all()

    production_tasks = db.execute(
        select(ProductionTask).where(ProductionTask.created_at.between(start, end))
    ).scalars().all()
    planned_quantity = sum((Decimal(row.planned_quantity or 0) for row in production_tasks), ZERO)
    produced_quantity = sum((Decimal(row.produced_quantity or 0) for row in production_tasks), ZERO)

    queues = db.execute(
        select(InventoryDispatchQueue)
        .where(InventoryDispatchQueue.created_at.between(start, end))
    ).scalars().all()
    placement_times = [value for row in queues if (value := _duration_hours(row.created_at, row.placement_marked_at)) is not None]
    shipment_times = [value for row in queues if (value := _duration_hours(row.placement_marked_at or row.created_at, row.shipment_marked_at)) is not None]
    production_times = [value for row in production_tasks if (value := _duration_hours(row.started_at or row.created_at, row.completed_at)) is not None]
    receipt_times = []
    order_created = {row.id: row.created_at for row in purchase_orders}
    for receipt in purchase_receipts:
        value = _duration_hours(order_created.get(receipt.purchase_order_id), receipt.recorded_at)
        if value is not None:
            receipt_times.append(value)

    fulfillment_demands = db.execute(
        select(FulfillmentDemand).where(FulfillmentDemand.created_at.between(start, end))
    ).scalars().all()
    demand_groups = {}
    for mode, label in (("trade", "采购补货"), ("production", "生产补货")):
        rows = [row for row in fulfillment_demands if row.fulfillment_mode == mode]
        durations = [
            value for row in rows
            if row.status in FINISHED_STATUSES and (value := _duration_hours(row.created_at, row.updated_at)) is not None
        ]
        demand_groups[mode] = {
            "label": label,
            "total": len(rows),
            "pending": sum(1 for row in rows if row.status not in FINISHED_STATUSES and row.status != "cancelled"),
            "completed": sum(1 for row in rows if row.status in FINISHED_STATUSES),
            "avg_hours": _average(durations),
        }

    return {
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "headline": {
            "sales_amount": _number(sales_amount),
            "sales_orders": len(shipped_sales),
            "sales_cost": _number(sales_cost),
            "sales_profit": _number(sales_profit),
            "gross_profit": _number(gross_profit),
            "gross_margin": _number(gross_margin, "0.00"),
            "purchase_amount": _number(purchase_amount),
            "purchase_orders": len(purchase_orders),
            "inventory_value": _number(sum((Decimal(row["value"]) for row in warehouses), ZERO)),
            "inventory_quantity": _number(sum((Decimal(row["quantity"]) for row in warehouses), ZERO)),
        },
        "sales": {
            "regions": region_sales,
            "products": product_sales[:20],
            "sellers": seller_performance[:20],
        },
        "inventory": {
            "warehouses": warehouses,
            "types": [{"name": name, "quantity": _number(row["quantity"]), "value": _number(row["value"]), "items": row["items"]} for name, row in inventory_types.items()],
            "warning_count": warning_count,
            "unpriced_count": unpriced_count,
        },
        "operations": {
            "purchase": {
                "orders": len(purchase_orders), "amount": _number(purchase_amount),
                "receipts": len(purchase_receipts), "requests": len(purchase_requests),
                "statuses": _status_counts(purchase_orders),
            },
            "production": {
                "tasks": len(production_tasks), "planned_quantity": _number(planned_quantity),
                "produced_quantity": _number(produced_quantity), "statuses": _status_counts(production_tasks),
            },
            "shipment": {
                "demands": len(queues),
                "placed": sum(1 for row in queues if row.placement_status in {"hidden", "completed", "placed"}),
                "shipped": sum(1 for row in queues if row.shipment_status in {"completed", "shipped"}),
                "pending": sum(1 for row in queues if row.shipment_status not in {"completed", "shipped", "cancelled"}),
            },
        },
        "speed": {
            "purchase": {**demand_groups["trade"], "order_to_receipt_hours": _average(receipt_times)},
            "production": {**demand_groups["production"], "task_hours": _average(production_times)},
            "placement": {"label": "置货需求", "total": len(queues), "completed": len(placement_times), "pending": sum(1 for row in queues if not row.placement_marked_at), "avg_hours": _average(placement_times)},
            "shipment": {"label": "发货需求", "total": len(queues), "completed": len(shipment_times), "pending": sum(1 for row in queues if not row.shipment_marked_at), "avg_hours": _average(shipment_times)},
        },
    }
