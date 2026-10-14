-- 仅用于采购流程模拟；请在目标数据库确认后手动执行。
-- 可重复执行：复用同一测试供应商，只补充尚未关联的产品和原料。
-- 当前 supplier_catalog_items 只支持 product_id / material_id；包装采购不校验供应商名录。
-- 本脚本不创建真实银行账户，也不执行付款。

START TRANSACTION;

INSERT INTO suppliers (
    code, name, contact_name, phone, address, status, supplier_type, remark,
    created_at, updated_at
)
SELECT
    'TEST_ALL_ITEMS', '【测试】全品类模拟供应商', '测试联系人', '00000000000',
    '仅供系统采购流程测试，非真实供货地址', 'enabled', 'manufacturer_trader',
    '测试数据：可用于产品、原料、包装的采购流程模拟；禁止真实付款',
    NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM suppliers WHERE code = 'TEST_ALL_ITEMS'
);

SET @test_supplier_id := (
    SELECT id FROM suppliers
    WHERE code = 'TEST_ALL_ITEMS' AND name = '【测试】全品类模拟供应商'
    LIMIT 1
);

-- 复用已存在的测试供应商时，确保它处于可选状态。
UPDATE suppliers
SET status = 'enabled', updated_at = NOW()
WHERE id = @test_supplier_id;

INSERT INTO supplier_catalog_items (
    supplier_id, product_id, material_id, remark, created_at, updated_at
)
SELECT @test_supplier_id, p.id, NULL, '测试供货名录：产品', NOW(), NOW()
FROM products AS p
WHERE @test_supplier_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM supplier_catalog_items AS sci
      WHERE sci.supplier_id = @test_supplier_id AND sci.product_id = p.id
  );

INSERT INTO supplier_catalog_items (
    supplier_id, product_id, material_id, remark, created_at, updated_at
)
SELECT @test_supplier_id, NULL, m.id, '测试供货名录：原料', NOW(), NOW()
FROM materials AS m
WHERE @test_supplier_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM supplier_catalog_items AS sci
      WHERE sci.supplier_id = @test_supplier_id AND sci.material_id = m.id
  );

COMMIT;

SELECT s.id, s.code, s.name, s.status,
       COUNT(DISTINCT sci.product_id) AS product_count,
       COUNT(DISTINCT sci.material_id) AS material_count
FROM suppliers AS s
LEFT JOIN supplier_catalog_items AS sci ON sci.supplier_id = s.id
WHERE s.code = 'TEST_ALL_ITEMS'
GROUP BY s.id, s.code, s.name, s.status;
