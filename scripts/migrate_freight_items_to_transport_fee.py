"""将销售订单中误录为产品的“运费”明细迁移到订单运费字段。

默认仅预览，不会写入数据库。只匹配产品名称完全等于“运费”的行；
如有其他命名，可重复传入 --product-name。有关联发货、退货或补货记录的
明细会被跳过，以免破坏历史业务数据。

本脚本仅使用当前数据库实际存在的列，兼容尚未完成全部 Alembic 迁移的旧库。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import bindparam, text

from database import SessionLocal
from services.money import money


ZERO = Decimal("0")
# 这两类记录完全由错误的“运费产品”明细产生，迁移时应一并清除。
# 其他任何外键引用仍一律阻止删除。
REMOVABLE_ITEM_REFERENCE_TABLES = {"fulfillment_demands", "sales_order_price_change_logs"}


def _amount(value) -> Decimal:
    return Decimal(str(value or 0))


def _identifier(value: str) -> str:
    """引用 information_schema 返回的标识符，避免动态 SQL 被注入。"""
    return "`" + value.replace("`", "``") + "`"


def _table_columns(db, table_name: str) -> set[str]:
    rows = db.execute(text("""
        SELECT column_name AS column_name
        FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = :table_name
    """), {"table_name": table_name}).mappings()
    return {row["column_name"] for row in rows}


def _foreign_key_references(db, referenced_table: str) -> list[tuple[str, str]]:
    """找出数据库中实际引用指定表的外键，适配不同版本表结构。"""
    rows = db.execute(text("""
        SELECT table_name AS table_name, column_name AS column_name
        FROM information_schema.key_column_usage
        WHERE referenced_table_schema = DATABASE()
          AND referenced_table_name = :referenced_table
    """), {"referenced_table": referenced_table}).mappings()
    return [(row["table_name"], row["column_name"]) for row in rows]


def _reference_counts(db, item_id: int, references: list[tuple[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name, column_name in references:
        count = db.scalar(text(
            f"SELECT COUNT(*) FROM {_identifier(table_name)} WHERE {_identifier(column_name)} = :item_id"
        ), {"item_id": item_id}) or 0
        if count:
            counts[f"{table_name}.{column_name}"] = count
    return counts


def _orders_with_freight_items(db, names: set[str], order_nos: set[str], *, has_queue_warehouse: bool):
    # warehouse_id 是较新的队列表字段；旧库按系统既有逻辑使用默认成品仓。
    warehouse_select = (
        "dispatch_queue.warehouse_id AS warehouse_id"
        if has_queue_warehouse
        else "COALESCE((SELECT id FROM warehouses WHERE code IN ('CANGZHOU_FINISHED', 'WH-FG') ORDER BY id LIMIT 1), (SELECT id FROM warehouses WHERE warehouse_type = 'finished' ORDER BY id LIMIT 1)) AS warehouse_id"
    )
    queue_join = "LEFT JOIN inventory_dispatch_queues AS dispatch_queue ON dispatch_queue.sales_order_id = so.id" if has_queue_warehouse else ""
    query = text(f"""
        SELECT so.id AS order_id, so.order_no, so.status, so.total_amount, so.transport_fee,
               soi.id AS item_id, soi.total_amount AS item_amount,
               soi.product_id, soi.packaging_config_id, {warehouse_select},
               COALESCE(soi.placed_quantity, 0) AS placed_quantity,
               COALESCE(soi.shipped_quantity, 0) AS shipped_quantity,
               COALESCE(soi.reserved_quantity, 0) AS reserved_quantity,
               COALESCE(soi.packaging_reserved_quantity, 0) AS packaging_reserved_quantity,
               COALESCE(soi.packaging_shipped_quantity, 0) AS packaging_shipped_quantity
        FROM sales_orders AS so
        JOIN sales_order_items AS soi ON soi.sales_order_id = so.id
        JOIN products AS p ON p.id = soi.product_id
        {queue_join}
        WHERE p.name IN :names
          AND (:filter_orders = 0 OR so.order_no IN :order_nos)
        ORDER BY so.order_no, soi.id
    """).bindparams(bindparam("names", expanding=True), bindparam("order_nos", expanding=True))
    # expanding 参数不能传空列表；用一个不可能的占位订单号配合 filter_orders 关闭过滤。
    parameters = {
        "names": sorted(names),
        "filter_orders": 1 if order_nos else 0,
        "order_nos": sorted(order_nos) or ["__NO_ORDER_FILTER__"],
    }
    return db.execute(query, parameters).mappings().all()


def _item_totals(db, order_ids: list[int]) -> dict[int, Decimal]:
    query = text("""
        SELECT sales_order_id, COALESCE(SUM(total_amount), 0) AS item_total
        FROM sales_order_items
        WHERE sales_order_id IN :order_ids
        GROUP BY sales_order_id
    """).bindparams(bindparam("order_ids", expanding=True))
    return {
        row["sales_order_id"]: _amount(row["item_total"])
        for row in db.execute(query, {"order_ids": order_ids}).mappings()
    }


def _release_reservations(db, items: list[dict], *, apply: bool) -> list[str]:
    """反向释放错误运费行的置货占用；不会恢复任何已发货库存。"""
    notes: list[str] = []
    for item in items:
        warehouse_id = item["warehouse_id"]
        if warehouse_id is None:
            raise RuntimeError(f"明细#{item['item_id']} 无法确定置货仓库，不能安全释放")
        product_reserved = _amount(item["reserved_quantity"])
        packaging_reserved = _amount(item["packaging_reserved_quantity"])
        product_rows = []
        if product_reserved:
            product_rows = list(db.execute(text("""
                SELECT id, reserved_quantity FROM inventories
                WHERE warehouse_id = :warehouse_id AND product_id = :product_id AND material_id IS NULL
                ORDER BY id FOR UPDATE
            """), {"warehouse_id": warehouse_id, "product_id": item["product_id"]}).mappings())
            if sum((_amount(row["reserved_quantity"]) for row in product_rows), ZERO) < product_reserved:
                raise RuntimeError(f"明细#{item['item_id']} 的成品库存占用余额不足，不能安全释放")
        packaging_rows = []
        if packaging_reserved:
            if item["packaging_config_id"] is None:
                raise RuntimeError(f"明细#{item['item_id']} 有包装占用但未绑定包装配置，不能安全释放")
            packaging_rows = list(db.execute(text("""
                SELECT id, reserved_quantity FROM inventories
                WHERE warehouse_id = :warehouse_id AND product_id IS NULL AND material_id IS NULL
                  AND packaging_config_id = :packaging_config_id
                ORDER BY id FOR UPDATE
            """), {"warehouse_id": warehouse_id, "packaging_config_id": item["packaging_config_id"]}).mappings())
            if sum((_amount(row["reserved_quantity"]) for row in packaging_rows), ZERO) < packaging_reserved:
                raise RuntimeError(f"明细#{item['item_id']} 的包装库存占用余额不足，不能安全释放")
        if apply:
            for rows, release in ((product_rows, product_reserved), (packaging_rows, packaging_reserved)):
                remaining = release
                for row in rows:
                    if remaining <= ZERO:
                        break
                    amount = min(_amount(row["reserved_quantity"]), remaining)
                    if amount:
                        db.execute(text("UPDATE inventories SET reserved_quantity = reserved_quantity - :amount WHERE id = :id"),
                                   {"amount": amount, "id": row["id"]})
                        remaining -= amount
        notes.append(f"释放明细#{item['item_id']} 成品占用 {product_reserved}、包装占用 {packaging_reserved}，撤销置货 {_amount(item['placed_quantity'])}")
    return notes


def _freight_demands(db, item_ids: list[int]) -> list[dict]:
    """读取将随错误运费明细一并清理的补货需求。"""
    query = text("""
        SELECT id
        FROM fulfillment_demands
        WHERE sales_order_item_id IN :item_ids
    """).bindparams(bindparam("item_ids", expanding=True))
    return list(db.execute(query, {"item_ids": item_ids}).mappings())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-name", action="append", dest="product_names",
                        help="要视为运费的产品名称；可重复传入，默认仅“运费”")
    parser.add_argument("--order-no", action="append", dest="order_nos",
                        help="仅处理指定订单号；可重复传入。省略则扫描全部订单")
    parser.add_argument("--release-placement-reservation", action="store_true",
                        help="同时释放未发货运费明细的置货、成品占用和包装占用")
    parser.add_argument("--apply", action="store_true", help="确认写入数据库；默认只预览")
    args = parser.parse_args()
    names = {name.strip() for name in (args.product_names or ["运费"]) if name.strip()}
    order_nos = {no.strip() for no in (args.order_nos or []) if no.strip()}
    if not names:
        parser.error("至少提供一个非空的 --product-name")

    processed = skipped = deleted = 0
    skip_reasons: Counter[str] = Counter()
    with SessionLocal() as db:
        try:
            required_columns = {"placed_quantity", "shipped_quantity", "reserved_quantity", "packaging_reserved_quantity", "packaging_shipped_quantity"}
            missing_columns = required_columns - _table_columns(db, "sales_order_items")
            if missing_columns:
                raise RuntimeError(f"sales_order_items 缺少安全检查所需字段：{', '.join(sorted(missing_columns))}")

            queue_columns = _table_columns(db, "inventory_dispatch_queues")
            rows = _orders_with_freight_items(
                db, names, order_nos, has_queue_warehouse="warehouse_id" in queue_columns
            )
            if not rows:
                print("未找到匹配的运费产品明细。")
                return 0
            by_order: dict[int, list] = defaultdict(list)
            for row in rows:
                by_order[row["order_id"]].append(row)
            item_totals = _item_totals(db, list(by_order))
            item_references = _foreign_key_references(db, "sales_order_items")
            demand_references = _foreign_key_references(db, "fulfillment_demands")

            for order_id, freight_items in by_order.items():
                first = freight_items[0]
                order_no = first["order_no"]
                reasons: list[str] = []
                cleanup_counts: Counter[str] = Counter()
                freight_item_ids = [item["item_id"] for item in freight_items]
                release_items: list[dict] = []
                for item in freight_items:
                    quantities = {
                        "已置货": item["placed_quantity"], "已发货": item["shipped_quantity"],
                        "已占用库存": item["reserved_quantity"], "已占用包装": item["packaging_reserved_quantity"],
                        "已发包装": item["packaging_shipped_quantity"],
                    }
                    stock_state = {label: value for label, value in quantities.items() if _amount(value) != ZERO}
                    if stock_state:
                        can_release = (
                            args.release_placement_reservation
                            and _amount(item["shipped_quantity"]) == ZERO
                            and _amount(item["packaging_shipped_quantity"]) == ZERO
                        )
                        if can_release:
                            release_items.append(item)
                        else:
                            reasons.extend(f"明细#{item['item_id']} {label}={value}" for label, value in stock_state.items())
                    for table_column, count in _reference_counts(db, item["item_id"], item_references).items():
                        table_name = table_column.split(".", 1)[0]
                        if table_name in REMOVABLE_ITEM_REFERENCE_TABLES:
                            cleanup_counts[table_name] += count
                        else:
                            reasons.append(f"明细#{item['item_id']} 关联 {table_column} {count} 条")
                demands = _freight_demands(db, freight_item_ids)
                for demand in demands:
                    for table_column, count in _reference_counts(db, demand["id"], demand_references).items():
                        reasons.append(f"补货需求#{demand['id']} 关联 {table_column} {count} 条")
                old_fee = money(_amount(first["transport_fee"]))
                freight_total = money(sum((_amount(item["item_amount"]) for item in freight_items), ZERO))
                expected_total = money(item_totals[order_id] + old_fee)
                if money(_amount(first["total_amount"])) != expected_total:
                    reasons.append(f"订单总额 {money(_amount(first['total_amount']))} 与明细+原运费 {expected_total} 不一致")
                if not reasons and release_items:
                    try:
                        release_notes = _release_reservations(db, release_items, apply=False)
                    except RuntimeError as exc:
                        reasons.append(str(exc))
                        release_notes = []
                else:
                    release_notes = []
                if reasons:
                    skipped += 1
                    skip_reasons.update(reasons)
                    print(f"跳过 {order_no}：{'；'.join(reasons)}")
                    continue

                new_fee = money(old_fee + freight_total)
                cleanup_text = "、".join(f"删除 {table} {count} 条" for table, count in sorted(cleanup_counts.items()))
                if release_notes:
                    cleanup_text = "；".join([cleanup_text, *release_notes]) if cleanup_text else "；".join(release_notes)
                print(f"{'执行' if args.apply else '预览'} {order_no}：{cleanup_text}；删除 {len(freight_items)} 条运费明细（{freight_total}），订单运费 {old_fee} -> {new_fee}，订单总额保持 {expected_total}")
                if args.apply:
                    _release_reservations(db, release_items, apply=True)
                    if cleanup_counts.get("sales_order_price_change_logs"):
                        db.execute(text("DELETE FROM sales_order_price_change_logs WHERE sales_order_item_id IN :item_ids")
                                   .bindparams(bindparam("item_ids", expanding=True)), {"item_ids": freight_item_ids})
                    if demands:
                        db.execute(text("DELETE FROM fulfillment_demands WHERE id IN :demand_ids")
                                   .bindparams(bindparam("demand_ids", expanding=True)),
                                   {"demand_ids": [demand["id"] for demand in demands]})
                    db.execute(text("UPDATE sales_orders SET transport_fee = :fee, total_amount = :total WHERE id = :order_id"),
                               {"fee": new_fee, "total": expected_total, "order_id": order_id})
                    db.execute(text("DELETE FROM sales_order_items WHERE id IN :item_ids")
                               .bindparams(bindparam("item_ids", expanding=True)),
                               {"item_ids": freight_item_ids})
                processed += 1
                deleted += len(freight_items)

            if args.apply:
                db.commit()
                print(f"已提交：处理 {processed} 张订单，删除 {deleted} 条运费明细，跳过 {skipped} 张订单。")
            else:
                db.rollback()
                print(f"预览完成：可处理 {processed} 张订单、{deleted} 条运费明细；跳过 {skipped} 张订单。")
            if skip_reasons:
                print("跳过原因汇总：")
                for reason, count in skip_reasons.most_common():
                    print(f"  - {reason}（{count}）")
        except Exception:
            db.rollback()
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
