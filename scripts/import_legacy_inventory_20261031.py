"""Consolidate and restore opening inventory from all 2026-10-31 ledgers.

Dry-run is the default.  ``--apply`` is intentionally explicit and should only
be used after the master-data importer has created every mapped product/material.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal  # noqa: E402
from models import Inventory, InventoryTransaction, Material, Product, User, Warehouse  # noqa: E402

SOURCES = ("niaosu2020", "paishui2020", "shiji2020")


def s(value) -> str:
    return str(value or "").strip()


def dec(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_csv(path: Path, key: str) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {s(row.get(key)): row for row in csv.DictReader(fh) if s(row.get(key))}


def tx_no(warehouse_code: str, kind: str, code: str) -> str:
    digest = hashlib.sha1(f"{warehouse_code}|{kind}|{code}".encode()).hexdigest()[:16].upper()
    return f"LEGACY20261031-{digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore consolidated legacy inventory")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--zero-missing", action="store_true", help="将映射范围内但旧结存没有的库存设为 0")
    parser.add_argument(
        "--zero-all-inventory",
        action="store_true",
        help="上线恢复专用：先将所有仓库库存余额归零，再写入本次完整快照",
    )
    parser.add_argument("--operator", default="sysadmin01")
    parser.add_argument("--out", default="output/legacy_migration_20261031/inventory_preview.csv")
    args = parser.parse_args()

    export_root = BASE_DIR / "old_data" / "export_20261031"
    mapping_root = BASE_DIR / "docs" / "legacy_migration_20261031"
    out_path = (BASE_DIR / args.out).resolve()

    # (warehouse_code, kind, target_code) -> accumulated snapshot
    aggregated: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"quantity": Decimal("0"), "total": Decimal("0"), "sources": []}
    )
    mapped_scope: set[tuple[str, str, str]] = set()
    errors: list[str] = []

    for source_name in SOURCES:
        item_map = load_csv(mapping_root / f"{source_name}_product_map.csv", "ptypeid")
        warehouse_map = load_csv(mapping_root / f"{source_name}_warehouse_map.csv", "ktypeid")
        ptypes = {s(row.get("ptypeid")): row for row in load_json(export_root / source_name / "02_ptype.json")}
        stocks = load_json(export_root / source_name / "11_goods_stocks.json")

        for old_id, mapping in item_map.items():
            kind = s(mapping.get("kind"))
            code = s(mapping.get("target_code"))
            if kind not in ("product", "material") or not code:
                continue
            # Scope is expanded below for each mapped physical warehouse.
            for warehouse in warehouse_map.values():
                target_warehouse = s(warehouse.get("target_warehouse_code"))
                if target_warehouse:
                    mapped_scope.add((target_warehouse, kind, code))

        for row in stocks:
            old_item_id = s(row.get("PtypeId"))
            old_warehouse_id = s(row.get("KtypeId"))
            item = item_map.get(old_item_id)
            warehouse = warehouse_map.get(old_warehouse_id)
            if item is None:
                errors.append(f"{source_name}: 库存物品 {old_item_id} 没有物品映射")
                continue
            if warehouse is None:
                errors.append(f"{source_name}: 库存仓库 {old_warehouse_id} 没有仓库映射")
                continue
            kind = s(item.get("kind"))
            code = s(item.get("target_code"))
            warehouse_code = s(warehouse.get("target_warehouse_code"))
            if kind not in ("product", "material") or not code or not warehouse_code:
                errors.append(f"{source_name}: {old_item_id} 的映射不完整")
                continue
            quantity = dec(row.get("qty"))
            total = dec(row.get("total_amount"))
            if total == 0 and dec(row.get("avg_unit_cost")) != 0:
                total = quantity * dec(row.get("avg_unit_cost"))
            key = (warehouse_code, kind, code)
            target = aggregated[key]
            target["quantity"] += quantity
            target["total"] += total
            old_item = ptypes.get(old_item_id, {})
            target["name"] = s(old_item.get("pfullname")) or s(old_item.get("pname"))
            target["sources"].append(f"{source_name}:{old_item_id}")

    rows = []
    for key in sorted(aggregated):
        data = aggregated[key]
        quantity = data["quantity"]
        avg_cost = data["total"] / quantity if quantity else Decimal("0")
        rows.append(
            {
                "warehouse_code": key[0],
                "kind": key[1],
                "target_code": key[2],
                "name": data.get("name", ""),
                "quantity": f"{quantity:.4f}",
                "total_cost": f"{data['total']:.4f}",
                "avg_unit_cost": f"{avg_cost:.8f}",
                "sources": ";".join(data["sources"]),
            }
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["warehouse_code"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"汇总库存：{len(rows)} 条；源库存行：{sum(len(v['sources']) for v in aggregated.values())}")
    print(f"数量合计（不同单位仅供核对）：{sum(v['quantity'] for v in aggregated.values()):.4f}")
    print(f"预览：{out_path}")
    if errors:
        print(f"映射错误 {len(errors)} 条：")
        for error in errors[:50]:
            print(f"  - {error}")
        return 2
    if not args.apply:
        print("当前为试运行，未修改数据库。")
        return 0

    db = SessionLocal()
    try:
        operator = db.execute(select(User).where(User.username == args.operator)).scalar_one_or_none()
        warehouses = {w.code: w for w in db.execute(select(Warehouse)).scalars().all()}
        products = {p.product_code: p for p in db.execute(select(Product)).scalars().all()}
        materials = {m.material_code: m for m in db.execute(select(Material)).scalars().all()}

        unresolved = []
        for warehouse_code, kind, code in aggregated:
            if warehouse_code not in warehouses:
                unresolved.append(f"仓库 {warehouse_code}")
            elif kind == "product" and code not in products:
                unresolved.append(f"产品 {code}")
            elif kind == "material" and code not in materials:
                unresolved.append(f"原料 {code}")
        if unresolved:
            raise RuntimeError("主数据尚未导入：" + "、".join(unresolved[:30]))

        if args.zero_all_inventory:
            for existing in db.execute(select(Inventory)).scalars().all():
                existing.quantity = Decimal("0")
                existing.reserved_quantity = Decimal("0")
                existing.in_transit = Decimal("0")
                existing.cost_balance = Decimal("0")
                existing.avg_unit_cost = Decimal("0")
                existing.cost_status = "unpriced"
                existing.opening_cost_pending = False

        if args.zero_missing:
            for warehouse_code, kind, code in mapped_scope - set(aggregated):
                warehouse = warehouses.get(warehouse_code)
                item = products.get(code) if kind == "product" else materials.get(code)
                if warehouse is None or item is None:
                    continue
                existing = db.execute(
                    select(Inventory).where(
                        Inventory.warehouse_id == warehouse.id,
                        Inventory.product_id == (item.id if kind == "product" else None),
                        Inventory.material_id == (item.id if kind == "material" else None),
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.quantity = Decimal("0")
                    existing.reserved_quantity = Decimal("0")
                    existing.in_transit = Decimal("0")
                    existing.cost_balance = Decimal("0")
                    existing.avg_unit_cost = Decimal("0")
                    existing.cost_status = "unpriced"
                    existing.opening_cost_pending = False

        for (warehouse_code, kind, code), data in aggregated.items():
            warehouse = warehouses[warehouse_code]
            item = products[code] if kind == "product" else materials[code]
            product_id = item.id if kind == "product" else None
            material_id = item.id if kind == "material" else None
            inventory = db.execute(
                select(Inventory).where(
                    Inventory.warehouse_id == warehouse.id,
                    Inventory.product_id == product_id,
                    Inventory.material_id == material_id,
                )
            ).scalar_one_or_none()
            if inventory is None:
                inventory = Inventory(
                    warehouse_id=warehouse.id,
                    product_id=product_id,
                    material_id=material_id,
                )
                db.add(inventory)
            quantity = data["quantity"]
            total = data["total"]
            avg_cost = total / quantity if quantity else Decimal("0")
            inventory.quantity = quantity
            inventory.reserved_quantity = Decimal("0")
            inventory.in_transit = Decimal("0")
            inventory.cost_balance = total
            inventory.avg_unit_cost = avg_cost
            inventory.cost_status = "estimated" if avg_cost else "unpriced"
            inventory.opening_cost_pending = bool(quantity)

            number = tx_no(warehouse_code, kind, code)
            transaction = db.execute(
                select(InventoryTransaction).where(InventoryTransaction.transaction_no == number)
            ).scalar_one_or_none()
            if transaction is None:
                db.add(
                    InventoryTransaction(
                        transaction_no=number,
                        transaction_type="legacy_opening_balance",
                        warehouse_id=warehouse.id,
                        product_id=product_id,
                        material_id=material_id,
                        quantity=quantity,
                        unit_cost=avg_cost,
                        total_cost=total,
                        cost_status="estimated" if avg_cost else "unpriced",
                        related_type="legacy_20261031",
                        created_by_id=operator.id if operator else None,
                    )
                )
        db.commit()
        print(f"已写入 {len(aggregated)} 条库存快照。")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
