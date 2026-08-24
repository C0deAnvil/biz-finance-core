from __future__ import annotations

import re
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    Customer,
    Material,
    PackagingConfig,
    PayableRecord,
    Product,
    PurchaseOrder,
    PurchaseReceipt,
    PurchaseReturn,
    QualityCostRecord,
    ReceivableRecord,
    RefundRecord,
    SalesOrder,
    SalesReturn,
    StockTakeTask,
    Supplier,
)


STATUS_LABELS = {
    "pending_material_outbound": "待领料出库",
    "draft": "草稿",
    "pending": "待处理",
    "unrequested": "未申请拨款",
    "pending_confirmation": "待确认",
    "pending_price_approval": "待财务改价审批",
    "pending_approval": "待审批",
    "pending_purchase_confirm": "待采购确认",
    "pending_shipment": "待发货",
    "pending_material": "待领料",
    "pending_material_approval": "待领料审批",
    "pending_special_approval": "待特殊领用审批",
    "pending_disposition": "待异常处理",
    "pending_manager_approval": "待经理审批",
    "pending_finance_approval": "待财务审批",
    "pending_chairman_approval": "待董事长确认",
    "pending_finance_check": "待财务核对",
    "pending_finance_payment": "待财务出账",
    "pending_completion": "待业务确认完成",
    "pending_inbound": "待库管入库",
    "receipt_abnormal": "收款异常",
    "payment_pending": "待付款申请",
    "receipt_pending": "待收款确认",
    "payment_requested": "已提交付款申请",
    "receipt_requested": "已提交收款确认",
    "partially_completed": "部分完成",
    "not_arrived": "未到货",
    "arrived": "已到货",
    "planned": "已计划",
    "in_progress": "进行中",
    "partial_received_payment": "部分付款",
    "partially_received": "部分收款",
    "partially_paid": "部分付款",
    "partially_paid_received": "部分到货待结算",
    "request_cancelled": "已取消申请",
    "request_completed": "已执行完成",
    "processing": "处理中",
    "issued": "已出库",
    "inbound_completed": "已入库",
    "outbound_completed": "已出库",
    "trade": "贸易型",
    "production": "生产型",
    "unpaid": "待付款",
    "partial_settled": "部分结算",
    "purchasing": "采购中",
    "partial_received": "部分到货",
    "fully_received": "全部到货",
    "received": "已到货",
    "inspecting": "质检中",
    "waiting_quality": "待质检",
    "ready_for_inbound": "待准许入库",
    "inbound_pending": "待入库审批",
    "submitted": "已提交",
    "approved": "已通过",
    "recognized": "已确认",
    "completed": "已完成",
    "material_issued": "已领料",
    "rejected": "已驳回",
    "cancelled": "已取消",
    "shipped": "已发货",
    "paid": "已收款",
    "refunded": "已退款",
    "returned": "已退货",
    "settled": "已结算",
    "rework_created": "已发起返工",
    "rework_pending": "待返工",
    "reworked": "返工完成",
    "scrapped": "已报废",
    "partially_scrapped": "部分报废",
    "placed": "已置货",
    "hidden": "已隐藏",
    "waiting_approval": "待审批",
    "not_required": "无需审批",
    "enabled": "启用",
    "disabled": "停用",
}

PAYMENT_TYPE_LABELS = {
    "advance": "先付定金后付尾款",
    "cash": "全款发货",
    "full_payment": "全款发货",
    "installment": "货到付款/分期付款",
    "bank_transfer": "银行转账",
    "deposit": "先付定金后付尾款",
    "deposit_then_balance": "先付定金后付尾款",
    "prepayment": "预付款",
    "先付定金后付尾款": "先付定金后付尾款",
    "全款发货": "全款发货",
    "货到付款/分期付款": "货到付款/分期付款",
    "银行转账": "银行转账",
    "预付款": "预付款",
    "先到货后付款": "先到货后付款",
    "先付款后到货": "先付款后到货",
    "arrival_then_payment": "先到货后付款",
    "payment_then_arrival": "先付款后到货",
    "先到货后付款": "先到货后付款",
    "先付款后到货": "先付款后到货",
}

RETURN_TYPE_LABELS = {
    "return": "退货",
    "exchange": "换货",
}

LOG_ACTION_LABELS = {
    "create_order": "创建销售订单",
    "update_order": "更新销售订单",
    "delete_order": "删除销售订单",
    "approve_order": "审批销售订单",
    "complete_order": "完成销售订单",
    "create_return": "创建销售退换货",
    "approve_return": "审批销售退换货",
    "create_purchase_order": "创建采购订单",
    "update_purchase_order": "更新采购订单",
    "cancel_purchase_order": "取消采购订单",
    "approve_purchase_order": "审批采购订单",
    "confirm_purchase_order": "确认采购订单",
    "create_purchase_return": "创建采购退货",
    "submit_special_request": "提交特殊领用申请",
    "submit_material_request": "提交领料申请",
    "approve_special_request": "审批特殊领用申请",
    "request_purchase_fund": "申请采购拨款",
    "approve_purchase_fund": "审批采购拨款",
    "settle_purchase_fund": "完成采购结算",
    "collect_order_payment": "登记客户回款",
    "submit_purchase_payment_request": "提交采购付款申请",
    "approve_purchase_payment_request": "审批采购付款申请",
    "complete_purchase_payment_request": "完成采购付款结算",
    "submit_sales_receipt_request": "提交销售收款确认",
    "approve_sales_receipt_request": "确认销售收款",
    "create_inventory_operation_request": "创建库存作业请求",
    "create_fulfillment_demand": "创建履约补货需求",
    "complete_sales_order": "完成客户订单",
    "complete_inventory_operation": "完成库存作业",
    "create_production_plan": "创建生产计划",
    "update_production_plan": "更新生产计划",
    "cancel_fulfillment_demand": "取消补货需求",
    "complete_task": "完成生产任务",
    "cancel_production_task": "取消生产任务",
    "create_rework_task": "创建返工任务",
    "create_scrap_record": "登记报废",
    "save_stock_take_items": "保存盘点明细",
    "approve_stock_take": "完成盘点入账",
    "recognize_quality_cost": "确认质量成本",
    "allow_quality_inbound": "质检准许入库",
    "create_inbound_application": "提交质检入库申请",
    "approve_inbound_application": "审批质检入库申请",
    "complete_production_inbound": "完成生产入库",
    "create_task": "创建质检任务",
}

TRANSACTION_TYPE_LABELS = {
    "purchase_inbound": "采购入库",
    "purchase_return_outbound": "采购退货出库",
    "sales_outbound": "销售出库",
    "sales_return_inbound": "销售退货入库",
    "production_material_outbound": "生产领料出库",
    "production_material_return_inbound": "生产退料入库",
    "quality_inbound": "质检入库",
    "manual_inbound": "手工入库",
    "manual_outbound": "手工出库",
    "stock_take_adjustment": "盘点调整",
}

PAYMENT_STAGE_LABELS = {
    "deposit": "定金",
    "balance": "尾款",
    "cod": "到货后全款",
    "cod_full": "到货后全款",
    "full": "发货前全款",
    "pre_shipment_full": "发货前全款",
    "installment": "分期款",
    "mixed": "合并收款",
    "direct": "直接登记",
}

INVENTORY_REQUEST_CATEGORY_LABELS = {
    "manual_inbound_request": "手工入库",
    "manual_outbound_request": "手工出库",
    "production_material_issue": "生产领料出库",
    "production_finished_inbound": "生产完工入库申请",
    "production_quality_inbound": "生产质检通过入库申请",
    "purchase_receipt_inbound": "采购到货入库申请",
    "purchase_quality_inbound": "采购质检通过入库申请",
    "purchase_return_outbound": "退货处理出库",
    "sales_shipment": "客户订单发货出库",
    "sales_return_inbound": "销售退货回库",
}

_ROUND_REMARK_PATTERN = re.compile(r"round(?P<round>\d+)-(receive|pay|refund)-", re.IGNORECASE)
_ASCII_MACHINE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")
_HANGUL_PATTERN = re.compile(r"[\u3130-\u318F\uAC00-\uD7AF]")
_COMMON_MOJIBAKE = ("閿", "璐", "搴", "鐢", "鍙", "寰", "宸", "鏃", "鍒", "鍏", "閲", "缁", "璇", "鎿", "妯", "瀹")


def status_label(status: str | None) -> str:
    value = str(status or "").strip()
    return STATUS_LABELS.get(value, value or "-")


def payment_type_label(
    payment_type: str | None,
    *,
    deposit_amount: Decimal | float | int | None = None,
    module: str | None = None,
) -> str:
    value = str(payment_type or "").strip()
    normalized = value.lower()
    if value in PAYMENT_TYPE_LABELS:
        return PAYMENT_TYPE_LABELS[value]
    if normalized in PAYMENT_TYPE_LABELS:
        return PAYMENT_TYPE_LABELS[normalized]
    if "install" in normalized:
        return PAYMENT_TYPE_LABELS["installment"]
    if "cash" in normalized:
        return PAYMENT_TYPE_LABELS["cash"]
    if "bank" in normalized:
        return PAYMENT_TYPE_LABELS["bank_transfer"]
    if _needs_text_repair(value):
        deposit_value = Decimal(str(deposit_amount or 0))
        if module == "sales":
            return "先付定金后付尾款" if deposit_value > 0 else "全款发货"
        return "银行转账"
    return value or "-"


def return_type_label(return_type: str | None) -> str:
    value = str(return_type or "").strip()
    return RETURN_TYPE_LABELS.get(value, value or "退货")


def action_label(action: str | None) -> str:
    value = str(action or "").strip()
    return LOG_ACTION_LABELS.get(value, value or "-")


def transaction_type_label(transaction_type: str | None) -> str:
    value = str(transaction_type or "").strip()
    return TRANSACTION_TYPE_LABELS.get(value, value or "-")


def payment_stage_label(payment_stage: str | None) -> str:
    value = str(payment_stage or "").strip()
    return PAYMENT_STAGE_LABELS.get(value, value or "-")


def inventory_request_category_label(category: str | None) -> str:
    value = str(category or "").strip()
    return INVENTORY_REQUEST_CATEGORY_LABELS.get(value, value or "-")


def packaging_spec_text(kg_per_bag: Decimal | float | int | None) -> str:
    amount = Decimal(str(kg_per_bag or 0)).quantize(Decimal("0.01")).normalize()
    return f"{format(amount, 'f')} kg/袋"


_PACKAGING_SPEC_NAME_PATTERN = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:kg|KG|公斤|千克)\s*/?\s*(?:袋|包|桶)?\s*$"
)


def packaging_name_needs_repair(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return (
        _needs_text_repair(text)
        or text in {"标准袋装", "默认包装"}
        or bool(_PACKAGING_SPEC_NAME_PATTERN.match(text))
    )


def calculate_bag_count(quantity: Decimal | float | int | None, kg_per_bag: Decimal | float | int | None) -> Decimal | None:
    quantity_value = Decimal(str(quantity or 0))
    unit_value = Decimal(str(kg_per_bag or 0))
    if quantity_value <= 0 or unit_value <= 0:
        return None
    return (quantity_value / unit_value).to_integral_value(rounding=ROUND_CEILING)


def get_default_packaging_config(db: Session, *, entity_type: str, entity_id: int) -> PackagingConfig | None:
    return db.execute(
        select(PackagingConfig)
        .where(
            PackagingConfig.entity_type == entity_type,
            PackagingConfig.entity_id == entity_id,
            PackagingConfig.is_active.is_(True),
        )
        .order_by(PackagingConfig.is_default.desc(), PackagingConfig.id.asc())
    ).scalars().first()


def get_global_packaging_name(db: Session, kg_per_bag: Decimal | float | int | None) -> str | None:
    amount = Decimal(str(kg_per_bag or 0))
    row = db.execute(
        select(PackagingConfig)
        .where(
            PackagingConfig.entity_type == "global",
            PackagingConfig.entity_id == 0,
            PackagingConfig.is_active.is_(True),
            PackagingConfig.kg_per_bag == amount,
        )
        .order_by(PackagingConfig.is_default.desc(), PackagingConfig.id.asc())
    ).scalars().first()
    if row is not None:
        return row.packaging_name
    return None


def upsert_default_packaging_config(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    packaging_name: str,
    kg_per_bag: Decimal | float | int,
    unit_price: Decimal | float | int | None = None,
    is_active: bool = True,
) -> PackagingConfig:
    rows = db.execute(
        select(PackagingConfig).where(
            PackagingConfig.entity_type == entity_type,
            PackagingConfig.entity_id == entity_id,
        )
    ).scalars().all()
    current = rows[0] if rows else None
    if current is None:
        current = PackagingConfig(
            entity_type=entity_type,
            entity_id=entity_id,
            packaging_name=packaging_name,
            kg_per_bag=kg_per_bag,
            unit_price=unit_price if unit_price is not None else 0,
            is_default=True,
            is_active=is_active,
        )
        db.add(current)
        db.flush()
    else:
        current.packaging_name = packaging_name
        current.kg_per_bag = kg_per_bag
        if unit_price is not None:
            current.unit_price = unit_price
        current.is_default = True
        current.is_active = is_active
        for row in rows[1:]:
            row.is_default = False
    return current


def clean_display_text(value: str | None, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text or _needs_text_repair(text):
        return fallback
    return text


def format_legacy_remark(remark: str | None, *, business_type: str | None = None) -> str:
    value = str(remark or "").strip()
    if not value:
        return ""
    match = _ROUND_REMARK_PATTERN.search(value)
    if match:
        round_index = match.group("round")
        if "receive" in value:
            return f"第 {round_index} 期回款"
        if "pay" in value:
            return f"第 {round_index} 期采购付款"
        if "refund" in value:
            return f"第 {round_index} 期退款"
    if value.startswith("finance-recognize"):
        return "财务确认质量成本"
    if value.startswith("finance-scrap"):
        return "财务确认报废成本"
    if value == "finance":
        return "财务处理"
    if _needs_text_repair(value):
        if business_type == "receivable":
            return "财务登记回款"
        if business_type == "payable":
            return "财务登记付款"
        if business_type == "purchase_return":
            return "采购退货退款"
        if business_type == "sales_return":
            return "销售退换货退款"
        if business_type == "quality_cost":
            return "质量成本处理"
        return "备注待补充"
    return value


def repair_legacy_demo_data(db: Session):
    _repair_customer_data(db)
    _repair_supplier_data(db)
    _repair_product_data(db)
    _repair_material_data(db)
    _repair_sales_orders(db)
    _repair_purchase_orders(db)
    _repair_sales_returns(db)
    _repair_purchase_returns(db)
    _repair_purchase_receipts(db)
    _repair_stock_take_tasks(db)
    _repair_finance_remarks(db)


def _needs_text_repair(value: str | None) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return True
    if "?" in text or "�" in text or "锟" in text:
        return True
    if bool(_HANGUL_PATTERN.search(text)):
        return True
    if _ASCII_MACHINE_PATTERN.match(text) and not any("\u4e00" <= char <= "\u9fff" for char in text):
        return True
    return sum(text.count(fragment) for fragment in _COMMON_MOJIBAKE) >= 2


def _needs_catalog_name_repair(value: str | None) -> bool:
    text = str(value or "").strip()
    return not text or _needs_text_repair(text)


def _suffix(code: str | None) -> str:
    text = str(code or "")
    return text[-6:] if len(text) >= 6 else text


def _repair_customer_data(db: Session):
    for row in db.execute(select(Customer)).scalars().all():
        if _needs_text_repair(row.name):
            row.name = f"客户-{_suffix(row.code)}"
        if _needs_text_repair(row.contact_name):
            row.contact_name = "默认联系人"
        if _needs_text_repair(row.address):
            row.address = "地址待补充"


def _repair_supplier_data(db: Session):
    for row in db.execute(select(Supplier)).scalars().all():
        if _needs_text_repair(row.name):
            row.name = f"供应商-{_suffix(row.code)}"
        if _needs_text_repair(row.contact_name):
            row.contact_name = "默认联系人"
        if _needs_text_repair(row.address):
            row.address = "地址待补充"


def _repair_product_data(db: Session):
    for row in db.execute(select(Product)).scalars().all():
        if _needs_catalog_name_repair(row.name):
            row.name = f"产品-{_suffix(row.product_code)}"
        row.unit = "kg"
        config = get_default_packaging_config(db, entity_type="product", entity_id=row.id)
        if config is None:
            packaging_name = get_global_packaging_name(db, Decimal("25")) or "25kg编织袋"
            config = upsert_default_packaging_config(
                db,
                entity_type="product",
                entity_id=row.id,
                packaging_name=packaging_name,
                kg_per_bag=Decimal("25"),
            )
        elif packaging_name_needs_repair(config.packaging_name):
            config.packaging_name = get_global_packaging_name(db, config.kg_per_bag) or config.packaging_name
        row.specification = packaging_spec_text(config.kg_per_bag)


def _repair_material_data(db: Session):
    for row in db.execute(select(Material)).scalars().all():
        if _needs_catalog_name_repair(row.name):
            row.name = f"原料-{_suffix(row.material_code)}"
        row.unit = "kg"
        config = get_default_packaging_config(db, entity_type="material", entity_id=row.id)
        if config is None:
            packaging_name = get_global_packaging_name(db, Decimal("25")) or "25kg编织袋"
            config = upsert_default_packaging_config(
                db,
                entity_type="material",
                entity_id=row.id,
                packaging_name=packaging_name,
                kg_per_bag=Decimal("25"),
            )
        elif packaging_name_needs_repair(config.packaging_name):
            config.packaging_name = get_global_packaging_name(db, config.kg_per_bag) or config.packaging_name
        row.specification = packaging_spec_text(config.kg_per_bag)


def _repair_sales_orders(db: Session):
    for row in db.execute(select(SalesOrder)).scalars().all():
        if _needs_text_repair(row.consignee):
            row.consignee = f"收货人-{_suffix(row.order_no)}"
        if _needs_text_repair(row.delivery_address):
            row.delivery_address = "地址待补充"
        row.payment_type = payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales")


def _repair_purchase_orders(db: Session):
    for row in db.execute(select(PurchaseOrder)).scalars().all():
        row.payment_type = payment_type_label(row.payment_type, module="purchase")


def _repair_sales_returns(db: Session):
    for row in db.execute(select(SalesReturn)).scalars().all():
        if _needs_text_repair(row.reason):
            row.reason = f"销售{return_type_label(row.return_type)}申请"
        if row.review_note and _needs_text_repair(row.review_note):
            row.review_note = "审批备注待补充"


def _repair_purchase_returns(db: Session):
    for row in db.execute(select(PurchaseReturn)).scalars().all():
        if _needs_text_repair(row.reason):
            row.reason = f"采购{return_type_label(row.return_type)}申请"


def _repair_purchase_receipts(db: Session):
    for row in db.execute(select(PurchaseReceipt)).scalars().all():
        if row.remark and _needs_text_repair(row.remark):
            row.remark = "采购到货入库"


def _repair_stock_take_tasks(db: Session):
    for row in db.execute(select(StockTakeTask)).scalars().all():
        if row.difference_note and _needs_text_repair(row.difference_note):
            row.difference_note = "库存盘点差异调整"


def _repair_finance_remarks(db: Session):
    for row in db.execute(select(ReceivableRecord)).scalars().all():
        row.remark = format_legacy_remark(row.remark, business_type="receivable")
    for row in db.execute(select(PayableRecord)).scalars().all():
        row.remark = format_legacy_remark(row.remark, business_type="payable")
    for row in db.execute(select(RefundRecord)).scalars().all():
        row.remark = format_legacy_remark(row.remark, business_type="sales_return" if row.sales_return_id else "purchase_return")
    for row in db.execute(select(QualityCostRecord)).scalars().all():
        row.remark = format_legacy_remark(row.remark, business_type="quality_cost")
