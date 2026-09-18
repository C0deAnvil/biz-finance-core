-- 在 yecai_db 执行。仅修改产品档案类型，不改历史订单、生产任务或补货需求。
-- 先运行此预览，确认生产型产品名单符合预期。
SELECT id, product_code, name, specification, fulfillment_mode AS old_mode,
       CASE WHEN (name LIKE '%尿素%' AND name NOT LIKE '%球%') OR name LIKE '%铜丝%'
            THEN 'production' ELSE 'trade' END AS new_mode
FROM products ORDER BY new_mode, product_code;

-- 确认预览后执行以下事务；球状/球体/球形尿素均归贸易型。
START TRANSACTION;
UPDATE products
SET fulfillment_mode = CASE
    WHEN (name LIKE '%尿素%' AND name NOT LIKE '%球%') OR name LIKE '%铜丝%'
    THEN 'production' ELSE 'trade' END;
SELECT fulfillment_mode, COUNT(*) AS product_count FROM products GROUP BY fulfillment_mode;
-- 核对后手动执行 COMMIT;，发现分类不符则执行 ROLLBACK;
