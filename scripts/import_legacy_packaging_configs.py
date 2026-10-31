"""Import parseable legacy packaging specifications as packaging configs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from database import SessionLocal  # noqa: E402
from models import Material, PackagingConfig, Product  # noqa: E402

SOURCES = ("niaosu2020", "paishui2020", "shiji2020")
PATTERNS = (
    re.compile(r"(?P<kg>\d+(?:\.\d+)?)\s*(?:kg|KG|Kg|公斤|千克)\s*[/／]\s*(?:袋|包|桶|瓶|箱|罐|件)"),
    re.compile(r"(?:每|1)\s*(?:袋|包|桶|瓶|箱|罐|件)\s*[=＝]\s*(?P<kg>\d+(?:\.\d+)?)\s*(?:kg|KG|Kg|公斤|千克)"),
)


def s(value) -> str:
    return str(value or "").strip()


def parse_kg(spec: str) -> Decimal | None:
    for pattern in PATTERNS:
        match = pattern.search(spec)
        if match:
            value = Decimal(match.group("kg"))
            if value > 0:
                return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    export_root = BASE_DIR / "old_data" / "export_20261031"
    map_root = BASE_DIR / "docs" / "legacy_migration_20261031"

    candidates: dict[tuple[str, str, str], Decimal] = {}
    for source in SOURCES:
        ptypes = {
            s(row.get("ptypeid")): row
            for row in json.loads((export_root / source / "02_ptype.json").read_text(encoding="utf-8-sig"))
        }
        with (map_root / f"{source}_product_map.csv").open(encoding="utf-8-sig", newline="") as fh:
            for mapping in csv.DictReader(fh):
                old_id = s(mapping.get("ptypeid"))
                kind = s(mapping.get("kind"))
                code = s(mapping.get("target_code"))
                row = ptypes.get(old_id, {})
                spec = s(row.get("Standard"))
                kg = parse_kg(spec)
                if kind in ("product", "material") and code and spec and kg:
                    candidates[(kind, code, spec[:100])] = kg

    db = SessionLocal()
    created = existing_count = unresolved = 0
    try:
        products = {row.product_code: row for row in db.execute(select(Product)).scalars().all()}
        materials = {row.material_code: row for row in db.execute(select(Material)).scalars().all()}
        for (kind, code, name), kg in candidates.items():
            entity = products.get(code) if kind == "product" else materials.get(code)
            if entity is None:
                unresolved += 1
                continue
            entity_type = kind
            current = db.execute(
                select(PackagingConfig).where(
                    PackagingConfig.entity_type == entity_type,
                    PackagingConfig.entity_id == entity.id,
                    PackagingConfig.packaging_name == name,
                )
            ).scalar_one_or_none()
            if current is not None:
                existing_count += 1
                continue
            has_default = db.execute(
                select(PackagingConfig.id).where(
                    PackagingConfig.entity_type == entity_type,
                    PackagingConfig.entity_id == entity.id,
                    PackagingConfig.is_default.is_(True),
                )
            ).first() is not None
            if args.apply:
                db.add(
                    PackagingConfig(
                        entity_type=entity_type,
                        entity_id=entity.id,
                        packaging_name=name,
                        kg_per_bag=kg,
                        unit_price=Decimal("0"),
                        is_default=not has_default,
                        is_active=True,
                    )
                )
            created += 1
        if args.apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(f"可识别包装规格 {len(candidates)} 条：已有 {existing_count}，新增 {created}，主数据未找到 {unresolved}")
    print("已写入。" if args.apply else "试运行，未写入。")
    return 0 if unresolved == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
