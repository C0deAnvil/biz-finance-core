"""Supplier catalog matrix aggregates relations, prices and latest quality results."""

from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.master_data import get_supplier_catalog_matrix
from models import (
    Base,
    Material,
    PackagingConfig,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    QualityInspection,
    QualityTask,
    Role,
    Supplier,
    SupplierCatalogItem,
    SupplierSample,
    User,
    Warehouse,
)


def test_supplier_catalog_matrix_contains_three_item_types_and_quality_report():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        role = Role(code="purchase", name="采购", home_route="/", navigation_json=[], scope_json=[])
        db.add(role)
        db.flush()
        user = User(username="matrix_user", full_name="矩阵测试", hashed_password="x", role_id=role.id, is_active=True)
        supplier = Supplier(code="MATRIX_SUP", name="矩阵供应商", contact_name="甲", phone="1", address="地址", status="enabled")
        product = Product(product_code="MATRIX_P", name="矩阵产品", specification="工业级", unit="kg")
        material = Material(material_code="MATRIX_M", name="矩阵原料", specification="99%", unit="kg")
        packaging = PackagingConfig(entity_type="global", entity_id=0, packaging_name="25kg 包装袋", kg_per_bag=25, is_active=True)
        warehouse = Warehouse(code="MATRIX_W", name="矩阵仓", warehouse_type="raw")
        db.add_all([user, supplier, product, material, packaging, warehouse])
        db.flush()
        db.add_all([
            SupplierCatalogItem(supplier_id=supplier.id, product_id=product.id, remark="稳定供货"),
            SupplierCatalogItem(supplier_id=supplier.id, material_id=material.id),
            SupplierCatalogItem(supplier_id=supplier.id, packaging_config_id=packaging.id),
        ])
        order = PurchaseOrder(
            order_no="CG-MATRIX", supplier_id=supplier.id, purchaser_user_id=user.id,
            operator_user_id=user.id, payment_type="prepaid", status="submitted", total_amount=Decimal("12.3450"),
        )
        db.add(order)
        db.flush()
        db.add(PurchaseOrderItem(
            purchase_order_id=order.id, product_id=product.id, quantity=Decimal("1"),
            unit_price=Decimal("12.3450"), total_amount=Decimal("12.3450"),
        ))
        task = QualityTask(
            quality_no="ZJY-MATRIX", batch_no="B1", source_category="supplier_sample",
            required_tests=1, status="completed", submitted_by_id=user.id, target_warehouse_id=warehouse.id,
        )
        db.add(task)
        db.flush()
        sample = SupplierSample(
            sample_no="YP-MATRIX", supplier_id=supplier.id, product_id=product.id,
            warehouse_id=warehouse.id, quantity=Decimal("1"), received_quantity=Decimal("1"),
            unit="kg", batch_no="B1", status="completed", requested_by_id=user.id, quality_task_id=task.id,
        )
        db.add(sample)
        db.flush()
        inspection = QualityInspection(
            quality_task_id=task.id, inspector_user_id=user.id, sequence_no=1,
            result_text="合格", grade="A", is_passed=True, inspected_quantity=Decimal("1"),
            report_summary="指标符合要求",
        )
        db.add(inspection)
        db.commit()

        payload = get_supplier_catalog_matrix(current_user=user, db=db)["data"]
        assert payload["summary"]["supplier_count"] == 1
        assert payload["summary"]["item_count"] == 3
        assert {item["item_type"] for item in payload["items"]} == {"product", "material", "packaging"}
        product_relation = next(row for row in payload["relations"] if row["item_type"] == "product")
        assert product_relation["quality_state"] == "qualified"
        assert product_relation["latest_price"] == 12.345
        assert product_relation["latest_report_sample"]["report_summary"] == "指标符合要求"
