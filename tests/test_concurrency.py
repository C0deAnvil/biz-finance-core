"""并发一致性测试:验证行级锁防止超卖。

策略:并发扣减同一库存,断言最终库存 >= 0 且总扣减量正确。
使用真实数据库事务模拟(不经过 HTTP,直接调服务层),避免 TestClient 串行化。
"""

import threading
from decimal import Decimal

from database import SessionLocal
from models import Inventory, Product, Warehouse
from services.core import apply_inventory_change


def _setup_test_inventory():
    db = SessionLocal()
    try:
        # 找成品仓,无商品则自建
        warehouse = db.query(Warehouse).filter(Warehouse.warehouse_type == "finished").first()
        assert warehouse, "测试需要成品仓"
        product = db.query(Product).first()
        if product is None:
            product = Product(
                product_code="TEST-PROD-001",
                name="并发测试商品",
                fulfillment_mode="trading",
                unit="kg",
                is_active=True,
            )
            db.add(product)
            db.flush()
        # 清掉旧测试库存,重建 100
        inv = (
            db.query(Inventory)
            .filter(
                Inventory.warehouse_id == warehouse.id,
                Inventory.product_id == product.id,
                Inventory.material_id.is_(None),
            )
            .first()
        )
        if inv is None:
            inv = Inventory(
                warehouse_id=warehouse.id,
                product_id=product.id,
                material_id=None,
                quantity=Decimal("100"),
                reserved_quantity=Decimal("0"),
                avg_unit_cost=Decimal("10"),
            )
            db.add(inv)
        else:
            inv.quantity = Decimal("100")
            inv.reserved_quantity = Decimal("0")
        db.commit()
        return warehouse.id, product.id
    finally:
        db.close()


def test_concurrent_deduction_no_oversell():
    warehouse_id, product_id = _setup_test_inventory()
    # 10 个线程各扣 15(总 150 > 100),应只有部分成功,库存不为负
    errors = []
    successes = []

    def worker():
        db = SessionLocal()
        try:
            apply_inventory_change(
                db,
                warehouse_id=warehouse_id,
                product_id=product_id,
                material_id=None,
                quantity=Decimal("-15"),
                unit_cost=Decimal("10"),
                transaction_type="test_concurrent",
                created_by_id=None,
            )
            db.commit()
            successes.append(1)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append(str(exc))
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db = SessionLocal()
    try:
        inv = (
            db.query(Inventory)
            .filter(
                Inventory.warehouse_id == warehouse_id,
                Inventory.product_id == product_id,
                Inventory.material_id.is_(None),
            )
            .first()
        )
        final_qty = Decimal(inv.quantity or 0)
        # 库存不能为负
        assert final_qty >= 0, f"超卖!库存为负: {final_qty}"
        # 成功扣减数 * 15 + 最终库存 = 100
        assert len(successes) * 15 + final_qty == 100, f"数量不一致: 成功{len(successes)} 库存{final_qty}"
    finally:
        db.close()