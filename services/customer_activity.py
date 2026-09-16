"""客户活跃度维护：末次下单时间、流失复购判定、转交日期。

流失复购规则（《销帮帮标签表》口径）：
    客户下新单时，若距离上一笔订单已超过 365 天，且本单金额 > 100 元，
    则本单日期记为该客户的「流失复购日期」，同时自动打上 T 标签（出过问题/流失复购）。

字段由系统维护，不开放给前端修改：
    customers.last_order_at        末次下单时间
    customers.churn_repurchase_at  流失复购日期
    customers.last_transferred_at  最近一次转交日期
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Customer, CustomerTag, SalesOrder
from services.tag_catalog import customer_tag_dimension
from services.time_utils import now_local


# 判定阈值：集中在这里，口径要调整只改这两个常量
CHURN_GAP_DAYS = 365
CHURN_MIN_AMOUNT = Decimal("100")

# 流失复购命中后自动打的标签（T = 出过问题：有过退换货或流失复购的客户）
CHURN_REPURCHASE_TAG = "T"


def _ensure_tag(db: Session, customer_id: int, tag: str, created_by_id: int | None) -> bool:
    """给客户补一个标签，已存在则不动。返回是否新增。"""
    exists = db.execute(
        select(CustomerTag.id).where(CustomerTag.customer_id == customer_id, CustomerTag.tag == tag)
    ).first()
    if exists:
        return False
    db.add(
        CustomerTag(
            customer_id=customer_id,
            tag=tag,
            dimension=customer_tag_dimension(tag),
            created_by_id=created_by_id,
        )
    )
    return True


def register_customer_order(
    db: Session,
    *,
    customer: Customer,
    order_amount: Decimal | float | int | None,
    order_time=None,
    created_by_id: int | None = None,
) -> dict:
    """客户下单时调用：更新末次下单时间，并判定是否构成流失复购。

    调用方负责 commit —— 本函数只写入 session，方便和下单逻辑同一事务提交。

    返回 {"churn_repurchase": bool, "gap_days": int | None, "tagged": bool}
    """
    occurred_at = order_time or now_local()
    amount = Decimal(str(order_amount or 0))
    previous_at = customer.last_order_at

    is_churn_repurchase = False
    gap_days = None
    tagged = False

    if previous_at is not None:
        gap = occurred_at - previous_at
        gap_days = gap.days
        if gap > timedelta(days=CHURN_GAP_DAYS) and amount > CHURN_MIN_AMOUNT:
            is_churn_repurchase = True
            customer.churn_repurchase_at = occurred_at
            tagged = _ensure_tag(db, customer.id, CHURN_REPURCHASE_TAG, created_by_id)

    # 末次下单时间只向前推进，避免补录历史单把它拉回过去
    if previous_at is None or occurred_at > previous_at:
        customer.last_order_at = occurred_at

    return {"churn_repurchase": is_churn_repurchase, "gap_days": gap_days, "tagged": tagged}


def register_customer_transfer(db: Session, *, customer: Customer, transferred_at=None) -> None:
    """客户转交时调用：记录转交日期。调用方负责 commit。"""
    customer.last_transferred_at = transferred_at or now_local()


def recalculate_customer_activity(db: Session, customer_id: int, *, created_by_id: int | None = None) -> dict:
    """按历史订单重算某个客户的末次下单时间与流失复购日期。

    用于数据订正 / 历史回填，不依赖增量调用是否漏掉过。
    """
    customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none()
    if customer is None:
        return {"customer_id": customer_id, "found": False}

    rows = db.execute(
        select(SalesOrder.created_at, SalesOrder.total_amount)
        .where(SalesOrder.customer_id == customer_id, SalesOrder.status != "cancelled")
        .order_by(SalesOrder.created_at.asc())
    ).all()

    last_order_at = None
    churn_at = None
    previous_at = None
    for created_at, total_amount in rows:
        if created_at is None:
            continue
        amount = Decimal(str(total_amount or 0))
        if previous_at is not None:
            if created_at - previous_at > timedelta(days=CHURN_GAP_DAYS) and amount > CHURN_MIN_AMOUNT:
                churn_at = created_at
        previous_at = created_at
        last_order_at = created_at

    customer.last_order_at = last_order_at
    customer.churn_repurchase_at = churn_at
    if churn_at is not None:
        _ensure_tag(db, customer.id, CHURN_REPURCHASE_TAG, created_by_id)

    return {
        "customer_id": customer_id,
        "found": True,
        "order_count": len(rows),
        "last_order_at": last_order_at,
        "churn_repurchase_at": churn_at,
    }


def recalculate_all_customers(db: Session, *, created_by_id: int | None = None) -> dict:
    """全量重算。返回命中流失复购的客户数。"""
    customer_ids = db.execute(
        select(SalesOrder.customer_id).where(SalesOrder.status != "cancelled").group_by(SalesOrder.customer_id)
    ).scalars().all()
    churn_count = 0
    for customer_id in customer_ids:
        result = recalculate_customer_activity(db, customer_id, created_by_id=created_by_id)
        if result.get("churn_repurchase_at"):
            churn_count += 1
    return {"processed": len(customer_ids), "churn_repurchase": churn_count}


def backfill_transfer_dates(db: Session) -> int:
    """按 customer_transfer_records 回填转交日期。返回更新条数。"""
    from models import CustomerTransferRecord

    rows = db.execute(
        select(CustomerTransferRecord.customer_id, func.max(CustomerTransferRecord.transferred_at))
        .group_by(CustomerTransferRecord.customer_id)
    ).all()
    updated = 0
    for customer_id, transferred_at in rows:
        customer = db.execute(select(Customer).where(Customer.id == customer_id)).scalar_one_or_none()
        if customer is not None and transferred_at is not None:
            customer.last_transferred_at = transferred_at
            updated += 1
    return updated
