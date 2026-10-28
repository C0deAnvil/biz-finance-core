from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from api.routers.inventory.queues import shipment_blocked_conditions
from api.routers.warehouse_scanning import _placement_reason, _shipment_reason
from scripts.warehouse_scan_desktop import api_error_message


def test_deposit_balance_is_reported_before_shipment():
    order = SimpleNamespace(id=1, payment_type="advance", deposit_amount=Decimal("20"), total_amount=Decimal("100"))
    with patch("api.routers.inventory.queues._sales_received_amount", return_value=Decimal("20")):
        assert shipment_blocked_conditions(None, order) == [
            "尾款尚有 ¥80.0000 未收（订单 ¥100.0000，已收 ¥20.0000）"
        ]
    order.payment_type = "deposit"
    with patch("api.routers.inventory.queues._sales_received_amount", return_value=Decimal("20")):
        assert "尾款尚有" in shipment_blocked_conditions(None, order)[0]


def test_shipment_preflight_uses_placement_and_payment_not_unrelated_inbound():
    order = SimpleNamespace(payment_type="installment", deposit_amount=None, total_amount=Decimal("100"),
                            status="pending_shipment", finance_edit_pending=False)
    queue = SimpleNamespace(placement_status="hidden")
    assert _shipment_reason(None, order, queue) is None
    queue.placement_status = "pending"
    assert "先扫描置货码" in _shipment_reason(None, order, queue)


def test_scan_placement_reports_product_and_packaging_shortages():
    order = SimpleNamespace(status="pending_shipment")
    stock = {"stock_status": "yellow", "items": [{
        "product_name": "测试产品", "shortage_quantity": 2,
        "packaging_name": "纸箱", "packaging_shortage_quantity": 1,
    }]}
    with patch("api.routers.warehouse_scanning._placement_blocked_reason", return_value=None):
        reason = _placement_reason(None, order, stock)
    assert "测试产品成品缺 2" in reason
    assert "测试产品的纸箱缺 1" in reason
    stock["stock_status"] = "red"
    with patch("api.routers.warehouse_scanning._placement_blocked_reason", return_value="成品库存不足"):
        assert _placement_reason(None, order, stock).startswith("整单置货库存不足：测试产品成品缺 2")


def test_client_displays_fastapi_and_business_error_details():
    assert api_error_message({"detail": "发货凭证不能超过 10MB"}, 422) == "发货凭证不能超过 10MB"
    assert "file" in api_error_message({"detail": [{"loc": ["body", "file"], "msg": "Field required"}]}, 422)
    assert api_error_message({"msg": "存在未满足条件", "data": {"blocked_conditions": ["尾款未收"]}}, 200) == "尾款未收"
