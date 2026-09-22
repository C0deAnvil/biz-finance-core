"""Standalone SQLite checks; run with unittest without the MySQL pytest fixture."""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import (
    Base, FulfillmentDemand, InventoryDispatchQueue, InventoryOperationRequest,
    OperationLog, PayableRecord, ProductionTask, PurchaseOrder, PurchaseReceipt, SalesOrder,
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

    def test_unconverted_demand_starts_at_submission_and_stays_open(self):
        t = self.base
        self.order.status = "pending_confirmation"
        self.db.add(OperationLog(
            module="sales", action="submit_order", content=f"客户订单 {self.order.order_no} 提交财务审批",
            after_data={"order_id": self.order.id}, created_at=t + timedelta(minutes=10),
        ))
        demand = FulfillmentDemand(
            demand_no="BH-PENDING", sales_order_id=self.order.id, product_id=1,
            fulfillment_mode="purchase", shortage_quantity=5, status="pending",
            created_at=t + timedelta(minutes=25),
        )
        self.db.add(demand)
        self.db.flush()

        tasks, _ = build_sales_order_tasks(self.db, self.order, [])
        placement = next(row for row in tasks[2]["children"] if row["key"] == "placement-work")
        branch = placement["children"][0]
        processing = next(row for row in branch["children"] if row["key"] == f"demand-process-{demand.id}")
        self.assertEqual(branch["start_at"], (t + timedelta(minutes=25)).isoformat())
        self.assertIsNone(processing["end_at"])
        self.assertEqual(processing["status_text"], "待创建采购订单")
        self.assertTrue(any(row["label"] == "提交补货需求" for row in branch["children"]))

    def test_placement_completion_starts_shipment(self):
        t = self.base
        self.order.status = "shipped"
        self.order.shipped_at = t + timedelta(minutes=45)
        self.db.add(OperationLog(
            module="sales", action="submit_order", content=f"客户订单 {self.order.order_no} 提交财务审批",
            after_data={"order_id": self.order.id}, created_at=t + timedelta(minutes=10),
        ))
        self.db.add(InventoryDispatchQueue(
            sales_order_id=self.order.id,
            placement_marked_at=t + timedelta(minutes=30),
            shipment_marked_at=t + timedelta(minutes=45),
        ))
        self.db.flush()

        tasks, _ = build_sales_order_tasks(self.db, self.order, [])
        placement = tasks[2]
        shipment = next(row for row in placement["children"] if row["key"] == "shipment")
        self.assertEqual(shipment["start_at"], (t + timedelta(minutes=30)).isoformat())
        self.assertEqual(shipment["end_at"], (t + timedelta(minutes=45)).isoformat())
        self.assertTrue(any(row["key"] == "placement-done" for row in placement["children"]))

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
        second_purchase_demand = FulfillmentDemand(
            demand_no="BH-3", sales_order_id=self.order.id, product_id=3,
            fulfillment_mode="purchase", shortage_quantity=0, status="converted",
            created_at=t + timedelta(minutes=22),
        )
        self.db.add(second_purchase_demand)
        self.db.flush()
        self.db.add(ProductionTask(
            task_no="SC-1", sales_order_id=self.order.id,
            fulfillment_demand_id=production_demand.id, product_id=2,
            planned_quantity=3, created_by_id=1,
            created_at=t + timedelta(minutes=31),
        ))
        po = PurchaseOrder(
            order_no="CG-1", supplier_id=1, purchaser_user_id=1, operator_user_id=1,
            payment_type="prepaid", status="fully_received", total_amount=10,
            sales_order_id=None,
            replenishment_source_type="fulfillment_demand", replenishment_source_id=999,
            created_at=t + timedelta(minutes=30),
        )
        self.db.add(po)
        self.db.flush()
        self.db.add(OperationLog(
            module="purchase", action="create_purchase_order",
            content=f"合并创建采购订单 {po.order_no}，来源：补货需求",
            after_data={"replenishment_sources": [
                f"fulfillment_demand:{demand.id}",
                f"fulfillment_demand:{second_purchase_demand.id}",
            ]},
            created_at=t + timedelta(minutes=30),
        ))
        self.db.add(PayableRecord(
            purchase_order_id=po.id, amount=10, order_amount=10,
            paid_at=t + timedelta(minutes=40), paid_by_id=1, status="paid",
        ))
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
        placement = next(row for row in tasks[2]["children"] if row["key"] == "placement-work")
        self.assertEqual(placement["children"][0]["label"], "采购补货 · 产品#1")
        replenishment = placement["children"][0]
        purchase = next(row for row in replenishment["children"] if row["key"] == f"purchase-{po.id}")
        self.assertEqual(replenishment["start_at"], (t + timedelta(minutes=20)).isoformat())
        production_demand_task = next(row for row in placement["children"] if row["key"] == f"demand-{production_demand.id}")
        production = next(row for row in production_demand_task["children"] if row["key"].startswith("production-"))
        self.assertEqual(production["lane"], "production")
        self.assertEqual(production["start_at"], (t + timedelta(minutes=31)).isoformat())
        self.assertEqual(purchase["start_at"], (t + timedelta(minutes=30)).isoformat())
        self.assertEqual(purchase["end_at"], (t + timedelta(minutes=60)).isoformat())
        self.assertTrue(any(row["label"] == "采购订单创建" for row in purchase["children"]))
        self.assertTrue(any(row["label"] == "采购到货" for row in purchase["children"]))
        self.assertTrue(any(row["label"] == "提交补货需求" for row in replenishment["children"]))
        self.assertTrue(any(row["lane"] == "inbound" for row in purchase["children"]))
        self.assertFalse(any(row["lane"] == "quality" for row in purchase["children"]))
        other_branch = next(row for row in placement["children"] if row["key"] == f"demand-{second_purchase_demand.id}")
        self.assertTrue(any(row["key"].startswith(f"purchase-{po.id}::") for row in other_branch["children"]))


if __name__ == "__main__":
    unittest.main()
