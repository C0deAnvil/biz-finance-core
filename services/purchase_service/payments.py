from __future__ import annotations
from services.financial_accounts import validate_settlement_accounts, settlement_account_fields, validate_supplier_account, requested_account_id
"""采购域服务。"""


import json
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from models import (
    Attachment,
    AppSetting,
    FulfillmentDemand,
    Inventory,
    Material,
    PayableRecord,
    Product,
    ProductionMaterialUsage,
    PurchaseOrder,
    PurchaseOrderApproval,
    PurchaseOrderItem,
    PurchasePaymentRequest,
    PurchaseReceipt,
    PurchaseReceiptItem,
    PurchaseReturn,
    Role,
    SupplierCatalogItem,
    Supplier,
    TodoItem,
    User,
    Warehouse,
)
from responses import api_response
from schemas.purchases import (
    PurchasePaymentInvoiceUpdate,
    PurchaseApprovalRequest,
    PurchaseFundApprovalRequest,
    PurchaseFundRequestCreate,
    PurchaseMergedPaymentRequestCreate,
    PurchaseOrderCreate,
    PurchasePaymentApprovalRequest,
    PurchasePaymentCompleteRequest,
    PurchasePaymentRequestCreate,
    PurchaseOrderUpdate,
    PurchaseReceiptCreate,
    PurchaseReturnCreate,
)
from schemas.finance import FinanceMergedPurchasePaymentRequestRegisterRequest
from services.core import apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, next_business_no, resolve_assigned_finance_user_id, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label, status_label
from services.quality_workflow import create_purchase_quality_tasks, create_return_reinspection_quality_task
from services.time_utils import now_local
from services.workflow import create_inventory_operation_request, purchase_arrival_status, purchase_flow_steps


REPLENISHMENT_SOURCE_TYPES = {
    "raw_material_replenishment",
    "low_stock_replenishment",
    "low_stock_product_material_replenishment",
    "fulfillment_demand",
    "sales_order_material_replenishment",
}




from services.purchase_service._common import _chairman_user_ids, _create_purchase_payment_request_row, _ensure_order_owner, _finance_user_ids, _latest_fund_record, _load_order, _manager_user_ids, _purchase_approval_amount_threshold, _purchase_arrival_display_label, _purchase_contract_attachments, _purchase_order_business_completed, _purchase_paid_order_amount, _round_robin_finance_user_id, _validate_merged_orders_same_supplier, _validate_order_payment_requestable, _validate_payment_amounts

def request_purchase_fund(payload: PurchaseFundRequestCreate, current_user: User, db: Session):
    order = _load_order(db, payload.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="已作废采购单不能申请拨款")

    latest_record = _latest_fund_record(db, order.id)
    if latest_record and latest_record.status in {"pending_manager_approval", "pending_finance_approval", "paid"}:
        raise HTTPException(status_code=409, detail="当前采购单已有未结清拨款流程")

    record = PayableRecord(
        purchase_order_id=order.id,
        amount=payload.amount,
        paid_at=now_local(),
        paid_by_id=current_user.id,
        status="pending_manager_approval",
        remark=(payload.remark or f"采购拨款申请：{order.order_no}").strip(),
    )
    db.add(record)
    db.flush()

    chairman_user_ids = _chairman_user_ids(db)
    finance_user_ids = _finance_user_ids(db)
    for user_id in chairman_user_ids:
        create_todo(
            db,
            title=f"待经理审批采购拨款 {order.order_no}",
            description=f"采购申请拨款 {float(payload.amount)}，请审批。",
            module="purchase",
            assigned_user_id=user_id,
            business_type="purchase_fund_request",
            business_id=record.id,
            priority="high",
        )
    create_notification(
        db,
        title="新增采购拨款申请",
        content=f"采购订单 {order.order_no} 已申请拨款 {float(payload.amount)}，等待经理与财务协同处理。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="request_purchase_fund",
        content=f"为采购订单 {order.order_no} 申请拨款",
        after_data={"payable_id": record.id, "amount": float(record.amount), "status": record.status},
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})


def submit_purchase_payment_request(payload: PurchasePaymentRequestCreate, current_user: User, db: Session):
    order = _load_order(db, payload.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _validate_order_payment_requestable(order, current_user, db)

    if Decimal(payload.requested_amount or 0) != Decimal(payload.order_amount or 0) + Decimal(payload.other_fee_amount or 0):
        raise HTTPException(status_code=409, detail="申请总金额必须等于订单金额加其他费用")
    _validate_payment_amounts(db, order, Decimal(payload.order_amount or 0), Decimal(payload.other_fee_amount or 0))

    # 显式指定的财务必须校验(改造前直接采信入参,可把付款申请指派给销售等非财务角色);
    # 未指定时沿用采购域按"待处理付款申请数"的轮询,比按待办总数更贴合该场景。
    assigned_finance_user_id = (
        resolve_assigned_finance_user_id(db, payload.assigned_finance_user_id)
        if payload.assigned_finance_user_id is not None
        else _round_robin_finance_user_id(db)
    )
    # 只有付款金额达到阈值才需要董事长审批;未达阈值直接进入财务出账环节。
    # 原先所有付款申请一律先派给董事长,小额付款也要等审批,流程无谓变长。
    requested_amount = Decimal(payload.requested_amount or 0)
    threshold_amount = _purchase_approval_amount_threshold(db)
    needs_chairman_approval = threshold_amount > 0 and requested_amount >= threshold_amount

    request = _create_purchase_payment_request_row(
        db,
        order=order,
        current_user=current_user,
        requested_amount=requested_amount,
        order_amount=Decimal(payload.order_amount or 0),
        other_fee_amount=Decimal(payload.other_fee_amount or 0),
        payment_detail_remark=payload.payment_detail_remark,
        assigned_finance_user_id=assigned_finance_user_id,
        preset_status="pending_chairman_approval" if needs_chairman_approval else "pending_finance_payment",
    )

    request.counterparty_account_id = validate_supplier_account(db, payload.counterparty_account_id, order.supplier_id)
    chairman_user_ids = _chairman_user_ids(db)
    finance_user_ids = _finance_user_ids(db)
    if needs_chairman_approval:
        for user_id in chairman_user_ids:
            create_todo(
                db,
                title=f"待审批采购付款申请 {request.request_no}",
                description=f"采购订单 {order.order_no} 提交付款申请，申请金额 {float(request.requested_amount)}。",
                module="purchase",
                assigned_user_id=user_id,
                business_type="purchase_payment_request",
                business_id=request.id,
                priority="high",
            )
        notice_content = (
            f"采购订单 {order.order_no} 已提交付款申请 {request.request_no}，"
            f"金额 {float(requested_amount)} 达到审批阈值 {float(threshold_amount)}，等待董事长审批。"
        )
    else:
        # 未达阈值:跳过董事长,直接给财务派出账待办(与审批通过后的派单口径一致)
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"采购付款待出账 {request.request_no}",
                description=(
                    f"采购订单 {order.order_no} 的付款申请金额 {float(requested_amount)} "
                    f"未达审批阈值 {float(threshold_amount)}，无需董事长审批，请财务上传回单并确认出账。"
                ),
                module="finance",
                assigned_user_id=user_id,
                business_type="purchase_payment_request",
                business_id=request.id,
                priority="high",
            )
        notice_content = (
            f"采购订单 {order.order_no} 已提交付款申请 {request.request_no}，"
            f"金额 {float(requested_amount)} 未达审批阈值，已直接流转至财务确认出账。"
        )
    recipient_user_ids = list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids]))
    create_notification(
        db,
        title="采购付款申请已提交",
        content=notice_content,
        module="purchase",
        recipient_user_ids=recipient_user_ids,
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="submit_purchase_payment_request",
        content=f"提交采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "purchase_order_id": order.id,
            "requested_amount": float(request.requested_amount or 0),
            "order_amount": float(request.order_amount or 0),
            "other_fee_amount": float(request.other_fee_amount or 0),
            "arrival_status": request.arrival_status,
            "status": request.status,
            "contract_attachments": _purchase_contract_attachments(db, order.id),
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "request_no": request.request_no, "status": request.status})


def submit_merged_purchase_payment_request(payload: PurchaseMergedPaymentRequestCreate, current_user: User, db: Session):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择两张采购订单")
    order_ids = [item.purchase_order_id for item in payload.items]
    if len(set(order_ids)) != len(order_ids):
        raise HTTPException(status_code=409, detail="合并付款中存在重复采购订单")
    orders = [order for order in (_load_order(db, order_id) for order_id in order_ids) if order is not None]
    if len(orders) != len(order_ids):
        raise HTTPException(status_code=404, detail="存在不存在的采购订单")
    _validate_merged_orders_same_supplier(orders, payload.supplier_id)

    payload_map = {item.purchase_order_id: item for item in payload.items}
    merge_no = next_business_no(db, "HFK")
    chairman_user_ids = _chairman_user_ids(db)
    finance_user_ids = _finance_user_ids(db)
    created_rows: list[PurchasePaymentRequest] = []
    requested_total = Decimal("0")

    first_request_id: int | None = None
    # 整批合并付款派给同一个财务(在循环外解析一次,避免同批被拆给不同人)。
    # 改造前合并路径完全不写该字段,导致合并付款申请恒为"未分配"。
    # 显式指定的财务必须校验(改造前直接采信入参,可把付款申请指派给销售等非财务角色);
    # 未指定时沿用采购域按"待处理付款申请数"的轮询,比按待办总数更贴合该场景。
    assigned_finance_user_id = (
        resolve_assigned_finance_user_id(db, payload.assigned_finance_user_id)
        if payload.assigned_finance_user_id is not None
        else _round_robin_finance_user_id(db)
    )
    # 与单笔口径一致:按阈值决定是否需要董事长审批。
    # 合并付款是一次性出账,风险敞口是**批次总额**,所以用总额和阈值比,
    # 而不是逐单比(否则拆成多张小单就能绕过审批)。
    # 先算总额,再据此决定所有子申请的初始状态。
    prepared_rows: list[tuple] = []
    for order in orders:
        _validate_order_payment_requestable(order, current_user, db)
        item = payload_map[order.id]
        order_amount = Decimal(item.order_amount or 0)
        other_fee_amount = Decimal(item.other_fee_amount or 0)
        _validate_payment_amounts(db, order, order_amount, other_fee_amount)
        requested_amount = order_amount + other_fee_amount
        prepared_rows.append((order, order_amount, other_fee_amount, requested_amount))
        requested_total += requested_amount

    threshold_amount = _purchase_approval_amount_threshold(db)
    needs_chairman_approval = threshold_amount > 0 and requested_total >= threshold_amount
    preset_status = "pending_chairman_approval" if needs_chairman_approval else "pending_finance_payment"

    for order, order_amount, other_fee_amount, requested_amount in prepared_rows:
        request = _create_purchase_payment_request_row(
            db,
            order=order,
            current_user=current_user,
            requested_amount=requested_amount,
            order_amount=order_amount,
            other_fee_amount=other_fee_amount,
            payment_detail_remark=payload.payment_detail_remark,
            merge_no=merge_no,
            assigned_finance_user_id=assigned_finance_user_id,
            preset_status=preset_status,
        )
        request.counterparty_account_id = validate_supplier_account(db, payload.counterparty_account_id, order.supplier_id)
        created_rows.append(request)
        if first_request_id is None:
            first_request_id = request.id

    if needs_chairman_approval:
        for user_id in chairman_user_ids:
            create_todo(
                db,
                title=f"待审批合并付款申请 {merge_no}",
                description=f"合并付款批次 {merge_no} 包含 {len(created_rows)} 张采购订单，申请总额 {float(requested_total)}。",
                module="purchase",
                assigned_user_id=user_id,
                business_type="purchase_payment_request",
                business_id=first_request_id,
                priority="high",
            )
        merged_notice = (
            f"供应商合并付款批次 {merge_no} 已提交，共 {len(created_rows)} 张采购订单，"
            f"申请总额 {float(requested_total)} 达到审批阈值 {float(threshold_amount)}，等待董事长审批。"
        )
    else:
        # 未达阈值:跳过董事长,直接派财务出账待办
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"合并付款待出账 {merge_no}",
                description=(
                    f"合并付款批次 {merge_no} 共 {len(created_rows)} 张采购订单，"
                    f"总额 {float(requested_total)} 未达审批阈值 {float(threshold_amount)}，"
                    f"无需董事长审批，请财务上传回单并确认出账。"
                ),
                module="finance",
                assigned_user_id=user_id,
                business_type="purchase_payment_request",
                business_id=first_request_id,
                priority="high",
            )
        merged_notice = (
            f"供应商合并付款批次 {merge_no} 已提交，共 {len(created_rows)} 张采购订单，"
            f"申请总额 {float(requested_total)} 未达审批阈值，已直接流转至财务确认出账。"
        )

    create_notification(
        db,
        title="采购合并付款申请已提交",
        content=merged_notice,
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([current_user.id, *chairman_user_ids, *finance_user_ids])),
        created_by_id=current_user.id,
        category="warning",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="submit_purchase_payment_request",
        content=f"提交采购合并付款申请 {merge_no}",
        after_data={
            "merge_no": merge_no,
            "order_ids": order_ids,
            "requested_total": float(requested_total),
            "request_ids": [row.id for row in created_rows],
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


def register_merged_purchase_payment(payload, current_user: User, db: Session):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一张采购订单")
    order_ids = [item.purchase_order_id for item in payload.items]
    if len(set(order_ids)) != len(order_ids):
        raise HTTPException(status_code=409, detail="合并付款中存在重复采购订单")
    orders = [order for order in (_load_order(db, order_id) for order_id in order_ids) if order is not None]
    if len(orders) != len(order_ids):
        raise HTTPException(status_code=404, detail="存在不存在的采购订单")
    _validate_merged_orders_same_supplier(orders, payload.supplier_id, min_count=1)

    merge_no = next_business_no(db, "HFK")
    now = now_local()
    payload_map = {item.purchase_order_id: item for item in payload.items}
    expected_total = Decimal("0")
    request_ids: list[int] = []
    payable_ids: list[int] = []

    for order in orders:
        item = payload_map[order.id]
        order_amount = Decimal(item.order_amount or 0)
        other_fee_amount = Decimal(item.other_fee_amount or 0)
        _validate_payment_amounts(db, order, order_amount, other_fee_amount)
        requested_amount = order_amount + other_fee_amount
        expected_total += requested_amount

    if Decimal(payload.actual_amount or 0) != expected_total:
        raise HTTPException(status_code=409, detail="合并付款实付金额必须等于所有订单金额与其他费用之和")

    for order in orders:
        item = payload_map[order.id]
        order_amount = Decimal(item.order_amount or 0)
        other_fee_amount = Decimal(item.other_fee_amount or 0)
        requested_amount = order_amount + other_fee_amount
        request = _create_purchase_payment_request_row(
            db,
            order=order,
            current_user=current_user,
            requested_amount=requested_amount,
            order_amount=order_amount,
            other_fee_amount=other_fee_amount,
            payment_detail_remark=payload.remark,
            merge_no=merge_no,
            preset_status="pending_completion",
            approved_by_id=current_user.id,
            approved_at=now,
            manager_remark="应付管理直接登记付款",
        )
        request_ids.append(request.id)
        paid_order_amount = _purchase_paid_order_amount(db, order.id) + order_amount
        payable_status = "settled" if paid_order_amount >= Decimal(order.total_amount or 0) else "partial_settled"
        accounts = validate_settlement_accounts(db, payload.company_account_id, payload.counterparty_account_id, supplier_id=order.supplier_id)
        request.company_account_id = accounts["company_account_id"]
        request.counterparty_account_id = accounts["counterparty_account_id"]
        payable_record = PayableRecord(
            **accounts,
            purchase_order_id=order.id,
            purchase_payment_request_id=request.id,
            merge_no=merge_no,
            amount=requested_amount,
            order_amount=order_amount,
            other_fee_amount=other_fee_amount,
            paid_at=now,
            paid_by_id=current_user.id,
            status=payable_status,
            remark=payload.remark or f"采购合并付款 {merge_no}",
        )
        db.add(payable_record)
        db.flush()
        payable_ids.append(payable_record.id)
        create_cash_flow(
            db,
            flow_type="outgoing",
            business_type="purchase_order",
            business_id=order.id,
            related_no=order.order_no,
            amount=requested_amount,
            happened_at=now,
            remark=payload.remark or f"采购合并付款 {merge_no}",
        )
        create_notification(
            db,
            title="采购合并付款已登记",
            content=f"采购订单 {order.order_no} 已纳入合并付款批次 {merge_no}，请采购确认完成。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_finance_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_purchase_payment_request",
        content=f"登记采购合并付款 {merge_no}",
        after_data={
            "merge_no": merge_no,
            "order_ids": order_ids,
            "request_ids": request_ids,
            "payable_ids": payable_ids,
            "actual_amount": float(expected_total),
        },
    )
    db.commit()
    return api_response(data={"merge_no": merge_no, "request_ids": request_ids, "payable_ids": payable_ids})


def approve_purchase_payment_request(request_id: int, payload: PurchasePaymentApprovalRequest, current_user: User, db: Session):
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if request.status not in {"pending_manager_approval", "pending_chairman_approval"}:
        raise HTTPException(status_code=409, detail="当前付款申请状态不允许审批")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"chairman", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权审批采购付款申请")

    if payload.decision == "rejected":
        request.status = "rejected"
        request.manager_remark = payload.manager_remark
        request.approved_by_id = current_user.id
        request.approved_at = now_local()
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)
        create_notification(
            db,
            title="采购付款申请已驳回",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已被驳回，请重新填写。",
            module="purchase",
            recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
            created_by_id=current_user.id,
            category="warning",
        )
    else:
        actual_amount = Decimal(payload.actual_amount or 0)
        if actual_amount <= 0:
            actual_amount = Decimal(request.requested_amount or 0)
        if actual_amount != Decimal(request.requested_amount or 0):
            request.status = "rejected"
            request.manager_remark = payload.manager_remark or "审批金额与申请金额不一致，已退回采购重填"
            request.approved_by_id = current_user.id
            request.approved_at = now_local()
            complete_todos(db, business_type="purchase_payment_request", business_id=request.id)
            create_notification(
                db,
                title="采购付款申请金额不一致",
                content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 审批金额与申请金额不一致，已退回采购。",
                module="purchase",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
            db.commit()
            return api_response(data={"id": request.id, "status": request.status})

        request.actual_amount = actual_amount
        request.manager_remark = payload.manager_remark
        request.approved_by_id = current_user.id
        request.approved_at = now_local()
        request.status = "pending_finance_payment"
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)

        finance_user_ids = _finance_user_ids(db)
        for user_id in finance_user_ids:
            create_todo(
                db,
                title=f"采购付款待出账 {request.request_no}",
                description=f"采购订单 {order.order_no} 的付款申请已由董事长审批通过，请财务上传回单并确认出账。",
                module="finance",
                assigned_user_id=user_id,
                business_type="purchase_payment_request",
                business_id=request.id,
                priority="high",
            )
        create_notification(
            db,
            title="采购付款申请已通过",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已通过，已流转至财务确认出账。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *finance_user_ids])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="approve_purchase_payment_request",
        content=f"审批采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "decision": payload.decision,
            "actual_amount": float(payload.actual_amount or 0),
            "status": request.status,
            "contract_attachments": _purchase_contract_attachments(db, order.id),
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def register_purchase_payment_request(request_id: int, payload: PurchasePaymentApprovalRequest, current_user: User, db: Session):
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    db.refresh(request, with_for_update=True)
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    if request.status != "pending_finance_payment":
        raise HTTPException(status_code=409, detail="当前付款申请状态不允许登记出账")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"finance", "finance_super", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权登记采购出账")
    # 指定财务校验:普通财务只能处理分配给自己的;超级财务可接管任何未处理付款
    if (
        request.assigned_finance_user_id
        and request.assigned_finance_user_id != current_user.id
        and role_code not in {"finance_super", "system_admin"}
    ):
        assigned_user = db.execute(select(User).where(User.id == request.assigned_finance_user_id)).scalar_one_or_none()
        assigned_name = assigned_user.full_name if assigned_user else f"#{request.assigned_finance_user_id}"
        raise HTTPException(status_code=403, detail=f"该付款已指定由 {assigned_name} 处理,不能越权登记")

    actual_amount = Decimal(payload.actual_amount or 0)
    if actual_amount <= 0:
        raise HTTPException(status_code=409, detail="实际出账金额必须大于 0")
    if not getattr(payload, "bank_receipt_attachment_id", None):
        raise HTTPException(status_code=409, detail="请先上传银行回单或出账凭证")

    now = now_local()
    request.actual_amount = actual_amount
    request.manager_remark = payload.manager_remark or request.manager_remark
    request.payment_method = getattr(payload, "payment_method", None) or request.payment_method
    request.bank_receipt_attachment_id = getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id
    request.paid_at = getattr(payload, "paid_at", None) or now
    if request.approved_by_id is None:
        request.approved_by_id = current_user.id
    if request.approved_at is None:
        request.approved_at = now

    if actual_amount != Decimal(request.requested_amount or 0):
        request.status = "rejected"
        request.manager_remark = payload.manager_remark or "出账金额与申请金额不一致，已退回采购重填"
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)
        create_notification(
            db,
            title="采购付款申请金额不一致",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 出账金额与申请金额不一致，已退回采购。",
            module="purchase",
            recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
            created_by_id=current_user.id,
            category="warning",
        )
        write_operation_log(
            db,
            user_id=current_user.id,
            module="finance",
            action="register_purchase_payment_request",
            content=f"退回采购订单 {order.order_no} 的付款申请 {request.request_no}",
            after_data={"request_no": request.request_no, "actual_amount": float(actual_amount), "status": request.status},
        )
        db.commit()
        return api_response(data={"id": request.id, "status": request.status})

    request.status = "pending_completion"
    complete_todos(db, business_type="purchase_payment_request", business_id=request.id)

    paid_order_amount = _purchase_paid_order_amount(db, order.id) + Decimal(request.order_amount or 0)
    payable_status = "settled" if paid_order_amount >= Decimal(order.total_amount or 0) else "partial_settled"
    accounts = validate_settlement_accounts(db, payload.company_account_id, requested_account_id(payload.counterparty_account_id, request.counterparty_account_id, "供应商收款账户"), supplier_id=order.supplier_id)
    request.company_account_id = accounts["company_account_id"]
    request.counterparty_account_id = accounts["counterparty_account_id"]
    payable_record = PayableRecord(
        **accounts,
        purchase_order_id=order.id,
        purchase_payment_request_id=request.id,
        merge_no=request.merge_no,
        amount=actual_amount,
        order_amount=request.order_amount,
        other_fee_amount=request.other_fee_amount,
        paid_at=getattr(payload, "paid_at", None) or now,
        paid_by_id=current_user.id,
        status=payable_status,
        remark=request.payment_detail_remark or f"采购付款申请 {request.request_no}",
        payment_method=getattr(payload, "payment_method", None) or request.payment_method,
        bank_receipt_attachment_id=getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id,
    )
    db.add(payable_record)
    create_cash_flow(
        db,
        flow_type="outgoing",
        business_type="purchase_order",
        business_id=order.id,
        related_no=order.order_no,
        amount=actual_amount,
        happened_at=getattr(payload, "paid_at", None) or now,
        remark=request.payment_detail_remark or f"采购付款申请 {request.request_no}",
    )
    create_notification(
        db,
        title="采购出账已登记",
        content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已登记出账，请采购确认完成。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_chairman_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_purchase_payment_request",
        content=f"登记采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={"request_no": request.request_no, "actual_amount": float(actual_amount), "status": request.status},
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def register_purchase_payment_requests_batch(
    payload: FinanceMergedPurchasePaymentRequestRegisterRequest,
    current_user: User,
    db: Session,
):
    if not payload.items:
        raise HTTPException(status_code=409, detail="请至少选择一条采购付款申请")
    request_ids = [item.purchase_payment_request_id for item in payload.items]
    if len(set(request_ids)) != len(request_ids):
        raise HTTPException(status_code=409, detail="合并登记中存在重复付款申请")
    role_code = current_user.role.code if current_user.role else ""
    if role_code not in {"finance", "finance_super", "system_admin"}:
        raise HTTPException(status_code=403, detail="当前账号无权登记采购出账")
    requests = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id.in_(request_ids)).order_by(PurchasePaymentRequest.id).with_for_update()
    ).scalars().all()
    request_map = {row.id: row for row in requests}
    if len(request_map) != len(request_ids):
        raise HTTPException(status_code=404, detail="存在不存在的付款申请")
    orders = [_load_order(db, request_map[request_id].purchase_order_id) for request_id in request_ids]
    if any(order is None for order in orders):
        raise HTTPException(status_code=404, detail="存在不存在的采购订单")
    for request in requests:
        # 指定财务校验:普通财务只能处理分配给自己的;超级财务可接管任何未处理付款
        if (
            request.assigned_finance_user_id
            and request.assigned_finance_user_id != current_user.id
            and role_code not in {"finance_super", "system_admin"}
        ):
            assigned_user = db.execute(select(User).where(User.id == request.assigned_finance_user_id)).scalar_one_or_none()
            assigned_name = assigned_user.full_name if assigned_user else f"#{request.assigned_finance_user_id}"
            raise HTTPException(status_code=403, detail=f"该付款已指定由 {assigned_name} 处理,不能越权登记")
        if request.status != "pending_finance_payment":
            raise HTTPException(status_code=409, detail=f"付款申请 {request.request_no} 状态不允许登记出账")

    now = now_local()
    amount_map = {item.purchase_payment_request_id: Decimal(item.actual_amount or 0) for item in payload.items}
    payable_ids: list[int] = []
    rejected_ids: list[int] = []
    processed_ids: list[int] = []
    actual_total = Decimal("0")

    for request_id in request_ids:
        request = request_map[request_id]
        order = _load_order(db, request.purchase_order_id)
        actual_amount = amount_map[request_id]
        if actual_amount <= 0:
            raise HTTPException(status_code=409, detail="实际出账金额必须大于 0")
        if not getattr(payload, "bank_receipt_attachment_id", None):
            raise HTTPException(status_code=409, detail="请先上传银行回单或出账凭证")
        request.actual_amount = actual_amount
        request.manager_remark = payload.remark or request.manager_remark
        request.payment_method = getattr(payload, "payment_method", None) or request.payment_method
        request.bank_receipt_attachment_id = getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id
        request.paid_at = getattr(payload, "paid_at", None) or now
        if request.approved_by_id is None:
            request.approved_by_id = current_user.id
        if request.approved_at is None:
            request.approved_at = now
        complete_todos(db, business_type="purchase_payment_request", business_id=request.id)

        if actual_amount != Decimal(request.requested_amount or 0):
            request.status = "rejected"
            request.manager_remark = payload.remark or "出账金额与申请金额不一致，已退回采购重填"
            rejected_ids.append(request.id)
            create_notification(
                db,
                title="采购付款申请金额不一致",
                content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 出账金额与申请金额不一致，已退回采购。",
                module="purchase",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
            continue

        request.status = "pending_completion"
        paid_order_amount = _purchase_paid_order_amount(db, order.id) + Decimal(request.order_amount or 0)
        payable_status = "settled" if paid_order_amount >= Decimal(order.total_amount or 0) else "partial_settled"
        accounts = validate_settlement_accounts(db, payload.company_account_id, requested_account_id(payload.counterparty_account_id, request.counterparty_account_id, "供应商收款账户"), supplier_id=order.supplier_id)
        request.company_account_id = accounts["company_account_id"]
        request.counterparty_account_id = accounts["counterparty_account_id"]
        payable_record = PayableRecord(
            **accounts,
            purchase_order_id=order.id,
            purchase_payment_request_id=request.id,
            merge_no=request.merge_no,
            amount=actual_amount,
            order_amount=request.order_amount,
            other_fee_amount=request.other_fee_amount,
            paid_at=getattr(payload, "paid_at", None) or now,
            paid_by_id=current_user.id,
            status=payable_status,
            remark=payload.remark or request.payment_detail_remark or f"采购付款申请 {request.request_no}",
            payment_method=getattr(payload, "payment_method", None) or request.payment_method,
            bank_receipt_attachment_id=getattr(payload, "bank_receipt_attachment_id", None) or request.bank_receipt_attachment_id,
        )
        db.add(payable_record)
        db.flush()
        payable_ids.append(payable_record.id)
        processed_ids.append(request.id)
        actual_total += actual_amount
        create_cash_flow(
            db,
            flow_type="outgoing",
            business_type="purchase_order",
            business_id=order.id,
            related_no=order.order_no,
            amount=actual_amount,
            happened_at=getattr(payload, "paid_at", None) or now,
            remark=payload.remark or request.payment_detail_remark or f"采购付款申请 {request.request_no}",
        )
        create_notification(
            db,
            title="采购出账已登记",
            content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已登记出账，请采购确认完成。",
            module="purchase",
            recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_chairman_user_ids(db)])),
            created_by_id=current_user.id,
            category="success",
        )

    write_operation_log(
        db,
        user_id=current_user.id,
        module="finance",
        action="register_purchase_payment_requests_batch",
        content="批量登记采购付款申请出账",
        after_data={
            "request_ids": request_ids,
            "processed_ids": processed_ids,
            "rejected_ids": rejected_ids,
            "payable_ids": payable_ids,
            "actual_total": float(actual_total),
        },
    )
    db.commit()
    return api_response(
        data={
            "processed_ids": processed_ids,
            "rejected_ids": rejected_ids,
            "payable_ids": payable_ids,
            "actual_total": float(actual_total),
        }
    )


def complete_purchase_payment_request(request_id: int, payload: PurchasePaymentCompleteRequest, current_user: User, db: Session):
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if request.status != "pending_completion":
        raise HTTPException(status_code=409, detail="当前付款申请还不能完成")

    request.status = "completed"
    request.completed_by_id = current_user.id
    request.completed_at = now_local()
    request.completion_remark = payload.completion_remark
    create_notification(
        db,
        title="采购付款流程已完成",
        content=f"采购订单 {order.order_no} 的付款申请 {request.request_no} 已由采购确认完成。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_manager_user_ids(db), *_finance_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="complete_purchase_payment_request",
        content=f"完成采购订单 {order.order_no} 的付款申请 {request.request_no}",
        after_data={
            "request_no": request.request_no,
            "status": request.status,
            "completion_remark": request.completion_remark,
        },
    )
    db.commit()
    return api_response(data={"id": request.id, "status": request.status})


def update_purchase_payment_invoice(
    request_id: int,
    payload: PurchasePaymentInvoiceUpdate,
    current_user: User,
    db: Session,
):
    """采购付款单补充发票信息（发票号/金额/日期/附件）。"""
    request = db.execute(
        select(PurchasePaymentRequest).where(PurchasePaymentRequest.id == request_id)
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="采购付款申请不存在")
    order = _load_order(db, request.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")

    changed: list[str] = []
    if payload.invoice_no is not None and payload.invoice_no != request.invoice_no:
        request.invoice_no = payload.invoice_no
        changed.append("发票号")
    if payload.invoice_amount is not None and payload.invoice_amount != request.invoice_amount:
        request.invoice_amount = payload.invoice_amount
        changed.append("发票金额")
    if payload.invoice_date is not None and payload.invoice_date != request.invoice_date:
        request.invoice_date = payload.invoice_date
        changed.append("发票日期")
    if payload.invoice_attachment_id is not None and payload.invoice_attachment_id != request.invoice_attachment_id:
        request.invoice_attachment_id = payload.invoice_attachment_id
        changed.append("发票附件")

    if changed:
        write_operation_log(
            db,
            user_id=current_user.id,
            module="purchase",
            action="update_payment_invoice",
            content=f"采购订单 {order.order_no} 付款申请 {request.request_no} 补充发票信息",
            after_data={
                "request_no": request.request_no,
                "invoice_no": request.invoice_no,
                "invoice_amount": float(request.invoice_amount) if request.invoice_amount is not None else None,
                "invoice_date": request.invoice_date.isoformat() if request.invoice_date else None,
                "changed": changed,
            },
        )
        db.commit()
    return api_response(
        data={
            "id": request.id,
            "invoice_no": request.invoice_no,
            "invoice_amount": float(request.invoice_amount) if request.invoice_amount is not None else None,
            "invoice_date": request.invoice_date.isoformat() if request.invoice_date else None,
            "invoice_attachment_id": request.invoice_attachment_id,
        }
    )


def build_purchase_settlement_snapshot(current_user: User, db: Session, *, scoped_user_ids: list[int] | None = None):
    query = (
        select(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.supplier),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
        )
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
    )
    if scoped_user_ids is not None:
        query = query.where(PurchaseOrder.purchaser_user_id.in_(scoped_user_ids or [-1]))
    orders = db.execute(query).unique().scalars().all()
    order_ids = [row.id for row in orders]

    payment_requests = (
        db.execute(
            select(PurchasePaymentRequest)
            .where(PurchasePaymentRequest.purchase_order_id.in_(order_ids))
            .order_by(PurchasePaymentRequest.created_at.desc(), PurchasePaymentRequest.id.desc())
        ).scalars().all()
        if order_ids
        else []
    )
    payables = (
        db.execute(
            select(PayableRecord)
            .where(PayableRecord.purchase_order_id.in_(order_ids))
            .order_by(PayableRecord.paid_at.desc(), PayableRecord.id.desc())
        ).scalars().all()
        if order_ids
        else []
    )

    paid_amount_map: dict[int, Decimal] = {}
    paid_order_amount_map: dict[int, Decimal] = {}
    for row in payables:
        if row.status not in {"paid", "partial_settled", "settled"}:
            continue
        paid_amount_map[row.purchase_order_id] = paid_amount_map.get(row.purchase_order_id, Decimal("0")) + Decimal(row.amount or 0)
        paid_order_amount_map[row.purchase_order_id] = paid_order_amount_map.get(row.purchase_order_id, Decimal("0")) + Decimal(row.order_amount or 0)

    request_user_ids = {
        user_id
        for row in payment_requests
        for user_id in [row.requested_by_id, row.approved_by_id, row.completed_by_id]
        if user_id
    }
    user_map = {
        row.id: row
        for row in db.execute(select(User).where(User.id.in_(list(request_user_ids)))).scalars().all()
    } if request_user_ids else {}

    order_rows = []
    completed_rows = []
    arrival_status_map: dict[int, str] = {}
    arrival_label_map: dict[int, str] = {}
    for row in orders:
        total_amount = Decimal(row.total_amount or 0)
        order_amount_paid = paid_order_amount_map.get(row.id, Decimal("0"))
        cash_paid = paid_amount_map.get(row.id, Decimal("0"))
        settlement_status = "unpaid"
        if order_amount_paid > 0:
            settlement_status = "settled" if order_amount_paid >= total_amount else "partial_settled"
        arrival_status = purchase_arrival_status(db, row.id)
        arrival_label = _purchase_arrival_display_label(row, arrival_status)
        arrival_status_map[row.id] = arrival_status
        arrival_label_map[row.id] = arrival_label
        item_summary = "；".join(
            f"{item.product.name if item.product else (item.material.name if item.material else '-') } × {float(item.quantity or 0)}"
            for item in row.items[:3]
        )
        contract_attachments = _purchase_contract_attachments(db, row.id)
        is_completed_order = _purchase_order_business_completed(db, row, settlement_status=settlement_status)
        serialized = {
            "id": row.id,
            "order_no": row.order_no,
            "supplier_id": row.supplier_id,
            "supplier_name": row.supplier.name if row.supplier else None,
            "status": row.status,
            "status_label": status_label(row.status),
            "payment_type": row.payment_type,
            "payment_type_label": payment_type_label(row.payment_type, module="purchase"),
            "arrival_status": arrival_status,
            "arrival_status_label": arrival_label,
            "total_amount": float(total_amount),
            "paid_amount": float(cash_paid),
            "paid_order_amount": float(order_amount_paid),
            "remaining_amount": float(max(total_amount - order_amount_paid, Decimal("0"))),
            "fund_status": settlement_status,
            "fund_status_label": status_label(settlement_status),
            "item_summary": item_summary,
            "items": [
                {
                    "item_name": item.product.name if item.product else (item.material.name if item.material else "-"),
                    "item_code": item.product.product_code if item.product else (item.material.material_code if item.material else "-"),
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "total_amount": float(item.total_amount or 0),
                    "received_quantity": float(item.received_quantity or 0),
                    "display_specification": item.display_specification,
                }
                for item in row.items
            ],
            "contract_attachment_count": len(contract_attachments),
            "contract_attachment_names": [item["original_name"] for item in contract_attachments],
            "flow_steps": purchase_flow_steps(row, arrival_status=arrival_status, settlement_status=settlement_status),
            "can_submit_payment_request": row.status != "cancelled",
            "is_completed_order": is_completed_order,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if serialized["is_completed_order"]:
            completed_rows.append(serialized)
        else:
            order_rows.append(serialized)

    request_rows = [
        {
            "id": row.id,
            "request_no": row.request_no,
            **settlement_account_fields(row),
            "merge_no": row.merge_no,
            "purchase_order_id": row.purchase_order_id,
            "purchase_order_no": row.purchase_order.order_no if row.purchase_order else None,
            "requested_amount": float(row.requested_amount or 0),
            "order_amount": float(row.order_amount or 0),
            "other_fee_amount": float(row.other_fee_amount or 0),
            "actual_amount": float(row.actual_amount or 0),
            "arrival_status": arrival_status_map.get(row.purchase_order_id, row.arrival_status),
            "arrival_status_label": arrival_label_map.get(
                row.purchase_order_id,
                "已到货" if row.arrival_status == "arrived" else "未到货",
            ),
            "status": row.status,
            "status_label": status_label(row.status),
            "payment_detail_remark": row.payment_detail_remark,
            "manager_remark": row.manager_remark,
            "completion_remark": row.completion_remark,
            "requested_by_name": user_map[row.requested_by_id].full_name if row.requested_by_id in user_map else None,
            "approved_by_name": user_map[row.approved_by_id].full_name if row.approved_by_id in user_map else None,
            "completed_by_name": user_map[row.completed_by_id].full_name if row.completed_by_id in user_map else None,
            "contract_attachment_count": len(_purchase_contract_attachments(db, row.purchase_order_id)),
            "invoice_no": row.invoice_no,
            "invoice_amount": float(row.invoice_amount) if row.invoice_amount is not None else None,
            "invoice_date": row.invoice_date.isoformat() if row.invoice_date else None,
            "invoice_attachment_id": row.invoice_attachment_id,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in payment_requests
    ]
    return {
        "orders": order_rows,
        "completed_orders": completed_rows,
        "payment_requests": request_rows,
        "payables": payables,
    }


def approve_purchase_fund(payable_id: int, payload: PurchaseFundApprovalRequest, current_user: User, db: Session):
    record = db.execute(select(PayableRecord).where(PayableRecord.id == payable_id)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="采购拨款申请不存在")
    order = _load_order(db, record.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")

    role_code = current_user.role.code if current_user.role else ""
    note = (payload.note or "").strip() or None

    if record.status == "pending_manager_approval":
        if role_code not in {"manager", "system_admin"}:
            raise HTTPException(status_code=403, detail="当前仅经理可以处理该拨款申请")
        if payload.decision == "rejected":
            record.status = "rejected"
            record.remark = note or "经理驳回采购拨款申请"
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            create_notification(
                db,
                title="采购拨款申请已驳回",
                content=f"采购订单 {order.order_no} 的拨款申请已被经理驳回。",
                module="purchase",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
        else:
            if payload.amount is not None:
                record.amount = payload.amount
            record.status = "pending_finance_approval"
            record.remark = note or record.remark
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            finance_user_ids = _finance_user_ids(db)
            for user_id in finance_user_ids:
                create_todo(
                    db,
                    title=f"待财务审批采购拨款 {order.order_no}",
                    description=f"经理已通过采购拨款申请，请确认拨款金额 {float(record.amount)}。",
                    module="finance",
                    assigned_user_id=user_id,
                    business_type="purchase_fund_request",
                    business_id=record.id,
                    priority="high",
                )
            create_notification(
                db,
                title="采购拨款待财务审批",
                content=f"采购订单 {order.order_no} 已通过经理审批，待财务确认拨款 {float(record.amount)}。",
                module="finance",
                recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *finance_user_ids])),
                created_by_id=current_user.id,
                category="info",
            )
    elif record.status == "pending_finance_approval":
        if role_code not in {"finance", "system_admin"}:
            raise HTTPException(status_code=403, detail="当前仅财务可以完成拨款审批")
        if payload.decision == "rejected":
            record.status = "rejected"
            record.remark = note or "财务驳回采购拨款申请"
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            create_notification(
                db,
                title="采购拨款申请已驳回",
                content=f"采购订单 {order.order_no} 的拨款申请已被财务驳回。",
                module="finance",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="warning",
            )
        else:
            approved_amount = Decimal(payload.amount if payload.amount is not None else record.amount or 0)
            if approved_amount <= 0:
                raise HTTPException(status_code=409, detail="拨款金额必须大于 0")
            record.amount = approved_amount
            record.status = "paid"
            record.paid_by_id = current_user.id
            record.paid_at = now_local()
            record.remark = note or record.remark
            create_cash_flow(
                db,
                flow_type="outgoing",
                business_type="purchase_order",
                business_id=order.id,
                related_no=order.order_no,
                amount=approved_amount,
                happened_at=record.paid_at,
                remark=record.remark,
            )
            complete_todos(db, business_type="purchase_fund_request", business_id=record.id)
            create_notification(
                db,
                title="采购拨款已到账",
                content=f"采购订单 {order.order_no} 已完成拨款 {float(approved_amount)}，采购可继续预算协同与结算。",
                module="finance",
                recipient_user_ids=[order.purchaser_user_id] if order.purchaser_user_id else [],
                created_by_id=current_user.id,
                category="success",
            )
    else:
        raise HTTPException(status_code=409, detail="当前拨款申请状态不允许审批")

    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="approve_purchase_fund",
        content=f"审批采购订单 {order.order_no} 的拨款申请",
        after_data={"payable_id": record.id, "status": record.status, "amount": float(record.amount or 0), "decision": payload.decision},
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})


def settle_purchase_fund(payable_id: int, current_user: User, db: Session):
    record = db.execute(select(PayableRecord).where(PayableRecord.id == payable_id)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="采购拨款记录不存在")
    order = _load_order(db, record.purchase_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="采购订单不存在")
    _ensure_order_owner(order, current_user)
    if record.status != "paid":
        raise HTTPException(status_code=409, detail="当前拨款记录未到账，不能标记结算完成")

    record.status = "settled"
    create_notification(
        db,
        title="采购结算已完成",
        content=f"采购订单 {order.order_no} 已由采购标记为结算完成。",
        module="purchase",
        recipient_user_ids=list(dict.fromkeys([order.purchaser_user_id, *_finance_user_ids(db), *_manager_user_ids(db)])),
        created_by_id=current_user.id,
        category="success",
    )
    write_operation_log(
        db,
        user_id=current_user.id,
        module="purchase",
        action="settle_purchase_fund",
        content=f"完成采购订单 {order.order_no} 的采购结算",
        after_data={"payable_id": record.id, "status": record.status},
    )
    db.commit()
    return api_response(data={"id": record.id, "status": record.status})


