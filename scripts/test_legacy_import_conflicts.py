"""仅使用内存 SQLite 的导入回归检查：python scripts/test_legacy_import_conflicts.py。"""
import os
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

# 在加载项目配置之前强制隔离；不会连接 .env 的业务数据库。
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import import_legacy_master_data as legacy
import repair_legacy_item_names as name_repair
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from models import Base, Customer, Supplier, Department, Warehouse, Product, Material, Inventory, InventoryBatch, BOM, BOMItem, Role, User


class ImportConflictTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.report = legacy.Report(skip_conflicts=True)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def count(self, model):
        return self.db.scalar(select(func.count()).select_from(model))

    def customer_rows(self):
        return [
            dict(btypeid="1", busercode="C1", bname="改名", TaxNumber=""),
            dict(btypeid="2", busercode="C2", bname="已有客户"),
            dict(btypeid="3", busercode="C3", bname="同税号", TaxNumber="91310000MA12345678"),
            dict(btypeid="4", busercode="C4", bname="新增客户"),
            dict(btypeid="5", busercode="C5", bname="新增客户"),
        ]

    def test_customer_code_name_credit_conflicts_and_same_batch(self):
        existing = Customer(code="C1", name="已有客户", contact_name="联系人", phone="原电话", address="原地址", remark="原备注", unified_social_credit_code="91310000MA12345678")
        self.db.add(existing)
        self.db.commit()
        rows = self.customer_rows()
        kinds = {r["btypeid"]: "customer" for r in rows}
        legacy.import_customers(self.db, rows, kinds, {"1"}, [], {}, self.report, True)
        self.db.commit()
        self.db.refresh(existing)
        self.assertEqual(self.count(Customer), 2)
        self.assertEqual((existing.name, existing.phone, existing.remark), ("已有客户", "原电话", "原备注"))
        self.assertEqual(existing.unified_social_credit_code, "91310000MA12345678")
        self.assertEqual(self.report.stats["客户"]["冲突跳过"], 4)
        self.assertEqual(self.count(legacy.CustomerTag), 0)
        legacy.import_customers(self.db, rows, kinds, set(), [], {}, legacy.Report(True), True)
        self.db.commit()
        self.assertEqual(self.count(Customer), 2)

    def test_preview_does_not_modify_database_and_detects_pending_duplicates(self):
        rows = self.customer_rows()[3:]
        legacy.import_customers(self.db, rows, {"4": "customer", "5": "customer"}, set(), [], {}, self.report, False)
        self.assertEqual(self.report.stats["客户"]["新增"], 1)
        self.assertEqual(self.report.stats["客户"]["冲突跳过"], 1)
        self.assertEqual(self.count(Customer), 0)
        self.assertFalse(self.db.new)
        self.assertFalse(self.db.dirty)

    def test_strict_export_validation(self):
        with TemporaryDirectory() as directory:
            source = Path(directory)
            with self.assertRaises(ValueError):
                legacy.load_json(source, "input.json", strict=True)
            for raw in ("", "broken json", "{}", "[1]"):
                (source / "input.json").write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    legacy.load_json(source, "input.json", strict=True)
            (source / "input.json").write_text("[]", encoding="utf-8")
            self.assertEqual(legacy.load_json(source, "input.json", strict=True), [])

    def test_original_update_mode_is_preserved(self):
        existing = Customer(code="C1", name="原名称", contact_name="", phone="", address="")
        self.db.add(existing)
        self.db.commit()
        legacy.import_customers(self.db, [dict(btypeid="1", busercode="C1", bname="新名称")], {"1": "customer"}, set(), [], {}, legacy.Report(), True)
        self.db.commit()
        self.assertEqual(existing.name, "新名称")
        self.assertEqual(self.count(Customer), 1)

    def test_supplier_conflicts_preserve_existing(self):
        existing = Supplier(code="S1", name="供应商", contact_name="", phone="原电话", address="", unified_social_credit_code="91310000MA12345678")
        self.db.add(existing)
        self.db.commit()
        rows = [dict(btypeid="1", busercode="S1", bname="改名"), dict(btypeid="2", busercode="S2", bname="供应商"), dict(btypeid="3", busercode="S3", bname="税号重复", TaxNumber="91310000MA12345678"), dict(btypeid="4", busercode="S4", bname="新供应商")]
        legacy.import_suppliers(self.db, rows, {str(i): "supplier" for i in range(1, 5)}, self.report, True)
        self.db.commit()
        self.assertEqual(self.count(Supplier), 2)
        self.assertEqual(existing.phone, "原电话")
        self.assertEqual(existing.name, "供应商")

    def masters(self):
        warehouse = Warehouse(code="W", name="成品仓", warehouse_type="finished")
        product = Product(product_code="P", name="产品", price=10, unit="kg")
        material = Material(material_code="M", name="原料", price=5, unit="kg")
        self.db.add_all([warehouse, product, material])
        self.db.commit()
        return warehouse, product, material

    def test_products_reuse_only_matching_identity_and_keep_price(self):
        _, product, _ = self.masters()
        rows = [dict(ptypeid="1", pusercode="P", pname="产品", LastBuyPrice=99), dict(ptypeid="2", pusercode="P", pname="另一产品", LastBuyPrice=88), dict(ptypeid="3", pusercode="NEW", pname="新产品", LastBuyPrice=7)]
        mapping, _ = legacy.import_products(self.db, rows, [], {}, self.report, True)
        self.db.commit()
        self.assertIs(mapping["1"], product)
        self.assertNotIn("2", mapping)
        self.assertEqual(product.price, 10)
        self.assertEqual(self.count(Product), 2)

    def test_item_import_prefers_full_name_and_falls_back_to_short_name(self):
        rows = [
            dict(ptypeid="1", pusercode="P1", pname="车用尿素", pfullname="车用尿素溶液（可兰素）"),
            dict(ptypeid="2", pusercode="M1", pname="原料简称", pfullname="原料完整名称"),
            dict(ptypeid="3", pusercode="P2", pname="仅有简称", pfullname="  "),
        ]
        products, materials = legacy.import_products(self.db, rows, [], {"2": "material"}, self.report, True)
        self.db.commit()
        self.assertEqual(products["1"].name, "车用尿素溶液（可兰素）")
        self.assertEqual(materials["2"].name, "原料完整名称")
        self.assertEqual(products["3"].name, "仅有简称")

    def test_name_repair_preview_apply_and_repeat(self):
        _, product, material = self.masters()
        rows = [dict(ptypeid="1", pusercode="P", pname="产品", pfullname="产品完整名称"),
                dict(ptypeid="2", pusercode="M", pname="原料", pfullname="原料完整名称")]
        changes, skipped = name_repair.plan_name_repairs(self.db, rows)
        self.assertEqual(len(changes), 2)
        self.assertEqual(skipped, [])
        self.assertEqual(product.name, "产品")
        self.assertFalse(self.db.dirty)
        original_ids = (product.id, material.id)
        name_repair.apply_name_repairs(self.db, changes)
        self.db.commit()
        self.db.refresh(product)
        self.db.refresh(material)
        self.assertEqual((product.name, material.name), ("产品完整名称", "原料完整名称"))
        self.assertEqual((product.price, material.price, product.id, material.id), (10, 5, *original_ids))
        self.assertEqual((self.count(Product), self.count(Material)), (1, 1))
        self.assertEqual(name_repair.plan_name_repairs(self.db, rows), ([], []))

    def test_name_repair_skips_custom_names_and_ambiguous_codes(self):
        _, product, material = self.masters()
        rows = [dict(ptypeid="1", pusercode="P", pname="不匹配简称", pfullname="新名称"),
                dict(ptypeid="2", pusercode="M", pname="原料", pfullname="全称甲"),
                dict(ptypeid="3", pusercode="M", pname="原料", pfullname="全称乙")]
        changes, skipped = name_repair.plan_name_repairs(self.db, rows)
        self.assertEqual(changes, [])
        self.assertEqual(len(skipped), 2)
        # 两个目标表中重复的编码也不自动选取。
        self.db.add(Material(material_code="P", name="产品"))
        self.db.commit()
        rows = [dict(ptypeid="1", pusercode="P", pname="产品", pfullname="产品全称")]
        self.assertEqual(len(name_repair.plan_name_repairs(self.db, rows)[1]), 1)

    def test_name_repair_rolls_back_when_target_changed(self):
        _, product, material = self.masters()
        rows = [dict(ptypeid="1", pusercode="P", pname="产品", pfullname="产品全称"),
                dict(ptypeid="2", pusercode="M", pname="原料", pfullname="原料全称")]
        changes, _ = name_repair.plan_name_repairs(self.db, rows)
        material.name = "人工改名"
        self.db.commit()
        with self.assertRaises(RuntimeError):
            name_repair.apply_name_repairs(self.db, changes)
        self.db.rollback()
        self.assertEqual(product.name, "产品")
        self.assertEqual(material.name, "人工改名")

    def test_department_parent_is_not_changed_for_existing_record(self):
        existing = Department(code="D", name="部门")
        self.db.add(existing)
        self.db.commit()
        rows = [dict(dtypeid="parent", dusercode="NEW", dname="父部门"), dict(dtypeid="child", dusercode="D", dname="部门", parid="parent")]
        legacy.import_departments(self.db, rows, self.report, True)
        self.db.commit()
        self.assertIsNone(existing.parent_id)
        self.assertEqual(self.count(Department), 2)

    def test_warehouse_conflict_does_not_map_different_name(self):
        self.masters()
        mapping = legacy.import_warehouses(self.db, [dict(ktypeid="1", kusercode="W", kname="成品仓"), dict(ktypeid="2", kusercode="W", kname="另一仓")], self.report, True)
        self.assertIn("1", mapping)
        self.assertNotIn("2", mapping)
        self.assertEqual(self.count(Warehouse), 1)

    def test_existing_employee_stays_unchanged(self):
        role = Role(code="sales", name="销售", home_route="/")
        self.db.add(role)
        self.db.flush()
        user = User(username="cphw_alice", full_name="Alice", hashed_password="unchanged", is_active=True, role_id=role.id)
        self.db.add(user)
        self.db.commit()
        mapping = legacy.import_employees(self.db, [dict(etypeid="1", ename="Alice", isStop=1)], {}, {"1": "sales"}, self.report, True)
        self.db.commit()
        self.assertIs(mapping["1"], user)
        self.assertTrue(user.is_active)
        self.assertEqual(user.hashed_password, "unchanged")

    def test_existing_bom_is_skipped_across_versions(self):
        _, product, material = self.masters()
        bom = BOM(product_id=product.id, version_no="original", notes="原配方")
        self.db.add(bom)
        self.db.flush()
        self.db.add(BOMItem(bom_id=bom.id, material_id=material.id, standard_quantity=2))
        self.db.commit()
        legacy.import_boms(self.db, [dict(VchCode="99", PtypeId="p")], [dict(VchCode="99", Ptypeid="m", Qty=9)], {"p": product}, {"m": material}, self.report, True)
        self.db.commit()
        self.assertEqual(bom.notes, "原配方")
        self.assertEqual(self.count(BOM), 1)
        self.assertEqual(self.db.scalar(select(BOMItem.standard_quantity)), 2)

    def test_incomplete_bom_is_skipped_entirely(self):
        _, product, material = self.masters()
        legacy.import_boms(self.db, [dict(VchCode="99", PtypeId="p")], [dict(VchCode="99", Ptypeid="m", Qty=2), dict(VchCode="99", Ptypeid="missing", Qty=3)], {"p": product}, {"m": material}, self.report, True)
        self.db.commit()
        self.assertEqual(self.count(BOM), 0)

    def test_inventory_skip_also_blocks_new_batches(self):
        warehouse, product, _ = self.masters()
        inventory = Inventory(warehouse_id=warehouse.id, product_id=product.id, quantity=100, reserved_quantity=7, avg_unit_cost=3)
        self.db.add(inventory)
        self.db.commit()
        stock = [dict(KtypeId="w", PtypeId="p", qty=999, avg_unit_cost=99)]
        batches = [dict(KtypeId="w", PtypeId="p", GoodsBatchID="new", Qty=999)]
        legacy.import_inventory(self.db, stock, batches, {"w": warehouse}, {"p": product}, {}, self.report, True)
        self.db.commit()
        self.assertEqual((inventory.quantity, inventory.reserved_quantity, inventory.avg_unit_cost), (100, 7, 3))
        self.assertEqual(self.count(InventoryBatch), 0)
        legacy.import_inventory(self.db, [], batches, {"w": warehouse}, {"p": product}, {}, legacy.Report(True), True)
        self.assertEqual(self.count(InventoryBatch), 0)

    def test_new_inventory_and_batches_are_idempotent(self):
        warehouse, product, _ = self.masters()
        stock = [dict(KtypeId="w", PtypeId="p", qty=12, avg_unit_cost=4)]
        batches = [dict(KtypeId="w", PtypeId="p", GoodsBatchID="b", Qty=12, Price=4)] * 2
        for _ in range(2):
            legacy.import_inventory(self.db, stock, batches, {"w": warehouse}, {"p": product}, {}, legacy.Report(True), True)
            self.db.commit()
        self.assertEqual(self.count(Inventory), 1)
        self.assertEqual(self.count(InventoryBatch), 1)
        self.assertEqual(self.db.scalar(select(Inventory.quantity)), Decimal("12"))


if __name__ == "__main__":
    unittest.main()
