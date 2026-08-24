from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    CashFlowRecord,
    Inventory,
    InventoryBatch,
    InventoryTransaction,
    Material,
    Notification,
    NotificationRecipient,
    OperationLog,
    Product,
    Role,
    SequenceCounter,
    TodoItem,
    User,
    Warehouse,
)
from services.realtime import realtime_manager
from services.request_trace import emit
from services.time_utils import now_local

LOW_STOCK_BUSINESS_TYPE = 'low_stock_alert'


def model_to_dict(instance, fields: list[str] | None = None) -> dict:
    data = {}
    for column in instance.__table__.columns:
        if fields and column.name not in fields:
            continue
        value = getattr(instance, column.name)
        if isinstance(value, Decimal):
            data[column.name] = float(value)
        elif isinstance(value, (datetime, date)):
            data[column.name] = value.isoformat()
        else:
            data[column.name] = value
    return data


def jsonable(data):
    return json.loads(json.dumps(data, default=str))


def next_business_no(db: Session, prefix: str, business_date: date | None = None) -> str:
    current_date = business_date or date.today()
    date_key = current_date.strftime('%Y%m%d')
    counter = db.execute(
        select(SequenceCounter).where(
            SequenceCounter.sequence_key == prefix,
            SequenceCounter.business_date == date_key,
        )
    ).scalar_one_or_none()
    if counter is None:
        counter = SequenceCounter(sequence_key=prefix, business_date=date_key, current_value=0)
        db.add(counter)
        db.flush()
    counter.current_value += 1
    db.flush()
    return f'{prefix}{date_key}{counter.current_value:03d}'


def create_todo(
    db: Session,
    *,
    title: str,
    description: str | None,
    module: str,
    assigned_user_id: int,
    business_type: str | None = None,
    business_id: int | None = None,
    priority: str = 'normal',
):
    if business_type is not None and business_id is not None:
        existing = db.execute(
            select(TodoItem)
            .where(
                TodoItem.business_type == business_type,
                TodoItem.business_id == business_id,
                TodoItem.assigned_user_id == assigned_user_id,
                TodoItem.status != 'completed',
            )
            .order_by(TodoItem.id.asc())
        ).scalars().first()
        if existing is not None:
            return existing

    todo = TodoItem(
        title=title,
        description=description,
        module=module,
        assigned_user_id=assigned_user_id,
        business_type=business_type,
        business_id=business_id,
        priority=priority,
    )
    db.add(todo)
    db.flush()
    emit(
        'todo_created',
        todo_id=todo.id,
        business_type=business_type,
        business_id=business_id,
        assigned_user_id=assigned_user_id,
        module=module,
    )
    realtime_manager.push_to_user(
        assigned_user_id,
        {
            'type': 'todo_created',
            'id': todo.id,
            'title': todo.title,
            'description': todo.description,
            'module': todo.module,
            'status': todo.status,
            'priority': todo.priority,
            'created_at': todo.created_at.isoformat() if todo.created_at else None,
        },
    )
    return todo


def complete_todos(
    db: Session,
    *,
    todo_ids: list[int] | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    assigned_user_id: int | None = None,
):
    statement = select(TodoItem).where(TodoItem.status != 'completed')
    if todo_ids:
        statement = statement.where(TodoItem.id.in_(todo_ids))
    if business_type:
        statement = statement.where(TodoItem.business_type == business_type)
    if business_id is not None:
        statement = statement.where(TodoItem.business_id == business_id)
    if assigned_user_id is not None:
        statement = statement.where(TodoItem.assigned_user_id == assigned_user_id)

    rows = db.execute(statement).scalars().all()
    completed_at = now_local()
    for row in rows:
        row.status = 'completed'
        row.completed_at = completed_at
        realtime_manager.push_to_user(row.assigned_user_id, {'type': 'todo_completed', 'id': row.id})
    db.flush()
    return rows


def create_notification(
    db: Session,
    *,
    title: str,
    content: str,
    module: str,
    recipient_user_ids: list[int],
    category: str = 'info',
    created_by_id: int | None = None,
    is_popup: bool = True,
):
    unique_recipient_ids = [user_id for user_id in dict.fromkeys(recipient_user_ids) if user_id is not None]

    notification = Notification(
        title=title,
        content=content,
        module=module,
        category=category,
        is_popup=is_popup,
        created_by_id=created_by_id,
    )
    db.add(notification)
    db.flush()
    recipients = []
    for user_id in unique_recipient_ids:
        recipient = NotificationRecipient(notification_id=notification.id, user_id=user_id)
        recipients.append(recipient)
        db.add(recipient)
    db.flush()
    emit(
        'notification_created',
        notification_id=notification.id,
        module=module,
        recipient_user_ids=unique_recipient_ids,
        category=category,
    )
    for recipient in recipients:
        realtime_manager.push_to_user(
            recipient.user_id,
            {
                'type': 'notification_created',
                'id': recipient.id,
                'notification_id': notification.id,
                'title': notification.title,
                'content': notification.content,
                'module': notification.module,
                'category': notification.category,
                'created_at': recipient.created_at.isoformat() if recipient.created_at else None,
            },
        )
    return notification


def ensure_todo(
    db: Session,
    *,
    title: str,
    description: str | None,
    module: str,
    assigned_user_id: int,
    business_type: str | None = None,
    business_id: int | None = None,
    priority: str = 'normal',
):
    """Create one pending todo per user and business object."""
    return create_todo(
        db,
        title=title,
        description=description,
        module=module,
        assigned_user_id=assigned_user_id,
        business_type=business_type,
        business_id=business_id,
        priority=priority,
    )


def ensure_notification(
    db: Session,
    *,
    title: str,
    content: str,
    module: str,
    recipient_user_ids: list[int],
    category: str = 'info',
    created_by_id: int | None = None,
    is_popup: bool = True,
):
    """Create a notification only when this workflow message is absent."""
    unique_recipient_ids = [user_id for user_id in dict.fromkeys(recipient_user_ids) if user_id is not None]
    if unique_recipient_ids:
        existing = db.execute(
            select(Notification)
            .join(NotificationRecipient, NotificationRecipient.notification_id == Notification.id)
            .where(
                Notification.title == title,
                Notification.content == content,
                Notification.module == module,
                NotificationRecipient.user_id.in_(unique_recipient_ids),
            )
            .order_by(Notification.id.asc())
        ).scalars().first()
        if existing is not None:
            return existing
    return create_notification(
        db,
        title=title,
        content=content,
        module=module,
        recipient_user_ids=unique_recipient_ids,
        category=category,
        created_by_id=created_by_id,
        is_popup=is_popup,
    )


def write_operation_log(
    db: Session,
    *,
    user_id: int | None,
    module: str,
    action: str,
    content: str,
    before_data=None,
    after_data=None,
):
    log = OperationLog(
        user_id=user_id,
        module=module,
        action=action,
        content=content,
        before_data=jsonable(before_data),
        after_data=jsonable(after_data),
    )
    db.add(log)
    db.flush()
    return log


def ensure_inventory_record(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int | None = None,
    material_id: int | None = None,
):
    conditions = [Inventory.warehouse_id == warehouse_id]
    conditions.append(Inventory.product_id == product_id if product_id is not None else Inventory.product_id.is_(None))
    conditions.append(Inventory.material_id == material_id if material_id is not None else Inventory.material_id.is_(None))
    inventory = db.execute(select(Inventory).where(*conditions)).scalar_one_or_none()
    if inventory is None:
        inventory = Inventory(
            warehouse_id=warehouse_id,
            product_id=product_id,
            material_id=material_id,
            quantity=0,
            reserved_quantity=0,
            avg_unit_cost=0,
        )
        db.add(inventory)
        db.flush()
    return inventory


def _role_user_ids(db: Session, *role_codes: str) -> list[int]:
    if not role_codes:
        return []
    return [
        row.id
        for row in db.execute(
            select(User).join(Role, User.role_id == Role.id).where(Role.code.in_(role_codes), User.is_active.is_(True))
        ).scalars().all()
    ]


def resolve_workflow_recipients(
    db: Session,
    *role_codes: str,
    fallback_role_codes: tuple[str, ...] = ('system_admin',),
    exclude_user_ids: set[int] | None = None,
    workflow: str = 'workflow',
    business_type: str | None = None,
    business_id: int | None = None,
) -> list[int]:
    """Resolve active workflow owners and fail explicitly when none exist."""
    excluded = exclude_user_ids or set()
    primary_ids = [user_id for user_id in _role_user_ids(db, *role_codes) if user_id not in excluded]
    selected_ids = list(dict.fromkeys(primary_ids))
    resolution = 'primary'
    if not selected_ids and fallback_role_codes:
        selected_ids = [user_id for user_id in _role_user_ids(db, *fallback_role_codes) if user_id not in excluded]
        selected_ids = list(dict.fromkeys(selected_ids))
        resolution = 'fallback'

    emit(
        'workflow_recipient_resolution',
        workflow=workflow,
        business_type=business_type,
        business_id=business_id,
        requested_roles=list(role_codes),
        fallback_roles=list(fallback_role_codes),
        resolution=resolution,
        recipient_user_ids=selected_ids,
    )
    if not selected_ids:
        raise HTTPException(
            status_code=503,
            detail=f'{workflow} 未找到可处理人员，请先配置有效的责任人或系统管理员账号',
        )
    return selected_ids


def ensure_workflow_routing(
    db: Session,
    *,
    title: str,
    description: str | None,
    module: str,
    business_type: str,
    business_id: int,
    recipient_role_codes: tuple[str, ...],
    notification_content: str,
    notification_recipient_user_ids: list[int] | None = None,
    priority: str = 'normal',
    category: str = 'info',
    created_by_id: int | None = None,
    workflow: str = 'workflow',
):
    """Persist and repair one workflow's todos and notification idempotently."""
    recipient_ids = resolve_workflow_recipients(
        db,
        *recipient_role_codes,
        workflow=workflow,
        business_type=business_type,
        business_id=business_id,
    )
    todos = [
        ensure_todo(
            db,
            title=title,
            description=description,
            module=module,
            assigned_user_id=user_id,
            business_type=business_type,
            business_id=business_id,
            priority=priority,
        )
        for user_id in recipient_ids
    ]
    notification = ensure_notification(
        db,
        title=title,
        content=notification_content,
        module=module,
        recipient_user_ids=list(dict.fromkeys([*(notification_recipient_user_ids or []), *recipient_ids])),
        category=category,
        created_by_id=created_by_id,
    )
    emit(
        'workflow_routed',
        workflow=workflow,
        business_type=business_type,
        business_id=business_id,
        recipient_user_ids=recipient_ids,
        todo_ids=[todo.id for todo in todos],
        notification_id=notification.id if notification else None,
    )
    return todos, notification


def _inventory_item_context(inventory: Inventory) -> dict:
    item = inventory.product or inventory.material
    safety_stock_qty = Decimal(getattr(item, 'safety_stock_qty', 0) or 0)
    available_quantity = Decimal(inventory.quantity or 0) - Decimal(inventory.reserved_quantity or 0)
    return {
        'item_name': getattr(item, 'name', '未命名物料'),
        'item_code': getattr(item, 'product_code', None) or getattr(item, 'material_code', None) or '-',
        'item_type': 'product' if inventory.product_id else 'material',
        'warehouse_name': inventory.warehouse.name if inventory.warehouse else f'仓库#{inventory.warehouse_id}',
        'safety_stock_qty': safety_stock_qty,
        'available_quantity': available_quantity,
    }


def _pending_low_stock_todos(db: Session, inventory_id: int) -> list[TodoItem]:
    return db.execute(
        select(TodoItem).where(
            TodoItem.business_type == LOW_STOCK_BUSINESS_TYPE,
            TodoItem.business_id == inventory_id,
            TodoItem.status != 'completed',
        )
    ).scalars().all()


def _sync_low_stock_alert_for_inventory(
    db: Session,
    inventory: Inventory,
    *,
    notify_on_recovery: bool = False,
):
    context = _inventory_item_context(inventory)
    safety_stock_qty = Decimal(context['safety_stock_qty'])
    available_quantity = Decimal(context['available_quantity'])
    pending_todos = _pending_low_stock_todos(db, inventory.id)
    pending_user_ids = [todo.assigned_user_id for todo in pending_todos]

    if safety_stock_qty <= 0:
        if pending_todos:
            complete_todos(db, business_type=LOW_STOCK_BUSINESS_TYPE, business_id=inventory.id)
        return

    if inventory.product_id:
        from services.replenishment_service import sync_product_low_stock_replenishment

        sync_product_low_stock_replenishment(db, inventory=inventory, notify_on_recovery=notify_on_recovery)
    elif inventory.material_id:
        from services.replenishment_service import sync_material_low_stock_replenishment

        sync_material_low_stock_replenishment(db, inventory=inventory, notify_on_recovery=notify_on_recovery)

    is_below = available_quantity < safety_stock_qty
    target_user_ids = list(dict.fromkeys(_role_user_ids(db, 'manager', 'chairman')))

    if is_below:
        missing_user_ids = [user_id for user_id in target_user_ids if user_id not in pending_user_ids]
        if not missing_user_ids:
            return

        description = (
            f"{context['warehouse_name']} 的 {context['item_name']}（{context['item_code']}）当前可用库存 "
            f"{float(available_quantity)}，已低于安全库存 {float(safety_stock_qty)}。"
        )
        for user_id in missing_user_ids:
            create_todo(
                db,
                title=f"安全库存预警：{context['item_name']}",
                description=description,
                module='inventory',
                assigned_user_id=user_id,
                business_type=LOW_STOCK_BUSINESS_TYPE,
                business_id=inventory.id,
            )
        create_notification(
            db,
            title='安全库存预警',
            content=description,
            module='inventory',
            recipient_user_ids=missing_user_ids,
            category='warning',
        )
        return

    if pending_todos:
        complete_todos(db, business_type=LOW_STOCK_BUSINESS_TYPE, business_id=inventory.id)
        if notify_on_recovery:
            create_notification(
                db,
                title='安全库存恢复',
                content=(
                    f"{context['warehouse_name']} 的 {context['item_name']}（{context['item_code']}）可用库存已恢复至 "
                    f"{float(available_quantity)}，不再低于安全库存 {float(safety_stock_qty)}。"
                ),
                module='inventory',
                recipient_user_ids=pending_user_ids,
                category='info',
                is_popup=False,
            )


def sync_low_stock_alerts(db: Session):
    finished_warehouse = db.execute(
        select(Warehouse).where(Warehouse.warehouse_type == 'finished').order_by(Warehouse.id.asc())
    ).scalars().first()
    raw_warehouse = db.execute(
        select(Warehouse).where(Warehouse.warehouse_type == 'raw').order_by(Warehouse.id.asc())
    ).scalars().first()
    if finished_warehouse:
        existing_product_ids = {
            row[0]
            for row in db.execute(
                select(Inventory.product_id).where(
                    Inventory.warehouse_id == finished_warehouse.id,
                    Inventory.product_id.is_not(None),
                )
            ).all()
        }
        product_ids = [
            row[0]
            for row in db.execute(select(Product.id).where(Product.is_active.is_(True))).all()
        ]
        for product_id in product_ids:
            if product_id not in existing_product_ids:
                ensure_inventory_record(db, warehouse_id=finished_warehouse.id, product_id=product_id)
    if raw_warehouse:
        existing_material_ids = {
            row[0]
            for row in db.execute(
                select(Inventory.material_id).where(
                    Inventory.warehouse_id == raw_warehouse.id,
                    Inventory.material_id.is_not(None),
                )
            ).all()
        }
        material_ids = [
            row[0]
            for row in db.execute(select(Material.id).where(Material.is_active.is_(True))).all()
        ]
        for material_id in material_ids:
            if material_id not in existing_material_ids:
                ensure_inventory_record(db, warehouse_id=raw_warehouse.id, material_id=material_id)
    db.flush()
    inventories = db.execute(select(Inventory).order_by(Inventory.id.asc())).scalars().all()
    for inventory in inventories:
        _sync_low_stock_alert_for_inventory(db, inventory)
    db.flush()


def apply_inventory_change(
    db: Session,
    *,
    warehouse_id: int,
    quantity: Decimal,
    transaction_type: str,
    unit_cost: Decimal = Decimal('0'),
    batch_no: str | None = None,
    product_id: int | None = None,
    material_id: int | None = None,
    related_type: str | None = None,
    related_id: int | None = None,
    created_by_id: int | None = None,
):
    inventory = ensure_inventory_record(
        db,
        warehouse_id=warehouse_id,
        product_id=product_id,
        material_id=material_id,
    )
    previous_qty = Decimal(inventory.quantity or 0)
    previous_cost = Decimal(inventory.avg_unit_cost or 0)
    inventory.quantity = previous_qty + quantity
    if inventory.quantity < 0:
        raise ValueError('库存数量不能小于0')

    if quantity > 0 and unit_cost >= 0:
        total_cost_before = previous_qty * previous_cost
        total_cost_after = total_cost_before + quantity * unit_cost
        inventory.avg_unit_cost = (total_cost_after / inventory.quantity) if inventory.quantity else 0

    transaction = InventoryTransaction(
        transaction_no=next_business_no(db, 'KC'),
        transaction_type=transaction_type,
        warehouse_id=warehouse_id,
        product_id=product_id,
        material_id=material_id,
        batch_no=batch_no,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=quantity * unit_cost,
        related_type=related_type,
        related_id=related_id,
        created_by_id=created_by_id,
    )
    db.add(transaction)

    if batch_no:
        batch = db.execute(
            select(InventoryBatch).where(
                InventoryBatch.warehouse_id == warehouse_id,
                InventoryBatch.batch_no == batch_no,
                InventoryBatch.product_id == product_id,
                InventoryBatch.material_id == material_id,
            )
        ).scalar_one_or_none()
        if batch is None:
            batch = InventoryBatch(
                warehouse_id=warehouse_id,
                product_id=product_id,
                material_id=material_id,
                batch_no=batch_no,
                quantity=0,
                unit_cost=unit_cost,
            )
            db.add(batch)
        batch.quantity = Decimal(batch.quantity or 0) + quantity
        batch.unit_cost = unit_cost

    db.flush()
    _sync_low_stock_alert_for_inventory(db, inventory, notify_on_recovery=True)
    return inventory, transaction


def create_cash_flow(
    db: Session,
    *,
    flow_type: str,
    business_type: str,
    business_id: int,
    related_no: str | None,
    amount: Decimal,
    happened_at: datetime,
    remark: str | None = None,
):
    record = CashFlowRecord(
        record_no=next_business_no(db, 'ZJ'),
        flow_type=flow_type,
        business_type=business_type,
        business_id=business_id,
        related_no=related_no,
        amount=amount,
        happened_at=happened_at,
        remark=remark,
    )
    db.add(record)
    db.flush()
    return record
