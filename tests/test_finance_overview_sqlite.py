"""Isolated finance arithmetic check; run with `python tests/test_finance_overview_sqlite.py`."""

import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import (  # noqa: E402
    Base, CashFlowRecord, Customer, PayableRecord, Product, PurchaseOrder, PurchasePaymentRequest,
    ReceivablePlan, ReceivableRecord, RefundRecord, Reimbursement, Role, SalesOrder, SalesReturn,
    SalesOrderItem, Supplier, User,
)
from services.finance_overview import build_finance_overview  # noqa: E402


class FinanceOverviewTests(unittest.TestCase):
    def test_period_and_money_reconcile_to_details(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        when = datetime(2026, 10, 12, 10, 0)
        with Session(engine) as db:
            role = Role(code="finance", name="财务", home_route="/dashboard")
            db.add(role)
            db.flush()
            user = User(username="finance-test", full_name="财务测试", hashed_password="unused", role_id=role.id)
            another_finance = User(username="finance-other", full_name="另一财务", hashed_password="unused", role_id=role.id)
            product = Product(product_code="P-TEST", name="测试产品")
            customer = Customer(code="C-TEST", name="测试客户", contact_name="甲", phone="1", address="测试地址")
            supplier = Supplier(code="S-TEST", name="测试供应商", contact_name="乙", phone="2", address="测试地址")
            db.add_all([user, another_finance, product, customer, supplier])
            db.flush()
            sale = SalesOrder(
                order_no="XS-TEST", customer_id=customer.id, sales_user_id=user.id, operator_user_id=user.id,
                consignee="甲", consignee_phone="1", delivery_address="测试地址", payment_type="full",
                status="shipped", total_amount=Decimal("12.3456"), shipped_at=when, created_at=when,
            )
            draft = SalesOrder(
                order_no="XS-DRAFT", customer_id=customer.id, sales_user_id=user.id, operator_user_id=user.id,
                consignee="甲", consignee_phone="1", delivery_address="测试地址", payment_type="full",
                status="draft", total_amount=Decimal("999.0000"), created_at=when,
            )
            purchase = PurchaseOrder(
                order_no="CG-TEST", supplier_id=supplier.id, purchaser_user_id=user.id, operator_user_id=user.id,
                payment_type="before_arrival", status="purchasing", total_amount=Decimal("8.1111"),
                created_at=datetime(2026, 9, 30, 10, 0), approved_at=when,
            )
            reimbursement = Reimbursement(
                reimbursement_no="BX-TEST", applicant_user_id=user.id, expense_category="other",
                expense_subcategory="交通", amount=Decimal("1.2222"), occurred_at=when,
                reason="测试", status="approved", created_at=when,
            )
            paid_reimbursement = Reimbursement(
                reimbursement_no="BX-PAID", applicant_user_id=user.id, expense_category="other",
                expense_subcategory="办公", amount=Decimal("0.7778"), occurred_at=when,
                reason="已出账", status="paid", paid_at=when, created_at=when,
            )
            db.add_all([sale, draft, purchase, reimbursement, paid_reimbursement])
            db.flush()
            sales_return = SalesReturn(return_no="TH-TEST", sales_order_id=sale.id,
                                       requested_by_id=user.id, return_type="return",
                                       reason="测试退款", status="refunded", refund_amount=Decimal("0.5000"))
            db.add(sales_return)
            db.flush()
            db.add_all([
                SalesOrderItem(sales_order_id=sale.id, product_id=product.id, quantity=Decimal("2"),
                               unit_price=Decimal("5.0000"), total_amount=Decimal("10.0000")),
                ReceivablePlan(sales_order_id=sale.id, due_date=date(2026, 11, 1),
                               planned_amount=Decimal("5.0000"), received_amount=Decimal("2.0000"),
                               created_by_id=user.id, created_at=when),
                ReceivableRecord(sales_order_id=sale.id, amount=Decimal("2.0000"),
                                 received_at=when, received_by_id=user.id),
                PayableRecord(purchase_order_id=purchase.id, amount=Decimal("3.0000"),
                              paid_at=when, paid_by_id=user.id),
                PayableRecord(purchase_order_id=purchase.id, amount=Decimal("1.0000"),
                              paid_at=when, paid_by_id=user.id, status="pending"),
                CashFlowRecord(record_no="CF-TEST", flow_type="incoming", business_type="sales",
                               business_id=sale.id, amount=Decimal("2.0000"), happened_at=when),
                PurchasePaymentRequest(request_no="FK-OTHER", purchase_order_id=purchase.id,
                                       requested_by_id=user.id, requested_amount=Decimal("4.0000"),
                                       status="pending_finance_payment", assigned_finance_user_id=another_finance.id,
                                       created_at=when),
                RefundRecord(business_type="sales_return", sales_return_id=sales_return.id,
                             amount=Decimal("0.5000"), refunded_at=when, processed_by_id=user.id),
            ])
            db.flush()
            payload = build_finance_overview(
                db, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31), current_user=user,
            )
            summary = payload["summary"]
            self.assertEqual(summary["sales_count"], 1)
            self.assertEqual(summary["sales_amount"], "12.3456")
            self.assertEqual(payload["details"]["products"][0]["amount"], "10.0000")
            self.assertEqual(summary["receivable_amount"], "5.0000")
            self.assertEqual(summary["receivable_balance"], "3.0000")
            self.assertEqual(summary["payable_amount"], "8.1111")
            self.assertEqual(summary["payable_balance"], "5.1111")
            self.assertEqual(summary["reimbursement_count"], 2)
            self.assertEqual(summary["reimbursement_amount"], "2.0000")
            self.assertEqual(summary["reimbursement_paid_amount"], "0.7778")
            self.assertEqual(summary["sales_refund_amount"], "0.5000")
            self.assertEqual(summary["collection_amount"], "2.0000")
            self.assertEqual(summary["payment_amount"], "3.0000")
            self.assertEqual(summary["cash_in"], "2.0000")
            self.assertEqual(summary["finance_task_count"], 1)
            self.assertNotIn("FK-OTHER", [row["no"] for row in payload["details"]["finance_tasks"]])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
