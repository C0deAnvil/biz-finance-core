-- Yecai business-data reset for MySQL 8.x.
-- Run only after a verified backup. This preserves master data and accounts.
-- It removes business history and resets every inventory record to zero.

SET FOREIGN_KEY_CHECKS = 0;
START TRANSACTION;

-- Workflow, user-facing history, uploaded-file metadata, and generated analytics.
DELETE FROM notification_recipients;
DELETE FROM notifications;
DELETE FROM todo_items;
DELETE FROM operation_logs;
DELETE FROM attachments;
DELETE FROM report_snapshots;
DELETE FROM customer_ai_results;
DELETE FROM supplier_ai_results;
DELETE FROM customer_transfer_records;
DELETE FROM customer_complaints;
DELETE FROM customer_inquiries;
DELETE FROM standard_price_change_logs;

-- Finance and reimbursement data.
DELETE FROM cash_flow_records;
DELETE FROM refund_records;
DELETE FROM receivable_records;
DELETE FROM receivable_plans;
DELETE FROM sales_receipt_requests;
DELETE FROM payable_records;
DELETE FROM purchase_payment_requests;
DELETE FROM reimbursements;
DELETE FROM business_matters;
DELETE FROM quality_cost_records;

-- Quality, logistics, stock take, and inventory-operation history.
DELETE FROM quality_dispositions;
DELETE FROM quality_inbound_applications;
DELETE FROM quality_inspections;
DELETE FROM quality_tasks;
DELETE FROM logistics_records;
DELETE FROM stock_take_items;
DELETE FROM stock_take_tasks;
DELETE FROM inventory_operation_requests;
DELETE FROM inventory_transactions;
DELETE FROM inventory_batches;

-- Production data and demand generated from sales orders.
DELETE FROM production_material_usages;
DELETE FROM production_plans;
DELETE FROM production_tasks;
DELETE FROM fulfillment_demands;

-- Purchase data.
DELETE FROM purchase_receipt_items;
DELETE FROM purchase_receipts;
DELETE FROM purchase_order_approvals;
DELETE FROM purchase_returns;
DELETE FROM purchase_order_items;
DELETE FROM purchase_orders;

-- Sales data.
DELETE FROM sales_order_price_change_logs;
DELETE FROM sales_return_items;
DELETE FROM sales_returns;
DELETE FROM inventory_dispatch_queues;
DELETE FROM sales_order_approvals;
DELETE FROM sales_order_items;
DELETE FROM sales_orders;

-- Keep inventory master records but reset all stock values.
UPDATE inventories
SET quantity = 0,
    reserved_quantity = 0,
    avg_unit_cost = 0;

-- Reset generated business-number counters. New documents start from a clean sequence.
DELETE FROM sequence_counters;

COMMIT;
SET FOREIGN_KEY_CHECKS = 1;

-- Master data intentionally retained:
-- users, roles, departments, permissions, app_settings, customers, suppliers,
-- warehouses, products, materials, packaging_configs, boms, bom_items,
-- supplier_catalog_items, and payment_method_options.
