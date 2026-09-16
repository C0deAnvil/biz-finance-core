-- =====================================================================
-- 清空 customers，为旧系统客户主数据的完整重导做准备
-- 目标库：yecai_db
-- MySQL 8.x / InnoDB
--
-- 背景
--   customers 表在之前的「清模拟数据」里被刻意保留，里面存着一批
--   编码与旧系统对不上的客户。按 code 做 upsert 的导入脚本匹配不到它们，
--   会走 INSERT，撞上 customers.name 的唯一约束（1062），整批回滚。
--   清空后重导，能拿到带编码、带负责人、带备注、带待去重标签的完整数据。
--
-- 安全设计
--   1. 先整表复制到 customers_backup_before_reimport，随时可还原
--   2. 7 张引用 customers 的子表若有残留数据，直接中止，不留悬空外键
--   3. 全程在一个事务里，任何一步失败都整体回滚
--
-- ⚠️ 执行前仍建议做一次库级备份：
--   mysqldump -u root -p --single-transaction --routines --triggers \
--     yecai_db > ~/yecai_db_before_customer_reset_$(date +%F_%H%M).sql
-- =====================================================================

USE yecai_db;

-- ---------------------------------------------------------------------
-- 第一步：现状（只读）
-- ---------------------------------------------------------------------
SELECT '当前 customers 行数' AS 项目, COUNT(*) AS 值 FROM customers
UNION ALL SELECT '有 owner_user_id 的', COUNT(*) FROM customers WHERE owner_user_id IS NOT NULL
UNION ALL SELECT '有 code 的',          COUNT(*) FROM customers WHERE code IS NOT NULL AND code <> ''
UNION ALL SELECT 'customer_tags',              COUNT(*) FROM customer_tags
UNION ALL SELECT 'customer_follow_ups',        COUNT(*) FROM customer_follow_ups
UNION ALL SELECT 'customer_transfer_records',  COUNT(*) FROM customer_transfer_records
UNION ALL SELECT 'customer_complaints',        COUNT(*) FROM customer_complaints
UNION ALL SELECT 'customer_inquiries',         COUNT(*) FROM customer_inquiries
UNION ALL SELECT 'customer_ai_results',        COUNT(*) FROM customer_ai_results
UNION ALL SELECT 'sales_orders',               COUNT(*) FROM sales_orders;


-- ---------------------------------------------------------------------
-- 第二步：备份 + 清空
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS _reset_customers;

DELIMITER //
CREATE PROCEDURE _reset_customers()
BEGIN
    DECLARE v_orders   BIGINT DEFAULT 0;
    DECLARE v_follow   BIGINT DEFAULT 0;
    DECLARE v_transfer BIGINT DEFAULT 0;
    DECLARE v_complain BIGINT DEFAULT 0;
    DECLARE v_inquiry  BIGINT DEFAULT 0;
    DECLARE v_ai       BIGINT DEFAULT 0;
    DECLARE v_before   BIGINT DEFAULT 0;
    DECLARE v_backup   BIGINT DEFAULT 0;
    DECLARE v_after    BIGINT DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    -- 前置校验放在事务外：有任何一张子表还有数据就中止，
    -- 说明库里已经产生了真实业务，客户不能整表删。
    -- customer_tags 例外 —— 标签是客户自身的附属属性，随客户一起重建。
    SELECT COUNT(*) INTO v_orders   FROM sales_orders;
    SELECT COUNT(*) INTO v_follow   FROM customer_follow_ups;
    SELECT COUNT(*) INTO v_transfer FROM customer_transfer_records;
    SELECT COUNT(*) INTO v_complain FROM customer_complaints;
    SELECT COUNT(*) INTO v_inquiry  FROM customer_inquiries;
    SELECT COUNT(*) INTO v_ai       FROM customer_ai_results;

    IF v_orders + v_follow + v_transfer + v_complain + v_inquiry + v_ai > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：仍有业务数据引用 customers（订单/跟进/转交/投诉/询价/AI 结果），不能整表清空';
    END IF;

    SELECT COUNT(*) INTO v_before FROM customers;

    -- ---- 备份 ----
    DROP TABLE IF EXISTS customers_backup_before_reimport;
    CREATE TABLE customers_backup_before_reimport AS SELECT * FROM customers;
    SELECT COUNT(*) INTO v_backup FROM customers_backup_before_reimport;

    IF v_backup <> v_before THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：备份行数与原表不一致，未做任何删除';
    END IF;

    -- ---- 清空 ----
    START TRANSACTION;

    DELETE FROM customer_tags;      -- 标签随客户重建
    DELETE FROM customers;

    SELECT COUNT(*) INTO v_after FROM customers;
    IF v_after <> 0 THEN
        ROLLBACK;
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '中止：customers 未清空，已回滚';
    END IF;

    COMMIT;

    ALTER TABLE customers AUTO_INCREMENT = 1;

    SELECT '清空完成'                          AS 结果,
           v_before                            AS 清空前行数,
           v_backup                            AS 已备份行数,
           'customers_backup_before_reimport'  AS 备份表名;
END //
DELIMITER ;

CALL _reset_customers();

DROP PROCEDURE IF EXISTS _reset_customers;


-- ---------------------------------------------------------------------
-- 第三步：确认
-- ---------------------------------------------------------------------
SELECT 'customers'   AS 表, COUNT(*) AS 行数 FROM customers
UNION ALL
SELECT '备份表',            COUNT(*)         FROM customers_backup_before_reimport;


-- =====================================================================
-- 还原（万一导错了）
--   DELETE FROM customer_tags;
--   DELETE FROM customers;
--   INSERT INTO customers SELECT * FROM customers_backup_before_reimport;
--
-- 确认新数据没问题后，再删备份表：
--   DROP TABLE customers_backup_before_reimport;
-- =====================================================================
