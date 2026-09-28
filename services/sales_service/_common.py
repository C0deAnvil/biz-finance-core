"""销售域服务。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
from services.core import _role_user_ids, apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, ensure_notification, next_business_no, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, get_inventory_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity
from services.request_trace import emit
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_finished_warehouse
from services.time_utils import now_local
from services.money import money



"""销售域服务。"""


from datetime import datetime
from decimal import Decimal

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
from services.display import calculate_bag_count, get_default_packaging_config, get_inventory_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity
from services.request_trace import emit
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_finished_warehouse
from services.time_utils import now_local
from services.inventory_availability import placeable_of


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
    packaging_fee = money(Decimal(bag_count) * unit_price)
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
        if unit_price < 0:
            raise HTTPException(status_code=409, detail="商品单价不能小于 0")
        if enforce_price_remark and unit_price < standard_price and len(price_change_remark) < 2:
            raise HTTPException(status_code=409, detail=f"产品 {product.name} 单价低于标准价格，请填写改价备注")

        packaging = _resolve_line_packaging(db, product_id=product_id, packaging_config_id=packaging_config_id)
        bag_count, packaging_unit_price, packaging_fee = _calculate_packaging_fee(quantity, packaging)
        product_amount = money(quantity * unit_price)
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
        # 仅低于标准价的让利销售才需改价审批；高于标准价属于增收，不阻塞订单流程。
        if create_price_approval and unit_price < standard_price:
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
        # 董事长收到改价提醒时也应有可打开、可审批的待办，而不是只有通知。
        price_approver_user_ids = list(dict.fromkeys([*finance_user_ids, *_role_user_ids(db, "chairman")]))
        if order is not None:
            order.price_approval_status = "pending"
            if order.status == "pending_confirmation":
                order.status = "pending_price_approval"
        for user_id in price_approver_user_ids:
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
            recipient_user_ids=list(dict.fromkeys([*price_approver_user_ids, *_manager_chairman_user_ids(db)])),
            created_by_id=operator_user_id,
            category="warning",
        )
    return total_amount


def _create_dispatch_queue(db: Session, sales_order_id: int) -> None:
    """创建或重置该订单的置货队列行(幂等)。

    inventory_dispatch_queues.sales_order_id 上有唯一约束,而提交审批这条路径
    在"草稿提交"和"驳回后重新提交"时都会走到这里。原先无条件 insert,
    导致销售按财务驳回意见改完再提交时必然撞 1062 Duplicate entry 而 500 ——
    "驳回→修改→重提"这条路径整个走不通。
    已有行时把状态重置回初始态即可,不再新增。
    """
    existing = db.execute(
        select(InventoryDispatchQueue).where(
            InventoryDispatchQueue.sales_order_id == sales_order_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.placement_status = "pending"
        existing.shipment_status = "waiting_approval"
        return
    db.add(
        InventoryDispatchQueue(
            sales_order_id=sales_order_id,
            placement_status="pending",
            shipment_status="waiting_approval",
        )
    )


def _lock_finished_inventory_map(db: Session, *, order: SalesOrder) -> dict[int, Inventory]:
    """按产品加行级锁取出成品仓库存行,防止并发占用导致超卖。"""
    warehouse = _finished_warehouse(db)
    product_ids = [item.product_id for item in order.items] or [-1]
    return {
        row.product_id: row
        for row in db.execute(
            select(Inventory)
            .where(
                Inventory.warehouse_id == warehouse.id,
                Inventory.product_id.in_(product_ids),
                Inventory.material_id.is_(None),
            )
            .with_for_update()
        ).scalars().all()
    }


def occupy_inventory_for_order(db: Session, *, order: SalesOrder) -> dict[int, Decimal]:
    """财务审批通过后占用成品库存。**幂等**:重复调用不会叠加占用。

    幂等的实现方式:以明细上已记录的 reserved_quantity 为准,只补足"还差多少",
    而不是每次都加一遍。这一点很重要,因为本函数有 3 条调用路径
    (财务审批 / 改价审批放行 / 财务改单放行),同一订单可能被走到多次。

    占用量 = min(尚未占用的订单量, 当前可置货量)。缺口部分不在这里处理,
    由库管在置货环节按需生成补货需求。

    返回各明细本次新增的占用量。
    """
    inventory_map = _lock_finished_inventory_map(db, order=order)
    occupied_map: dict[int, Decimal] = {}
    for item in order.items:
        inventory = inventory_map.get(item.product_id)
        already = Decimal(item.reserved_quantity or 0)
        # 已发货的部分不需要再占用(发货时已扣减实有并释放占用)
        outstanding = max(
            Decimal(item.quantity or 0) - Decimal(item.shipped_quantity or 0) - already,
            Decimal("0"),
        )
        placeable = max(placeable_of(inventory), Decimal("0"))
        occupy = min(outstanding, placeable)
        if occupy > 0 and inventory is not None:
            inventory.reserved_quantity = Decimal(inventory.reserved_quantity or 0) + occupy
            item.reserved_quantity = already + occupy
        occupied_map[item.id] = occupy
    return occupied_map


def release_order_reservation(db: Session, *, order: SalesOrder) -> Decimal:
    """释放该订单剩余的占用库存(驳回/取消/作废时调用)。

    不释放会造成占用永久泄漏——库存被一张已作废的订单锁死,别人也用不了。
    以明细记录的占用量为准逐笔退还,并归零,保证可重复调用。
    """
    inventory_map = _lock_finished_inventory_map(db, order=order)
    released_total = Decimal("0")
    for item in order.items:
        reserved = Decimal(item.reserved_quantity or 0)
        if reserved > 0:
            inventory = inventory_map.get(item.product_id)
            if inventory is not None:
                inventory.reserved_quantity = max(
                    Decimal(inventory.reserved_quantity or 0) - reserved, Decimal("0")
                )
            item.reserved_quantity = Decimal("0")
            released_total += reserved
        packaging_reserved = Decimal(item.packaging_reserved_quantity or 0)
        if packaging_reserved <= 0:
            continue
        packaging = get_inventory_packaging_config(db, item.packaging_config)
        if packaging is not None:
            packaging_inventory = db.execute(
                select(Inventory)
                .where(
                    Inventory.warehouse_id == _finished_warehouse(db).id,
                    Inventory.product_id.is_(None),
                    Inventory.material_id.is_(None),
                    Inventory.packaging_config_id == packaging.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if packaging_inventory is not None:
                packaging_inventory.reserved_quantity = max(
                    Decimal(packaging_inventory.reserved_quantity or 0) - packaging_reserved,
                    Decimal("0"),
                )
        item.packaging_reserved_quantity = Decimal("0")
    return released_total


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
    # 放行即占用成品库存;缺口不自动生成补货需求,由库管置货时按需创建
    occupy_inventory_for_order(db, order=order)
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
        placed = Decimal(item.placed_quantity or 0)
        # 本明细自己锁定但还没置的量,对本单来说是可用的(那批货别人拿不走);
        # 只看"实有-总占用"会把已为本单锁定的货算成别人的,凭空造出等量缺口。
        # 该函数会在每次启动的 repair_workflows 里被调用,若算错会持续生成假需求。
        reserved_for_me = max(Decimal(item.reserved_quantity or 0) - placed, Decimal("0"))
        # 散量全仓共享,边分配边扣,避免同产品多条明细重复计算
        free_pool = inventory_map.get(item.product_id, Decimal("0"))
        remaining_order_quantity = max(
            Decimal(item.quantity or 0) - placed - Decimal(item.shipped_quantity or 0),
            Decimal("0"),
        )
        take_from_pool = min(free_pool, max(remaining_order_quantity - reserved_for_me, Decimal("0")))
        if item.product_id in inventory_map:
            inventory_map[item.product_id] = free_pool - take_from_pool
        available_quantity = reserved_for_me + take_from_pool
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
                FulfillmentDemand.status.in_(["pending", "pending_material", "converted"]),
            )
            .order_by(FulfillmentDemand.id.desc())
        ).scalars().first()
        if existing_demand is not None:
            if existing_demand.status == "pending":
                # 尚未开始处理(无处理备注)的需求可直接改量;此时原始需求量同步更新,
                # 已开始分批处理的需求不覆盖,避免把已补货进度算乱。
                if not existing_demand.process_note:
                    existing_demand.shortage_quantity = shortage_quantity
                    existing_demand.required_quantity = shortage_quantity
                    existing_demand.fulfilled_quantity = 0
                ensure_fulfillment_demand_routing(
                    db,
                    demand=existing_demand,
                    order=order,
                    product=product,
                    current_user=current_user,
                )
                # 原料采购由生产人员确认数量后提交，不随成品补货自动生成。
            created_demands.append(
                {
                    "id": existing_demand.id,
                    "demand_no": existing_demand.demand_no,
                    "product_id": item.product_id,
                    "product_name": product.name,
                    "fulfillment_mode": existing_demand.fulfillment_mode,
                    "fulfillment_mode_label": "采购" if existing_demand.fulfillment_mode == "trade" else "生产",
                    "shortage_quantity": float(existing_demand.shortage_quantity or shortage_quantity),
                    # 复用了已存在的未完成需求(去重),不是新建。
                    # 调用方据此区分提示文案:否则重复点"提补货需求"也报
                    # "已提 N 条",库管分不清到底提没提。
                    "reused": True,
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
            # 此处只创建成品生产需求，原料补货在生产页面单独确认。
            created_demands.append(
                {
                    "id": demand.id,
                    "demand_no": demand.demand_no,
                    "product_id": item.product_id,
                    "product_name": product.name,
                    "fulfillment_mode": demand.fulfillment_mode,
                    "fulfillment_mode_label": "采购" if demand.fulfillment_mode == "trade" else "生产",
                    "shortage_quantity": float(shortage_quantity),
                    "reused": False,
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


