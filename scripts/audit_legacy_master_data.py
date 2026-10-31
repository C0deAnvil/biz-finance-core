"""Build a read-only reconciliation report for the 2026-10-31 legacy exports.

This script never mutates either the legacy exports or the target database.  It
is intentionally separate from the importer so production migration always has
an inspectable preview first.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import select

BASE_DIR = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal  # noqa: E402
from models import Customer, Material, Product, Supplier, User, Warehouse  # noqa: E402


SOURCE_NAMES = ("niaosu2020", "paishui2020", "shiji2020")
PACKAGING_KEYWORDS = ("包装", "编织袋", "内膜袋", "吨包", "纸箱", "塑料桶", "包装袋")


def text(value) -> str:
    return str(value or "").strip()


def read_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def normalized(value) -> str:
    return re.sub(r"[\s（）()\-—_·.,，。]+", "", text(value)).lower()


def is_leaf(row: dict) -> bool:
    try:
        return int(row.get("soncount") or 0) == 0
    except (TypeError, ValueError):
        return False


def classify_btypes(rows: list[dict], doc_types: list[dict]) -> dict[str, str]:
    by_id = {text(row.get("btypeid")): row for row in rows}
    result: dict[str, str] = {}
    for row in rows:
        old_id = text(row.get("btypeid"))
        if not old_id or not is_leaf(row):
            continue
        current = row
        seen = set()
        branch = ""
        while current:
            try:
                level = int(current.get("leveal") or 0)
            except (TypeError, ValueError):
                level = 0
            if level == 1:
                branch = text(current.get("bname"))
                break
            parent = text(current.get("parid"))
            if not parent or parent in seen:
                break
            seen.add(parent)
            current = by_id.get(parent)
        path = f"{branch} {text(row.get('bfullname'))}"
        if "供应" in path:
            result[old_id] = "supplier"
        elif "客户" in path or "重复" in path:
            result[old_id] = "customer"
    # Some legacy ledgers store units directly at level 1 instead of below a
    # customer/supplier category.  Resolve those from their historical document
    # types, matching the production importer.
    sale_docs: Counter[str] = Counter()
    buy_docs: Counter[str] = Counter()
    for row in doc_types:
        old_id = text(row.get("btypeid"))
        name = text(row.get("vchname"))
        count = int(float(row.get("doc_count") or 0))
        if any(key in name for key in ("销售", "销货", "收款")):
            sale_docs[old_id] += count
        elif any(key in name for key in ("采购", "进货", "付款")):
            buy_docs[old_id] += count
    for row in rows:
        old_id = text(row.get("btypeid"))
        if not old_id or not is_leaf(row) or old_id in result:
            continue
        result[old_id] = "supplier" if buy_docs[old_id] > sale_docs[old_id] else "customer"
    return result


def first_nonempty(row: dict, *keys: str) -> str:
    for key in keys:
        value = text(row.get(key))
        if value:
            return value
    return ""


def table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        values = [text(value).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def target_match(row: dict, by_code: dict[str, object], by_name: dict[str, object], code_key: str):
    code = text(row.get(code_key))
    name = first_nonempty(row, "bfullname", "bname")
    return by_code.get(code) or by_name.get(normalized(name))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit legacy exports against the current target database")
    parser.add_argument("--source-root", default="old_data/export_20261031")
    parser.add_argument("--out", default="docs/旧系统数据导入对账报告_20261031.md")
    args = parser.parse_args()

    source_root = (BASE_DIR / args.source_root).resolve()
    out_path = (BASE_DIR / args.out).resolve()

    db = SessionLocal()
    try:
        customers = db.execute(select(Customer)).scalars().all()
        suppliers = db.execute(select(Supplier)).scalars().all()
        products = db.execute(select(Product)).scalars().all()
        materials = db.execute(select(Material)).scalars().all()
        users = db.execute(select(User)).scalars().all()
        warehouses = db.execute(select(Warehouse)).scalars().all()
    finally:
        db.close()

    customer_code = {text(item.code): item for item in customers}
    customer_name = {normalized(item.name): item for item in customers}
    supplier_code = {text(item.code): item for item in suppliers}
    supplier_name = {normalized(item.name): item for item in suppliers}
    product_code = {text(item.product_code): item for item in products}
    material_code = {text(item.material_code): item for item in materials}
    user_name = {normalized(item.full_name): item for item in users if text(item.full_name)}

    lines = [
        "# 旧系统数据导入对账报告（2026-10-31）",
        "",
        "> 只读生成；不会修改旧库或新系统。正式导入前必须确认仓库与人员映射。",
        "",
        "## 新系统当前基线",
        "",
    ]
    lines += table(
        ["对象", "当前数量"],
        [
            ["客户", len(customers)], ["供应商", len(suppliers)], ["产品", len(products)],
            ["原料", len(materials)], ["用户", len(users)], ["仓库", len(warehouses)],
        ],
    )
    lines += ["", "### 当前仓库", ""]
    lines += table(["ID", "编码", "名称", "类型"], [[w.id, w.code, w.name, w.warehouse_type] for w in warehouses])

    all_owner_candidates: dict[str, dict] = {}
    for source_name in SOURCE_NAMES:
        source = source_root / source_name
        btypes = read_json(source / "01_btype.json")
        doc_types = read_json(source / "08_btype_doc_types.json")
        ptypes = read_json(source / "02_ptype.json")
        employees = read_json(source / "04_employee.json")
        stocks = read_json(source / "06_stock.json")
        owners = read_json(source / "07_customer_owner.json")
        inventory = read_json(source / "11_goods_stocks.json")
        bom_headers = read_json(source / "09_bom_ndx.json")
        bom_items = read_json(source / "10_bom_detail.json")

        kinds = classify_btypes(btypes, doc_types)
        customer_rows = [r for r in btypes if kinds.get(text(r.get("btypeid"))) == "customer"]
        supplier_rows = [r for r in btypes if kinds.get(text(r.get("btypeid"))) == "supplier"]
        leaf_items = [r for r in ptypes if is_leaf(r)]
        ptype_by_id = {text(r.get("ptypeid")): r for r in ptypes}
        stock_by_id = {text(r.get("ktypeid")): r for r in stocks}
        employee_by_id = {text(r.get("etypeid")): r for r in employees}

        customer_matches = [target_match(r, customer_code, customer_name, "busercode") for r in customer_rows]
        supplier_matches = [target_match(r, supplier_code, supplier_name, "busercode") for r in supplier_rows]
        missing_contact = 0
        fillable_contact = 0
        for row, existing in zip(customer_rows, customer_matches):
            if existing is None:
                continue
            old_contact = first_nonempty(row, "Person", "linker")
            old_phone = first_nonempty(row, "moPhone", "linkerTel", "TelAndAddress")
            old_address = text(row.get("Area"))
            if not text(existing.contact_name) or not text(existing.phone) or not text(existing.address):
                missing_contact += 1
                if old_contact or old_phone or old_address:
                    fillable_contact += 1

        product_existing = sum(1 for r in leaf_items if text(r.get("pusercode")) in product_code)
        material_existing = sum(1 for r in leaf_items if text(r.get("pusercode")) in material_code)
        packaging_candidates = [
            r for r in leaf_items
            if any(keyword in f"{text(r.get('pname'))}{text(r.get('pfullname'))}{text(r.get('Type'))}" for keyword in PACKAGING_KEYWORDS)
        ]

        lines += ["", f"## {source_name}", ""]
        lines += table(
            ["项目", "旧库数量", "新系统已有匹配", "预计新增/待判定"],
            [
                ["客户", len(customer_rows), sum(x is not None for x in customer_matches), sum(x is None for x in customer_matches)],
                ["供应商", len(supplier_rows), sum(x is not None for x in supplier_matches), sum(x is None for x in supplier_matches)],
                ["商品叶节点", len(leaf_items), product_existing + material_existing, len(leaf_items) - product_existing - material_existing],
                ["包装候选", len(packaging_candidates), "-", "需人工确认归属"],
                ["库存结存", len(inventory), "-", "需确认仓库映射"],
                ["BOM / 明细", f"{len(bom_headers)} / {len(bom_items)}", "-", "旧库为空"],
            ],
        )
        lines += ["", f"已有客户中关键字段不完整：{missing_contact} 家；旧库可补齐至少一个字段：{fillable_contact} 家。", ""]

        lines += ["### 前 10 条客户样本", ""]
        lines += table(
            ["旧编码", "名称", "联系人", "电话", "地址", "新系统匹配"],
            [[
                r.get("busercode"), first_nonempty(r, "bfullname", "bname"),
                first_nonempty(r, "Person", "linker"), first_nonempty(r, "moPhone", "linkerTel", "TelAndAddress"),
                r.get("Area"), "是" if target_match(r, customer_code, customer_name, "busercode") else "否",
            ] for r in customer_rows[:10]],
        )

        lines += ["", "### 前 10 条供应商样本", ""]
        lines += table(
            ["旧编码", "名称", "联系人", "电话", "地址", "新系统匹配"],
            [[
                r.get("busercode"), first_nonempty(r, "bfullname", "bname"),
                first_nonempty(r, "Person", "linker"), first_nonempty(r, "moPhone", "linkerTel", "TelAndAddress"),
                r.get("Area"), "是" if target_match(r, supplier_code, supplier_name, "busercode") else "否",
            ] for r in supplier_rows[:10]],
        )

        lines += ["", "### 旧仓库", ""]
        lines += table(
            ["旧仓库ID", "编码", "名称", "地址", "负责人", "电话"],
            [[r.get("ktypeid"), r.get("kusercode"), r.get("kfullname") or r.get("kname"), r.get("Address"), r.get("Person"), r.get("Tel")] for r in stocks],
        )

        lines += ["", "### 全部库存结存（数量为非零）", ""]
        inventory_rows = []
        for row in inventory:
            item = ptype_by_id.get(text(row.get("PtypeId")), {})
            stock = stock_by_id.get(text(row.get("KtypeId")), {})
            inventory_rows.append([
                row.get("KtypeId"), stock.get("kfullname") or stock.get("kname"),
                item.get("pusercode"), item.get("pfullname") or item.get("pname"),
                item.get("Standard"), item.get("punitname"), row.get("qty"), row.get("avg_unit_cost"),
            ])
        lines += table(["旧仓库ID", "旧仓库", "物品编码", "物品", "规格", "单位", "数量", "移动均价"], inventory_rows)

        lines += ["", "### 负责人候选映射", ""]
        owner_counts = Counter(text(r.get("etypeid")) for r in owners)
        mapping_rows = []
        for employee_id, count in owner_counts.most_common():
            employee = employee_by_id.get(employee_id, {})
            name = first_nonempty(employee, "efullname", "ename")
            matched = user_name.get(normalized(name))
            mapping_rows.append([employee_id, name, count, matched.username if matched else "待指定"])
            all_owner_candidates[f"{source_name}:{employee_id}"] = {
                "source": source_name, "etypeid": employee_id, "employee_name": name,
                "suggested_username": matched.username if matched else "",
            }
        lines += table(["旧员工ID", "姓名", "负责客户数", "建议新账号"], mapping_rows)

        if packaging_candidates:
            lines += ["", "### 包装候选（需人工确认）", ""]
            lines += table(
                ["旧ID", "编码", "名称", "规格", "单位", "类型"],
                [[r.get("ptypeid"), r.get("pusercode"), r.get("pfullname") or r.get("pname"), r.get("Standard"), r.get("punitname"), r.get("Type")] for r in packaging_candidates],
            )

    lines += [
        "", "## 必须人工确认后才能正式导入", "",
        "1. 每个旧仓库应映射到哪个新仓库；库存只能覆盖目标仓库对应物品，不能跨三个旧库直接相加。",
        "2. 产品、原料、包装的分类，尤其是包装候选；旧系统没有与新系统完全相同的包装模型。",
        "3. 未能按姓名自动匹配的旧员工，应指定现有 username。",
        "4. 三个旧库 BOM 均为空，本轮不导入 BOM，保留新系统现有 BOM。",
        "5. 已有客户/供应商仅补空的联系人、电话、地址、银行字段，不覆盖新系统已有非空值。",
        "",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    mapping_path = out_path.with_name("旧员工到新账号映射_20261031.csv")
    csv_lines = ["source,etypeid,employee_name,target_username,confirmed"]
    for item in all_owner_candidates.values():
        username = item["suggested_username"]
        if item["employee_name"] == "王飞":
            username = "cphw_wangfei"
        csv_lines.append(
            ",".join([
                item["source"], item["etypeid"], item["employee_name"].replace(",", "，"), username, "Y" if username else "",
            ])
        )
    mapping_path.write_text("\ufeff" + "\n".join(csv_lines), encoding="utf-8")

    print(out_path)
    print(mapping_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
