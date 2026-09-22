"""Standalone SQLite checks; run with unittest without the MySQL pytest fixture."""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import (
    Base, FulfillmentDemand, InventoryDispatchQueue, InventoryOperationRequest,
    OperationLog, ProductionTask, PurchaseOrder, PurchaseReceipt, SalesOrder,
)
from services.sales_order_timeline import build_sales_order_tasks


class SalesOrderTimelineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.base = datetime(2026, 9, 22, 9, 0)
        self.order = SalesOrder(
            order_no="XS-TIMELINE-1", sales_user_id=1, operator_user_id=1,
            consignee="A", consignee_phone="123", delivery_address="X",
            payment_type="full_before_delivery", status="draft", created_at=self.base,
        )
        self.db.add(self.order)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_draft_has_no_lifecycle(self):
        tasks, submitted = build_sales_order_tasks(self.db, self.order, [])
        self.assertEqual(tasks, [])
        self.assertIsNone(submitted)

    def test_parallel_tasks_and_procurement_children_use_real_times(self):
        t = self.base
        self.order.status = "pending_confirmation"
        self.db.add(OperationLog(
            module="sales", action="submit_order", content=f"客户订单 {self.order.order_no} 提交财务审批",
            after_data={"order_id": self.order.id}, created_at=t + timedelta(minutes=10),
        ))
        self.db.add(InventoryDispatchQueue(sales_order_id=self.order.id))
        demand = FulfillmentDemand(
            demand_no="BH-1", sales_order_id=self.order.id, product_id=1,
            fulfillment_mode="purchase", shortage_quantity=2, status="pending",
            created_at=t + timedelta(minutes=20),
        )
        self.db.add(demand)
        production_demand = FulfillmentDemand(
            demand_no="BH-2", sales_order_id=self.order.id, product_id=2,
            fulfillment_mode="production", shortage_quantity=3, status="pending",
            created_at=t + timedelta(minutes=21),
        )
        self.db.add(production_demand)
        self.db.flush()
        self.db.add(ProductionTask(
            task_no="SC-1", sales_order_id=self.order.id,
            fulfillment_demand_id=production_demand.id, product_id=2,
            planned_quantity=3, created_by_id=1,
            created_at=t + timedelta(minutes=31),
        ))
        po = PurchaseOrder(
            order_no="CG-1", supplier_id=1, purchaser_user_id=1, operator_user_id=1,
            payment_type="prepaid", status="fully_received", sales_order_id=self.order.id,
            replenishment_source_type="fulfillment_demand", replenishment_source_id=demand.id,
            created_at=t + timedelta(minutes=30),
        )
        self.db.add(po)
        self.db.flush()
        receipt = PurchaseReceipt(
            receipt_no="SH-1", purchase_order_id=po.id, warehouse_id=1,
            received_by_id=1, requires_quality=False, status="pending_inbound",
            created_at=t + timedelta(minutes=50), recorded_at=t + timedelta(minutes=51),
        )
        self.db.add(receipt)
        self.db.flush()
        self.db.add(InventoryOperationRequest(
            request_no="RK-1", operation_type="inbound", request_category="purchase_receipt_inbound",
            source_module="purchase", source_type="purchase_receipt", source_id=receipt.id,
            warehouse_id=1, requested_by_id=1, status="completed",
            created_at=t + timedelta(minutes=52), completed_at=t + timedelta(minutes=60),
        ))
        self.db.flush()

        tasks, submitted = build_sales_order_tasks(self.db, self.order, [])
        self.assertEqual(submitted, t + timedelta(minutes=10))
        self.assertEqual([row["key"] for row in tasks[:3]], ["submitted", "finance", "placement"])
        self.assertEqual(tasks[1]["start_at"], tasks[2]["start_at"])
        self.assertEqual(tasks[1]["children"][0]["label"], "客户订单审批 / 收款确认")
        replenishment = tasks[2]["children"][0]
        purchase = replenishment["children"][0]
        self.assertEqual(replenishment["start_at"], (t + timedelta(minutes=20)).isoformat())
        production = tasks[2]["children"][1]["children"][0]
        self.assertEqual(production["lane"], "production")
        self.assertEqual(production["start_at"], (t + timedelta(minutes=31)).isoformat())
        self.assertEqual(purchase["start_at"], (t + timedelta(minutes=30)).isoformat())
        self.assertEqual(purchase["end_at"], (t + timedelta(minutes=60)).isoformat())
        self.assertEqual(purchase["children"][0]["label"], "采购到货")
        self.assertTrue(any(row["lane"] == "inbound" for row in purchase["children"]))
        self.assertFalse(any(row["lane"] == "quality" for row in purchase["children"]))


if __name__ == "__main__":
    unittest.main()
