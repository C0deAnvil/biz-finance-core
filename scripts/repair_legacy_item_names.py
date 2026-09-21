"""按旧导出补齐供应商、产品和原料全称；默认只预览，不重新导入其他数据。"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from import_legacy_master_data import (
    BASE_DIR, Report, _with_suffix, classify_btype, is_leaf, load_json, s, truncate,
)
from database import SessionLocal
from models import Material, Product, Supplier
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
        if len(matches) != 1:
            skipped.append({"code": code, "reason": "编码对应多份源数据或多个目标档案，需人工核对"})
            continue
        table, label, row_id, current = matches[0]
        if any(full and current == truncate(full, 200) for _short, full in candidates):
            continue
        matching_candidates = {(short, full) for short, full in candidates if current == truncate(short, 200)}
        if len(matching_candidates) != 1:
            skipped.append({"code": code, "reason": "当前名称不能唯一对应旧简称，保留现有名称"})
            continue
        short, full = next(iter(matching_candidates))
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


def plan_supplier_name_repairs(db, rows):
    """按供应商编码补齐全称；只修仍等于旧简称的记录。"""
    source_by_code = defaultdict(set)
    for row in rows:
        if not s(row.get("btypeid")) or not is_leaf(row):
            continue
        code = truncate(s(row.get("busercode")) or f"OLD_{s(row['btypeid'])}", 50)
        source_by_code[code].add((s(row.get("bname")), s(row.get("bfullname"))))

    suppliers = list(db.execute(select(Supplier.id, Supplier.code, Supplier.name)))
    target_by_code = defaultdict(list)
    occupied_names = defaultdict(set)
    for row_id, code, name in suppliers:
        target_by_code[code].append((row_id, name))
        occupied_names[name].add(row_id)

    changes, skipped = [], []
    for code, candidates in source_by_code.items():
        matches = target_by_code.get(code, [])
        if not matches:
            continue
        if len(matches) != 1:
            skipped.append({"code": code, "reason": "编码对应多个供应商，需人工核对"})
            continue
        row_id, current = matches[0]
        if any(
            full and current in {
                truncate(full, 150), _with_suffix(truncate(full, 150), f"（{code}）", 150)
            }
            for _short, full in candidates
        ):
            continue
        matching_candidates = {
            (short, full) for short, full in candidates
            if current in {truncate(short, 150), truncate(f"{short}（{code}）", 150)}
        }
        if len(matching_candidates) != 1:
            skipped.append({"code": code, "reason": "当前名称不能唯一对应旧简称，保留现有名称"})
            continue
        _short, full = next(iter(matching_candidates))
        full = truncate(full, 150)
        if not full or current == full:
            continue
        changes.append({"table": "suppliers", "type": "供应商", "id": row_id,
                        "code": code, "before": current, "after": full})

    proposed_names = defaultdict(list)
    for change in changes:
        proposed_names[change["after"]].append(change)
    for group in proposed_names.values():
        for change in group:
            occupied_by_other = any(
                other_id != change["id"] for other_id in occupied_names.get(change["after"], set())
            )
            if len(group) > 1 or occupied_by_other:
                change["after"] = _with_suffix(change["after"], f"（{change['code']}）", 150)

    final_names = defaultdict(list)
    for change in changes:
        final_names[change["after"]].append(change)
    kept = []
    for change in changes:
        occupied_by_other = any(
            other_id != change["id"] for other_id in occupied_names.get(change["after"], set())
        )
        if len(final_names[change["after"]]) > 1 or occupied_by_other:
            skipped.append({"code": change["code"], "reason": "全称及编码后缀仍与其他供应商冲突，需人工核对"})
        else:
            kept.append(change)
    changes = kept
    return changes, skipped


def apply_name_repairs(db, changes):
    """仅更新名称；用原值条件防止覆盖预览后发生的修改，由调用者提交事务。"""
    models = {"products": Product, "materials": Material, "suppliers": Supplier}
    for change in changes:
        model = models[change["table"]]
        if model is Product:
            code_field = model.product_code
        elif model is Material:
            code_field = model.material_code
        else:
            code_field = model.code
        result = db.execute(
            update(model).where(model.id == change["id"], code_field == change["code"],
                                model.name == change["before"])
            .values(name=change["after"])
        )
        if result.rowcount != 1:
            raise RuntimeError(f"{change['code']} 已发生变化，请重新预览；本批次将回滚")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, action="append",
        help="原导入数据目录，如 old_data/export；需要核对多套旧账时可重复传入",
    )
    parser.add_argument("--apply", action="store_true", help="正式提交名称修复；默认只读预览")
    args = parser.parse_args()
    item_rows = []
    supplier_rows = []
    for raw_source in args.source:
        source = Path(raw_source)
        if not source.is_absolute():
            source = BASE_DIR / source
        item_rows.extend(load_json(source, "02_ptype.json", strict=True))
        btype_rows = load_json(source, "01_btype.json", strict=True)
        doc_rows = load_json(source, "08_btype_doc_types.json")
        kind_map, _ = classify_btype(btype_rows, doc_rows, Report())
        supplier_rows.extend(
            row for row in btype_rows if kind_map.get(s(row.get("btypeid"))) == "supplier"
        )
    with SessionLocal() as db:
        item_changes, item_skipped = plan_name_repairs(db, item_rows)
        supplier_changes, supplier_skipped = plan_supplier_name_repairs(db, supplier_rows)
        changes = supplier_changes + item_changes
        skipped = supplier_skipped + item_skipped
        for change in changes:
            print(f"{change['type']} {change['code']}: {change['before']} -> {change['after']}")
        for item in skipped:
            print(f"跳过 {item['code']}: {item['reason']}")
        print(f"供应商 {sum(c['table'] == 'suppliers' for c in changes)} 条，"
              f"产品 {sum(c['table'] == 'products' for c in changes)} 条，"
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
