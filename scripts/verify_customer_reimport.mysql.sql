-- =====================================================================
-- 客户重导后的核对：确认旧那批 6249 条里没有「只此一份」的客户被删掉
--
-- 背景
--   清空前库里 6249 条客户，created_at 全部是 2026-08-18 00:00:00，
--   说明是一次性批量写入的。体检显示它们的 code 与旧系统 busercode
--   零重合，名称也只对上 429 条 —— 数量还比旧库全量（5869）多 380。
--   需要弄清这 6249 条到底是什么，再决定备份能不能删。
--
-- 全部只读。输出是数量和长度统计，不打印客户名称/电话/编码。
-- =====================================================================

USE yecai_db;

-- ---------------------------------------------------------------------
-- 1. 两边的规模
-- ---------------------------------------------------------------------
SELECT '备份表（清空前）' AS 数据集, COUNT(*) AS 行数 FROM customers_backup_before_reimport
UNION ALL
SELECT '当前表（重导后）',           COUNT(*)         FROM customers;


-- ---------------------------------------------------------------------
-- 2. 备份表的编码长什么样
--    旧系统 busercode 是「姓名+手机号」这类不定长串；
--    如果这里是 C0001 之类的定长生成码，说明那批数据不是旧系统直出的。
-- ---------------------------------------------------------------------
SELECT COUNT(*)                                       AS 总数,
       MIN(CHAR_LENGTH(code))                         AS 编码最短,
       MAX(CHAR_LENGTH(code))                         AS 编码最长,
       SUM(code REGEXP '^[A-Za-z]+[0-9]+$')           AS 字母加数字型,
       SUM(code REGEXP '^[0-9]+$')                    AS 纯数字型,
       SUM(code REGEXP '[一-龥]')                     AS 含中文型,
       SUM(phone IS NOT NULL AND phone <> '')         AS 有电话的,
       SUM(address IS NOT NULL AND address <> '')     AS 有地址的,
       SUM(contact_name IS NOT NULL AND contact_name <> '') AS 有联系人的
  FROM customers_backup_before_reimport;


-- ---------------------------------------------------------------------
-- 3. 真实覆盖率：备份里的客户，在新数据里还找得到吗
--    重导时 493 家重名客户被挂了「（旧编码）」后缀，
--    所以精确匹配之外还要匹配前缀。
-- ---------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM customers_backup_before_reimport) AS 备份总数,
    (SELECT COUNT(*) FROM customers_backup_before_reimport b
      WHERE EXISTS (SELECT 1 FROM customers c
                     WHERE c.name = b.name
                        OR c.name LIKE CONCAT(b.name, '（%'))) AS 按名称找得到,
    (SELECT COUNT(*) FROM customers_backup_before_reimport b
      WHERE b.phone IS NOT NULL AND b.phone <> ''
        AND EXISTS (SELECT 1 FROM customers c WHERE c.phone = b.phone)) AS 按电话找得到,
    (SELECT COUNT(*) FROM customers_backup_before_reimport b
      WHERE NOT EXISTS (SELECT 1 FROM customers c
                         WHERE c.name = b.name
                            OR c.name LIKE CONCAT(b.name, '（%'))
        AND (b.phone IS NULL OR b.phone = ''
             OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.phone = b.phone))
    ) AS 名称和电话都找不到;


-- ---------------------------------------------------------------------
-- 4. 反向：新导入的客户里，有多少是备份里没有的
-- ---------------------------------------------------------------------
SELECT COUNT(*) AS 新导入里备份没有的
  FROM customers c
 WHERE NOT EXISTS (
        SELECT 1 FROM customers_backup_before_reimport b
         WHERE b.name = c.name
            OR c.name LIKE CONCAT(b.name, '（%')
            OR (b.phone IS NOT NULL AND b.phone <> '' AND b.phone = c.phone));


-- ---------------------------------------------------------------------
-- 5. 新表的完整度自检
-- ---------------------------------------------------------------------
SELECT COUNT(*)                                         AS 客户总数,
       SUM(owner_user_id IS NOT NULL)                   AS 有负责人,
       SUM(region_province IS NOT NULL)                 AS 有省份,
       SUM(remark IS NOT NULL AND remark <> '')         AS 有备注,
       SUM(archived_at IS NOT NULL)                     AS 已停用,
       SUM(source_type = 'company_transfer')            AS 标记为公司转交
  FROM customers;

SELECT tag AS 标签, COUNT(*) AS 数量 FROM customer_tags GROUP BY tag;

-- 负责人分布（只看账号数和挂载量，不列姓名）
SELECT COUNT(DISTINCT owner_user_id) AS 有客户的销售账号数,
       MIN(n) AS 单人最少, MAX(n) AS 单人最多, ROUND(AVG(n)) AS 单人平均
  FROM (SELECT owner_user_id, COUNT(*) AS n
          FROM customers WHERE owner_user_id IS NOT NULL
         GROUP BY owner_user_id) t;
