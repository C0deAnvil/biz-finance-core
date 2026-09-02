"""销售域服务。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models import (
    BOM,
    Customer,
    Inventory,
    InventoryDispatchQueue,
    FulfillmentDemand,
    PackagingConfig,
    Product,
    ReceivableRecord,
    RefundRecord,
    Role,
    SalesOrder,
    SalesOrderApproval,
    SalesOrderItem,
    SalesOrderPriceChangeLog,
    SalesReceiptRequest,
    SalesReturn,
    SalesReturnItem,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.sales import SalesApprovalRequest, SalesMergedReceiptRequestCreate, SalesOrderCreate, SalesOrderUpdate, SalesReceiptApprovalRequest, SalesReceiptRequestCreate, SalesReturnApprovalRequest, SalesReturnCreate, SalesSettlementCollectRequest
from services.core import apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, ensure_notification, next_business_no, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity
from services.request_trace import emit
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_finished_warehouse
from services.inventory_dimensions import sync_inventory_dimensions
from services.time_utils import now_local


def _role_user_ids(db: Session, *role_codes: str, exclude_user_id: int | None = None) -> list[int]:
    user_ids = [
        user.id
        for user in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]
    if exclude_user_id is not None:
        user_ids = [user_id for user_id in user_ids if user_id != exclude_user_id]
    return list(dict.fromkeys(user_ids))


def _finished_warehouse(db: Session) -> Warehouse:
    return get_finished_warehouse(db)


def _chairman_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "chairman", workflow="chairman_workflow")


def _finance_user_ids(db: Session) -> list[int]:
    return resolve_workflow_recipients(db, "finance", workflow="finance_workflow")


def _manager_chairman_user_ids(db: Session) -> list[int]:
    return _role_user_ids(db, "manager", "chairman")


def _pending_finance_edit_confirm_user_ids(db: Session, order_id: int, business_type: str) -> list[int]:
    from models import TodoItem

    return [
        row.assigned_user_id
        for row in db.execute(
            select(TodoItem).where(
                TodoItem.business_type == business_type,
                TodoItem.business_id == order_id,
                TodoItem.status != "completed",
            )
        ).scalars().all()
    ]


def _has_pending_finance_edit_confirmation(db: Session, order_id: int) -> bool:
    return bool(
        _pending_finance_edit_confirm_user_ids(db, order_id, "sales_order_finance_edit_sales_confirm")
        or _pending_finance_edit_confirm_user_ids(db, order_id, "sales_order_finance_edit_inventory_confirm")
    )


def _resolve_sales_operator_user_id(db: Session, operator_user_id: int | None, current_user: User) -> int:
    candidate_id = operator_user_id or current_user.id
    user = db.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(User.id == candidate_id, User.is_active.is_(True), Role.code == "sales")
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=409, detail="经办人必须为启用状态的销售人员")
    return user.id


def _sales_received_amount(db: Session, order_id: int) -> Decimal:
    rows = db.execute(
        select(ReceivableRecord).where(ReceivableRecord.sales_order_id == order_id).order_by(ReceivableRecord.id.asc())
    ).scalars().all()
    total = Decimal("0")
    for row in rows:
        total += Decimal(row.amount or 0)
    return total


def _latest_sales_receipt_request(db: Session, order_id: int) -> SalesReceiptRequest | None:
    return db.execute(
        select(SalesReceiptRequest)
        .where(SalesReceiptRequest.sales_order_id == order_id)
        .order_by(SalesReceiptRequest.id.desc())
    ).scalars().first()


def _load_order(db: Session, order_id: int) -> SalesOrder | None:
    return (
        db.execute(
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.customer),
                joinedload(SalesOrder.sales_user),
                joinedload(SalesOrder.operator_user),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            )
            .where(SalesOrder.id == order_id)
        )
        .unique()
        .scalar_one_or_none()
    )


def _ensure_order_owner_or_admin(order: SalesOrder, current_user: User) -> None:
    role_code = current_user.role.code if current_user.role else None
    if role_code == "sales" and order.sales_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可操作本人提交的客户订单")


def _payload_value(item_payload, field_name: str):
    if isinstance(item_payload, dict):
        return item_payload.get(field_name)
    return getattr(item_payload, field_name)


def _resolve_line_packaging(db: Session, *, product_id: int, packaging_config_id: int | None) -> PackagingConfig | None:
    def first_global_packaging() -> PackagingConfig | None:
        return db.execute(
            select(PackagingConfig)
            .where(
                PackagingConfig.entity_type == "global",
                PackagingConfig.entity_id == 0,
                PackagingConfig.is_active.is_(True),
            )
            .order_by(PackagingConfig.is_default.desc(), PackagingConfig.id.asc())
        ).scalars().first()

    def matching_global_packaging(packaging: PackagingConfig) -> PackagingConfig | None:
        return db.execute(
            select(PackagingConfig)
            .where(
                PackagingConfig.entity_type == "global",
                PackagingConfig.entity_id == 0,
                PackagingConfig.is_active.is_(True),
                PackagingConfig.packaging_name == packaging.packaging_name,
                PackagingConfig.kg_per_bag == packaging.kg_per_bag,
            )
            .order_by(PackagingConfig.is_default.desc(), PackagingConfig.id.asc())
        ).scalars().first()

    packaging: PackagingConfig | None = None
    if packaging_config_id:
        packaging = db.execute(
            select(PackagingConfig).where(
                PackagingConfig.id == packaging_config_id,
                PackagingConfig.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if packaging is None:
            raise HTTPException(status_code=404, detail="包装配置不存在或已停用")
        if packaging.entity_type == "product" and packaging.entity_id != product_id:
            raise HTTPException(status_code=409, detail="所选包装配置与商品不匹配")
        if packaging.entity_type not in {"global", "product"}:
            raise HTTPException(status_code=409, detail="销售订单只能选择商品或通用包装")
        if packaging.entity_type == "product" and Decimal(packaging.unit_price or 0) <= 0:
            global_packaging = matching_global_packaging(packaging)
            if global_packaging is not None:
                return global_packaging
        return packaging
    packaging = first_global_packaging()
    if packaging is not None:
        return packaging
    return get_default_packaging_config(db, entity_type="product", entity_id=product_id)


def _calculate_packaging_fee(quantity: Decimal, packaging: PackagingConfig | None) -> tuple[Decimal | None, Decimal, Decimal]:
    if packaging is None:
        return None, Decimal("0"), Decimal("0")
    bag_count = calculate_bag_count(quantity, packaging.kg_per_bag)
    if bag_count is None:
        return None, Decimal(packaging.unit_price or 0), Decimal("0")
    unit_price = Decimal(packaging.unit_price or 0)
    packaging_fee = (Decimal(bag_count) * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return bag_count, unit_price, packaging_fee


def _build_order_items(
    db: Session,
    *,
    order_id: int,
    payload_items,
    enforce_price_remark: bool = True,
    create_price_approval: bool = True,
) -> Decimal:
    total_amount = Decimal("0")
    packaging_total = Decimal("0")
    price_change_notices: list[str] = []
    order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one_or_none()
    operator_user_id = (order.operator_user_id or order.sales_user_id) if order else None
    for item_payload in payload_items:
        product_id = _payload_value(item_payload, "product_id")
        quantity = Decimal(str(_payload_value(item_payload, "quantity") or 0))
        unit_price = Decimal(str(_payload_value(item_payload, "unit_price") or 0))
        packaging_config_id = _payload_value(item_payload, "packaging_config_id")
        remark = _payload_value(item_payload, "remark")
        price_change_remark = str(_payload_value(item_payload, "price_change_remark") or "").strip()
        product = db.execute(
            select(Product).where(Product.id == product_id, Product.is_active.is_(True))
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail=f"产品不存在或已停用：{product_id}")
        standard_price = Decimal(product.price or 0)
        if enforce_price_remark and unit_price != standard_price and len(price_change_remark) < 2:
            raise HTTPException(status_code=409, detail=f"产品 {product.name} 单价与标准价格不一致，请填写改价备注")

        packaging = _resolve_line_packaging(db, product_id=product_id, packaging_config_id=packaging_config_id)
        bag_count, packaging_unit_price, packaging_fee = _calculate_packaging_fee(quantity, packaging)
        product_amount = quantity * unit_price
        line_total = product_amount + packaging_fee
        total_amount += line_total
        packaging_total += packaging_fee
        order_item = SalesOrderItem(
            sales_order_id=order_id,
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            total_amount=line_total,
            packaging_config_id=packaging.id if packaging else None,
            kg_per_bag=packaging.kg_per_bag if packaging else None,
            bag_count=bag_count,
            packaging_unit_price=packaging_unit_price,
            packaging_fee=packaging_fee,
            display_specification=packaging_spec_text(packaging.kg_per_bag) if packaging else product.specification,
            remark=remark,
            shipped_quantity=Decimal("0"),
        )
        db.add(order_item)
        db.flush()
        if create_price_approval and unit_price != standard_price:
            if operator_user_id is None:
                raise HTTPException(status_code=500, detail="无法记录销售改价人员")
            db.add(
                SalesOrderPriceChangeLog(
                    sales_order_id=order_id,
                    sales_order_item_id=order_item.id,
                    product_id=product_id,
                    standard_price=standard_price,
                    actual_price=unit_price,
                    remark=price_change_remark,
                    changed_by_id=operator_user_id,
                )
            )
            price_change_notices.append(f"{product.name} 标准价 {standard_price}，订单价 {unit_price}，备注：{price_change_remark}")
    if order is not None:
        order.packaging_fee = packaging_total
    if price_change_notices:
        finance_user_ids = _finance_user_ids(db)
        if order is not None:
            order.price_approval_status = "pending"
            if order.status == "pending_confirmation":
                order.status = "pending_price_approval"
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"待审批销售改价 {order.order_no if order else order_id}",
                description="客户订单商品价格与标准价格不一致，请财务审核改价备注和明细。",
                module="finance",
                assigned_user_id=user_id,
                business_type="sales_order_price_approval",
                business_id=order_id,
                priority="high",
            )
        create_notification(
            db,
            title="客户订单存在销售改价",
            content=f"客户订单 {order.order_no if order else order_id} 存在商品价格调整：" + "；".join(price_change_notices),
            module="sales",
            recipient_user_ids=list(dict.fromkeys([*finance_user_ids, *_manager_chairman_user_ids(db)])),
            created_by_id=operator_user_id,
            category="warning",
        )
    return total_amount


def _create_dispatch_queue(db: Session, sales_order_id: int) -> None:
    db.add(
        InventoryDispatchQueue(
            sales_order_id=sales_order_id,
            placement_status="pending",
            shipment_status="waiting_approval",
        )
    )


def _create_inventory_placement_todos(db: Session, *, order: SalesOrder) -> None:
    inventory_user_ids = _role_user_ids(db, "inventory")
    for user_id in inventory_user_ids:
        create_todo(
            db,
            title=f"待置货客户订单 {order.order_no}",
            description="客户订单已进入待置货列表，请库管提前完成置货准备。",
            module="inventory",
            assigned_user_id=user_id,
            business_type="sales_order_placement",
            business_id=order.id,
            priority="high",
        )


def _create_order_todos_and_notice(db: Session, *, order: SalesOrder, customer_name: str, current_user: User) -> None:
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    chairman_user_ids = _chairman_user_ids(db)
    finance_user_ids = _finance_user_ids(db)
    inventory_user_ids = _role_user_ids(db, "inventory", exclude_user_id=current_user.id)
    _create_inventory_placement_todos(db, order=order)
    create_notification(
        db,
        title="客户订单已提交",
        content=(
            f"客户订单 {order.order_no} 已提交，"
            f"{'等待销售在收到款项后提交收款确认申请。' if payment_type in {'先付定金后付尾款', '全款发货'} else '已同步库管准备发货，财务与董事长仅接收通知。'}"
        ),
        module="sales",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids, *inventory_user_ids])),
        created_by_id=current_user.id,
    )


def _route_order_to_inventory_shipment(db: Session, *, order: SalesOrder, current_user: User) -> None:
    order.status = "pending_shipment"
    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is None:
        queue = InventoryDispatchQueue(
            sales_order_id=order.id,
            placement_status="pending",
            shipment_status="waiting_approval",
        )
        db.add(queue)
        db.flush()
    _create_fulfillment_demands(db, order=order, current_user=current_user)
    if queue.placement_status != "hidden":
        queue.placement_status = "pending"
        queue.shipment_status = "waiting_approval"
        complete_todos(db, business_type="sales_order", business_id=order.id)
        _create_inventory_placement_todos(db, order=order)
    else:
        queue.shipment_status = "pending"
    inventory_user_ids = _role_user_ids(db, "inventory")
    if queue.placement_status == "hidden":
        existing_todo_user_ids = {
            todo.assigned_user_id
            for todo in db.execute(
                select(TodoItem).where(
                    TodoItem.business_type == "sales_order",
                    TodoItem.business_id == order.id,
                    TodoItem.status != "completed",
                )
            ).scalars().all()
        }
        for user_id in inventory_user_ids:
            if user_id in existing_todo_user_ids:
                continue
            create_todo(
                db,
                title=f"待发货客户订单 {order.order_no}",
                description="订单已完成置货，请库管安排出库发货。",
                module="inventory",
                assigned_user_id=user_id,
                business_type="sales_order",
                business_id=order.id,
                priority="high",
            )
    create_notification(
        db,
        title="客户订单已直达发货",
        content=f"客户订单 {order.order_no} 为货到付款/分期付款，已同步库管处理发货。",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *inventory_user_ids, *_chairman_user_ids(db), *_finance_user_ids(db)])),
        created_by_id=current_user.id,
        category="info",
    )


def route_sales_order_to_inventory_shipment(db: Session, order: SalesOrder, current_user: User) -> None:
    """统一销售订单放行入口，财务和销售审批必须复用此流程。"""
    _route_order_to_inventory_shipment(db, order=order, current_user=current_user)


def ensure_sales_order_fulfillment_demands(db: Session, order: SalesOrder, current_user: User) -> list[dict]:
    """Refresh production/purchase demands without changing the order approval state."""
    return _create_fulfillment_demands(db, order=order, current_user=current_user)


def _build_split_items(db: Session, payload_items) -> tuple[list[dict], list[dict]]:
    warehouse = _finished_warehouse(db)
    product_ids = [_payload_value(item, "product_id") for item in payload_items]
    inventories = (
        db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == warehouse.id,
                Inventory.product_id.in_(product_ids),
                Inventory.material_id.is_(None),
            )
        ).scalars().all()
        if product_ids
        else []
    )
    inventory_map = {
        row.product_id: max(Decimal(row.quantity or 0) - Decimal(row.reserved_quantity or 0), Decimal("0"))
        for row in inventories
    }

    phase_one_items: list[dict] = []
    phase_two_items: list[dict] = []
    for item_payload in payload_items:
        product_id = _payload_value(item_payload, "product_id")
        unit_price = Decimal(str(_payload_value(item_payload, "unit_price") or 0))
        remark = _payload_value(item_payload, "remark")
        available_quantity = inventory_map.get(product_id, Decimal("0"))
        requested_quantity = Decimal(str(_payload_value(item_payload, "quantity") or 0))
        first_quantity = min(requested_quantity, available_quantity)
        second_quantity = requested_quantity - first_quantity
        item_data = {
            "product_id": product_id,
            "unit_price": unit_price,
            "packaging_config_id": _payload_value(item_payload, "packaging_config_id"),
            "remark": remark,
            "price_change_remark": _payload_value(item_payload, "price_change_remark"),
        }
        if first_quantity > 0:
            phase_one_items.append({**item_data, "quantity": first_quantity})
        if second_quantity > 0:
            phase_two_items.append({**item_data, "quantity": second_quantity})
    return phase_one_items, phase_two_items if phase_one_items and phase_two_items else []


def _create_fulfillment_demands(
    db: Session,
    *,
    order: SalesOrder,
    current_user: User,
    shortage_override: dict[int, Decimal] | None = None,
) -> list[dict]:
    warehouse = _finished_warehouse(db)
    inventory_rows = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == warehouse.id,
            Inventory.product_id.in_([item.product_id for item in order.items]),
            Inventory.material_id.is_(None),
        )
    ).scalars().all() if order.items else []
    inventory_map = {
        row.product_id: max(Decimal(row.quantity or 0) - Decimal(row.reserved_quantity or 0), Decimal("0"))
        for row in inventory_rows
    }
    created_demands: list[dict] = []
    for item in order.items:
        available_quantity = inventory_map.get(item.product_id, Decimal("0"))
        remaining_order_quantity = max(
            Decimal(item.quantity or 0) - Decimal(item.placed_quantity or 0) - Decimal(item.shipped_quantity or 0),
            Decimal("0"),
        )
        shortage_quantity = (
            Decimal(shortage_override.get(item.product_id, Decimal("0")))
            if shortage_override is not None
            else max(remaining_order_quantity - available_quantity, Decimal("0"))
        )
        if shortage_quantity <= 0:
            continue
        product = item.product
        if product is None:
            continue
        existing_demand = db.execute(
            select(FulfillmentDemand)
            .where(
                FulfillmentDemand.sales_order_item_id == item.id,
                FulfillmentDemand.status.in_(["pending", "converted"]),
            )
            .order_by(FulfillmentDemand.id.desc())
        ).scalars().first()
        if existing_demand is not None:
            if existing_demand.status == "pending":
                if not existing_demand.process_note:
                    existing_demand.shortage_quantity = shortage_quantity
                ensure_fulfillment_demand_routing(
                    db,
                    demand=existing_demand,
                    order=order,
                    product=product,
                    current_user=current_user,
                )
                if existing_demand.fulfillment_mode == "production":
                    _ensure_sales_order_material_replenishment_todos(
                        db,
                        order=order,
                        demand=existing_demand,
                        current_user=current_user,
                    )
            created_demands.append(
                {
                    "id": existing_demand.id,
                    "demand_no": existing_demand.demand_no,
                    "product_id": item.product_id,
                    "product_name": product.name,
                    "fulfillment_mode": existing_demand.fulfillment_mode,
                    "fulfillment_mode_label": "采购" if existing_demand.fulfillment_mode == "trade" else "生产",
                    "shortage_quantity": float(existing_demand.shortage_quantity or shortage_quantity),
                }
            )
            continue
        demand = create_sales_shortage_demand(
            db,
            order=order,
            order_item=item,
            product=product,
            shortage_quantity=shortage_quantity,
            current_user=current_user,
        )
        if demand is not None:
            if demand.fulfillment_mode == "production":
                _ensure_sales_order_material_replenishment_todos(
                    db,
                    order=order,
                    demand=demand,
                    current_user=current_user,
                )
            created_demands.append(
                {
                    "id": demand.id,
                    "demand_no": demand.demand_no,
                    "product_id": item.product_id,
                    "product_name": product.name,
                    "fulfillment_mode": demand.fulfillment_mode,
                    "fulfillment_mode_label": "采购" if demand.fulfillment_mode == "trade" else "生产",
                    "shortage_quantity": float(shortage_quantity),
                }
            )
    return created_demands


def _ensure_sales_order_material_replenishment_todos(
    db: Session,
    *,
    order: SalesOrder,
    demand: FulfillmentDemand,
    current_user: User,
) -> None:
    """为生产补货需求创建幂等的原料采购待办。"""
    if demand.fulfillment_mode != "production":
        return
    bom = db.execute(
        select(BOM).options(joinedload(BOM.items)).where(BOM.product_id == demand.product_id)
    ).unique().scalar_one_or_none()
    if bom is None or not bom.items:
        emit(
            "sales_order_material_replenishment_skipped",
            reason="missing_bom",
            sales_order_id=order.id,
            fulfillment_demand_id=demand.id,
            product_id=demand.product_id,
        )
        return
    raw_warehouse = db.execute(
        select(Warehouse).where(Warehouse.warehouse_type == "raw")
    ).scalar_one_or_none()
    if raw_warehouse is None:
        emit(
            "sales_order_material_replenishment_skipped",
            reason="missing_raw_warehouse",
            sales_order_id=order.id,
            fulfillment_demand_id=demand.id,
            product_id=demand.product_id,
        )
        return

    material_ids = [item.material_id for item in bom.items]
    inventory_rows = db.execute(
        select(Inventory).where(
            Inventory.warehouse_id == raw_warehouse.id,
            Inventory.material_id.in_(material_ids or [-1]),
            Inventory.product_id.is_(None),
        )
    ).scalars().all()
    inventory_map = {row.material_id: row for row in inventory_rows}
    shortage_items: list[dict] = []
    demand_quantity = Decimal(demand.shortage_quantity or 0)
    for bom_item in bom.items:
        required_quantity = demand_quantity * Decimal(bom_item.standard_quantity or 0)
        inventory = inventory_map.get(bom_item.material_id)
        expected_available = material_expected_available_quantity(
            db,
            material_id=bom_item.material_id,
            inventory=inventory,
        )
        shortage_quantity = max(required_quantity - expected_available, Decimal("0"))
        if shortage_quantity <= 0:
            continue
        material_name = f"原料#{bom_item.material_id}"
        if inventory is not None and inventory.material is not None:
            material_name = inventory.material.name
        else:
            from models import Material

            material = db.execute(select(Material).where(Material.id == bom_item.material_id)).scalar_one_or_none()
            if material is not None:
                material_name = material.name
        shortage_items.append(
            {
                "material_id": bom_item.material_id,
                "material_name": material_name,
                "required_quantity": float(required_quantity),
                "expected_available_quantity": float(expected_available),
                "shortage_quantity": float(shortage_quantity),
            }
        )

    business_type = "sales_order_material_replenishment"
    pending_todos = db.execute(
        select(TodoItem).where(
            TodoItem.business_type == business_type,
            TodoItem.business_id == demand.id,
            TodoItem.status != "completed",
        )
    ).scalars().all()
    if not shortage_items:
        for todo in pending_todos:
            todo.status = "completed"
            todo.completed_at = now_local()
        emit(
            "sales_order_material_replenishment_skipped",
            reason="no_material_shortage",
            sales_order_id=order.id,
            fulfillment_demand_id=demand.id,
            product_id=demand.product_id,
            demand_quantity=float(demand_quantity),
        )
        return

    import json

    purchase_user_ids = resolve_workflow_recipients(
        db,
        "purchase",
        workflow=business_type,
        business_type=business_type,
        business_id=demand.id,
    )
    payload = {
        "sales_order_id": order.id,
        "sales_order_no": order.order_no,
        "fulfillment_demand_id": demand.id,
        "fulfillment_demand_no": demand.demand_no,
        "product_id": demand.product_id,
        "product_name": demand.product.name if demand.product else None,
        "production_quantity": float(demand_quantity),
        "items": shortage_items,
    }
    description = (
        f"客户订单 {order.order_no} 的生产补货需求 {demand.demand_no} 存在原料缺口，"
        f"共 {len(shortage_items)} 种原料，请采购人员按上游需求处理。"
        f"|sales_order_material_replenishment:{json.dumps(payload, ensure_ascii=False)}"
    )
    for user_id in purchase_user_ids:
        todo = next((row for row in pending_todos if row.assigned_user_id == user_id), None)
        if todo is None:
            create_todo(
                db,
                title=f"待采购生产原料 {order.order_no}",
                description=description,
                module="purchase",
                assigned_user_id=user_id,
                business_type=business_type,
                business_id=demand.id,
                priority="high",
            )
        else:
            todo.title = f"待采购生产原料 {order.order_no}"
            todo.description = description
    if purchase_user_ids:
        ensure_notification(
            db,
            title="销售订单已生成原料采购需求",
            content=f"客户订单 {order.order_no} 的生产需求 {demand.demand_no} 存在原料缺口，已通知采购人员。",
            module="purchase",
            recipient_user_ids=purchase_user_ids,
            created_by_id=current_user.id,
            category="warning",
        )
    emit(
        "sales_order_material_replenishment_synced",
        sales_order_id=order.id,
        fulfillment_demand_id=demand.id,
        product_id=demand.product_id,
        material_ids=[item["material_id"] for item in shortage_items],
        recipient_user_ids=purchase_user_ids,
    )


def create_order(payload: SalesOrderCreate, current_user: User, db: Session):
    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")

    # 不再拆单：整单保留全部明细，库存缺口统一通过生产/采购补货需求处理
    phase_one_items, phase_two_items = _build_split_items(db, payload.items)
    effective_items = [
        {
            "product_id": item.product_id,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "packaging_config_id": item.packaging_config_id,
            "remark": item.remark,
            "price_change_remark": getattr(item, "price_change_remark", None),
        }
        for item in payload.items
    ]

    operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id, current_user)
    order = SalesOrder(
        order_no=next_business_no(db, "XS"),
        split_phase=1,
        customer_id=payload.customer_id,
        sales_user_id=current_user.id,
        operator_user_id=operator_user_id,
        consignee=payload.consignee,
        consignee_phone=payload.consignee_phone,
        delivery_address=payload.delivery_address,
        payment_type=payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales"),
        order_type=payload.order_type or "normal",
        expected_delivery_date=payload.expected_delivery_date,
        status="pending_confirmation",
        deposit_amount=payload.deposit_amount,
        transport_fee=payload.transport_fee,
        packaging_fee=0,
        total_amount=0,
        cost_amount=0,
        profit_amount=0,
        order_remark=payload.order_remark,
    )
    db.add(order)
    db.flush()
    order.split_group_no = order.order_no

    order.total_amount = _build_order_items(db, order_id=order.id, payload_items=effective_items) + Decimal(order.transport_fee or 0)
    _create_dispatch_queue(db, order.id)
    _create_order_todos_and_notice(db, order=order, customer_name=customer.name, current_user=current_user)

    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    has_available_batch = bool(phase_one_items)
    has_shortage_batch = bool(phase_two_items) or not has_available_batch
    if order.status != "pending_price_approval" and payment_type == "货到付款/分期付款" and has_available_batch:
        _route_order_to_inventory_shipment(db, order=order, current_user=current_user)

    # 缺口直接挂在整单上生成补货需求，不再另建拆分单据
    created_demands = _create_fulfillment_demands(db, order=order, current_user=current_user)

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="create_order",
        content=f"创建客户订单 {order.order_no}",
        after_data={
            "customer_id": order.customer_id,
            "status": order.status,
            "total_amount": float(order.total_amount),
            "item_count": len(effective_items),
            "split_child_order_no": None,
        },
    )
    db.commit()
    return api_response(
        data={
            "id": order.id,
            "order_no": order.order_no,
            "split_created": False,
            "split_child_order_no": None,
            "replenishment_created": bool(created_demands),
            "replenishment_demands": created_demands,
            "shipment_deferred_by_shortage": has_shortage_batch and not has_available_batch,
        }
    )


def update_order(order_id: int, payload: SalesOrderUpdate, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status not in {"pending_confirmation", "pending_price_approval", "rejected"}:
        raise HTTPException(status_code=409, detail="仅待确认、待改价审批或已驳回订单允许修改")

    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")

    before_data = {
        "customer_id": order.customer_id,
        "status": order.status,
        "total_amount": float(order.total_amount),
        "item_count": len(order.items),
    }

    order.customer_id = payload.customer_id
    order.operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id or order.operator_user_id, current_user)
    order.consignee = payload.consignee
    order.consignee_phone = payload.consignee_phone
    order.delivery_address = payload.delivery_address
    order.payment_type = payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales")
    order.order_type = payload.order_type or "normal"
    order.expected_delivery_date = payload.expected_delivery_date
    order.deposit_amount = payload.deposit_amount
    order.transport_fee = payload.transport_fee
    order.packaging_fee = 0
    order.order_remark = payload.order_remark
    order.rejection_reason = None

    for item in list(order.items):
        db.delete(item)
    db.flush()
    for log in db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all():
        db.delete(log)
    order.price_approval_status = "not_required"
    order.price_approved_by_id = None
    order.price_approved_at = None
    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    order.total_amount = _build_order_items(db, order_id=order.id, payload_items=payload.items) + Decimal(order.transport_fee or 0)
    if order.price_approval_status != "pending":
        order.status = "pending_confirmation"

    create_notification(
        db,
        title="客户订单已更新",
        content=f"客户订单 {order.order_no} 已更新，请按最新内容进行审批。",
        module="sales",
        recipient_user_ids=_role_user_ids(db, "finance", "chairman"),
        created_by_id=current_user.id,
        is_popup=False,
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="update_order",
        content=f"更新客户订单 {order.order_no}",
        before_data=before_data,
        after_data={
            "customer_id": order.customer_id,
            "status": order.status,
            "total_amount": float(order.total_amount),
            "item_count": len(payload.items),
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no})


def approve_order(order_id: int, payload: SalesApprovalRequest, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status != "pending_confirmation":
        raise HTTPException(status_code=409, detail="当前订单状态不允许审批")
    if order.price_approval_status == "pending":
        raise HTTPException(status_code=409, detail="当前订单存在待审批改价，需财务审批通过后再推进")

    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    actual_amount = Decimal(payload.actual_amount or payload.voucher_amount or 0)
    deposit_amount = Decimal(order.deposit_amount or 0)
    if payload.decision == "approved" and payment_type == "先付定金后付尾款" and actual_amount < deposit_amount:
        raise HTTPException(status_code=409, detail="定金到账金额不足，不能通过审批")
    if payload.decision == "approved" and payment_type == "全款发货" and actual_amount < Decimal(order.total_amount or 0):
        raise HTTPException(status_code=409, detail="全款发货订单到账金额不足，不能通过审批")

    db.add(
        SalesOrderApproval(
            sales_order_id=order.id,
            approver_user_id=current_user.id,
            decision=payload.decision,
            voucher_amount=actual_amount,
            note=payload.note,
        )
    )

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is None:
        queue = InventoryDispatchQueue(sales_order_id=order.id)
        db.add(queue)

    if payload.decision == "approved":
        order.status = "pending_shipment"
        order.approved_by_id = current_user.id
        order.approved_at = now_local()
        order.rejection_reason = None
        _create_fulfillment_demands(db, order=order, current_user=current_user)
        if queue.placement_status == "hidden":
            queue.shipment_status = "pending"
        else:
            queue.placement_status = "pending"
            queue.shipment_status = "waiting_approval"
            complete_todos(db, business_type="sales_order", business_id=order.id)
            _create_inventory_placement_todos(db, order=order)

        if actual_amount > 0:
            db.add(
                ReceivableRecord(
                    sales_order_id=order.id,
                    amount=actual_amount,
                    payment_stage="deposit" if payment_type == "先付定金后付尾款" else "full",
                    received_at=order.approved_at,
                    received_by_id=current_user.id,
                    remark=f"订单确认到账：{payment_type}",
                )
            )
            create_cash_flow(
                db,
                flow_type="incoming",
                business_type="sales_order",
                business_id=order.id,
                related_no=order.order_no,
                amount=actual_amount,
                happened_at=order.approved_at,
                remark=f"客户订单确认到账：{payment_type}",
            )

        inventory_user_ids = _role_user_ids(db, "inventory")
        if queue.placement_status == "hidden":
            existing_todo_user_ids = {
                todo.assigned_user_id
                for todo in db.execute(
                    select(TodoItem).where(
                        TodoItem.business_type == "sales_order",
                        TodoItem.business_id == order.id,
                        TodoItem.status != "completed",
                    )
                ).scalars().all()
            }
            for user_id in inventory_user_ids:
                if user_id in existing_todo_user_ids:
                    continue
                create_todo(
                    db,
                    title=f"待发货客户订单 {order.order_no}",
                    description="订单已完成置货，请库管安排出库发货。",
                    module="inventory",
                    assigned_user_id=user_id,
                    business_type="sales_order",
                    business_id=order.id,
                    priority="high",
                )
        create_notification(
            db,
            title="客户订单审批通过",
            content=f"客户订单 {order.order_no} 已确认到账并进入待发货阶段。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *inventory_user_ids, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )
    else:
        order.status = "rejected"
        order.rejection_reason = payload.note
        queue.shipment_status = "rejected"
        create_notification(
            db,
            title="客户订单已驳回",
            content=f"客户订单 {order.order_no} 审批未通过，请根据驳回原因调整。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_order_approval", business_id=order.id)
    sync_inventory_dimensions(db)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_order",
        content=f"审批客户订单 {order.order_no}",
        after_data={
            "decision": payload.decision,
            "actual_amount": float(actual_amount),
            "status": order.status,
            "note": payload.note,
        },
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status})


def approve_sales_price_change(order_id: int, payload, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.price_approval_status != "pending" and order.status != "pending_price_approval":
        raise HTTPException(status_code=409, detail="当前客户订单没有待审批的改价")
    logs = db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all()
    if not logs:
        raise HTTPException(status_code=409, detail="当前客户订单没有待审批的改价明细")

    now = now_local()
    if payload.decision == "approved":
        order.price_approval_status = "approved"
        order.price_approved_by_id = current_user.id
        order.price_approved_at = now
        if order.status == "pending_price_approval":
            order.status = "pending_confirmation"
        for row in logs:
            row.status = "approved"
            row.approved_by_id = current_user.id
            row.approved_at = now
        create_notification(
            db,
            title="销售改价审批通过",
            content=f"客户订单 {order.order_no} 的销售改价已通过财务审批，可继续推进收款确认或发货流程。{payload.note or ''}",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, order.operator_user_id, *_manager_chairman_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )
        if payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales") == "货到付款/分期付款":
            _route_order_to_inventory_shipment(db, order=order, current_user=current_user)
    else:
        order.price_approval_status = "rejected"
        order.status = "rejected"
        order.rejection_reason = payload.note or "销售改价审批未通过"
        for row in logs:
            row.status = "rejected"
            row.approved_by_id = current_user.id
            row.approved_at = now
        create_notification(
            db,
            title="销售改价审批驳回",
            content=f"客户订单 {order.order_no} 的销售改价被驳回，请销售调整订单。{payload.note or ''}",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, order.operator_user_id, *_manager_chairman_user_ids(db)])),
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_sales_price_change",
        content=f"审批客户订单 {order.order_no} 的销售改价",
        after_data={"decision": payload.decision, "status": order.status, "price_approval_status": order.price_approval_status},
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status, "price_approval_status": order.price_approval_status})


def finance_edit_order(order_id: int, payload, current_user: User, db: Session):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status in {"shipped", "completed", "cancelled"} or order.shipped_at is not None:
        raise HTTPException(status_code=409, detail="客户订单已发货、已完成或已取消，财务不能再修改")
    if not payload.items:
        raise HTTPException(status_code=400, detail="客户订单至少需要一条明细")

    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="客户不存在")

    original_customer_name = order.customer.name if order.customer else None
    original_operator_name = order.operator_user.full_name if order.operator_user else None

    def _serialize_order_items(items):
        return [
            {
                "product_id": item.product_id,
                "product_name": item.product.name if item.product else None,
                "product_code": item.product.product_code if item.product else None,
                "display_specification": item.display_specification,
                "packaging_config_id": item.packaging_config_id,
                "packaging_name": item.packaging_config.packaging_name if item.packaging_config else None,
                "kg_per_bag": float(item.kg_per_bag) if item.kg_per_bag is not None else None,
                "bag_count": float(item.bag_count) if item.bag_count is not None else None,
                "packaging_unit_price": float(item.packaging_unit_price or 0),
                "packaging_fee": float(item.packaging_fee or 0),
                "quantity": float(item.quantity or 0),
                "unit_price": float(item.unit_price or 0),
                "total_amount": float(item.total_amount or 0),
                "remark": item.remark,
            }
            for item in items
        ]

    before_data = {
        "order_id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": original_customer_name,
        "operator_user_id": order.operator_user_id,
        "operator_name": original_operator_name,
        "consignee": order.consignee,
        "consignee_phone": order.consignee_phone,
        "delivery_address": order.delivery_address,
        "payment_type": order.payment_type,
        "deposit_amount": float(order.deposit_amount or 0),
        "transport_fee": float(order.transport_fee or 0),
        "packaging_fee": float(order.packaging_fee or 0),
        "order_remark": order.order_remark,
        "total_amount": float(order.total_amount or 0),
        "items": _serialize_order_items(order.items),
    }

    order.customer_id = payload.customer_id
    order.operator_user_id = _resolve_sales_operator_user_id(db, payload.operator_user_id or order.operator_user_id, order.sales_user or current_user)
    order.consignee = payload.consignee
    order.consignee_phone = payload.consignee_phone
    order.delivery_address = payload.delivery_address
    order.payment_type = payment_type_label(payload.payment_type, deposit_amount=payload.deposit_amount, module="sales")
    order.order_type = payload.order_type or "normal"
    order.expected_delivery_date = payload.expected_delivery_date
    order.deposit_amount = payload.deposit_amount
    order.transport_fee = payload.transport_fee
    order.packaging_fee = 0
    order.order_remark = payload.order_remark
    order.finance_edit_pending = True

    shipped_qty_map = {item.id: Decimal(item.shipped_quantity or 0) for item in order.items}
    if any(quantity > 0 for quantity in shipped_qty_map.values()):
        raise HTTPException(status_code=409, detail="客户订单存在已出库明细，不能通过财务改单修改")

    for item in list(order.items):
        db.delete(item)
    db.flush()
    for log in db.execute(
        select(SalesOrderPriceChangeLog).where(
            SalesOrderPriceChangeLog.sales_order_id == order.id,
            SalesOrderPriceChangeLog.status == "pending",
        )
    ).scalars().all():
        db.delete(log)
    complete_todos(db, business_type="sales_order_price_approval", business_id=order.id)
    order.price_approval_status = "not_required"
    order.price_approved_by_id = None
    order.price_approved_at = None
    order.total_amount = _build_order_items(
        db,
        order_id=order.id,
        payload_items=payload.items,
        enforce_price_remark=False,
        create_price_approval=False,
    ) + Decimal(order.transport_fee or 0)
    updated_operator = db.execute(select(User).where(User.id == order.operator_user_id)).scalar_one_or_none()
    if order.status == "pending_price_approval":
        if payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales") == "货到付款/分期付款":
            _route_order_to_inventory_shipment(db, order=order, current_user=current_user)
        else:
            order.status = "pending_confirmation"

    sales_confirm_ids = [order.sales_user_id, order.operator_user_id]
    inventory_user_ids = _role_user_ids(db, "inventory")
    complete_todos(db, business_type="sales_order_finance_edit_sales_confirm", business_id=order.id)
    complete_todos(db, business_type="sales_order_finance_edit_inventory_confirm", business_id=order.id)

    for user_id in list(dict.fromkeys([user_id for user_id in sales_confirm_ids if user_id])):
        create_todo(
            db,
            title=f"确认财务修改客户订单 {order.order_no}",
            description=f"财务已在发货前修改客户订单，请核对客户、金额、收货与商品明细后确认。{payload.edit_note or ''}",
            module="sales",
            assigned_user_id=user_id,
            business_type="sales_order_finance_edit_sales_confirm",
            business_id=order.id,
            priority="high",
        )
    for user_id in inventory_user_ids:
        create_todo(
            db,
            title=f"确认财务修改待发订单 {order.order_no}",
            description=f"财务已在发货前修改客户订单，库存确认前该订单暂不能继续发货。{payload.edit_note or ''}",
            module="inventory",
            assigned_user_id=user_id,
            business_type="sales_order_finance_edit_inventory_confirm",
            business_id=order.id,
            priority="high",
        )

    create_notification(
        db,
        title="客户订单已由财务修改",
        content=f"客户订单 {order.order_no} 已由财务在发货前修改，请销售和库存分别确认后再推进后续流程。{payload.edit_note or ''}",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([*sales_confirm_ids, *inventory_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    after_data = {
        "order_id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer.name if customer else None,
        "operator_user_id": order.operator_user_id,
        "operator_name": updated_operator.full_name if updated_operator else None,
        "consignee": order.consignee,
        "consignee_phone": order.consignee_phone,
        "delivery_address": order.delivery_address,
        "payment_type": order.payment_type,
        "deposit_amount": float(order.deposit_amount or 0),
        "transport_fee": float(order.transport_fee or 0),
        "packaging_fee": float(order.packaging_fee or 0),
        "order_remark": order.order_remark,
        "total_amount": float(order.total_amount or 0),
        "finance_edit_pending": order.finance_edit_pending,
        "note": payload.edit_note,
        "items": _serialize_order_items(order.items),
    }

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="finance_edit_order",
        content=f"财务修改客户订单 {order.order_no}",
        before_data=before_data,
        after_data=after_data,
    )
    db.commit()
    return api_response(data={"id": order.id, "order_no": order.order_no, "finance_edit_pending": order.finance_edit_pending})


def confirm_finance_edit_order(order_id: int, current_user: User, db: Session, note: str | None = None):
    order = _load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if not order.finance_edit_pending:
        return api_response(data={"id": order.id, "finance_edit_pending": False}, msg="该订单无需确认财务修改")

    role_code = current_user.role.code if current_user.role else ""
    if role_code == "sales":
        if current_user.id not in {order.sales_user_id, order.operator_user_id}:
            raise HTTPException(status_code=403, detail="仅订单销售人员或经办人可确认该财务修改")
        complete_todos(
            db,
            business_type="sales_order_finance_edit_sales_confirm",
            business_id=order.id,
            assigned_user_id=current_user.id,
        )
        confirm_type = "sales"
    elif role_code == "inventory":
        complete_todos(
            db,
            business_type="sales_order_finance_edit_inventory_confirm",
            business_id=order.id,
            assigned_user_id=current_user.id,
        )
        confirm_type = "inventory"
    elif current_user.role and current_user.role.is_admin:
        complete_todos(db, business_type="sales_order_finance_edit_sales_confirm", business_id=order.id)
        complete_todos(db, business_type="sales_order_finance_edit_inventory_confirm", business_id=order.id)
        confirm_type = "admin"
    else:
        raise HTTPException(status_code=403, detail="当前账号无权确认财务修改")

    if not _has_pending_finance_edit_confirmation(db, order.id):
        order.finance_edit_pending = False
        create_notification(
            db,
            title="客户订单财务修改已确认完成",
            content=f"客户订单 {order.order_no} 的财务修改已由销售和库存确认，可继续后续流程。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, order.operator_user_id, *_finance_user_ids(db), *_role_user_ids(db, "inventory")])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="confirm_finance_edit_order",
        content=f"确认客户订单 {order.order_no} 的财务修改",
        after_data={"confirm_type": confirm_type, "note": note, "finance_edit_pending": order.finance_edit_pending},
    )
    db.commit()
    return api_response(data={"id": order.id, "finance_edit_pending": order.finance_edit_pending})


def complete_order(order_id: int, current_user: User, db: Session):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status != "shipped":
        raise HTTPException(status_code=409, detail="仅已发货订单允许完结")

    received_amount = db.execute(
        select(func.coalesce(func.sum(ReceivableRecord.amount), 0)).where(ReceivableRecord.sales_order_id == order.id)
    ).scalar_one()
    if Decimal(received_amount or 0) < Decimal(order.total_amount or 0):
        raise HTTPException(status_code=409, detail="当前订单回款未达订单金额，不能标记完结")

    order.status = "completed"
    order.completed_at = now_local()

    queue = db.execute(select(InventoryDispatchQueue).where(InventoryDispatchQueue.sales_order_id == order.id)).scalar_one_or_none()
    if queue is not None:
        queue.shipment_status = "completed"
        queue.placement_status = "hidden"

    complete_todos(db, business_type="sales_order", business_id=order.id)
    create_notification(
        db,
        title="客户订单已完成",
        content=f"客户订单 {order.order_no} 已完成闭环。",
        module="sales",
        recipient_user_ids=[order.sales_user_id],
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="complete_order",
        content=f"完结客户订单 {order.order_no}",
        after_data={"status": order.status},
    )
    db.commit()
    return api_response(data={"id": order.id, "status": order.status})


def collect_order_payment(payload: SalesSettlementCollectRequest, current_user: User, db: Session):
    order = db.execute(select(SalesOrder).where(SalesOrder.id == payload.sales_order_id)).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status in {"cancelled", "completed"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许继续登记收款")

    from schemas.finance import ReceivableRecordCreate
    from services.finance_service import create_receivable_record

    stage_label = "定金" if payload.stage == "deposit" else "尾款"
    remark_parts = [f"{stage_label}收款"]
    if payload.remark and payload.remark.strip():
        remark_parts.append(payload.remark.strip())
    record_payload = ReceivableRecordCreate(
        sales_order_id=payload.sales_order_id,
        amount=payload.amount,
        received_at=now_local(),
        remark="；".join(remark_parts),
    )
    result = create_receivable_record(record_payload, current_user, db)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="collect_order_payment",
        content=f"登记客户订单 {order.order_no} 的{stage_label}收款",
        after_data={"order_id": order.id, "amount": float(payload.amount), "stage": payload.stage},
    )
    db.commit()
    return result


def submit_sales_receipt_request(payload: SalesReceiptRequestCreate, current_user: User, db: Session):
    order = _load_order(db, payload.sales_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    _ensure_order_owner_or_admin(order, current_user)
    if order.status in {"cancelled", "completed"}:
        raise HTTPException(status_code=409, detail="当前订单状态不允许继续发起收款确认")

    latest_request = _latest_sales_receipt_request(db, order.id)
    if latest_request and latest_request.status in {"pending_chairman_approval", "pending_finance_check"}:
        raise HTTPException(status_code=409, detail="当前客户订单存在待确认的收款申请")
    payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
    if payment_type not in {"先付定金后付尾款", "全款发货", "货到付款/分期付款"}:
        raise HTTPException(status_code=409, detail="当前付款方式不需要销售结算协同")

    received_amount = _sales_received_amount(db, order.id)
    remaining_amount = max(Decimal(order.total_amount or 0) - received_amount, Decimal("0"))
    if Decimal(payload.requested_amount or 0) > remaining_amount:
        raise HTTPException(status_code=409, detail="本次收款确认金额不能超过订单待收金额")
    if payload.payment_stage in {"deposit", "balance"} and payment_type != "先付定金后付尾款":
        raise HTTPException(status_code=409, detail="当前订单不适用该收款阶段")
    if payload.payment_stage == "pre_shipment_full" and payment_type not in {"先付定金后付尾款", "全款发货"}:
        raise HTTPException(status_code=409, detail="当前订单不适用发货前全款确认")
    if payload.payment_stage == "installment":
        if payment_type != "货到付款/分期付款":
            raise HTTPException(status_code=409, detail="当前订单不适用分期收款")
        if not payload.installment_total or not payload.installment_index:
            raise HTTPException(status_code=409, detail="请填写分期总期数和当前期数")
        if payload.installment_index > payload.installment_total:
            raise HTTPException(status_code=409, detail="当前期数不能大于总期数")
    if payload.payment_stage == "cod_full" and payment_type != "货到付款/分期付款":
        raise HTTPException(status_code=409, detail="当前订单不适用到货后全款确认")

    request = SalesReceiptRequest(
        request_no=next_business_no(db, "SK"),
        sales_order_id=order.id,
        requested_by_id=current_user.id,
        requested_amount=payload.requested_amount,
        actual_amount=payload.requested_amount,
        payment_stage=payload.payment_stage,
        installment_total=payload.installment_total,
        installment_index=payload.installment_index,
        status="pending_finance_check",
        remark=payload.remark,
    )
    db.add(request)
    db.flush()

    finance_user_ids = resolve_workflow_recipients(
        db,
        "finance",
        workflow="sales_receipt_request",
        business_type="sales_receipt_request",
        business_id=request.id,
    )
    for user_id in finance_user_ids:
        create_todo(
            db,
            title=f"待确认销售收款 {request.request_no}",
            description=f"客户订单 {order.order_no} 提交收款确认，金额 {float(request.requested_amount or 0)}。",
            module="sales",
            assigned_user_id=user_id,
            business_type="sales_receipt_request",
            business_id=request.id,
            priority="high",
        )
    create_notification(
        db,
        title="销售收款确认已提交",
        content=f"客户订单 {order.order_no} 已提交收款确认 {request.request_no}，等待董事长确认。",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *finance_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="submit_sales_receipt_request",
        content=f"提交客户订单 {order.order_no} 的收款确认 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "sales_order_id": order.id,
            "requested_amount": float(request.requested_amount or 0),
            "payment_stage": request.payment_stage,
            "installment_total": request.installment_total,
            "installment_index": request.installment_index,
            "received_amount": float(received_amount),
            "remaining_amount": float(remaining_amount),
            "status": request.status,
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "request_no": request.request_no, "status": request.status})


def submit_merged_sales_receipt_request(payload: SalesMergedReceiptRequestCreate, current_user: User, db: Session):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一张客户订单")
    order_ids = [item.sales_order_id for item in payload.items]
    if len(set(order_ids)) != len(order_ids):
        raise HTTPException(status_code=409, detail="合并收款确认中存在重复客户订单")

    orders = [_load_order(db, order_id) for order_id in order_ids]
    if any(order is None for order in orders):
        raise HTTPException(status_code=404, detail="存在不存在的客户订单")
    customer_ids = {order.customer_id for order in orders}
    if payload.customer_id is not None:
        customer_ids.add(payload.customer_id)
    if len({customer_id for customer_id in customer_ids if customer_id is not None}) != 1:
        raise HTTPException(status_code=409, detail="仅支持同一客户的客户订单合并收款确认")

    item_map = {item.sales_order_id: item for item in payload.items}
    merge_no = next_business_no(db, "HSK")
    created_rows: list[SalesReceiptRequest] = []
    requested_total = Decimal("0")
    finance_user_ids = resolve_workflow_recipients(
        db,
        "finance",
        workflow="sales_merged_receipt_request",
        business_type="sales_receipt_request",
    )

    for order in orders:
        _ensure_order_owner_or_admin(order, current_user)
        if order.status in {"cancelled", "completed"}:
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 状态不允许继续发起收款确认")
        latest_request = _latest_sales_receipt_request(db, order.id)
        if latest_request and latest_request.status in {"pending_chairman_approval", "pending_finance_check"}:
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 存在待确认的收款申请")

        payment_type = payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")
        if payment_type not in {"先付定金后付尾款", "全款发货", "货到付款/分期付款"}:
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 当前付款方式不需要销售结算协同")

        requested_amount = Decimal(item_map[order.id].requested_amount or 0)
        received_amount = _sales_received_amount(db, order.id)
        remaining_amount = max(Decimal(order.total_amount or 0) - received_amount, Decimal("0"))
        if requested_amount <= 0:
            raise HTTPException(status_code=409, detail="收款确认金额必须大于 0")
        if requested_amount > remaining_amount:
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 本次收款确认金额不能超过待收金额")
        if payload.payment_stage in {"deposit", "balance"} and payment_type != "先付定金后付尾款":
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 不适用该收款阶段")
        if payload.payment_stage == "pre_shipment_full" and payment_type not in {"先付定金后付尾款", "全款发货"}:
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 不适用发货前全款确认")
        if payload.payment_stage == "cod_full" and payment_type != "货到付款/分期付款":
            raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 不适用到货后全款确认")
        if payload.payment_stage == "installment":
            if payment_type != "货到付款/分期付款":
                raise HTTPException(status_code=409, detail=f"客户订单 {order.order_no} 不适用分期收款")
            if not payload.installment_total or not payload.installment_index:
                raise HTTPException(status_code=409, detail="请填写分期总期数和当前期数")
            if payload.installment_index > payload.installment_total:
                raise HTTPException(status_code=409, detail="当前期数不能大于总期数")

        remark_parts = [f"合并收款批次：{merge_no}"]
        if payload.remark and payload.remark.strip():
            remark_parts.append(payload.remark.strip())
        request = SalesReceiptRequest(
            request_no=next_business_no(db, "SK"),
            sales_order_id=order.id,
            requested_by_id=current_user.id,
            requested_amount=requested_amount,
            actual_amount=requested_amount,
            payment_stage=payload.payment_stage,
            installment_total=payload.installment_total,
            installment_index=payload.installment_index,
            status="pending_finance_check",
            remark="；".join(remark_parts),
        )
        db.add(request)
        db.flush()
        created_rows.append(request)
        requested_total += requested_amount

    if created_rows:
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"待确认合并销售收款 {merge_no}",
                description=f"合并收款批次 {merge_no} 包含 {len(created_rows)} 张客户订单，申请总额 {float(requested_total)}。",
                module="sales",
                assigned_user_id=user_id,
                business_type="sales_receipt_request",
                business_id=created_rows[0].id,
                priority="high",
            )

    create_notification(
        db,
        title="销售合并收款确认已提交",
        content=f"合并收款批次 {merge_no} 已提交，共 {len(created_rows)} 张客户订单，申请总额 {float(requested_total)}。",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *finance_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="submit_merged_sales_receipt_request",
        content=f"提交销售合并收款确认 {merge_no}",
        after_data={
            "merge_no": merge_no,
            "order_ids": order_ids,
            "request_ids": [row.id for row in created_rows],
            "requested_total": float(requested_total),
        },
    )
    db.commit()
    return api_response(
        data={
            "merge_no": merge_no,
            "request_ids": [row.id for row in created_rows],
            "request_count": len(created_rows),
            "requested_total": float(requested_total),
        }
    )


def approve_sales_receipt_request(request_id: int, payload: SalesReceiptApprovalRequest, current_user: User, db: Session):
    request = db.execute(select(SalesReceiptRequest).where(SalesReceiptRequest.id == request_id)).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="收款确认申请不存在")
    order = _load_order(db, request.sales_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if request.status != "pending_chairman_approval":
        raise HTTPException(status_code=409, detail="当前收款确认申请不在待董事长审批状态")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"chairman", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前角色无权审批收款确认")

    request.approval_remark = payload.approval_remark
    request.approved_by_id = current_user.id
    request.approved_at = now_local()
    complete_todos(db, business_type="sales_receipt_request", business_id=request.id)

    if payload.decision == "rejected":
        request.status = "rejected"
        create_notification(
            db,
            title="收款确认已驳回",
            content=f"客户订单 {order.order_no} 的收款确认申请 {request.request_no} 已被驳回。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="warning",
        )
    else:
        request.status = "pending_finance_check"
        request.actual_amount = Decimal(payload.actual_amount or request.requested_amount or 0)
        if payload.payment_type:
            order.payment_type = payment_type_label(payload.payment_type, deposit_amount=order.deposit_amount, module="sales")
        finance_user_ids = _finance_user_ids(db)
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"待核对客户入账 {request.request_no}",
                description=f"客户订单 {order.order_no} 的收款确认已通过董事长审批，请财务核对实际入账金额、收款方式并上传银行回单。",
                module="finance",
                assigned_user_id=user_id,
                business_type="sales_receipt_request",
                business_id=request.id,
                priority="high",
            )
        create_notification(
            db,
            title="收款确认待财务核对",
            content=f"客户订单 {order.order_no} 的收款确认申请 {request.request_no} 已通过审批，待财务核对入账。",
            module="finance",
            recipient_user_ids=list(dict.fromkeys([order.sales_user_id, *finance_user_ids])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_sales_receipt_request",
        content=f"审批客户订单 {order.order_no} 的收款确认 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "decision": payload.decision,
            "actual_amount": float(payload.actual_amount or request.actual_amount or 0),
            "status": request.status,
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})

def create_return(payload: SalesReturnCreate, current_user: User, db: Session):
    order = _load_order(db, payload.sales_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="客户订单不存在")
    if order.status not in {"shipped", "completed"}:
        raise HTTPException(status_code=409, detail="仅已发货或已完成订单允许提交退换货")
    _ensure_order_owner_or_admin(order, current_user)
    if not payload.items:
        raise HTTPException(status_code=400, detail="退换货至少需要一条明细")

    order_item_map = {item.id: item for item in order.items}
    existing_return_items = db.execute(
        select(SalesReturnItem, SalesReturn.status)
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.sales_return_id)
        .where(
            SalesReturn.sales_order_id == order.id,
            SalesReturn.status.in_(["pending", "approved", "refunded"]),
        )
    ).all()
    returned_qty_map: dict[int, Decimal] = {}
    for item_row, _status in existing_return_items:
        returned_qty_map[item_row.sales_order_item_id] = returned_qty_map.get(item_row.sales_order_item_id, Decimal("0")) + Decimal(item_row.quantity or 0)

    sales_return = SalesReturn(
        return_no=next_business_no(db, "XT"),
        sales_order_id=order.id,
        requested_by_id=current_user.id,
        reason=payload.reason,
        status="pending",
        return_type=payload.return_type,
        refund_amount=payload.refund_amount,
        requires_reinspection=payload.requires_reinspection,
        reinspection_status="pending" if payload.requires_reinspection else "not_required",
    )
    db.add(sales_return)
    db.flush()

    detail_count = 0
    for index, item_payload in enumerate(payload.items, start=1):
        order_item = order_item_map.get(item_payload.sales_order_item_id)
        if order_item is None:
            raise HTTPException(status_code=404, detail="退换货明细对应的订单商品不存在")

        sold_quantity = Decimal(order_item.quantity or 0)
        already_returned = Decimal(returned_qty_map.get(order_item.id, 0))
        if Decimal(item_payload.quantity) + already_returned > sold_quantity:
            raise HTTPException(status_code=409, detail="退换货数量超过可退数量")

        db.add(
            SalesReturnItem(
                sales_return_id=sales_return.id,
                sales_order_item_id=order_item.id,
                quantity=item_payload.quantity,
                batch_suffix=f"RT{sales_return.id:04d}-{index:02d}",
            )
        )
        detail_count += 1

    approver_user_ids = _role_user_ids(db, "manager", "chairman")
    return_type_text = "退货" if payload.return_type == "return" else "换货"
    for user_id in approver_user_ids:
        create_todo(
            db,
            title=f"待审批{return_type_text}单 {sales_return.return_no}",
            description=payload.reason,
            module="sales",
            assigned_user_id=user_id,
            business_type="sales_return_approval",
            business_id=sales_return.id,
            priority="high",
        )

    create_notification(
        db,
        title="销售退换货已提交",
        content=f"{return_type_text}单 {sales_return.return_no} 已提交，等待审批。",
        module="sales",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *approver_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="create_return",
        content=f"创建销售退换货 {sales_return.return_no}",
        after_data={
            "sales_order_id": order.id,
            "return_type": sales_return.return_type,
            "refund_amount": float(sales_return.refund_amount),
            "detail_count": detail_count,
        },
    )
    db.commit()
    return api_response(data={"id": sales_return.id, "return_no": sales_return.return_no})


def approve_return(return_id: int, payload: SalesReturnApprovalRequest, current_user: User, db: Session):
    sales_return = db.execute(select(SalesReturn).where(SalesReturn.id == return_id)).scalar_one_or_none()
    if sales_return is None:
        raise HTTPException(status_code=404, detail="退换货单不存在")
    if sales_return.status != "pending":
        raise HTTPException(status_code=409, detail="当前退换货单状态不允许审批")

    return_items = db.execute(select(SalesReturnItem).where(SalesReturnItem.sales_return_id == sales_return.id)).scalars().all()
    if not return_items:
        raise HTTPException(status_code=409, detail="退换货单缺少明细")

    order_items = {
        row.id: row
        for row in db.execute(
            select(SalesOrderItem).options(joinedload(SalesOrderItem.product)).where(
                SalesOrderItem.id.in_([item.sales_order_item_id for item in return_items])
            )
        ).scalars().all()
    }

    sales_return.approved_by_id = current_user.id
    sales_return.approved_at = now_local()
    sales_return.review_note = payload.note

    if payload.decision == "approved":
        warehouse = _finished_warehouse(db)
        request_items: list[dict] = []
        total_reinspection_quantity = Decimal("0")
        reinspection_names: list[str] = []
        for item in return_items:
            order_item = order_items.get(item.sales_order_item_id)
            if order_item is None:
                raise HTTPException(status_code=404, detail="退换货对应订单明细不存在")

            inventory = db.execute(
                select(Inventory).where(
                    Inventory.warehouse_id == warehouse.id,
                    Inventory.product_id == order_item.product_id,
                    Inventory.material_id.is_(None),
                )
            ).scalar_one_or_none()
            unit_cost = Decimal(inventory.avg_unit_cost or 0) if inventory else Decimal("0")
            request_items.append(
                {
                    "product_id": order_item.product_id,
                    "material_id": None,
                    "batch_no": f"{sales_return.return_no}-{item.batch_suffix or item.id}",
                    "quantity": float(item.quantity or 0),
                    "unit_cost": float(unit_cost),
                }
            )
            total_reinspection_quantity += Decimal(item.quantity or 0)
            if order_item.product and order_item.product.name:
                reinspection_names.append(order_item.product.name)

        sales_return.status = "approved"
        inventory_user_ids = _role_user_ids(db, "inventory")
        if request_items:
            create_inventory_operation_request(
                db,
                operation_type="inbound",
                request_category="sales_return_inbound",
                source_module="sales",
                source_type="sales_return",
                source_id=sales_return.id,
                source_no=sales_return.return_no,
                warehouse_id=warehouse.id,
                requested_by_id=current_user.id,
                items=request_items,
                remark=sales_return.reason,
                recipient_user_ids=inventory_user_ids,
                title=f"待执行销售退货入库 {sales_return.return_no}",
                description=f"销售退换货单 {sales_return.return_no} 已审批通过，请库管执行退货入库。",
            )
        if sales_return.requires_reinspection:
            quality_task = create_return_reinspection_quality_task(
                db,
                business_type="sales_return",
                business_id=sales_return.id,
                business_no=sales_return.return_no,
                source_label="销售退换货复检",
                item_label="、".join(dict.fromkeys(reinspection_names)) or "退换货产品",
                quantity=total_reinspection_quantity,
                submitted_by_id=current_user.id,
            )
            quality_user_ids = _role_user_ids(db, "quality", "manager", "chairman")
            for user_id in quality_user_ids:
                create_todo(
                    db,
                    title=f"待复检销售退换货 {sales_return.return_no}",
                    description=f"销售退换货单 {sales_return.return_no} 标记需要复检，请处理质检任务 {quality_task.quality_no}。",
                    module="quality",
                    assigned_user_id=user_id,
                    business_type="quality_task",
                    business_id=quality_task.id,
                    priority="high",
                )
            if quality_user_ids:
                create_notification(
                    db,
                    title="销售退换货已发起复检",
                    content=f"销售退换货单 {sales_return.return_no} 已生成复检任务 {quality_task.quality_no}。",
                    module="quality",
                    recipient_user_ids=quality_user_ids,
                    created_by_id=current_user.id,
                    category="warning",
                )
        finance_user_ids = _role_user_ids(db, "finance", "manager", "chairman")
        if Decimal(sales_return.refund_amount or 0) > 0:
            for user_id in finance_user_ids:
                create_todo(
                    db,
                    title=f"待处理销售退款 {sales_return.return_no}",
                    description=sales_return.reason,
                    module="finance",
                    assigned_user_id=user_id,
                    business_type="sales_return",
                    business_id=sales_return.id,
                )
        create_notification(
            db,
            title="销售退换货审批通过",
            content=f"{'退货' if sales_return.return_type == 'return' else '换货'}单 {sales_return.return_no} 已审批通过，并已同步库管执行退货入库。",
            module="sales",
            recipient_user_ids=list(dict.fromkeys([sales_return.requested_by_id, *finance_user_ids, *inventory_user_ids])),
            created_by_id=current_user.id,
            category="success",
        )
    else:
        sales_return.status = "rejected"
        create_notification(
            db,
            title="销售退换货已驳回",
            content=f"{'退货' if sales_return.return_type == 'return' else '换货'}单 {sales_return.return_no} 审批未通过，请查看驳回说明。",
            module="sales",
            recipient_user_ids=[sales_return.requested_by_id],
            created_by_id=current_user.id,
            category="warning",
        )

    complete_todos(db, business_type="sales_return_approval", business_id=sales_return.id)
    write_operation_log(
        db,
        user_id=current_user.id,
        module="sales",
        action="approve_return",
        content=f"审批销售退换货 {sales_return.return_no}",
        after_data={
            "decision": payload.decision,
            "status": sales_return.status,
            "note": payload.note,
        },
    )
    db.commit()
    return api_response(data={"id": sales_return.id, "status": sales_return.status})
