"""按旧导出补齐产品/原料全称；默认只预览，不重新导入其他数据。"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from import_legacy_master_data import BASE_DIR, is_leaf, load_json, s, truncate
from database import SessionLocal
from models import Material, Product
from sqlalchemy import select, update


def plan_name_repairs(db, rows):
    """只有编码唯一且当前名称仍为旧简称时才允许补齐，保护人工改名。"""
    source_by_code = defaultdict(set)
    for row in rows:
        if not s(row.get("ptypeid")) or not is_leaf(row):
            continue
        code = truncate(s(row.get("pusercode")) or f"OLD_{s(row['ptypeid'])}", 50)
        source_by_code[code].add((s(row.get("pname")), s(row.get("pfullname"))))

    targets = defaultdict(list)
    for model, field, label in ((Product, Product.product_code, "产品"),
                                (Material, Material.material_code, "原料")):
        for row in db.execute(select(model.id, field, model.name)):
            targets[row[1]].append((model.__tablename__, label, row[0], row[2]))

    changes, skipped = [], []
    for code, candidates in source_by_code.items():
        matches = targets.get(code, [])
        if not matches:
            continue
        if len(candidates) != 1 or len(matches) != 1:
            skipped.append({"code": code, "reason": "编码对应多份源数据或多个目标档案，需人工核对"})
            continue
        short, full = next(iter(candidates))
        table, label, row_id, current = matches[0]
        if not full or current == full:
            continue
        if len(full) > 200:
            skipped.append({"code": code, "reason": "全称超过 200 字，需人工核对"})
            continue
        if not short or current != truncate(short, 200):
            skipped.append({"code": code, "reason": "当前名称与旧简称不一致，保留现有名称"})
            continue
        changes.append({"table": table, "type": label, "id": row_id, "code": code,
                        "before": current, "after": full})
    return changes, skipped


def apply_name_repairs(db, changes):
    """仅更新名称；用原值条件防止覆盖预览后发生的修改，由调用者提交事务。"""
    models = {"products": Product, "materials": Material}
    for change in changes:
        model = models[change["table"]]
        code_field = model.product_code if model is Product else model.material_code
        result = db.execute(
            update(model).where(model.id == change["id"], code_field == change["code"],
                                model.name == change["before"])
            .values(name=change["after"])
        )
        if result.rowcount != 1:
            raise RuntimeError(f"{change['code']} 已发生变化，请重新预览；本批次将回滚")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="原导入数据目录，如 old_data/export")
    parser.add_argument("--apply", action="store_true", help="正式提交名称修复；默认只读预览")
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_absolute():
        source = BASE_DIR / source
    rows = load_json(source, "02_ptype.json", strict=True)
    with SessionLocal() as db:
        changes, skipped = plan_name_repairs(db, rows)
        for change in changes:
            print(f"{change['type']} {change['code']}: {change['before']} -> {change['after']}")
        for item in skipped:
            print(f"跳过 {item['code']}: {item['reason']}")
        print(f"产品 {sum(c['table'] == 'products' for c in changes)} 条，"
              f"原料 {sum(c['table'] == 'materials' for c in changes)} 条，需核对 {len(skipped)} 条")
        if args.apply:
            try:
                apply_name_repairs(db, changes)
                db.commit()
            except Exception:
                db.rollback()
                raise
            print("名称修复已提交。")
        else:
            db.rollback()
            print("只读预览，未修改数据库。确认后添加 --apply 执行。")


if __name__ == "__main__":
    main()
