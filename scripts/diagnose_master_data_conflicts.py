"""导入前体检：看清目标库里已经有什么，和旧系统数据是否撞车。

只读，不写库。输出全部是数量统计，不打印任何客户名称/电话/编码，
可以直接把结果贴出来看。

    python scripts/diagnose_master_data_conflicts.py --source old_data/export
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import func, select  # noqa: E402

from database import SessionLocal  # noqa: E402
from models import Customer, Material, Product, Supplier, User, Warehouse  # noqa: E402

from import_legacy_master_data import (  # noqa: E402
    build_name_disambiguation,
    classify_btype,
    first_nonempty,
    is_leaf,
    load_json,
    s,
    truncate,
    Report,
)


def section(title: str):
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def report_entity(label, db_pairs, incoming_pairs):
    """db_pairs / incoming_pairs: list[(code, name)]"""
    db_codes = {c for c, _ in db_pairs if c}
    db_names = {n for _, n in db_pairs if n}
    in_codes = {c for c, _ in incoming_pairs if c}
    in_names = {n for _, n in incoming_pairs if n}

    code_hit = in_codes & db_codes
    name_hit = in_names & db_names

    # 编码对不上但名称撞车的，就是这次报错的来源
    name_only = [
        (c, n) for c, n in incoming_pairs
        if n in db_names and c not in db_codes
    ]

    print(f"\n【{label}】")
    print(f"  库里现有            : {len(db_pairs)} 条（不同编码 {len(db_codes)} / 不同名称 {len(db_names)}）")
    print(f"  本次待导入          : {len(incoming_pairs)} 条")
    print(f"  编码能对上（会更新）: {len(code_hit)}")
    print(f"  名称能对上          : {len(name_hit)}")
    print(f"  ★ 编码对不上但重名  : {len(name_only)}   <- 唯一约束会在这里报错")
    return len(name_only)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="old_data/export")
    args = ap.parse_args()
    source = Path(args.source)
    if not source.is_absolute():
        source = BASE_DIR / source

    db = SessionLocal()
    report = Report()
    try:
        section("目标库现状")
        for model, label in (
            (Customer, "customers"),
            (Supplier, "suppliers"),
            (Product, "products"),
            (Material, "materials"),
            (Warehouse, "warehouses"),
            (User, "users"),
        ):
            n = db.execute(select(func.count()).select_from(model)).scalar()
            print(f"  {label:<12} {n}")

        first, last = db.execute(
            select(func.min(Customer.created_at), func.max(Customer.created_at))
        ).one()
        owned = db.execute(
            select(func.count()).select_from(Customer).where(Customer.owner_user_id.isnot(None))
        ).scalar()
        with_code = db.execute(
            select(func.count()).select_from(Customer).where(Customer.code.isnot(None), Customer.code != "")
        ).scalar()
        old_code = db.execute(
            select(func.count()).select_from(Customer).where(Customer.code.like("OLD_%"))
        ).scalar()
        print(f"\n  客户 created_at 区间 : {first} ~ {last}")
        print(f"  客户有编码的         : {with_code}")
        print(f"  其中 OLD_ 开头       : {old_code}")
        print(f"  客户已挂负责人的     : {owned}")

        section("与旧系统数据比对")
        btype = load_json(source, "01_btype.json")
        doc_types = load_json(source, "08_btype_doc_types.json")
        kind_map, _dups = classify_btype(btype, doc_types, report)

        # —— 客户 ——
        overrides = build_name_disambiguation(btype, kind_map, "customer", "btypeid", "bname", "busercode")
        incoming_customers = []
        for row in btype:
            old_id = s(row.get("btypeid"))
            if kind_map.get(old_id) != "customer" or not is_leaf(row):
                continue
            name = overrides.get(old_id) or truncate(first_nonempty(row.get("bname"), row.get("bfullname")), 150)
            code = truncate(first_nonempty(row.get("busercode"), f"OLD_{old_id}"), 50)
            if old_id and name:
                incoming_customers.append((code, name))
        db_customers = [(c or "", n or "") for c, n in db.execute(select(Customer.code, Customer.name)).all()]
        blockers = report_entity("客户", db_customers, incoming_customers)

        # —— 供应商 ——
        s_overrides = build_name_disambiguation(btype, kind_map, "supplier", "btypeid", "bname", "busercode")
        incoming_suppliers = []
        for row in btype:
            old_id = s(row.get("btypeid"))
            if kind_map.get(old_id) != "supplier" or not is_leaf(row):
                continue
            name = s_overrides.get(old_id) or truncate(first_nonempty(row.get("bname"), row.get("bfullname")), 150)
            code = truncate(first_nonempty(row.get("busercode"), f"OLD_{old_id}"), 50)
            if old_id and name:
                incoming_suppliers.append((code, name))
        db_suppliers = [(c or "", n or "") for c, n in db.execute(select(Supplier.code, Supplier.name)).all()]
        blockers += report_entity("供应商", db_suppliers, incoming_suppliers)

        section("结论")
        if blockers:
            print(f"  有 {blockers} 条会因为「名称唯一约束」导入失败。")
            print("  先决定这批已有数据怎么处理，再导入。")
        else:
            print("  没有名称冲突，可以直接 --apply。")
        print()
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
