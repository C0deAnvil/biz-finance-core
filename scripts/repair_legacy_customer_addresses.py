"""从旧库 Area 修复被误导入为电话的客户地址，默认只读预览。"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from import_legacy_master_data import BASE_DIR, _with_suffix, is_leaf, load_json, s, truncate
from database import SessionLocal
from models import Customer
from sqlalchemy import select, update


def is_phone_text(value):
    return bool(re.fullmatch(r"[\d\s+()（）—\-/;,，；]+", value or "")) and len(re.sub(r"\D", "", value)) >= 7


def plan_address_repairs(db, rows):
    sources = defaultdict(set)
    for row in rows:
        if not is_leaf(row) or not s(row.get("busercode")):
            continue
        sources[truncate(row["busercode"], 50)].add((s(row.get("bname")) or s(row.get("bfullname")),
                                                    s(row.get("TelAndAddress")), s(row.get("Area"))))
    changes, skipped = [], []
    for customer in db.scalars(select(Customer)):
        if not is_phone_text(customer.address) or customer.code not in sources:
            continue
        candidates = sources[customer.code]
        if len(candidates) != 1:
            skipped.append({"id": customer.id, "reason": "源数据编码重复，需核对"})
            continue
        name, phone, address = next(iter(candidates))
        expected_names = {truncate(name, 150), _with_suffix(name, f"（{customer.code}）")}
        if customer.name not in expected_names or customer.address != truncate(phone, 255):
            skipped.append({"id": customer.id, "reason": "身份或原地址不匹配，保留现有资料"})
            continue
        if not address or is_phone_text(address) or len(address) > 255:
            skipped.append({"id": customer.id, "reason": "旧地址缺失、疑似电话或超过 255 字，需核对"})
            continue
        changes.append({"id": customer.id, "code": customer.code, "name": customer.name,
                        "before": customer.address, "after": address})
    return changes, skipped


def apply_address_repairs(db, changes):
    for change in changes:
        result = db.execute(update(Customer).where(
            Customer.id == change["id"], Customer.code == change["code"],
            Customer.name == change["name"], Customer.address == change["before"]
        ).values(address=change["after"]))
        if result.rowcount != 1:
            raise RuntimeError(f"客户 {change['id']} 已变化，本批次回滚，请重新预览")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="原导入 JSON 所在目录")
    parser.add_argument("--report", help="可选：将变更明细保存为本地 JSON 文件")
    parser.add_argument("--apply", action="store_true", help="正式修复地址；默认不写库")
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_absolute():
        source = BASE_DIR / source
    rows = load_json(source, "01_btype.json", strict=True)
    with SessionLocal() as db:
        changes, skipped = plan_address_repairs(db, rows)
        print(f"目标数据库：{db.get_bind().url.database}")
        print(f"可修复 {len(changes)} 条，需核对 {len(skipped)} 条")
        if args.report:
            Path(args.report).write_text(json.dumps({"changes": changes, "skipped": skipped},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
        if args.apply:
            try:
                apply_address_repairs(db, changes)
                db.commit()
            except Exception:
                db.rollback()
                raise
            print("地址修复已提交。")
        else:
            db.rollback()
            print("只读预览，未修改数据库。添加 --apply 才会正式修复。")


if __name__ == "__main__":
    main()
