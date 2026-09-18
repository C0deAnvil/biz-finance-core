"""销售域服务。"""

from __future__ import annotations
from services.financial_accounts import validate_settlement_accounts

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
from services.core import apply_inventory_change, complete_todos, create_cash_flow, create_notification, create_todo, ensure_notification, next_business_no, resolve_assigned_finance_user_id, resolve_workflow_recipients, write_operation_log
from services.display import calculate_bag_count, get_default_packaging_config, packaging_spec_text, payment_type_label
from services.quality_workflow import create_return_reinspection_quality_task
from services.replenishment_service import create_sales_shortage_demand, ensure_fulfillment_demand_routing, material_expected_available_quantity
from services.request_trace import emit
from services.workflow import create_inventory_operation_request
from services.warehouse_lookup import get_finished_warehouse
from services.time_utils import now_local



from services.sales_service._common import _ensure_order_owner_or_admin, _finance_user_ids, _latest_sales_receipt_request, _load_order, _sales_received_amount

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
        **validate_settlement_accounts(db, payload.company_account_id, payload.counterparty_account_id, customer_id=order.customer_id),
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
        # 指定处理该收款申请的财务;不指定则按待办量轮询分配
        assigned_finance_user_id=resolve_assigned_finance_user_id(
            db, payload.assigned_finance_user_id
        ),
    )
    db.add(request)
    db.flush()

    # 指定了财务就派给他,否则回落到全体财务
    if request.assigned_finance_user_id:
        finance_user_ids = [request.assigned_finance_user_id]
    else:
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
    # 整批合并收款指派给同一个财务(在循环外校验一次,避免同批被拆给不同人)。
    # 改造前合并路径完全不写该字段,导致合并收款申请恒为"未分配"、广播给全体财务。
    assigned_finance_user_id = resolve_assigned_finance_user_id(db, payload.assigned_finance_user_id)
    if assigned_finance_user_id:
        finance_user_ids = [assigned_finance_user_id]
    else:
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
            **validate_settlement_accounts(db, payload.company_account_id, payload.counterparty_account_id, customer_id=order.customer_id),
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
            assigned_finance_user_id=assigned_finance_user_id,
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

