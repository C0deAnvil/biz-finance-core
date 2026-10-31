"""Go-live transactional data cleanup with mandatory preview and backup guard.

Preview only (safe default):
    python scripts/go_live_cleanup.py

Execution requires an existing database backup and an exact confirmation phrase:
    python scripts/go_live_cleanup.py --execute --backup-file /path/to/backup.sql --confirm CLEAR-TRANSACTION-DATA

Uploaded files are intentionally not deleted by this script. Archive the upload
directory separately after the database cleanup so rollback remains possible.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlalchemy import func, select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import SessionLocal  # noqa: E402
from models import Base  # noqa: E402


CORE_TABLES = {
    "departments", "roles", "users", "user_module_permissions", "app_settings",
    "customers", "customer_tags", "suppliers", "supplier_tags", "supplier_catalog_items",
    "warehouses", "financial_accounts", "products", "materials", "packaging_configs",
    "boms", "bom_items", "payment_method_options", "placement_print_agents", "warehouse_scan_devices",
}
INVENTORY_TABLES = {"inventories", "inventory_batches", "inventory_cost_adjustments"}


def main() -> int:
    parser = argparse.ArgumentParser(description="正式启用前清理测试业务数据")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--backup-file")
    parser.add_argument("--keep-inventory", action="store_true", help="保留当前库存、批次和移动平均成本")
    args = parser.parse_args()
    preserved = CORE_TABLES | (INVENTORY_TABLES if args.keep_inventory else set())
    tables = [table for table in reversed(Base.metadata.sorted_tables) if table.name not in preserved]

    with SessionLocal() as db:
        print("\n将保留的核心表：")
        print(", ".join(sorted(preserved)))
        print("\n拟清空的业务表及当前行数：")
        total = 0
        for table in tables:
            count = db.scalar(select(func.count()).select_from(table)) or 0
            total += count
            print(f"  {table.name:<38} {count:>10}")
        print(f"\n合计拟删除 {total} 行。附件磁盘文件不会由本脚本直接删除。")
        if not args.execute:
            print("当前仅预览，未修改任何数据。")
            return 0
        backup = Path(args.backup_file or "")
        if args.confirm != "CLEAR-TRANSACTION-DATA" or not backup.is_file() or backup.stat().st_size == 0:
            print("拒绝执行：必须提供非空备份文件，并输入精确确认语 CLEAR-TRANSACTION-DATA。", file=sys.stderr)
            return 2
        try:
            db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in tables:
                db.execute(table.delete())
            db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            db.commit()
        except Exception:
            db.rollback()
            raise
    print("业务表清理完成。请登录后核对主数据、账号和库存起始数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
