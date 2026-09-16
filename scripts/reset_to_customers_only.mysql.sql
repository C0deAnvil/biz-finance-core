-- =====================================================================
-- 业财一体化系统 — 清空模拟数据，只保留客户主档
-- 目标库：yecai_db（本机与服务器同名）
-- MySQL 8.x / InnoDB
--
-- v2 修订：
--   1. 移除 order_flow_status —— 该表已被迁移 a1b2c3d4e5f6a7b8 主动删除
--      （注释原文：drop redundant order_flow_status，与 document_status_snapshots 重复）
--   2. 改为「表不存在就跳过」，单张表缺失不再中断整个脚本
--   3. 结尾输出每张表的删除行数，缺失的表会明确标出来
--
-- 【保留】8 张表
--   customers                客户主档
--   users / roles / departments / warehouses
--                            账号、角色、部门、仓库
--   app_settings             系统参数
--   payment_method_options   收付款方式字典
--   user_module_permissions  用户页面权限
--
-- 【清空】67 张表
--
-- ⚠️ 执行前必做备份：
--   mysqldump -u root -p --single-transaction --routines --triggers \
--     yecai_db > ~/yecai_db_before_reset_$(date +%F_%H%M).sql
-- =====================================================================


-- ---------------------------------------------------------------------
-- 第一步：执行前现状（只读）
-- ---------------------------------------------------------------------
SELECT '执行前' AS 阶段,
       (SELECT COUNT(*) FROM customers)       AS 客户,
       (SELECT COUNT(*) FROM sales_orders)    AS 销售单,
       (SELECT COUNT(*) FROM purchase_orders) AS 采购单,
       (SELECT COUNT(*) FROM products)        AS 产品,
       (SELECT COUNT(*) FROM materials)       AS 原料,
       (SELECT COUNT(*) FROM suppliers)       AS 供应商,
       (SELECT COUNT(*) FROM users)           AS 用户;


-- ---------------------------------------------------------------------
-- 第二步：准备工具（DDL，必须在事务之外）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS _purge_log;
CREATE TABLE _purge_log (
  seq        INT AUTO_INCREMENT PRIMARY KEY,
  table_name VARCHAR(64) NOT NULL,
  existed    TINYINT(1)  NOT NULL,
  rows_deleted BIGINT    NOT NULL DEFAULT 0
) ENGINE=InnoDB;

DROP PROCEDURE IF EXISTS _purge_table;
DELIMITER $$
CREATE PROCEDURE _purge_table(IN tbl VARCHAR(64))
BEGIN
  DECLARE tbl_exists INT DEFAULT 0;
  SELECT COUNT(*) INTO tbl_exists
    FROM information_schema.TABLES
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl;

  IF tbl_exists = 0 THEN
    INSERT INTO _purge_log (table_name, existed, rows_deleted) VALUES (tbl, 0, 0);
  ELSE
    SET @sql = CONCAT('DELETE FROM `', tbl, '`');
    PREPARE stmt FROM @sql;
    EXECUTE stmt;
    SET @n = ROW_COUNT();
    DEALLOCATE PREPARE stmt;
    INSERT INTO _purge_log (table_name, existed, rows_deleted) VALUES (tbl, 1, @n);
  END IF;
END$$
DELIMITER ;


-- ---------------------------------------------------------------------
-- 第三步：清理
-- ---------------------------------------------------------------------
SET FOREIGN_KEY_CHECKS = 0;
START TRANSACTION;

-- ========== 1. 通知、待办、日志、附件、报表快照 ==========
CALL _purge_table('notification_recipients');
CALL _purge_table('notifications');
CALL _purge_table('todo_items');
CALL _purge_table('operation_logs');
CALL _purge_table('attachments');
CALL _purge_table('report_snapshots');
CALL _purge_table('user_sessions');              -- 所有人需重新登录

-- ========== 2. 单据状态追踪 ==========
CALL _purge_table('document_status_history');
CALL _purge_table('document_status_snapshots');
CALL _purge_table('document_relations');

-- ========== 3. 财务 ==========
CALL _purge_table('cash_flow_records');
CALL _purge_table('refund_records');
CALL _purge_table('receivable_records');
CALL _purge_table('receivable_plans');
CALL _purge_table('sales_receipt_requests');
CALL _purge_table('payable_records');
CALL _purge_table('purchase_payment_requests');
CALL _purge_table('reimbursements');
CALL _purge_table('business_matters');
CALL _purge_table('quality_cost_records');

-- ========== 4. 质检 ==========
CALL _purge_table('quality_dispositions');
CALL _purge_table('quality_inbound_applications');
CALL _purge_table('quality_inspections');
CALL _purge_table('quality_tasks');

-- ========== 5. 物流 ==========
CALL _purge_table('logistics_records');

-- ========== 6. 库存流水与盘点 ==========
CALL _purge_table('stock_take_items');
CALL _purge_table('stock_take_tasks');
CALL _purge_table('stock_transfers');
CALL _purge_table('inventory_operation_requests');
CALL _purge_table('inventory_dispatch_queues');
CALL _purge_table('inventory_transactions');
CALL _purge_table('inventory_batches');

-- ========== 7. 生产 ==========
CALL _purge_table('production_material_usages');
CALL _purge_table('production_plans');
CALL _purge_table('production_tasks');
CALL _purge_table('material_replenishment_demands');
CALL _purge_table('fulfillment_demands');

-- ========== 8. 采购 ==========
CALL _purge_table('purchase_receipt_items');
CALL _purge_table('purchase_receipts');
CALL _purge_table('purchase_order_approvals');
CALL _purge_table('purchase_returns');
CALL _purge_table('purchase_order_items');
CALL _purge_table('purchase_orders');

-- ========== 9. 销售 ==========
CALL _purge_table('sales_order_price_change_logs');
CALL _purge_table('sales_return_items');
CALL _purge_table('sales_returns');
CALL _purge_table('sales_order_approvals');
CALL _purge_table('sales_order_items');
CALL _purge_table('sales_orders');

-- ========== 10. 客户附属数据（customers 主表保留） ==========
CALL _purge_table('customer_ai_results');
CALL _purge_table('customer_inquiries');
CALL _purge_table('customer_complaints');
CALL _purge_table('customer_transfer_records');
CALL _purge_table('customer_follow_ups');
CALL _purge_table('customer_tags');

-- ========== 11. 供应商 ==========
CALL _purge_table('supplier_ai_results');
CALL _purge_table('supplier_catalog_items');
CALL _purge_table('supplier_tags');              -- 未跑本次迁移则会被标记为「表不存在」
CALL _purge_table('suppliers');

-- ========== 12. 产品 / 原料 / BOM / 包装 / 价格 ==========
CALL _purge_table('standard_price_change_logs');
CALL _purge_table('bom_items');
CALL _purge_table('boms');
CALL _purge_table('inventories');                -- 删除而非归零，原因见文件末尾
CALL _purge_table('packaging_configs');          -- 5 条全局包装规格由 seed.py 重启时重建
CALL _purge_table('products');
CALL _purge_table('materials');

-- ========== 13. 单号计数器 ==========
CALL _purge_table('sequence_counters');

COMMIT;
SET FOREIGN_KEY_CHECKS = 1;


-- ---------------------------------------------------------------------
-- 第四步：验收
-- ---------------------------------------------------------------------

-- 4.1 每张表删了多少行；existed=0 的是库里不存在的表（正常应为空）
SELECT table_name AS 表名, rows_deleted AS 删除行数
FROM _purge_log
WHERE existed = 0;

SELECT COUNT(*) AS 处理表数,
       SUM(existed = 0) AS 不存在被跳过,
       SUM(rows_deleted) AS 删除总行数
FROM _purge_log;

SELECT table_name AS 表名, rows_deleted AS 删除行数
FROM _purge_log
WHERE rows_deleted > 0
ORDER BY rows_deleted DESC;

-- 4.2 总体对照
SELECT '执行后' AS 阶段,
       (SELECT COUNT(*) FROM customers)       AS 客户_应保持6253,
       (SELECT COUNT(*) FROM users)           AS 用户_应为40,
       (SELECT COUNT(*) FROM roles)           AS 角色_应大于0,
       (SELECT COUNT(*) FROM departments)     AS 部门_应大于0,
       (SELECT COUNT(*) FROM warehouses)      AS 仓库_应大于0,
       (SELECT COUNT(*) FROM sales_orders)    AS 销售单_应为0,
       (SELECT COUNT(*) FROM purchase_orders) AS 采购单_应为0,
       (SELECT COUNT(*) FROM products)        AS 产品_应为0,
       (SELECT COUNT(*) FROM suppliers)       AS 供应商_应为0,
       (SELECT COUNT(*) FROM customer_tags)   AS 客户标签_应为0;

-- 4.3 客户的负责人/建档人外键有没有变成孤儿（应返回 0 行）
SELECT c.id, c.code, c.name, c.owner_user_id, c.created_by_id
FROM customers c
LEFT JOIN users uo ON uo.id = c.owner_user_id
LEFT JOIN users uc ON uc.id = c.created_by_id
WHERE (c.owner_user_id IS NOT NULL AND uo.id IS NULL)
   OR (c.created_by_id IS NOT NULL AND uc.id IS NULL);


-- ---------------------------------------------------------------------
-- 第五步：清理临时对象
-- 想保留 _purge_log 供核对，就先别执行这两行，核对完再手动删。
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS _purge_table;
-- DROP TABLE IF EXISTS _purge_log;


-- =====================================================================
-- 附：可选的自增 ID 归位（DDL，会隐式提交，必须单独执行）
-- =====================================================================
-- ALTER TABLE sales_orders      AUTO_INCREMENT = 1;
-- ALTER TABLE purchase_orders   AUTO_INCREMENT = 1;
-- ALTER TABLE products          AUTO_INCREMENT = 1;
-- ALTER TABLE materials         AUTO_INCREMENT = 1;
-- ALTER TABLE suppliers         AUTO_INCREMENT = 1;
-- ALTER TABLE production_tasks  AUTO_INCREMENT = 1;
-- ALTER TABLE quality_tasks     AUTO_INCREMENT = 1;
-- ALTER TABLE inventories       AUTO_INCREMENT = 1;


-- =====================================================================
-- 说明
--
-- 1. 为什么 inventories 是删除而不是归零？
--    inventories 有指向 products / materials 的外键。产品和原料清空后，
--    库存记录就成了指向不存在物料的孤儿行，留着会让库存页面报错。
--    warehouses 保留，重新录入产品后系统会按需重建库存记录。
--
-- 2. 后端重启后 seed.py 会自动重建：
--    部门、角色、初始用户、仓库、5 条全局包装规格
--    （25kg编织袋 / 50kg编织袋 / 吨袋 / 25kg纸塑复合袋 / 散装）。
--    产品、原料、客户、供应商 seed.py 不碰。
--
-- 3. payment_method_options 保留。它是后台维护的配置项而非测试数据，
--    seed.py 不会重建，删了下拉会空。要清就单独执行：
--    DELETE FROM payment_method_options;
--
-- 4. order_flow_status 不在清理列表里：该表已被迁移 a1b2c3d4e5f6a7b8 删除，
--    其职能由 document_status_snapshots 承担。
-- =====================================================================
