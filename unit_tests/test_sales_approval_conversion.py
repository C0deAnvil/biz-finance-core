from decimal import Decimal
from types import SimpleNamespace

from services.sales_service import orders


class CaptureSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def test_convert_full_payment_order_to_receivable_does_not_record_receipt(monkeypatch):
    db = CaptureSession()
    cash_flows = []
    monkeypatch.setattr(orders, "create_cash_flow", lambda *args, **kwargs: cash_flows.append(kwargs))
    order = SimpleNamespace(id=12, order_no="XS12", total_amount=Decimal("100.1250"),
                            deposit_amount=Decimal("0"), payment_type="全款发货", receipt_account_id=2)
    received, receivable = orders._record_approval_payment_and_receivable(
        db, order=order, current_user=SimpleNamespace(id=3), convert_to_receivable=True
    )
    assert (received, receivable) == (Decimal("0"), Decimal("100.1250"))
    assert not any(isinstance(item, orders.ReceivableRecord) for item in db.added)
    assert len([item for item in db.added if isinstance(item, orders.ReceivablePlan)]) == 1
    assert cash_flows == []


def test_regular_deposit_approval_records_deposit_and_tail(monkeypatch):
    db = CaptureSession()
    cash_flows = []
    monkeypatch.setattr(orders, "create_cash_flow", lambda *args, **kwargs: cash_flows.append(kwargs))
    order = SimpleNamespace(id=13, order_no="XS13", total_amount=Decimal("100.1250"),
                            deposit_amount=Decimal("20.1250"), payment_type="先收定金后收尾款", receipt_account_id=2)
    received, receivable = orders._record_approval_payment_and_receivable(
        db, order=order, current_user=SimpleNamespace(id=3), convert_to_receivable=False
    )
    assert (received, receivable) == (Decimal("20.1250"), Decimal("80.0000"))
    assert len([item for item in db.added if isinstance(item, orders.ReceivableRecord)]) == 1
    assert len(cash_flows) == 1
