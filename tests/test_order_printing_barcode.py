"""置货单打印任务码，认证码不得出现在纸面。"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from services.order_printing import render_order_pdf


def sample_order():
    product = SimpleNamespace(product_code="CP-001", name="测试产品")
    item = SimpleNamespace(
        product=product, packaging_config=None, kg_per_bag=None, bag_count=None,
        display_specification="普通型", quantity=Decimal("10"), placed_quantity=Decimal("0"),
        unit_price=Decimal("3.000"), total_amount=Decimal("30.000"), remark="核对包装",
    )
    seller = SimpleNamespace(full_name="销售甲", username="sales01")
    return SimpleNamespace(
        id=123, order_no="XS-TEST-123", order_type="normal", created_at=None,
        expected_delivery_date=None, sales_user=seller, operator_user=seller,
        customer=None, receipt_account=None, payment_type="全款发货", deposit_amount=None,
        consignee="张三", consignee_phone="13900000000", delivery_address="测试地址",
        order_remark="请轻拿轻放", items=[item], total_amount=Decimal("30.000"),
    )


def test_placement_pdf_has_separate_full_placement_and_shipment_codes():
    with patch("services.order_printing.Code128", wraps=__import__("reportlab.graphics.barcode.code128", fromlist=["Code128"]).Code128) as barcode:
        pdf = render_order_pdf(
            sample_order(),
            kind="placement",
            placement_token="A" * 24,
            shipment_token="B" * 24,
            warehouse_name="沧州仓库",
        )
    assert pdf.startswith(b"%PDF")
    assert barcode.call_count == 2
    assert barcode.call_args_list[0].args[0] == f"YCP01{'A' * 24}"
    assert barcode.call_args_list[1].args[0] == f"YCS01{'B' * 24}"


def test_sales_pdf_does_not_show_placement_barcode():
    with patch("services.order_printing.Code128") as barcode:
        pdf = render_order_pdf(sample_order(), kind="sales")
    assert pdf.startswith(b"%PDF")
    barcode.assert_not_called()
