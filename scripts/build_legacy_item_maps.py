"""Generate deterministic legacy item -> ERP item maps for all three ledgers."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from sqlalchemy import select

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal  # noqa: E402
from models import Material, Product  # noqa: E402

SOURCES = ("niaosu2020", "paishui2020", "shiji2020")
PREFIX = {"niaosu2020": "NS", "paishui2020": "PS", "shiji2020": "SJ"}


def s(value) -> str:
    return str(value or "").strip()


def norm(value) -> str:
    return re.sub(r"[\s（）()\-—_·.,，。/\\]+", "", s(value)).lower()


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def is_leaf(row: dict) -> bool:
    return s(row.get("soncount")) in ("", "0")


def main() -> int:
    source_root = BASE_DIR / "old_data" / "export_20261031"
    out_root = BASE_DIR / "docs" / "legacy_migration_20261031"
    out_root.mkdir(parents=True, exist_ok=True)

    manual_kind: dict[str, str] = {}
    old_manual = BASE_DIR / "old_data" / "export" / "product_material_split.csv"
    with old_manual.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if s(row.get("ptypeid")) and s(row.get("kind")) in ("product", "material", "skip"):
                manual_kind[s(row.get("ptypeid"))] = s(row.get("kind"))

    db = SessionLocal()
    try:
        products = db.execute(select(Product)).scalars().all()
        materials = db.execute(select(Material)).scalars().all()
    finally:
        db.close()

    by_code = {}
    by_identity: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for item, kind, code in [
        *((item, "product", item.product_code) for item in products),
        *((item, "material", item.material_code) for item in materials),
    ]:
        by_code[s(code).casefold()] = (kind, s(code), norm(item.name), norm(item.specification))
        by_identity.setdefault((norm(item.name), norm(item.specification)), []).append((kind, s(code)))

    assigned_identity: dict[tuple[str, str], tuple[str, str]] = {}
    assigned_code_identity: dict[tuple[str, str], tuple[str, str]] = {}
    rows_by_source: dict[str, list[dict]] = {name: [] for name in SOURCES}
    warnings: list[str] = []

    for source_name in SOURCES:
        rows = load_json(source_root / source_name / "02_ptype.json")
        stock_item_ids = {
            s(item.get("PtypeId"))
            for item in load_json(source_root / source_name / "11_goods_stocks.json")
            if s(item.get("PtypeId"))
        }
        for row in rows:
            if not is_leaf(row) and s(row.get("ptypeid")) not in stock_item_ids:
                continue
            old_id = s(row.get("ptypeid"))
            old_code = s(row.get("pusercode")) or f"OLD_{old_id}"
            name = s(row.get("pfullname")) or s(row.get("pname"))
            spec = s(row.get("Standard"))
            identity = (norm(name), norm(spec))

            if source_name == "niaosu2020":
                kind = manual_kind.get(old_id, "product")
            else:
                kind = "product"

            match_note = "新建"
            existing_by_code = by_code.get(old_code.casefold())
            if existing_by_code and existing_by_code[2:] == identity:
                kind, target_code = existing_by_code[0], existing_by_code[1]
                match_note = "按编码+名称规格匹配现有档案"
            elif len(by_identity.get(identity, [])) == 1:
                kind, target_code = by_identity[identity][0]
                match_note = "按名称+规格匹配现有档案"
            elif identity in assigned_identity:
                kind, target_code = assigned_identity[identity]
                match_note = "跨旧账套同名同规格合并"
            else:
                target_code = old_code[:50]
                occupied = assigned_code_identity.get((kind, target_code.casefold()))
                current_collision = existing_by_code is not None and existing_by_code[2:] != identity
                if current_collision or (occupied is not None and occupied != identity):
                    target_code = f"{PREFIX[source_name]}_{old_code}"[:50]
                    warnings.append(
                        f"{source_name}:{old_id} 编码 {old_code} 与其他物品冲突，改为 {target_code}"
                    )
                    match_note = "编码冲突，增加账套前缀"

            if kind != "skip":
                assigned_identity.setdefault(identity, (kind, target_code))
                assigned_code_identity[(kind, target_code.casefold())] = identity
            rows_by_source[source_name].append(
                {
                    "ptypeid": old_id,
                    "pusercode": old_code,
                    "pname": s(row.get("pname")),
                    "pfullname": name,
                    "specification": spec,
                    "kind": kind,
                    "target_code": target_code if kind != "skip" else "",
                    "judged_by": match_note,
                    "confirmed": "Y",
                }
            )

    fields = [
        "ptypeid", "pusercode", "pname", "pfullname", "specification",
        "kind", "target_code", "judged_by", "confirmed",
    ]
    for source_name, rows in rows_by_source.items():
        path = out_root / f"{source_name}_product_map.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    warning_path = out_root / "item_mapping_warnings.txt"
    warning_path.write_text("\n".join(warnings) or "无编码冲突\n", encoding="utf-8")
    print(f"generated {sum(map(len, rows_by_source.values()))} item mappings; warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
