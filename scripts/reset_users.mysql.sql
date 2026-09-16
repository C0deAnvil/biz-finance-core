-- =====================================================================
-- 清空用户账号，只保留一个管理员   v2
-- 目标库：yecai_db   MySQL 8.x / InnoDB
--
-- v1 的两个问题已修：
--   1. 【排序规则冲突】users.username 是 utf8mb4_unicode_ci，
--      而用户变量走连接默认的 utf8mb4_0900_ai_ci，`=` 比较直接报
--      ERROR 1267，导致 @keep_id 变成 NULL、一个账号都没删。
--      → 比较时显式加 COLLATE utf8mb4_unicode_ci。
--   2. 【UPDATE customers 没有守卫】@keep_id 为 NULL 时它照样执行了。
--      → 整段逻辑移进存储过程，保留账号找不到就 SIGNAL 中止，
--        在 START TRANSACTION 之前，一行都不会动。
--
-- ⚠️ 执行前备份：
--   mysqldump -u root -p --single-transaction --routines --triggers \
--     yecai_db > ~/yecai_db_before_userreset_$(date +%F_%H%M).sql
-- =====================================================================


-- ---------------------------------------------------------------------
-- 第一步：确认现状（只读）
-- ---------------------------------------------------------------------
SELECT u.id, u.username, u.full_name, r.code AS 角色, u.is_active AS 启用,
       u.last_login_at AS 最后登录,
       (SELECT COUNT(*) FROM customers c WHERE c.owner_user_id = u.id) AS 名下客户
FROM users u
LEFT JOIN roles r ON r.id = u.role_id
ORDER BY r.code, u.username;


-- ---------------------------------------------------------------------
-- 第二步：定义过程
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS _reset_users;

DELIMITER $$

CREATE PROCEDURE _reset_users(IN p_keep_username VARCHAR(50))
BEGIN
  DECLARE v_keep_id   INT DEFAULT NULL;
  DECLARE v_keep_role VARCHAR(50) DEFAULT NULL;

  -- 显式指定排序规则，避免 ERROR 1267
  SELECT u.id, r.code INTO v_keep_id, v_keep_role
    FROM users u
    LEFT JOIN roles r ON r.id = u.role_id
   WHERE u.username = p_keep_username COLLATE utf8mb4_unicode_ci
   LIMIT 1;

  -- 找不到就中止：此时还没开事务，数据库一行未动
  IF v_keep_id IS NULL THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = '保留账号不存在，脚本已中止，数据库未做任何改动';
  END IF;

  SELECT v_keep_id AS 保留用户ID, p_keep_username AS 保留账号, v_keep_role AS 角色,
         (SELECT COUNT(*) FROM users) - 1 AS 即将删除的账号数;

  -- 外键检查保持开启：若还有未预料到的表引用 users，
  -- 让它直接报错回滚，好过静默留下孤儿行。
  START TRANSACTION;

  -- 1. 客户归属置空（已执行过一次，重复执行无副作用）
  UPDATE customers
     SET owner_user_id = NULL, created_by_id = NULL
   WHERE owner_user_id IS NOT NULL OR created_by_id IS NOT NULL;

  -- 2. 仓库上的质检员 / 负责人
  UPDATE warehouses SET qc_user_id = NULL
   WHERE qc_user_id IS NOT NULL AND qc_user_id <> v_keep_id;
  UPDATE warehouses SET manager_user_id = NULL
   WHERE manager_user_id IS NOT NULL AND manager_user_id <> v_keep_id;

  -- 3. 收付款方式字典的创建人
  UPDATE payment_method_options SET created_by_id = NULL
   WHERE created_by_id IS NOT NULL AND created_by_id <> v_keep_id;

  -- 4. users 自引用（数据继承 / 数据接替人）
  UPDATE users SET inherited_from_user_id = NULL
   WHERE inherited_from_user_id IS NOT NULL AND inherited_from_user_id <> v_keep_id;
  UPDATE users SET data_successor_user_id = NULL
   WHERE data_successor_user_id IS NOT NULL AND data_successor_user_id <> v_keep_id;

  -- 5. 页面权限
  DELETE FROM user_module_permissions WHERE user_id <> v_keep_id;
  UPDATE user_module_permissions SET assigned_by_id = NULL
   WHERE assigned_by_id IS NOT NULL AND assigned_by_id <> v_keep_id;

  -- 6. 登录会话
  DELETE FROM user_sessions WHERE user_id <> v_keep_id;

  -- 7. 删除账号
  DELETE FROM users WHERE id <> v_keep_id;

  COMMIT;
END$$

DELIMITER ;


-- ---------------------------------------------------------------------
-- 第三步：执行
-- ⚠️ 括号里改成你要保留、并且知道密码的账号
-- ---------------------------------------------------------------------
CALL _reset_users('sysadmin01');

DROP PROCEDURE IF EXISTS _reset_users;


-- ---------------------------------------------------------------------
-- 第四步：验收
-- ---------------------------------------------------------------------
SELECT '执行后' AS 阶段,
       (SELECT COUNT(*) FROM users)                   AS 用户数_应为1,
       (SELECT GROUP_CONCAT(username) FROM users)     AS 剩余账号,
       (SELECT COUNT(*) FROM customers)               AS 客户数_应保持6253,
       (SELECT COUNT(owner_user_id) FROM customers)   AS 有负责人的客户_应为0,
       (SELECT COUNT(*) FROM user_module_permissions) AS 页面权限行数,
       (SELECT COUNT(*) FROM user_sessions)           AS 登录会话数,
       (SELECT COUNT(*) FROM roles)                   AS 角色_应保持10,
       (SELECT COUNT(*) FROM departments)             AS 部门_应保持21;

-- 孤儿检查：全部应为 0
SELECT 'customers.owner_user_id' AS 位置, COUNT(*) AS 孤儿数
  FROM customers c LEFT JOIN users u ON u.id = c.owner_user_id
 WHERE c.owner_user_id IS NOT NULL AND u.id IS NULL
UNION ALL
SELECT 'customers.created_by_id', COUNT(*)
  FROM customers c LEFT JOIN users u ON u.id = c.created_by_id
 WHERE c.created_by_id IS NOT NULL AND u.id IS NULL
UNION ALL
SELECT 'warehouses.qc_user_id', COUNT(*)
  FROM warehouses w LEFT JOIN users u ON u.id = w.qc_user_id
 WHERE w.qc_user_id IS NOT NULL AND u.id IS NULL
UNION ALL
SELECT 'warehouses.manager_user_id', COUNT(*)
  FROM warehouses w LEFT JOIN users u ON u.id = w.manager_user_id
 WHERE w.manager_user_id IS NOT NULL AND u.id IS NULL
UNION ALL
SELECT 'user_module_permissions.user_id', COUNT(*)
  FROM user_module_permissions p LEFT JOIN users u ON u.id = p.user_id
 WHERE u.id IS NULL
UNION ALL
SELECT 'payment_method_options.created_by_id', COUNT(*)
  FROM payment_method_options o LEFT JOIN users u ON u.id = o.created_by_id
 WHERE o.created_by_id IS NOT NULL AND u.id IS NULL;


-- =====================================================================
-- 执行完之后
--
-- 1. 【重启后端会自动补回 10 个默认账号，这是正常现象】
--    seed.py 写死了 11 个：sales01 / inventory01 / inventoryraw01 /
--    purchase01 / production01 / quality01 / finance01 / manager01 /
--    chairman01 / sysadmin01 / masterdata01
--    缺失即重建，密码取 .env 的 DEFAULT_USER_PASSWORD，is_active 强制为 true。
--    已存在的账号（sysadmin01）密码不会被覆盖。
--
-- 2. 【重启前务必改 .env 的 DEFAULT_USER_PASSWORD】
--    还是 ChangeMe123! 的话，重建出来的 chairman01、manager01 等
--    都是公开密码。改成强密码再重启。
--
-- 3. 【录入真实员工】
--    用 sysadmin01 登录 → 系统管理 → 用户管理，逐个新建。
--
-- 4. 【重新分配客户归属】
--    6253 个客户的销售负责人已全部置空，销售角色登录后看到的是空列表。
--    批量指派示例：
--      UPDATE customers SET owner_user_id =
--        (SELECT id FROM users WHERE username = 'xxx' COLLATE utf8mb4_unicode_ci)
--      WHERE id BETWEEN 1 AND 1000;
--    注意这里同样要加 COLLATE，否则又会撞上 ERROR 1267。
-- =====================================================================
