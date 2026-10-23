"""Generate synthetic PDFs for print-layout QA without real customer data."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Row

from services.order_printing import render_order_pdf


root = Path(__file__).resolve().parent.parent
target = root / "output" / "pdf"
target.mkdir(parents=True, exist_ok=True)
items = [
    Row(
        product=Row(name="碳酸氢钠", product_code="CP20260924001"),
        packaging_config=Row(packaging_name="编织袋"),
        kg_per_bag=Decimal("25"), bag_count=Decimal("80"),
        display_specification="工业级", quantity=Decimal("2000"),
        placed_quantity=Decimal("500"), unit_price=Decimal("2.5652"),
        total_amount=Decimal("5130.40"), remark="下雨时增加防潮外袋",
    ),
    Row(
        product=Row(name="普通型化妆品尿素", product_code="CP20260924002"),
        packaging_config=Row(packaging_name="纸塑复合袋"),
        kg_per_bag=Decimal("25"), bag_count=Decimal("12"),
        display_specification="25kg/袋", quantity=Decimal("300"),
        placed_quantity=Decimal("0"), unit_price=Decimal("3.2"),
        total_amount=Decimal("960"), remark="独立码放；如包装破损先登记异常",
    ),
]
order = Row(
    id=123,
    order_no="XS20260924001", order_type="urgent", created_at=datetime(2026, 9, 24, 10, 30),
    customer=Row(name="示例客户有限公司"), sales_user=Row(full_name="示例销售", username="sales"),
    operator_user=Row(full_name="示例经办人", username="operator"),
    receipt_account=Row(account_name="示例公司账户", bank_name="示例银行", account_number="1234567890"),
    payment_type="全款发货", deposit_amount=Decimal("0"), total_amount=Decimal("6090.40"),
    expected_delivery_date=date(2026, 9, 28), consignee="示例收货人", consignee_phone="13800000000",
    delivery_address="示例省示例市工业路 100 号", order_remark="下午送货，提前电话联系；仓库门口卸货。",
    items=items,
)
for kind in ("sales", "placement"):
    path = target / f"sample_{kind}_order.pdf"
    kwargs = {
        "placement_token": "A1B2C3D4E5F60718293A4B5C",
        "shipment_token": "F1E2D3C4B5A60718293F4E5D",
        "warehouse_name": "沧州仓库",
    } if kind == "placement" else {}
    path.write_bytes(render_order_pdf(order, kind=kind, **kwargs))
    print(path)
