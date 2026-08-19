from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal
from models import (
    BOM,
    BOMItem,
    CashFlowRecord,
    Customer,
    CustomerAIResult,
    CustomerComplaint,
    CustomerInquiry,
    FulfillmentDemand,
    Inventory,
    InventoryBatch,
    InventoryDispatchQueue,
    InventoryOperationRequest,
    InventoryTransaction,
    LogisticsRecord,
    Material,
    Notification,
    NotificationRecipient,
    PackagingConfig,
    PayableRecord,
    Product,
    ProductionMaterialUsage,
    ProductionPlan,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderApproval,
    PurchaseOrderItem,
    PurchasePaymentRequest,
    PurchaseReceipt,
    PurchaseReceiptItem,
    PurchaseReturn,
    QualityCostRecord,
    QualityDisposition,
    QualityInboundApplication,
    QualityInspection,
    QualityTask,
    ReceivablePlan,
    ReceivableRecord,
    RefundRecord,
    ReportSnapshot,
    Role,
    SalesOrder,
    SalesOrderApproval,
    SalesOrderItem,
    SalesReceiptRequest,
    SalesReturn,
    SalesReturnItem,
    SequenceCounter,
    StockTakeItem,
    StockTakeTask,
    Supplier,
    SupplierAIResult,
    SupplierCatalogItem,
    TodoItem,
    User,
    Warehouse,
)


BACKUP_DIR = BASE_DIR / ".run-logs" / "db-backups"


def dec(value: int | float | str) -> Decimal:
    return Decimal(str(value))


def dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def maybe_backup_sqlite() -> None:
    db_path = BASE_DIR / "yecai.db"
    if not db_path.exists() or db_path.stat().st_size <= 0:
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(db_path, BACKUP_DIR / f"yecai_{timestamp}.db")


def first_user(db: Session, role_code: str) -> User:
    user = db.execute(
        select(User).join(Role, User.role_id == Role.id).where(Role.code == role_code, User.is_active.is_(True)).order_by(User.id)
    ).scalars().first()
    if user is None:
        raise RuntimeError(f"缺少角色账号：{role_code}")
    return user


def clear_business_data(db: Session) -> None:
    ordered_models = [
        ReportSnapshot,
        StockTakeItem,
        StockTakeTask,
        LogisticsRecord,
        CashFlowRecord,
        RefundRecord,
        PayableRecord,
        ReceivableRecord,
        ReceivablePlan,
        SalesReceiptRequest,
        QualityCostRecord,
        QualityDisposition,
        QualityInboundApplication,
        QualityInspection,
        QualityTask,
        ProductionPlan,
        ProductionMaterialUsage,
        ProductionTask,
        BOMItem,
        BOM,
        PurchasePaymentRequest,
        PurchaseReturn,
        PurchaseReceiptItem,
        PurchaseReceipt,
        PurchaseOrderApproval,
        PurchaseOrderItem,
        PurchaseOrder,
        FulfillmentDemand,
        SalesReturnItem,
        SalesReturn,
        InventoryDispatchQueue,
        SalesOrderApproval,
        SalesOrderItem,
        SalesOrder,
        InventoryOperationRequest,
        InventoryTransaction,
        InventoryBatch,
        Inventory,
        CustomerAIResult,
        CustomerInquiry,
        CustomerComplaint,
        SupplierAIResult,
        SupplierCatalogItem,
        PackagingConfig,
        Product,
        Material,
        Customer,
        Supplier,
        Warehouse,
        TodoItem,
        NotificationRecipient,
        Notification,
    ]
    db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    for model in ordered_models:
        db.execute(delete(model))
    db.execute(delete(SequenceCounter))
    db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    db.flush()


def add_warehouses(db: Session) -> dict[str, Warehouse]:
    rows = [
        ("WH-FG", "成品中心仓", "A厂区1号库", "finished"),
        ("WH-RM", "原料中心仓", "A厂区2号库", "raw"),
        ("WH-QC", "待检暂存仓", "A厂区质检暂存区", "quality"),
    ]
    result: dict[str, Warehouse] = {}
    for code, name, location, warehouse_type in rows:
        row = Warehouse(code=code, name=name, location=location, warehouse_type=warehouse_type)
        db.add(row)
        db.flush()
        result[code] = row
    return result


PRODUCT_SOURCE = [
    ("SALT-01", "工业氯化钠", "无机盐", "纯度≥95%，50kg/袋", "0.55", "0.38", "production"),
    ("SALT-02", "食用氯化钠", "食品添加剂", "纯度≥99.1%，加碘，25kg/袋", "1.20", "0.85", "production"),
    ("SALT-03", "医药氯化钠", "医药原料", "纯度≥99.9%，注射级，25kg/桶", "3.50", "2.90", "production"),
    ("SALT-04", "软水专用盐", "水处理", "球形颗粒，10kg/袋", "1.35", "0.95", "trade"),
    ("SALT-05", "融雪剂（环保型）", "环保化工", "添加少量氯化钙，50kg/袋", "0.78", "0.45", "production"),
    ("SALT-06", "日晒海盐", "食品级", "颗粒度0.5-2mm，50kg/袋", "1.45", "1.05", "trade"),
    ("SALT-07", "分析纯氯化钠", "生化试剂", "AR级，500g/瓶", "7.80", "5.20", "trade"),
    ("SALT-08", "畜牧营养盐", "饲料添加剂", "含微量矿物元素，25kg/袋", "1.10", "0.75", "production"),
    ("SALT-09", "重结晶工业盐", "化工中间体", "高纯≥99%，氯碱行业专用", "0.92", "0.60", "production"),
    ("SALT-10", "雪盐（调味用）", "食品", "添加亚铁氰化钾，食用细盐", "1.85", "1.30", "trade"),
    ("SALT-11", "印染助剂盐", "纺织助剂", "颗粒均匀，高流动性", "1.05", "0.70", "production"),
    ("SALT-12", "再生软水盐", "环保", "10kg/包，用于洗碗机/软水机", "1.55", "1.10", "trade"),
    ("SALT-13", "金属热处理盐", "金属加工", "高温级，含杂质极少", "3.10", "2.20", "production"),
    ("SALT-14", "皮革防腐盐", "轻工", "粗粒，含水量<3%", "0.82", "0.50", "production"),
    ("SALT-15", "牙膏用氯化钠", "日化", "超细微，食品级标准", "2.45", "1.80", "trade"),
    ("SALT-16", "钻井液用盐", "油田助剂", "抗温120度，API标准", "3.20", "2.40", "production"),
]


MATERIAL_SOURCE = [
    ("RM-001", "卤水原液", "氯化钠含量≥285g/L，槽车", "kg", "0.18"),
    ("RM-002", "粗制原盐", "NaCl≥92%，吨袋", "kg", "0.28"),
    ("RM-003", "食品级精制盐基料", "NaCl≥99.1%，25kg/袋", "kg", "0.76"),
    ("RM-004", "药用高纯盐基料", "NaCl≥99.9%，25kg/桶", "kg", "2.65"),
    ("RM-005", "无水氯化钙", "含量≥94%，25kg/袋", "kg", "0.62"),
    ("RM-006", "碘酸钾添加剂", "食品添加剂级，500g/瓶", "kg", "18.00"),
    ("RM-007", "抗结剂", "食品级，25kg/袋", "kg", "5.60"),
    ("RM-008", "微量矿物元素预混料", "畜牧营养盐专用", "kg", "3.20"),
    ("RM-009", "高纯水", "电导率≤5μS/cm", "kg", "0.12"),
    ("RM-010", "包装袋", "25kg/50kg复合编织袋", "个", "0.48"),
]


CUSTOMER_ROWS = [
    ("C1001", "滨海市政环卫服务有限公司", "王志强", "13851001001", "江苏省盐城市亭湖区开放大道88号", "A"),
    ("C1002", "鲁东食品配料有限公司", "刘晓敏", "13953002002", "山东省潍坊市寒亭区食品工业园12号", "A"),
    ("C1003", "华北医药辅料科技有限公司", "赵建军", "13731003003", "河北省石家庄市高新区药谷路19号", "A"),
    ("C1004", "苏南水处理设备有限公司", "陈雅丽", "13661004004", "江苏省无锡市新吴区梅村工业园", "B"),
    ("C1005", "青岛纺织印染助剂厂", "孙海涛", "13562005005", "山东省青岛市即墨区蓝村街道", "B"),
    ("C1006", "中原畜牧饲料集团", "马志远", "13839006006", "河南省郑州市航空港区饲料产业园", "B"),
    ("C1007", "渤海油田化学品有限公司", "张明", "13933007007", "天津市滨海新区海洋高新区", "A"),
    ("C1008", "宁波金属热处理材料有限公司", "吴佳", "13777008008", "浙江省宁波市北仑区装备园", "B"),
    ("C1009", "川南皮革轻工材料有限公司", "周鹏", "13683009009", "四川省自贡市沿滩区轻工园", "C"),
    ("C1010", "杭州日化原料贸易有限公司", "郑丽", "13588001010", "浙江省杭州市萧山区建设三路", "B"),
    ("C1011", "甘肃路桥冬季养护中心", "李国庆", "13893001111", "甘肃省兰州市七里河区西津西路", "A"),
    ("C1012", "厦门净水科技有限公司", "林怡", "13959001212", "福建省厦门市集美区软件园三期", "B"),
    ("C1013", "安徽实验耗材供应链有限公司", "许峰", "13755001313", "安徽省合肥市高新区科学大道", "C"),
    ("C1014", "新疆钻采工程服务有限公司", "阿力木", "13699001414", "新疆克拉玛依市白碱滩区", "A"),
]


SUPPLIER_ROWS = [
    ("S001", "江苏海晶盐化有限公司", "顾建平", "13862010001", "江苏省连云港市徐圩新区盐化产业园"),
    ("S002", "山东鲁北原盐供应有限公司", "韩晓东", "13954010002", "山东省滨州市无棣县盐业园区"),
    ("S003", "河北冀盐精制盐有限公司", "郭志敏", "13785010003", "河北省沧州市黄骅港开发区"),
    ("S004", "浙江东海包装材料有限公司", "沈佩", "13657010004", "浙江省嘉兴市海盐县百步镇"),
    ("S005", "安徽华源氯化钙科技有限公司", "程磊", "13856010005", "安徽省滁州市定远盐化工业园"),
    ("S006", "天津海河食品添加剂有限公司", "郑晨", "13920010006", "天津市武清区京津科技谷"),
    ("S007", "河南牧源矿物饲料有限公司", "范立军", "13783010007", "河南省新乡市原阳县产业集聚区"),
    ("S008", "江西赣江化工辅料有限公司", "罗文", "13679010008", "江西省九江市湖口高新技术产业园"),
    ("S009", "辽宁辽河油田助剂有限公司", "于海峰", "13841010009", "辽宁省盘锦市兴隆台区石化园"),
    ("S010", "广东粤净水处理材料有限公司", "梁思远", "13926010010", "广东省佛山市顺德区容桂工业区"),
]


def add_master_data(db: Session, users: dict[str, User]) -> tuple[dict[str, Customer], dict[str, Supplier], dict[str, Product], dict[str, Material]]:
    customers: dict[str, Customer] = {}
    for row in CUSTOMER_ROWS:
        customer = Customer(code=row[0], name=row[1], contact_name=row[2], phone=row[3], address=row[4], star_level=row[5], created_by_id=users["sales"].id)
        db.add(customer)
        db.flush()
        customers[row[0]] = customer

    suppliers: dict[str, Supplier] = {}
    for row in SUPPLIER_ROWS:
        supplier = Supplier(code=row[0], name=row[1], contact_name=row[2], phone=row[3], address=row[4], status="enabled", created_by_id=users["purchase"].id)
        db.add(supplier)
        db.flush()
        suppliers[row[0]] = supplier

    products: dict[str, Product] = {}
    for code, name, category, specification, price, cost, mode in PRODUCT_SOURCE:
        product = Product(
            product_code=code,
            name=name,
            price=dec(price),
            specification=f"{category}；{specification}",
            unit="kg",
            safety_stock_qty=dec(1200 if mode == "trade" else 2500),
            purchase_threshold_qty=dec(8000 if mode == "trade" else 12000),
            fulfillment_mode=mode,
            is_active=True,
        )
        db.add(product)
        db.flush()
        products[code] = product
        kg_per_bag = Decimal("25") if "25kg" in specification else Decimal("10") if "10kg" in specification else Decimal("50")
        db.add(PackagingConfig(entity_type="product", entity_id=product.id, packaging_name=specification.split("，")[-1], kg_per_bag=kg_per_bag))

    materials: dict[str, Material] = {}
    for code, name, specification, unit, price in MATERIAL_SOURCE:
        material = Material(
            material_code=code,
            name=name,
            price=dec(price),
            specification=specification,
            unit=unit,
            safety_stock_qty=dec(5000 if unit == "kg" else 1000),
            purchase_threshold_qty=dec(18000 if unit == "kg" else 5000),
            is_active=True,
        )
        db.add(material)
        db.flush()
        materials[code] = material
        if unit == "kg":
            db.add(PackagingConfig(entity_type="material", entity_id=material.id, packaging_name="25kg/袋", kg_per_bag=dec(25)))

    catalog = {
        "S001": ["RM-001", "RM-002", "SALT-01", "SALT-09"],
        "S002": ["RM-002", "SALT-01", "SALT-06"],
        "S003": ["RM-003", "RM-004", "SALT-02", "SALT-03"],
        "S004": ["RM-010"],
        "S005": ["RM-005", "SALT-05"],
        "S006": ["RM-006", "RM-007", "SALT-10", "SALT-15"],
        "S007": ["RM-008", "SALT-08"],
        "S008": ["SALT-11", "SALT-13", "SALT-14"],
        "S009": ["SALT-16"],
        "S010": ["SALT-04", "SALT-12", "RM-009"],
    }
    for supplier_code, item_codes in catalog.items():
        for item_code in item_codes:
            db.add(
                SupplierCatalogItem(
                    supplier_id=suppliers[supplier_code].id,
                    product_id=products[item_code].id if item_code.startswith("SALT") else None,
                    material_id=materials[item_code].id if item_code.startswith("RM") else None,
                    remark="标准供货目录",
                )
            )

    return customers, suppliers, products, materials


def add_inventory(db: Session, warehouses: dict[str, Warehouse], products: dict[str, Product], materials: dict[str, Material], users: dict[str, User]) -> None:
    product_qty = {
        "SALT-01": 38000,
        "SALT-02": 9200,
        "SALT-03": 1600,
        "SALT-04": 4300,
        "SALT-05": 18500,
        "SALT-06": 7200,
        "SALT-07": 680,
        "SALT-08": 11000,
        "SALT-09": 15400,
        "SALT-10": 5200,
        "SALT-11": 8600,
        "SALT-12": 3900,
        "SALT-13": 2100,
        "SALT-14": 7600,
        "SALT-15": 1800,
        "SALT-16": 2600,
    }
    for code, qty in product_qty.items():
        product = products[code]
        inv = Inventory(warehouse_id=warehouses["WH-FG"].id, product_id=product.id, quantity=dec(qty), reserved_quantity=0, avg_unit_cost=dec(product.price) * dec("0.72"))
        db.add(inv)
        db.flush()
        db.add(InventoryBatch(warehouse_id=warehouses["WH-FG"].id, product_id=product.id, batch_no=f"FG-2026-{code[-2:]}", quantity=dec(qty), unit_cost=inv.avg_unit_cost, quality_grade="合格", source_type="opening"))
        db.add(InventoryTransaction(transaction_no=f"KC-FG-{code[-2:]}", transaction_type="opening", warehouse_id=warehouses["WH-FG"].id, product_id=product.id, batch_no=f"FG-2026-{code[-2:]}", quantity=dec(qty), unit_cost=inv.avg_unit_cost, total_cost=dec(qty) * inv.avg_unit_cost, related_type="opening", related_id=0, created_by_id=users["inventory"].id))

    material_qty = {
        "RM-001": 68000,
        "RM-002": 92000,
        "RM-003": 24000,
        "RM-004": 7600,
        "RM-005": 15500,
        "RM-006": 820,
        "RM-007": 1700,
        "RM-008": 5600,
        "RM-009": 45000,
        "RM-010": 30000,
    }
    for code, qty in material_qty.items():
        material = materials[code]
        inv = Inventory(warehouse_id=warehouses["WH-RM"].id, material_id=material.id, quantity=dec(qty), reserved_quantity=0, avg_unit_cost=dec(material.price))
        db.add(inv)
        db.flush()
        db.add(InventoryBatch(warehouse_id=warehouses["WH-RM"].id, material_id=material.id, batch_no=f"RM-2026-{code[-3:]}", quantity=dec(qty), unit_cost=dec(material.price), quality_grade="合格", source_type="opening"))
        db.add(InventoryTransaction(transaction_no=f"KC-RM-{code[-3:]}", transaction_type="opening", warehouse_id=warehouses["WH-RM"].id, material_id=material.id, batch_no=f"RM-2026-{code[-3:]}", quantity=dec(qty), unit_cost=dec(material.price), total_cost=dec(qty) * dec(material.price), related_type="opening", related_id=0, created_by_id=users["inventory"].id))


def add_boms(db: Session, products: dict[str, Product], materials: dict[str, Material]) -> None:
    recipes = {
        "SALT-01": [("RM-002", "1.03"), ("RM-010", "0.02")],
        "SALT-02": [("RM-003", "1.00"), ("RM-006", "0.00008"), ("RM-007", "0.001"), ("RM-010", "0.04")],
        "SALT-03": [("RM-004", "1.00"), ("RM-009", "0.08"), ("RM-010", "0.04")],
        "SALT-05": [("RM-002", "0.86"), ("RM-005", "0.14"), ("RM-010", "0.02")],
        "SALT-08": [("RM-002", "0.78"), ("RM-008", "0.22"), ("RM-010", "0.04")],
        "SALT-09": [("RM-001", "1.12"), ("RM-009", "0.12"), ("RM-010", "0.02")],
        "SALT-11": [("RM-002", "0.94"), ("RM-009", "0.06"), ("RM-010", "0.02")],
        "SALT-13": [("RM-004", "0.82"), ("RM-002", "0.18"), ("RM-010", "0.02")],
        "SALT-14": [("RM-002", "1.00"), ("RM-010", "0.02")],
        "SALT-16": [("RM-002", "0.72"), ("RM-005", "0.18"), ("RM-009", "0.10"), ("RM-010", "0.02")],
    }
    for product_code, items in recipes.items():
        bom = BOM(product_id=products[product_code].id, version_no="v1.0", is_active=True, notes="验收样例BOM")
        db.add(bom)
        db.flush()
        for material_code, qty in items:
            db.add(BOMItem(bom_id=bom.id, material_id=materials[material_code].id, standard_quantity=dec(qty)))


def add_sales_data(db: Session, customers: dict[str, Customer], products: dict[str, Product], warehouses: dict[str, Warehouse], users: dict[str, User]) -> list[SalesOrder]:
    rows = [
        ("XS202601001", "C1001", "SALT-05", "2026-01-10", 50000, "0.78", "先付定金后付尾款", "completed", 18000),
        ("XS202601002", "C1002", "SALT-02", "2026-01-15", 8000, "1.20", "全款发货", "completed", 0),
        ("XS202601003", "C1003", "SALT-03", "2026-01-20", 1200, "3.50", "先付定金后付尾款", "shipped", 1500),
        ("XS202602001", "C1004", "SALT-04", "2026-02-01", 3200, "1.35", "货到付款/分期付款", "pending_shipment", 0),
        ("XS202602002", "C1005", "SALT-11", "2026-02-12", 9000, "1.05", "先付定金后付尾款", "pending_shipment", 3000),
        ("XS202602003", "C1007", "SALT-16", "2026-02-21", 2600, "3.20", "全款发货", "shipped", 0),
        ("XS202603001", "C1008", "SALT-13", "2026-03-05", 1600, "3.10", "货到付款/分期付款", "pending_shipment", 0),
        ("XS202603002", "C1011", "SALT-05", "2026-03-18", 42000, "0.76", "先付定金后付尾款", "pending_confirmation", 12000),
        ("XS202604001", "C1014", "SALT-16", "2026-04-07", 1800, "3.25", "全款发货", "pending_confirmation", 0),
        ("XS202604002", "C1006", "SALT-08", "2026-04-16", 6000, "1.12", "货到付款/分期付款", "completed", 0),
    ]
    orders: list[SalesOrder] = []
    for order_no, customer_code, product_code, order_date, qty, price, payment_type, status, deposit in rows:
        product = products[product_code]
        total = dec(qty) * dec(price)
        cost = dec(qty) * dec(product.price) * dec("0.72")
        shipped_qty = dec(qty) if status in {"shipped", "completed"} else dec(qty if payment_type == "货到付款/分期付款" and status == "pending_shipment" else 0)
        order = SalesOrder(
            order_no=order_no,
            split_phase=1,
            split_group_no=order_no,
            customer_id=customers[customer_code].id,
            sales_user_id=users["sales"].id,
            consignee=customers[customer_code].contact_name,
            consignee_phone=customers[customer_code].phone,
            delivery_address=customers[customer_code].address,
            payment_type=payment_type,
            status=status,
            total_amount=total,
            deposit_amount=dec(deposit),
            transport_fee=dec("260"),
            packaging_fee=dec("180"),
            cost_amount=cost,
            profit_amount=total - cost,
            approved_by_id=users["chairman"].id if status != "pending_confirmation" else None,
            approved_at=dt(order_date) + timedelta(days=1) if status != "pending_confirmation" else None,
            shipped_at=dt(order_date) + timedelta(days=4) if status in {"shipped", "completed"} else None,
            completed_at=dt(order_date) + timedelta(days=8) if status == "completed" else None,
        )
        db.add(order)
        db.flush()
        db.add(
            SalesOrderItem(
                sales_order_id=order.id,
                product_id=product.id,
                quantity=dec(qty),
                unit_price=dec(price),
                total_amount=total,
                kg_per_bag=dec(50),
                bag_count=dec(qty) / dec(50),
                display_specification="50kg/袋",
                remark="标准销售业务样例",
                shipped_quantity=shipped_qty,
            )
        )
        db.add(
            InventoryDispatchQueue(
                sales_order_id=order.id,
                placement_status="completed" if status in {"pending_shipment", "shipped", "completed"} else "pending",
                shipment_status="completed" if status in {"shipped", "completed"} else "pending" if status == "pending_shipment" else "waiting_approval",
                placement_marked_by_id=users["inventory"].id if status != "pending_confirmation" else None,
                placement_marked_at=dt(order_date) + timedelta(days=2) if status != "pending_confirmation" else None,
                shipment_marked_by_id=users["inventory"].id if status in {"shipped", "completed"} else None,
                shipment_marked_at=dt(order_date) + timedelta(days=4) if status in {"shipped", "completed"} else None,
            )
        )
        if status != "pending_confirmation":
            db.add(SalesOrderApproval(sales_order_id=order.id, approver_user_id=users["chairman"].id, decision="approved", voucher_amount=dec(deposit) if deposit else total if payment_type == "全款发货" else 0, note="业务样例：收款条件已确认", approved_at=dt(order_date) + timedelta(days=1)))
        received = total if status == "completed" or payment_type == "全款发货" and status == "shipped" else dec(deposit)
        if received > 0:
            request = SalesReceiptRequest(
                request_no=f"SK{order_no[-6:]}",
                sales_order_id=order.id,
                requested_by_id=users["sales"].id,
                approved_by_id=users["chairman"].id,
                requested_amount=received,
                actual_amount=received,
                payment_stage="deposit" if deposit and received < total else "pre_shipment_full",
                status="approved",
                remark="销售侧收款确认样例",
                approval_remark="董事长确认到账",
                approved_at=dt(order_date) + timedelta(days=1),
            )
            db.add(request)
            db.flush()
            db.add(ReceivableRecord(sales_order_id=order.id, sales_receipt_request_id=request.id, amount=received, payment_stage=request.payment_stage, received_at=dt(order_date) + timedelta(days=1), received_by_id=users["chairman"].id, remark="业务样例：客户回款"))
            db.add(CashFlowRecord(record_no=f"ZJ-IN-{order_no[-6:]}", flow_type="incoming", business_type="sales_order", business_id=order.id, related_no=order.order_no, amount=received, happened_at=dt(order_date) + timedelta(days=1), remark="客户订单回款"))
        db.add(LogisticsRecord(logistics_no=f"WL-S-{order_no[-6:]}", module="sales", business_type="sales_order", business_id=order.id, related_no=order.order_no, warehouse_id=warehouses["WH-FG"].id, logistics_mode="road", carrier_name="盐城安捷物流有限公司", driver_name="周师傅", driver_phone="13800006666", vehicle_no=f"苏J{order_no[-4:]}", tracking_no=f"AJ{order_no[-6:]}", dispatched_at=dt(order_date) + timedelta(days=4) if status in {"shipped", "completed"} else None, arrived_at=dt(order_date) + timedelta(days=6) if status == "completed" else None, remark="标准销售物流样例", created_by_id=users["inventory"].id))
        orders.append(order)
    return orders


def add_purchase_data(db: Session, suppliers: dict[str, Supplier], products: dict[str, Product], materials: dict[str, Material], warehouses: dict[str, Warehouse], users: dict[str, User]) -> list[PurchaseOrder]:
    rows = [
        ("CG202601001", "S001", "RM-002", "material", "2026-01-06", 60000, "0.28", "先到货后付款", "fully_received", True, "approved"),
        ("CG202601002", "S003", "RM-004", "material", "2026-01-18", 6500, "2.65", "先付款后到货", "fully_received", True, "approved"),
        ("CG202602001", "S005", "RM-005", "material", "2026-02-03", 12000, "0.62", "先到货后付款", "partial_received", False, "not_required"),
        ("CG202602002", "S004", "RM-010", "material", "2026-02-15", 25000, "0.48", "先到货后付款", "fully_received", False, "not_required"),
        ("CG202603001", "S010", "SALT-04", "product", "2026-03-02", 3600, "1.02", "先付款后到货", "fully_received", False, "not_required"),
        ("CG202603002", "S009", "SALT-16", "product", "2026-03-20", 1800, "2.42", "先到货后付款", "purchasing", False, "not_required"),
        ("CG202604001", "S006", "RM-006", "material", "2026-04-05", 500, "18.00", "先付款后到货", "fully_received", True, "approved"),
    ]
    orders: list[PurchaseOrder] = []
    for order_no, supplier_code, item_code, item_type, order_date, qty, price, payment_type, status, requires_quality, quality_status in rows:
        total = dec(qty) * dec(price)
        order = PurchaseOrder(
            order_no=order_no,
            supplier_id=suppliers[supplier_code].id,
            purchaser_user_id=users["purchase"].id,
            payment_type=payment_type,
            status=status,
            total_amount=total,
            threshold_triggered=total >= dec("10000"),
            approved_by_id=users["manager"].id if total >= dec("10000") else None,
            approved_at=dt(order_date) + timedelta(days=1) if total >= dec("10000") else None,
            contract_received=True,
        )
        db.add(order)
        db.flush()
        product_id = products[item_code].id if item_type == "product" else None
        material_id = materials[item_code].id if item_type == "material" else None
        received_qty = dec(qty) if status == "fully_received" else dec(qty) * dec("0.45") if status == "partial_received" else dec(0)
        item = PurchaseOrderItem(
            purchase_order_id=order.id,
            product_id=product_id,
            material_id=material_id,
            quantity=dec(qty),
            unit_price=dec(price),
            total_amount=total,
            kg_per_bag=dec(25),
            bag_count=dec(qty) / dec(25),
            display_specification="25kg/袋" if item_type == "material" else "50kg/袋",
            received_quantity=received_qty,
            returned_quantity=0,
        )
        db.add(item)
        db.flush()
        if order.threshold_triggered:
            db.add(PurchaseOrderApproval(purchase_order_id=order.id, approver_user_id=users["manager"].id, decision="approved", note="业务样例：采购金额达到阈值，经理审批通过", approved_at=dt(order_date) + timedelta(days=1)))
        if received_qty > 0:
            receipt = PurchaseReceipt(receipt_no=f"DH{order_no[-6:]}", purchase_order_id=order.id, warehouse_id=warehouses["WH-RM" if item_type == "material" else "WH-FG"].id, received_by_id=users["inventory"].id, requires_quality=requires_quality, quality_status=quality_status, status="completed" if quality_status == "approved" or not requires_quality else "pending_quality", remark="标准到货样例")
            db.add(receipt)
            db.flush()
            db.add(PurchaseReceiptItem(purchase_receipt_id=receipt.id, purchase_order_item_id=item.id, received_quantity=received_qty, batch_no=f"IN-{order_no[-6:]}", unit_cost=dec(price)))
            if requires_quality:
                shadow_task = ProductionTask(task_no=f"QC-SHADOW-{order_no[-6:]}", source_type="purchase_quality", product_id=list(products.values())[0].id, planned_quantity=received_qty, produced_quantity=received_qty, requires_quality=True, status="completed", batch_no=f"IN-{order_no[-6:]}", created_by_id=users["quality"].id, started_at=dt(order_date) + timedelta(days=2), completed_at=dt(order_date) + timedelta(days=3), notes=f"采购到货质检影子任务：{order.order_no}")
                db.add(shadow_task)
                db.flush()
                qt = QualityTask(quality_no=f"ZJ{order_no[-6:]}", production_task_id=shadow_task.id, batch_no=f"IN-{order_no[-6:]}", source_category="purchase", required_tests=1, status="completed", submitted_by_id=users["inventory"].id)
                db.add(qt)
                db.flush()
                db.add(QualityInspection(quality_task_id=qt.id, inspector_user_id=users["quality"].id, sequence_no=1, result_text="外观、纯度及包装抽检符合入库要求。", grade="A", is_passed=True, inspected_quantity=received_qty, sample_consumption_qty=dec("2"), report_summary="采购到货质检通过", inspected_at=dt(order_date) + timedelta(days=3)))
        pay_amount = total if status == "fully_received" else total * dec("0.4") if status == "partial_received" else dec(0)
        if pay_amount > 0:
            req = PurchasePaymentRequest(request_no=f"FK{order_no[-6:]}", purchase_order_id=order.id, requested_by_id=users["purchase"].id, approved_by_id=users["manager"].id, completed_by_id=users["purchase"].id, requested_amount=pay_amount, order_amount=pay_amount, other_fee_amount=0, actual_amount=pay_amount, arrival_status="arrived" if status in {"fully_received", "partial_received"} else "not_arrived", status="completed", payment_detail_remark="采购侧付款申请样例", manager_remark="经理确认出账", completion_remark="采购确认流程结束", approved_at=dt(order_date) + timedelta(days=4), completed_at=dt(order_date) + timedelta(days=5))
            db.add(req)
            db.flush()
            db.add(PayableRecord(purchase_order_id=order.id, purchase_payment_request_id=req.id, amount=pay_amount, order_amount=pay_amount, other_fee_amount=0, paid_at=dt(order_date) + timedelta(days=4), paid_by_id=users["manager"].id, status="settled" if pay_amount >= total else "partial_settled", remark="业务样例：采购付款"))
            db.add(CashFlowRecord(record_no=f"ZJ-OUT-{order_no[-6:]}", flow_type="outgoing", business_type="purchase_order", business_id=order.id, related_no=order.order_no, amount=pay_amount, happened_at=dt(order_date) + timedelta(days=4), remark="采购订单付款"))
        db.add(LogisticsRecord(logistics_no=f"WL-P-{order_no[-6:]}", module="purchase", business_type="purchase_order", business_id=order.id, related_no=order.order_no, warehouse_id=warehouses["WH-RM" if item_type == "material" else "WH-FG"].id, logistics_mode="road", carrier_name="华东干线物流有限公司", driver_name="李师傅", driver_phone="13900008888", vehicle_no=f"鲁H{order_no[-4:]}", tracking_no=f"HD{order_no[-6:]}", dispatched_at=dt(order_date) + timedelta(days=1), arrived_at=dt(order_date) + timedelta(days=3) if received_qty > 0 else None, remark="标准采购物流样例", created_by_id=users["purchase"].id))
        orders.append(order)
    return orders


def add_production_quality_data(db: Session, sales_orders: list[SalesOrder], products: dict[str, Product], materials: dict[str, Material], warehouses: dict[str, Warehouse], users: dict[str, User]) -> None:
    tasks = [
        ("SC202601001", "SALT-05", 18000, 17880, "completed", True, "2026-01-12"),
        ("SC202602001", "SALT-11", 9000, 0, "pending_material", True, "2026-02-13"),
        ("SC202603001", "SALT-16", 2200, 2100, "waiting_quality", True, "2026-03-21"),
        ("SC202604001", "SALT-02", 6000, 6000, "ready_for_inbound", False, "2026-04-10"),
    ]
    for task_no, product_code, plan_qty, produced_qty, status, requires_quality, start in tasks:
        task = ProductionTask(task_no=task_no, source_type="manual", product_id=products[product_code].id, planned_quantity=dec(plan_qty), produced_quantity=dec(produced_qty), scrap_quantity=dec(max(plan_qty - produced_qty, 0)), fluctuation_ratio=dec("0.02"), requires_quality=requires_quality, status=status, batch_no=f"PC-{task_no[-6:]}", created_by_id=users["production"].id, started_at=dt(start), completed_at=dt(start) + timedelta(days=3) if status in {"completed", "waiting_quality", "ready_for_inbound"} else None, notes="标准生产任务样例")
        db.add(task)
        db.flush()
        bom = db.execute(select(BOM).where(BOM.product_id == products[product_code].id)).scalar_one_or_none()
        if bom:
            for bom_item in db.execute(select(BOMItem).where(BOMItem.bom_id == bom.id)).scalars().all():
                planned = dec(plan_qty) * dec(bom_item.standard_quantity)
                issued = planned if status != "pending_material" else 0
                db.add(ProductionMaterialUsage(production_task_id=task.id, material_id=bom_item.material_id, planned_quantity=planned, requested_quantity=planned, approved_quantity=issued, issued_quantity=issued, actual_quantity=issued, reason="标准BOM领料", special_request_status="not_required", inventory_approval_status="approved" if issued else "pending_approval", inventory_approved_by_id=users["inventory"].id if issued else None, inventory_approved_at=dt(start) + timedelta(days=1) if issued else None))
        if requires_quality and status in {"completed", "waiting_quality"}:
            qt = QualityTask(quality_no=f"ZJ-SC-{task_no[-6:]}", production_task_id=task.id, batch_no=task.batch_no, source_category="production", required_tests=2, status="completed" if status == "completed" else "pending", submitted_by_id=users["production"].id)
            db.add(qt)
            db.flush()
            if status == "completed":
                db.add(QualityInspection(quality_task_id=qt.id, inspector_user_id=users["quality"].id, sequence_no=1, result_text="外观、含量、粒度指标符合产品标准。", grade="A", is_passed=True, inspected_quantity=dec(produced_qty), sample_consumption_qty=dec("3"), report_summary="生产批次首检通过", inspected_at=dt(start) + timedelta(days=4)))
                app = QualityInboundApplication(quality_task_id=qt.id, warehouse_id=warehouses["WH-FG"].id, requested_by_id=users["production"].id, approved_by_id=users["inventory"].id, status="approved", approved_at=dt(start) + timedelta(days=4), remark="质检通过，准许入库")
                db.add(app)
        db.add(ProductionPlan(plan_no=f"JH{task_no[-6:]}", plan_name=f"{products[product_code].name}生产计划", period_type="month", period_start=dt(start), period_end=dt(start) + timedelta(days=30), product_id=products[product_code].id, assigned_user_id=users["production"].id, planned_quantity=dec(plan_qty), scheduled_start_at=dt(start), scheduled_end_at=dt(start) + timedelta(days=5), priority="normal", line_name="盐化产品一线", scheduling_note="根据销售订单与库存安全线安排", detail_json={"quality_required": requires_quality, "batch_no": task.batch_no}, converted_task_id=task.id, status="converted", created_by_id=users["production"].id, updated_by_id=users["production"].id))


def add_misc_data(db: Session, customers: dict[str, Customer], products: dict[str, Product], warehouses: dict[str, Warehouse], users: dict[str, User]) -> None:
    inquiries = [
        ("INQ20260101", "C1001", "融雪剂用，需要粗粒工业盐50吨，最好能提供冬季保供方案。", "微信", "融雪剂（环保型）", "0.92", "高意向"),
        ("INQ20260102", "C1002", "食品加工用，需要不含抗结剂的食用氯化钠，并提供质检报告。", "电话", "食用氯化钠", "0.88", "高意向"),
        ("INQ20260201", "C1003", "你们能做分析纯级别的吗？量大，包装能不能用吨桶。", "网页咨询", "分析纯氯化钠", "0.81", "高意向"),
        ("INQ20260301", "C1007", "钻井液用盐，要求抗温抗钙，想看一下供货周期。", "电话", "钻井液用盐", "0.79", "中高意向"),
        ("INQ20260401", "C1012", "软水机专用盐什么价格？如果按季度采购能否稳定供货。", "微信", "软水专用盐", "0.65", "中意向"),
        ("INQ20260402", "C1013", "实验耗材渠道询价，先了解分析纯氯化钠规格。", "网页咨询", "分析纯氯化钠", "0.42", "待观察"),
    ]
    for no, customer_code, text_value, channel, hint, score, label in inquiries:
        db.add(CustomerInquiry(inquiry_no=no, customer_id=customers[customer_code].id, inquiry_text=text_value, source_channel=channel, product_hint=hint, intent_score=dec(score), intent_label=label, model_version="customer-ai-v1", created_by_id=users["sales"].id))
    db.add(CustomerComplaint(complaint_no="TS202604001", customer_id=customers["C1004"].id, complainant_name="陈雅丽", complainant_phone=customers["C1004"].phone, complainant_address=customers["C1004"].address, complaint_reason="到货包装破损", complaint_detail="软水专用盐到货后发现外包装受潮，需要核查物流环节。", status="pending", created_by_id=users["sales"].id))
    for customer_code, segment, score, label, recs in [
        ("C1001", "高价值稳定客户", "0.91", "优先跟进", ["融雪剂（环保型）", "工业氯化钠"]),
        ("C1002", "食品级复购客户", "0.86", "优先跟进", ["食用氯化钠", "雪盐（调味用）"]),
        ("C1003", "高毛利专业客户", "0.82", "重点维护", ["医药氯化钠", "分析纯氯化钠"]),
        ("C1013", "潜在线索客户", "0.48", "待观察", ["分析纯氯化钠"]),
    ]:
        db.add(CustomerAIResult(customer_id=customers[customer_code].id, segment_label=segment, segment_reason="基于购买频次、购买金额、品类偏好与咨询意向形成客户标签。", lead_score=dec(score), lead_label=label, rfm_json={"recency": 30, "frequency": 3, "monetary": 50000}, recommendations_json=recs, model_version="customer-ai-v1"))

    inventories = db.execute(select(Inventory).order_by(Inventory.id).limit(6)).scalars().all()
    task = StockTakeTask(task_no="PD202604001", warehouse_id=warehouses["WH-FG"].id, submitted_by_id=users["inventory"].id, approved_by_id=users["manager"].id, status="completed", difference_note="标准盘点样例：轻微损耗已说明。", approved_at=dt("2026-04-30"))
    db.add(task)
    db.flush()
    for index, inv in enumerate(inventories):
        counted = dec(inv.quantity) - dec(index % 2)
        db.add(StockTakeItem(stock_take_task_id=task.id, inventory_id=inv.id, system_quantity=inv.quantity, counted_quantity=counted, difference_quantity=counted - dec(inv.quantity), reason="正常仓储损耗" if counted != dec(inv.quantity) else "账实一致"))


def refresh_supplier_ai(db: Session) -> None:
    supplier_rows = db.execute(select(Supplier).order_by(Supplier.id)).scalars().all()
    for index, supplier in enumerate(supplier_rows):
        risk = dec("0.12") + dec(index % 5) * dec("0.08")
        db.add(
            SupplierAIResult(
                supplier_id=supplier.id,
                predicted_grade="A" if index % 5 in {0, 1} else "B" if index % 5 in {2, 3} else "C",
                risk_score=risk,
                risk_label="高风险" if risk >= dec("0.55") else "中风险" if risk >= dec("0.25") else "低风险",
                confidence=dec("0.78") + dec(index % 3) * dec("0.04"),
                features_json={
                    "selected": {"risk_index": float(risk), "on_time_delivery_rate": 0.92 - index * 0.01},
                    "business": {"order_count": 3 + index, "total_purchase_amount": 50000 + index * 8000, "quality_pass_rate": 0.98 - index * 0.005},
                },
                probabilities_json={"A": 0.62, "B": 0.28, "C": 0.10},
                model_name="RandomForest + BiLSTM",
                model_version="supplier-ai-v1",
            )
        )


def reset_demo_data() -> None:
    maybe_backup_sqlite()
    with SessionLocal() as db:
        clear_business_data(db)
        users = {
            "sales": first_user(db, "sales"),
            "purchase": first_user(db, "purchase"),
            "inventory": first_user(db, "inventory"),
            "production": first_user(db, "production"),
            "quality": first_user(db, "quality"),
            "finance": first_user(db, "finance"),
            "manager": first_user(db, "manager"),
            "chairman": first_user(db, "chairman"),
        }
        warehouses = add_warehouses(db)
        customers, suppliers, products, materials = add_master_data(db, users)
        add_inventory(db, warehouses, products, materials, users)
        add_boms(db, products, materials)
        sales_orders = add_sales_data(db, customers, products, warehouses, users)
        add_purchase_data(db, suppliers, products, materials, warehouses, users)
        add_production_quality_data(db, sales_orders, products, materials, warehouses, users)
        add_misc_data(db, customers, products, warehouses, users)
        refresh_supplier_ai(db)
        db.commit()
        print("标准验收样例数据已重置完成")


if __name__ == "__main__":
    reset_demo_data()
