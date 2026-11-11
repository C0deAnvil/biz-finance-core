-- 收款/付款账户去重脚本（MySQL 8+）
-- 去重键：归属类型 + 客户/供应商 + 币种 + 银行名 + 非空账号（忽略首尾空格及大小写）。
-- 每组保留最小 ID；先迁移所有外键引用，最后物理删除重复账户。
-- 注意：不自动合并账号为空的账户，避免把同名但不同账户误删。

START TRANSACTION;

DROP TEMPORARY TABLE IF EXISTS tmp_financial_account_merge;
CREATE TEMPORARY TABLE tmp_financial_account_merge AS
SELECT
    duplicate_account.id AS duplicate_id,
    keeper.keep_id
FROM financial_accounts AS duplicate_account
JOIN (
    SELECT
        owner_type,
        COALESCE(customer_id, 0) AS customer_key,
        COALESCE(supplier_id, 0) AS supplier_key,
        UPPER(TRIM(COALESCE(currency, 'CNY'))) AS currency_key,
        UPPER(TRIM(COALESCE(bank_name, ''))) AS bank_key,
        UPPER(TRIM(account_number)) AS account_key,
        MIN(id) AS keep_id
    FROM financial_accounts
    WHERE TRIM(COALESCE(account_number, '')) <> ''
    GROUP BY
        owner_type,
        COALESCE(customer_id, 0),
        COALESCE(supplier_id, 0),
        UPPER(TRIM(COALESCE(currency, 'CNY'))),
        UPPER(TRIM(COALESCE(bank_name, ''))),
        UPPER(TRIM(account_number))
    HAVING COUNT(*) > 1
) AS keeper
    ON duplicate_account.owner_type = keeper.owner_type
   AND COALESCE(duplicate_account.customer_id, 0) = keeper.customer_key
   AND COALESCE(duplicate_account.supplier_id, 0) = keeper.supplier_key
   AND UPPER(TRIM(COALESCE(duplicate_account.currency, 'CNY'))) = keeper.currency_key
   AND UPPER(TRIM(COALESCE(duplicate_account.bank_name, ''))) = keeper.bank_key
   AND UPPER(TRIM(duplicate_account.account_number)) = keeper.account_key
WHERE duplicate_account.id <> keeper.keep_id;

-- 先核对候选映射；若结果不符合预期，执行 ROLLBACK。
SELECT m.keep_id, m.duplicate_id, a.account_name, a.bank_name, a.account_number
FROM tmp_financial_account_merge AS m
JOIN financial_accounts AS a ON a.id = m.duplicate_id
ORDER BY m.keep_id, m.duplicate_id;

UPDATE sales_orders AS t JOIN tmp_financial_account_merge AS m ON t.receipt_account_id = m.duplicate_id SET t.receipt_account_id = m.keep_id;
UPDATE sales_receipt_requests AS t JOIN tmp_financial_account_merge AS m ON t.company_account_id = m.duplicate_id SET t.company_account_id = m.keep_id;
UPDATE sales_receipt_requests AS t JOIN tmp_financial_account_merge AS m ON t.counterparty_account_id = m.duplicate_id SET t.counterparty_account_id = m.keep_id;
UPDATE purchase_payment_requests AS t JOIN tmp_financial_account_merge AS m ON t.company_account_id = m.duplicate_id SET t.company_account_id = m.keep_id;
UPDATE purchase_payment_requests AS t JOIN tmp_financial_account_merge AS m ON t.counterparty_account_id = m.duplicate_id SET t.counterparty_account_id = m.keep_id;
UPDATE receivable_records AS t JOIN tmp_financial_account_merge AS m ON t.company_account_id = m.duplicate_id SET t.company_account_id = m.keep_id;
UPDATE receivable_records AS t JOIN tmp_financial_account_merge AS m ON t.counterparty_account_id = m.duplicate_id SET t.counterparty_account_id = m.keep_id;
UPDATE payable_records AS t JOIN tmp_financial_account_merge AS m ON t.company_account_id = m.duplicate_id SET t.company_account_id = m.keep_id;
UPDATE payable_records AS t JOIN tmp_financial_account_merge AS m ON t.counterparty_account_id = m.duplicate_id SET t.counterparty_account_id = m.keep_id;
UPDATE cash_flow_records AS t JOIN tmp_financial_account_merge AS m ON t.company_account_id = m.duplicate_id SET t.company_account_id = m.keep_id;
UPDATE financial_account_transactions AS t JOIN tmp_financial_account_merge AS m ON t.account_id = m.duplicate_id SET t.account_id = m.keep_id;
UPDATE financial_account_transactions AS t JOIN tmp_financial_account_merge AS m ON t.related_account_id = m.duplicate_id SET t.related_account_id = m.keep_id;

-- 所有引用改完才允许删除；此检查应返回 0。
SELECT COUNT(*) AS remaining_references
FROM (
    SELECT receipt_account_id AS account_id FROM sales_orders
    UNION ALL SELECT company_account_id FROM sales_receipt_requests
    UNION ALL SELECT counterparty_account_id FROM sales_receipt_requests
    UNION ALL SELECT company_account_id FROM purchase_payment_requests
    UNION ALL SELECT counterparty_account_id FROM purchase_payment_requests
    UNION ALL SELECT company_account_id FROM receivable_records
    UNION ALL SELECT counterparty_account_id FROM receivable_records
    UNION ALL SELECT company_account_id FROM payable_records
    UNION ALL SELECT counterparty_account_id FROM payable_records
    UNION ALL SELECT company_account_id FROM cash_flow_records
    UNION ALL SELECT account_id FROM financial_account_transactions
    UNION ALL SELECT related_account_id FROM financial_account_transactions
) AS refs
JOIN tmp_financial_account_merge AS m ON refs.account_id = m.duplicate_id;

DELETE a
FROM financial_accounts AS a
JOIN tmp_financial_account_merge AS m ON a.id = m.duplicate_id;

COMMIT;
