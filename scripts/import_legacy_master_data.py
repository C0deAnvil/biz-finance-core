"""旧系统（SQL Server · 管家婆，niaosu2020 / shiji2020 等同结构库）主数据导入。

用法
    # 1) 试运行：只读旧数据、打印将要发生的变更，不写库；同时生成两个待复核 csv
    python scripts/import_legacy_master_data.py --source old_data/export

    # 2) 复核 old_data/export/employee_role_map.csv 与 product_material_split.csv，按需修改

    # 3) 正式导入
    python scripts/import_legacy_master_data.py --source old_data/export --apply

    # 追加另一套旧库：保留已有记录，遇到唯一键冲突跳过
    python scripts/import_legacy_master_data.py --source old_data/export_shiji2020 --skip-conflicts --apply

设计要点
    · 幂等：一律按 code 匹配 upsert，重复执行不会产生重复数据
    · 顺序：部门 → 用户 → 仓库 → 产品/原料 → 客户 → 供应商 → 负责人 → BOM → 库存
    · 不猜权限：员工角色由 employee_role_map.csv 决定，试运行先生成供人工确认
    · 不丢信息：新系统没有的字段统一汇入 remark / notes / ext_json，并在报告里列明
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from config import settings  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    BOM,
    BOMItem,
    Customer,
    CustomerTag,
    Department,
    Inventory,
    InventoryBatch,
    Material,
    Product,
    Role,
    Supplier,
    User,
    Warehouse,
)
from security import hash_password  # noqa: E402
from services.tag_catalog import CUSTOMER_PROVINCES  # noqa: E402
from services.time_utils import now_local  # noqa: E402


# ======================================================================
# 拼音
# ======================================================================
try:
    from pypinyin import Style, lazy_pinyin

    HAS_PYPINYIN = True
except ImportError:  # 没装就回退，脚本仍可跑，但用户名需要人工订正
    HAS_PYPINYIN = False


def to_pinyin(name: str) -> str:
    """中文姓名转全拼小写，去掉非字母字符。"""
    cleaned = re.sub(r"[^一-龥A-Za-z]", "", name or "")
    if not cleaned:
        return ""
    if HAS_PYPINYIN:
        return "".join(lazy_pinyin(cleaned, style=Style.NORMAL)).lower()
    return re.sub(r"[^a-z]", "", cleaned.lower())


# ======================================================================
# 工具
# ======================================================================
def load_json(source: Path, filename: str, *, strict: bool = False) -> list[dict]:
    """读 sqlcmd 导出的 FOR JSON 结果。文件不存在返回空列表。"""
    path = source / filename
    if not path.exists():
        if strict:
            raise ValueError(f"缺少导出文件：{path}；请完整导出后重试")
        print(f"    [跳过] 找不到 {filename}")
        return []
    raw = path.read_text(encoding="utf-8-sig", errors="strict" if strict else "replace").strip()
    if not raw:
        if strict:
            raise ValueError(f"导出文件为空：{path}；无数据的文件应为 []")
        return []
    # sqlcmd 会把超长 JSON 按行折断，拼回来即可
    raw = "".join(line.strip() for line in raw.splitlines() if not line.startswith("-"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError(f"导出文件 JSON 损坏：{path}") from exc
        print(f"    [错误] {filename} 解析失败：{exc}")
        return []
    if strict and (not isinstance(data, list) or not all(isinstance(row, dict) for row in data)):
        raise ValueError(f"导出文件应为对象数组：{path}")
    return data if isinstance(data, list) else [data]


def s(value) -> str:
    """转成去空白的字符串，None 变空串。"""
    if value is None:
        return ""
    return str(value).strip()


def dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def first_nonempty(*values) -> str:
    for value in values:
        text = s(value)
        if text:
            return text
    return ""


def is_stopped(row: dict, *keys) -> bool:
    for key in keys:
        if s(row.get(key)) not in ("", "0"):
            return True
    return False


def truncate(text: str, limit: int) -> str:
    text = s(text)
    return text[:limit] if len(text) > limit else text


def detect_province(*texts) -> str | None:
    """从地址/地区文本里识别省级行政区。"""
    blob = " ".join(s(t) for t in texts)
    if not blob:
        return None
    for province in CUSTOMER_PROVINCES:
        if province in blob:
            return province
        # 「河北省」也匹配「河北」
        short = province.rstrip("省市自治区特别行政区")
        if len(short) >= 2 and short in blob:
            return province
    return None


CREDIT_CODE_PATTERN = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")


class Report:
    """收集导入过程中的统计与告警，最后统一打印。"""

    def __init__(self, skip_conflicts: bool = False, fill_existing_empty: bool = False):
        self.stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.warnings: list[str] = []
        self.skip_conflicts = skip_conflicts
        self.fill_existing_empty = fill_existing_empty
        self.pending: list = []  # 试运行中新建的对象，不加入数据库会话
        self.seen: dict[str, set] = defaultdict(set)

    def conflicts(self, db: Session, model, label: str, **keys) -> list:
        """使用目标库自身的比较规则检查唯一键；同时检查试运行的待新增对象。"""
        if not self.skip_conflicts:
            return []
        keys = {key: value for key, value in keys.items() if value not in (None, "")}
        matches = list(db.execute(select(model).where(or_(
            *(getattr(model, key) == value for key, value in keys.items())
        ))).scalars())
        for obj in self.pending:
            if isinstance(obj, model) and any(
                s(getattr(obj, key)).casefold() == s(value).casefold() for key, value in keys.items()
            ):
                if obj not in matches:
                    matches.append(obj)
        if matches:
            self.count(label, "冲突跳过")
        return matches

    def remember(self, db: Session, obj, apply: bool):
        if self.skip_conflicts:
            if apply:
                # SessionLocal 关闭 autoflush；逐条 flush 才能发现同一批里的重复。
                db.flush()
            else:
                self.pending.append(obj)

    def reuse(self, matches: list, mapping: dict, old_id: str, **identity):
        """只有身份字段完全一致才复用关联，防止两套旧库相同编码指向不同实体。"""
        if len(matches) == 1 and all(
            s(getattr(matches[0], key)) == s(value) for key, value in identity.items()
        ):
            mapping[old_id] = matches[0]
        else:
            self.warn(f"旧 id {old_id} 的冲突档案身份不一致，跳过其关联映射，请人工核对")

    def count(self, section: str, key: str, n: int = 1):
        self.stats[section][key] += n

    def warn(self, message: str):
        self.warnings.append(message)

    def dump(self):
        print("\n" + "=" * 68)
        print("导入报告")
        print("=" * 68)
        for section, counters in self.stats.items():
            parts = "  ".join(f"{k}={v}" for k, v in sorted(counters.items()))
            print(f"  {section:14s} {parts}")
        if self.warnings:
            print(f"\n  告警 {len(self.warnings)} 条：")
            for message in self.warnings[:80]:
                print(f"    · {message}")
            if len(self.warnings) > 80:
                print(f"    …… 另有 {len(self.warnings) - 80} 条")
        print("=" * 68)


# ======================================================================
# 树形结构工具：btype / ptype 都是 parid + leveal 的树
# ======================================================================
def build_tree_index(rows: list[dict], id_key: str, parent_key: str) -> dict[str, dict]:
    return {s(row.get(id_key)): row for row in rows if s(row.get(id_key))}


def root_of(node_id: str, index: dict[str, dict], id_key: str, parent_key: str, name_key: str) -> str:
    """一路向上找到顶层节点的名称，用于分类归属判断。"""
    seen = set()
    current = index.get(node_id)
    last_name = ""
    while current is not None:
        current_id = s(current.get(id_key))
        if current_id in seen:
            break
        seen.add(current_id)
        last_name = s(current.get(name_key)) or last_name
        parent_id = s(current.get(parent_key))
        if not parent_id or parent_id == current_id:
            break
        parent = index.get(parent_id)
        if parent is None:
            break
        current = parent
    return last_name


def is_leaf(row: dict) -> bool:
    """有子节点的是分类目录，不是真实档案。"""
    return s(row.get("soncount")) in ("", "0")


# ======================================================================
# 各段导入
# ======================================================================
def import_departments(db: Session, rows: list[dict], report: Report, apply: bool) -> dict[str, Department]:
    """返回 旧 dtypeid -> Department 的映射。"""
    print("\n[1/9] 部门")
    mapping: dict[str, Department] = {}
    pending_parent: list[tuple[str, str]] = []

    for row in rows:
        old_id = s(row.get("dtypeid"))
        name = truncate(first_nonempty(row.get("dname"), row.get("dfullname")), 100)
        if not old_id or not name:
            continue
        code = truncate(first_nonempty(row.get("dusercode"), f"OLD_{old_id}"), 50)
        conflicts = report.conflicts(db, Department, "部门", code=code, name=name)
        if conflicts:
            report.reuse(conflicts, mapping, old_id, code=code, name=name)
            continue
        existing = db.execute(select(Department).where(Department.code == code)).scalar_one_or_none()
        if existing is None:
            existing = db.execute(select(Department).where(Department.name == name)).scalar_one_or_none()
        if existing is None:
            existing = Department(
                code=code,
                name=name,
                description=truncate(row.get("dcomment"), 255),
                unit_type="department",
                is_active=not is_stopped(row, "isStop"),
            )
            if apply:
                db.add(existing)
            report.count("部门", "新增")
        else:
            existing.name = name
            report.count("部门", "已存在")
        mapping[old_id] = existing
        report.remember(db, existing, apply)
        parent_old = s(row.get("parid"))
        if parent_old and parent_old != old_id:
            pending_parent.append((old_id, parent_old))

    if apply:
        db.flush()
        for child_old, parent_old in pending_parent:
            child = mapping.get(child_old)
            parent = mapping.get(parent_old)
            if child is not None and parent is not None and parent.id and child.id != parent.id:
                child.parent_id = parent.id
                child.unit_type = "sub_department"
        db.flush()
    print(f"    部门 {len(mapping)} 个")
    return mapping


def resolve_employee_roles(
    source: Path, rows: list[dict], owner_ids: set[str], report: Report
) -> dict[str, str]:
    """决定每个员工的角色。优先读人工复核过的 csv，没有就按关键词推断并写出 csv。"""
    csv_path = source / "employee_role_map.csv"
    keyword_rules = [
        ("sales", ("销售", "业务", "客服")),
        ("purchase", ("采购", "供应")),
        ("inventory", ("仓库", "库管", "仓储", "物流")),
        ("production", ("生产", "车间", "制造")),
        ("quality", ("质检", "品控", "质量", "化验")),
        ("finance", ("财务", "会计", "出纳")),
        ("manager", ("经理", "总监", "厂长", "主管")),
    ]

    if csv_path.exists():
        mapping: dict[str, str] = {}
        confirmed = 0
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            for item in csv.DictReader(fh):
                etypeid = s(item.get("etypeid"))
                role = s(item.get("role_code"))
                if s(item.get("confirmed")).upper() == "Y":
                    confirmed += 1
                if etypeid and role:
                    mapping[etypeid] = role
        # 脚本无法区分「人工确认过」和「上一轮自己猜的」，靠 confirmed 列显式标记
        if confirmed == 0:
            print(f"    [!] 读取角色表 {csv_path.name}（{len(mapping)} 条）—— "
                  f"confirmed 列全空，这看起来还是脚本上一轮自己猜的结果")
            report.warn(f"{csv_path.name} 未经人工确认（confirmed 列全空），角色分配可能不准")
        else:
            print(f"    读取角色表：{csv_path.name}（{len(mapping)} 条，已确认 {confirmed} 条）")
        return mapping

    mapping = {}
    out_rows = []
    for row in rows:
        etypeid = s(row.get("etypeid"))
        if not etypeid or not is_leaf(row):
            continue
        blob = " ".join(s(row.get(k)) for k in ("duty", "ecomment", "efullname"))
        role = ""
        for role_code, keywords in keyword_rules:
            if any(keyword in blob for keyword in keywords):
                role = role_code
                break
        source_note = f"职务关键词({blob.strip()[:10]})" if role else ""
        if not role and etypeid in owner_ids:
            role = "sales"
            source_note = "名下有客户"
        if not role:
            role = UNRESOLVED_ROLE_MARKER
            source_note = "未识别"
            name_for_log = first_nonempty(row.get("ename"), row.get("efullname")) or etypeid
            report.warn(f"员工 {name_for_log}（{etypeid}）无法判断角色，暂按 sales，请在 csv 里确认")
        mapping[etypeid] = role
        out_rows.append(
            {
                "etypeid": etypeid,
                # ename 常为空，真实姓名在 efullname 里；不取全名的话生成的 csv 没法给人复核
                "ename": first_nonempty(row.get("ename"), row.get("efullname")),
                "efullname": s(row.get("efullname")),
                "duty": s(row.get("duty")),
                "department": s(row.get("Department")),
                "has_customers": "Y" if etypeid in owner_ids else "",
                "role_code": role,
                "judged_by": source_note,
                "confirmed": "",
            }
        )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["etypeid", "ename", "efullname", "duty", "department", "has_customers", "role_code", "judged_by", "confirmed"],
        )
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"    已生成角色待确认表：{csv_path}（{len(out_rows)} 人）")
    print("    >>> 请复核 role_code 列后重新执行；judged_by=未识别 的务必人工确认")
    return mapping


def import_employees(
    db: Session,
    rows: list[dict],
    dept_map: dict[str, Department],
    role_map: dict[str, str],
    report: Report,
    apply: bool,
) -> dict[str, User]:
    """返回 旧 etypeid -> User 的映射。"""
    print("\n[2/9] 员工 / 用户账号")
    roles = {r.code: r for r in db.execute(select(Role)).scalars().all()}
    if not roles:
        report.warn("新系统没有任何角色，请先启动一次后端让 seed.py 初始化")
        return {}

    used_usernames = set(db.execute(select(User.username)).scalars().all())

    mapping: dict[str, User] = {}
    default_password = settings.default_user_password

    for row in rows:
        old_id = s(row.get("etypeid"))
        name = first_nonempty(row.get("ename"), row.get("efullname"))
        if not old_id or not name or not is_leaf(row):
            continue

        role_code = role_map.get(old_id, "sales")
        role = roles.get(role_code) or roles.get("sales")
        if role is None:
            report.warn(f"角色 {role_code} 不存在，跳过员工 {name}")
            continue

        pinyin = to_pinyin(name)
        if not pinyin:
            pinyin = re.sub(r"[^a-z0-9]", "", s(row.get("enamepy")).lower()) or f"emp{old_id}"
            report.warn(f"员工 {name} 无法生成拼音，用户名回退为 cphw_{pinyin}，请人工确认")
        username = f"cphw_{pinyin}"[:50]
        conflicts = report.conflicts(db, User, "员工", username=username)
        if conflicts:
            report.reuse(conflicts, mapping, old_id, username=username, full_name=truncate(name, 100))
            continue

        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing is None and username in used_usernames:
            # 同名不同人：加数字后缀
            for suffix in range(2, 100):
                candidate = f"cphw_{pinyin}{suffix}"[:50]
                if candidate not in used_usernames:
                    username = candidate
                    report.warn(f"用户名冲突，{name} 改用 {username}")
                    break
            existing = None

        active = not is_stopped(row, "isStop") and s(row.get("Incumbency")) not in ("0",)
        # 角色判断不出来的（名下无客户、职务为空），先建成停用账号，
        # 确认岗位后再到系统管理里启用并改角色
        if role_map.get(old_id) == UNRESOLVED_ROLE_MARKER:
            active = False
            role = roles.get("sales") or role
            report.count("员工", "角色待确认(已停用)")
        department = dept_map.get(s(row.get("Department")))

        if existing is None:
            existing = User(
                username=username,
                full_name=truncate(name, 100),
                hashed_password=hash_password(default_password),
                phone=truncate(row.get("Tel"), 30) or None,
                email=truncate(row.get("EMail"), 120) or None,
                is_active=active,
                role_id=role.id,
                department_id=department.id if department is not None else None,
                job_title=truncate(row.get("duty"), 100) or None,
                data_scope="self",
            )
            if apply:
                db.add(existing)
            used_usernames.add(username)
            report.count("员工", "新增")
            report.count("员工", "停用" if not active else "在职")
        else:
            existing.full_name = truncate(name, 100)
            existing.is_active = active
            if department is not None:
                existing.department_id = department.id
            report.count("员工", "已存在")
        mapping[old_id] = existing

        report.remember(db, existing, apply)
    if apply:
        db.flush()
    print(f"    员工 {len(mapping)} 人")
    return mapping


def load_existing_user_map(
    db: Session, source: Path, report: Report, mapping_path: Path | None = None
) -> dict[str, User]:
    """Resolve legacy employee ids to accounts that already exist in the ERP.

    The optional CSV has columns ``etypeid,target_username,confirmed``.  It is
    deliberately explicit: importing another ledger must not create duplicate
    accounts or guess between employees with the same name.
    """
    path = mapping_path or (source / "existing_user_map.csv")
    if not path.exists():
        return {}
    result: dict[str, User] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            old_id = s(row.get("etypeid"))
            username = s(row.get("target_username"))
            if not old_id or not username:
                continue
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                report.warn(f"人员映射 {old_id} 指向不存在的账号 {username}")
                continue
            result[old_id] = user
    if result:
        print(f"    读取现有账号映射：{path.name}（{len(result)} 人）")
    return result


def import_warehouses(
    db: Session,
    rows: list[dict],
    report: Report,
    apply: bool,
    source: Path | None = None,
    mapping_path: Path | None = None,
) -> dict[str, Warehouse]:
    print("\n[3/9] 仓库")
    mapping: dict[str, Warehouse] = {}
    explicit: dict[str, Warehouse] = {}
    map_path = mapping_path or (source / "existing_warehouse_map.csv" if source is not None else None)
    if map_path is not None and map_path.exists():
        with map_path.open(encoding="utf-8-sig", newline="") as fh:
            for item in csv.DictReader(fh):
                old_id = s(item.get("ktypeid"))
                target_code = s(item.get("target_warehouse_code"))
                if not old_id or not target_code:
                    continue
                warehouse = db.execute(select(Warehouse).where(Warehouse.code == target_code)).scalar_one_or_none()
                if warehouse is None:
                    report.warn(f"仓库映射 {old_id} 指向不存在的仓库编码 {target_code}")
                    continue
                explicit[old_id] = warehouse
        if explicit:
            print(f"    读取现有仓库映射：{map_path.name}（{len(explicit)} 个）")
    for row in rows:
        old_id = s(row.get("ktypeid"))
        name = truncate(first_nonempty(row.get("kname"), row.get("kfullname")), 100)
        if not old_id or not name or not is_leaf(row):
            continue
        if old_id in explicit:
            mapping[old_id] = explicit[old_id]
            report.count("仓库", "显式映射")
            continue
        code = truncate(first_nonempty(row.get("kusercode"), f"OLD_{old_id}"), 50)
        conflicts = report.conflicts(db, Warehouse, "仓库", code=code, name=name)
        if conflicts:
            report.reuse(conflicts, mapping, old_id, code=code, name=name)
            continue

        blob = name + s(row.get("kcomment"))
        if any(k in blob for k in ("原料", "材料", "辅料")):
            warehouse_type = "raw"
        elif any(k in blob for k in ("成品", "产成品", "商品")):
            warehouse_type = "finished"
        else:
            warehouse_type = "finished"
            report.warn(f"仓库「{name}」无法判断类型，暂按成品仓，请人工核对")

        existing = db.execute(select(Warehouse).where(Warehouse.code == code)).scalar_one_or_none()
        if existing is None:
            existing = db.execute(select(Warehouse).where(Warehouse.name == name)).scalar_one_or_none()
        if existing is None:
            existing = Warehouse(
                code=code,
                name=name,
                location=truncate(row.get("Address"), 255) or None,
                warehouse_type=warehouse_type,
            )
            if apply:
                db.add(existing)
            report.count("仓库", "新增")
        else:
            report.count("仓库", "已存在")
        mapping[old_id] = existing
        report.remember(db, existing, apply)
    if apply:
        db.flush()
    print(f"    仓库 {len(mapping)} 个")
    return mapping


def split_products(
    source: Path, rows: list[dict], report: Report, mapping_path: Path | None = None
) -> dict[str, str]:
    """决定每个商品归 product 还是 material。优先读人工复核过的 csv。"""
    csv_path = mapping_path or (source / "product_material_split.csv")
    if csv_path.exists():
        mapping = {}
        confirmed = 0
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            for item in csv.DictReader(fh):
                ptypeid = s(item.get("ptypeid"))
                kind = s(item.get("kind")).lower()
                if s(item.get("confirmed")).upper() == "Y":
                    confirmed += 1
                # skip = 明确不导入（运费、押金这类非货品）
                if ptypeid and kind in ("product", "material", "skip"):
                    mapping[ptypeid] = kind
        if confirmed == 0:
            print(f"    [!] 读取拆分表 {csv_path.name}（{len(mapping)} 条）—— "
                  f"confirmed 列全空，这看起来还是脚本上一轮自己猜的结果")
            report.warn(f"{csv_path.name} 未经人工确认（confirmed 列全空），产品/原料拆分可能不准")
        else:
            counts: dict[str, int] = defaultdict(int)
            for v in mapping.values():
                counts[v] += 1
            print(f"    读取拆分表：{csv_path.name}（已确认 {confirmed} 条 · "
                  f"产品 {counts['product']} / 原料 {counts['material']} / 不导入 {counts['skip']}）")
        return mapping

    mapping = {}
    out_rows = []
    for row in rows:
        ptypeid = s(row.get("ptypeid"))
        if not ptypeid or not is_leaf(row):
            continue
        path_text = s(row.get("pfullname")) + s(row.get("Type"))
        zz = s(row.get("sourcewayzz")) not in ("", "0")
        wg = s(row.get("sourcewaywg")) not in ("", "0")
        if any(k in path_text for k in ("原料", "材料", "辅料", "助剂")):
            kind, why = "material", "分类路径含原料关键词"
        elif wg and not zz:
            kind, why = "material", "纯外购"
        else:
            kind, why = "product", "默认按产品"
        mapping[ptypeid] = kind
        out_rows.append(
            {
                "ptypeid": ptypeid,
                "pusercode": s(row.get("pusercode")),
                "pname": s(row.get("pname")),
                "pfullname": s(row.get("pfullname")),
                "self_made": "Y" if zz else "",
                "purchased": "Y" if wg else "",
                "kind": kind,
                "judged_by": why,
                "confirmed": "",
            }
        )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["ptypeid", "pusercode", "pname", "pfullname", "self_made", "purchased", "kind", "judged_by", "confirmed"],
        )
        writer.writeheader()
        writer.writerows(out_rows)
    counts = defaultdict(int)
    for kind in mapping.values():
        counts[kind] += 1
    print(f"    已生成拆分待确认表：{csv_path}（产品 {counts['product']} / 原料 {counts['material']}）")
    print("    >>> 请复核 kind 列后重新执行")
    return mapping


def import_products(
    db: Session,
    rows: list[dict],
    prices: list[dict],
    kind_map: dict[str, str],
    target_code_map: dict[str, str],
    report: Report,
    apply: bool,
) -> tuple[dict[str, Product], dict[str, Material]]:
    print("\n[4/9] 产品 / 原料")
    # 每个商品取最新一条价格
    latest_price: dict[str, Decimal] = {}
    for row in sorted(prices, key=lambda r: s(r.get("UpDateDt")), reverse=True):
        ptypeid = s(row.get("PTypeId"))
        if ptypeid and ptypeid not in latest_price:
            latest_price[ptypeid] = dec(row.get("Price"))

    product_map: dict[str, Product] = {}
    material_map: dict[str, Material] = {}

    for row in rows:
        old_id = s(row.get("ptypeid"))
        # pname 是旧系统简称；名称应优先保留包含浓度、型号等信息的全称。
        name = truncate(first_nonempty(row.get("pfullname"), row.get("pname")), 200)
        if not old_id or not name or (not is_leaf(row) and old_id not in target_code_map):
            continue
        kind = kind_map.get(old_id, "product")
        if kind == "skip":
            report.count("产品", "按拆分表跳过")
            continue
        code = truncate(
            first_nonempty(target_code_map.get(old_id), row.get("pusercode"), f"OLD_{old_id}"),
            50,
        )
        price = latest_price.get(old_id) or dec(row.get("LastBuyPrice"))
        if price <= 0:
            report.warn(f"{name}（{code}）没有价格，写 0，请后续补录")
        unit = truncate(first_nonempty(row.get("punitname"), "kg"), 20)
        spec = truncate(row.get("Standard"), 255) or None
        active = not is_stopped(row, "isStop")
        extra = first_nonempty(row.get("pcomment"), row.get("Type"))
        if extra:
            report.count("产品", "带备注")

        if kind == "material":
            conflicts = report.conflicts(db, Material, "原料", material_code=code)
            if conflicts:
                report.reuse(conflicts, material_map, old_id, material_code=code, name=name, unit=unit, specification=spec)
                continue
            existing = db.execute(select(Material).where(Material.material_code == code)).scalar_one_or_none()
            if existing is None:
                existing = Material(
                    material_code=code,
                    name=name,
                    price=price,
                    specification=spec,
                    unit=unit,
                    safety_stock_qty=dec(row.get("warnup")),
                    purchase_threshold_qty=dec(row.get("warndown")),
                    is_active=active,
                )
                if apply:
                    db.add(existing)
                report.count("原料", "新增")
            else:
                existing.name, existing.price, existing.is_active = name, price, active
                report.count("原料", "已存在")
            material_map[old_id] = existing
        else:
            conflicts = report.conflicts(db, Product, "产品", product_code=code)
            if conflicts:
                report.reuse(conflicts, product_map, old_id, product_code=code, name=name, unit=unit, specification=spec)
                continue
            existing = db.execute(select(Product).where(Product.product_code == code)).scalar_one_or_none()
            if existing is None:
                existing = Product(
                    product_code=code,
                    name=name,
                    price=price,
                    specification=spec,
                    unit=unit,
                    safety_stock_qty=dec(row.get("warnup")),
                    purchase_threshold_qty=dec(row.get("warndown")),
                    fulfillment_mode="production" if s(row.get("sourcewayzz")) not in ("", "0") else "trade",
                    is_active=active,
                )
                if apply:
                    db.add(existing)
                report.count("产品", "新增")
            else:
                existing.name, existing.price, existing.is_active = name, price, active
                report.count("产品", "已存在")
            product_map[old_id] = existing

        report.remember(db, existing, apply)
    if apply:
        db.flush()
    print(f"    产品 {len(product_map)} / 原料 {len(material_map)}")
    return product_map, material_map


# 旧库实测的一级分类（2026-09 导出）：
#   单位(根) ├─ 客户 5665 家 ├─ 重复客户 204 家 ├─ 供应商 25 家
# 「重复客户」是旧系统里人工标出的疑似重复档案，按约定照常导入，
# 但自动打上 DUP 标签，方便在新系统里筛出来慢慢并。
DUPLICATE_BRANCH_KEYWORD = "重复"
DUPLICATE_TAG = "待去重"

# 角色判断不出来时写入 csv 的占位值：这类账号建出来但默认停用
UNRESOLVED_ROLE_MARKER = "__unresolved__"


def classify_btype(rows: list[dict], doc_types: list[dict], report: Report) -> tuple[dict[str, str], set[str]]:
    """判断每个往来单位是客户还是供应商。

    实测发现 bfullname 并不总是带分类路径（约 48 条命中不了任何关键词），
    所以改为沿 parid 一路上溯到 leveal==1 的一级分类节点，读它的 bname。
    旧库实测的一级分类：客户 5665 / 重复客户 204 / 供应商 25。

    返回 (归类映射, 疑似重复客户的 id 集合)
    """
    index = build_tree_index(rows, "btypeid", "parid")

    def level1_branch(row: dict) -> str:
        """沿父链上溯，返回 leveal==1 那一层的分类名。"""
        seen: set[str] = set()
        current = row
        while current is not None:
            if current.get("leveal") == 1:
                return s(current.get("bname"))
            parent_id = s(current.get("parid"))
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            current = index.get(parent_id)
        return ""

    result: dict[str, str] = {}
    duplicates: set[str] = set()
    branch_counts: dict[str, int] = defaultdict(int)
    unknown: list[str] = []

    for row in rows:
        btypeid = s(row.get("btypeid"))
        if not btypeid or not is_leaf(row):
            continue
        branch = level1_branch(row)
        branch_counts[branch or "(未归类)"] += 1

        if any(k in branch for k in ("供应商", "采购", "厂商", "供货")):
            result[btypeid] = "supplier"
        elif any(k in branch for k in ("客户", "客商", "销售")):
            result[btypeid] = "customer"
            if DUPLICATE_BRANCH_KEYWORD in branch:
                duplicates.add(btypeid)
        else:
            unknown.append(btypeid)

    # 兜底：分类树判断不了的，看它出现在哪类单据上
    if unknown and doc_types:
        sale_docs: dict[str, int] = defaultdict(int)
        buy_docs: dict[str, int] = defaultdict(int)
        for row in doc_types:
            btypeid = s(row.get("btypeid"))
            name = s(row.get("vchname"))
            count = int(dec(row.get("doc_count")))
            if any(k in name for k in ("销售", "销货", "收款")):
                sale_docs[btypeid] += count
            elif any(k in name for k in ("采购", "进货", "付款")):
                buy_docs[btypeid] += count
        resolved = 0
        for btypeid in unknown:
            sale_n, buy_n = sale_docs.get(btypeid, 0), buy_docs.get(btypeid, 0)
            if sale_n == 0 and buy_n == 0:
                result[btypeid] = "customer"
                report.warn(f"往来单位 {btypeid} 既无分类也无单据，暂按客户处理")
            elif sale_n >= buy_n:
                result[btypeid] = "customer"
                resolved += 1
            else:
                result[btypeid] = "supplier"
                resolved += 1
        if resolved:
            print(f"    有 {resolved} 条靠单据类型兜底判断")
    else:
        for btypeid in unknown:
            result[btypeid] = "customer"
            report.warn(f"往来单位 {btypeid} 无法判断客户/供应商，暂按客户处理")

    # 旧库「重复客户」分类之外，同名且电话+地址完全一致的也当疑似重复
    from collections import defaultdict as _dd
    contact_groups: dict[tuple, list[str]] = _dd(list)
    for row in rows:
        btypeid = s(row.get("btypeid"))
        if not btypeid or not is_leaf(row) or result.get(btypeid) != "customer":
            continue
        key = (s(row.get("bname")), s(row.get("moPhone")), s(row.get("TelAndAddress")))
        if key[0]:
            contact_groups[key].append(btypeid)
    same_contact = 0
    for ids in contact_groups.values():
        if len(ids) > 1:
            for btypeid in ids:
                if btypeid not in duplicates:
                    duplicates.add(btypeid)
                    same_contact += 1
    if same_contact:
        print(f"    另有 {same_contact} 家同名且联系方式完全一致，一并标记待去重")

    print("    一级分类分布：" + "、".join(f"{k} {v}" for k, v in sorted(branch_counts.items(), key=lambda kv: -kv[1])))
    counts: dict[str, int] = defaultdict(int)
    for kind in result.values():
        counts[kind] += 1
    print(f"    归类结果：客户 {counts['customer']}（其中疑似重复 {len(duplicates)}）/ 供应商 {counts['supplier']}")
    return result, duplicates


def build_name_disambiguation(rows: list[dict], kind_map: dict[str, str], kind: str,
                             id_key: str, name_key: str | tuple[str, ...], code_key: str) -> dict[str, str]:
    """同名的往来单位，名称后面挂上旧编码以满足新系统的唯一约束。

    实测 196 组重名、493 家，其中 182 组组内电话或地址不同 —— 是真实的不同实体，
    不能合并。加后缀既保住全部记录，搜原名也还能搜到。
    """
    from collections import Counter as _Counter
    name_keys = (name_key,) if isinstance(name_key, str) else name_key

    def source_name(row: dict) -> str:
        return first_nonempty(*(row.get(key) for key in name_keys))

    names = _Counter()
    for row in rows:
        old_id = s(row.get(id_key))
        if kind_map.get(old_id) != kind or not is_leaf(row):
            continue
        name = source_name(row)
        if name:
            names[name] += 1

    result: dict[str, str] = {}
    for row in rows:
        old_id = s(row.get(id_key))
        if kind_map.get(old_id) != kind or not is_leaf(row):
            continue
        name = source_name(row)
        if name and names[name] > 1:
            code = s(row.get(code_key)) or f"OLD_{old_id}"
            result[old_id] = truncate(f"{name}（{code}）", 150)
    return result


def _with_suffix(name: str, suffix: str, limit: int = 150) -> str:
    """加后缀且保证总长不超限——截的是名字本身，后缀必须完整保留。"""
    room = limit - len(suffix)
    if room < 1:
        return suffix[-limit:]
    return name[:room] + suffix


def make_name_claimer(db: Session, model, report: Report, label: str):
    """名称唯一约束的兜底。

    build_name_disambiguation 只解决「旧库内部重名」。这里解决另一种撞车：
    旧库的名字和目标库里**已有**的记录重名，而两边编码对不上，
    所以按 code 匹配的 upsert 发现不了，直到 flush 才抛 1062。

    占用时按 名称（旧编码） 改写，仍撞就再挂 ·2、·3。
    """
    taken = {n for n in db.execute(select(model.name)).scalars().all() if n}

    def claim(name: str, code: str, existing_name: str | None = None) -> str:
        if existing_name is not None:
            if existing_name == name:
                return name          # 本来就是这条记录自己的名字
            taken.discard(existing_name)
        if name not in taken:
            taken.add(name)
            return name
        candidate = _with_suffix(name, f"（{code}）")
        n = 2
        while candidate in taken:
            candidate = _with_suffix(name, f"（{code}·{n}）")
            n += 1
        taken.add(candidate)
        report.count(label, "因重名改写")
        return candidate

    return claim


def fill_empty_fields(obj, values: dict, report: Report, section: str) -> int:
    """Fill only blank target fields; never replace a maintained value."""
    changed = 0
    for key, value in values.items():
        if value in (None, ""):
            continue
        current = getattr(obj, key, None)
        if current in (None, ""):
            setattr(obj, key, value)
            changed += 1
            report.count(section, f"补齐{key}")
    if changed:
        report.count(section, "补齐已有档案")
    else:
        report.count(section, "已有档案无需补齐")
    return changed


def import_customers(
    db: Session,
    rows: list[dict],
    kind_map: dict[str, str],
    duplicates: set[str],
    owner_rows: list[dict],
    user_map: dict[str, User],
    report: Report,
    apply: bool,
) -> dict[str, Customer]:
    print("\n[5/9] 客户")
    owner_of = {s(r.get("btypeid")): s(r.get("etypeid")) for r in owner_rows}
    name_overrides = build_name_disambiguation(rows, kind_map, "customer", "btypeid", "bname", "busercode")
    if report.skip_conflicts:
        name_overrides = {}  # 跳过模式按原名称查重，不通过改名绕开冲突
    if name_overrides:
        print(f"    {len(name_overrides)} 家重名客户的名称已挂旧编码后缀以区分")
    used_credit_codes = {
        code for code in db.execute(select(Customer.unified_social_credit_code)).scalars().all() if code
    }
    claim_name = make_name_claimer(db, Customer, report, "客户")
    mapping: dict[str, Customer] = {}

    for row in rows:
        old_id = s(row.get("btypeid"))
        if kind_map.get(old_id) != "customer":
            continue
        name = name_overrides.get(old_id) or truncate(first_nonempty(row.get("bname"), row.get("bfullname")), 150)
        if not old_id or not name:
            continue
        code = truncate(first_nonempty(row.get("busercode"), f"OLD_{old_id}"), 50)

        raw_credit = s(row.get("TaxNumber")).upper()
        conflicts = report.conflicts(
            db, Customer, "客户", code=code, name=name,
            unified_social_credit_code=raw_credit if CREDIT_CODE_PATTERN.match(raw_credit) else None,
        )
        if conflicts:
            if report.fill_existing_empty and len(conflicts) == 1:
                existing = conflicts[0]
                owner = user_map.get(owner_of.get(old_id, ""))
                fill_empty_fields(
                    existing,
                    {
                        "contact_name": truncate(first_nonempty(row.get("Person"), row.get("linker")), 100),
                        "phone": truncate(first_nonempty(row.get("moPhone"), row.get("linkerTel"), row.get("TelAndAddress")), 30),
                        "address": truncate(row.get("Area"), 255),
                        "region_province": detect_province(row.get("Area"), row.get("TelAndAddress")),
                        "unified_social_credit_code": raw_credit if CREDIT_CODE_PATTERN.match(raw_credit) else None,
                    },
                    report,
                    "客户",
                )
                if owner is not None and existing.owner_user_id is None:
                    existing.owner_user_id = owner.id
                    report.count("客户", "补齐负责人")
                mapping[old_id] = existing
            continue

        notes: list[str] = []
        if s(row.get("bcomment")):
            notes.append(s(row.get("bcomment")))
        notes.append(f"[旧系统] 编码 {s(row.get('busercode')) or '-'} / id {old_id}")
        if old_id in duplicates:
            notes.append("[旧系统] 位于「重复客户」分类下，已打 待去重 标签，请人工核对后合并")
        for label, key in (("开户行", "BankAndAcount"), ("银行账号", "BankAccounts"), ("邮箱", "EMail"), ("传真", "Fax")):
            if s(row.get(key)):
                notes.append(f"{label}：{s(row.get(key))}")

        credit_code = s(row.get("TaxNumber")).upper()
        if credit_code and (not CREDIT_CODE_PATTERN.match(credit_code) or credit_code in used_credit_codes):
            notes.append(f"税号：{credit_code}")
            credit_code = ""
        if credit_code:
            used_credit_codes.add(credit_code)

        # 只按 code 匹配。旧库编码零重复且基本无空值，
        # 再按 name 兜底会让 493 家重名客户互相覆盖，丢数据。
        existing = db.execute(select(Customer).where(Customer.code == code)).scalar_one_or_none()
        name = claim_name(name, code, existing.name if existing is not None else None)

        owner = user_map.get(owner_of.get(old_id, ""))
        if owner_of.get(old_id) and owner is None:
            report.warn(f"客户「{name}」的业务员 {owner_of[old_id]} 没有对应账号，负责人留空")

        values = dict(
            code=code,
            name=name,
            contact_name=truncate(first_nonempty(row.get("Person"), row.get("linker")), 100),
            phone=truncate(first_nonempty(row.get("moPhone"), row.get("linkerTel"), row.get("TelAndAddress")), 30),
            # 旧库 Area 为详细地址，TelAndAddress 实际为电话（旧视图命名为 BPhoneNumber）。
            address=truncate(row.get("Area"), 255),
            region_province=detect_province(row.get("Area"), row.get("TelAndAddress")),
            unified_social_credit_code=credit_code or None,
            source_type="company_transfer",
            remark="\n".join(notes),
            archived_at=now_local() if is_stopped(row, "isStop") else None,
        )

        if existing is None:
            existing = Customer(**values, owner_user_id=owner.id if owner is not None else None)
            if apply:
                db.add(existing)
            report.count("客户", "新增")
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            if owner is not None:
                existing.owner_user_id = owner.id
            report.count("客户", "更新")
        if owner is not None:
            report.count("客户", "带负责人")

        # 疑似重复的自动打标签，新系统客户页按标签一筛就能集中处理
        # duplicates 既包含旧库「重复客户」分类，也包含同名且电话地址完全一致的
        if old_id in duplicates:
            report.count("客户", "疑似重复")
            if apply:
                db.flush()
                already = db.execute(
                    select(CustomerTag.id).where(
                        CustomerTag.customer_id == existing.id, CustomerTag.tag == DUPLICATE_TAG
                    )
                ).first()
                if not already:
                    db.add(
                        CustomerTag(
                            customer_id=existing.id,
                            tag=DUPLICATE_TAG,
                            dimension="custom",
                            created_by_id=None,
                        )
                    )
        mapping[old_id] = existing
        report.remember(db, existing, apply)

    if apply:
        db.flush()
    print(f"    客户 {len(mapping)} 家")
    return mapping


def import_suppliers(db: Session, rows: list[dict], kind_map: dict[str, str], report: Report, apply: bool) -> int:
    print("\n[6/9] 供应商")
    used_credit_codes = {
        code for code in db.execute(select(Supplier.unified_social_credit_code)).scalars().all() if code
    }
    # bname 是旧系统为了列表显示保留的四字左右简称，bfullname 才是供应商全称。
    name_overrides = build_name_disambiguation(
        rows, kind_map, "supplier", "btypeid", ("bfullname", "bname"), "busercode"
    )
    if report.skip_conflicts:
        name_overrides = {}
    claim_name = make_name_claimer(db, Supplier, report, "供应商")
    count = 0
    for row in rows:
        old_id = s(row.get("btypeid"))
        if kind_map.get(old_id) != "supplier":
            continue
        name = name_overrides.get(old_id) or truncate(first_nonempty(row.get("bfullname"), row.get("bname")), 150)
        if not name:
            continue
        code = truncate(first_nonempty(row.get("busercode"), f"OLD_{old_id}"), 50)

        raw_credit = s(row.get("TaxNumber")).upper()
        conflicts = report.conflicts(
            db, Supplier, "供应商", code=code, name=name,
            unified_social_credit_code=raw_credit if CREDIT_CODE_PATTERN.match(raw_credit) else None,
        )
        if conflicts:
            if report.fill_existing_empty and len(conflicts) == 1:
                fill_empty_fields(
                    conflicts[0],
                    {
                        "contact_name": truncate(first_nonempty(row.get("Person"), row.get("linker")), 100),
                        "phone": truncate(first_nonempty(row.get("moPhone"), row.get("linkerTel"), row.get("TelAndAddress")), 30),
                        "address": truncate(row.get("Area"), 255),
                        "unified_social_credit_code": raw_credit if CREDIT_CODE_PATTERN.match(raw_credit) else None,
                        "bank_name": truncate(row.get("BankAndAcount"), 150) or None,
                        "bank_account": truncate(row.get("BankAccounts"), 60) or None,
                    },
                    report,
                    "供应商",
                )
            continue

        notes = [s(row.get("bcomment"))] if s(row.get("bcomment")) else []
        notes.append(f"[旧系统] 编码 {s(row.get('busercode')) or '-'} / id {old_id}")
        if s(row.get("EMail")):
            notes.append(f"邮箱：{s(row.get('EMail'))}")

        credit_code = s(row.get("TaxNumber")).upper()
        if credit_code and (not CREDIT_CODE_PATTERN.match(credit_code) or credit_code in used_credit_codes):
            notes.append(f"税号：{credit_code}")
            credit_code = ""
        if credit_code:
            used_credit_codes.add(credit_code)

        existing = db.execute(select(Supplier).where(Supplier.code == code)).scalar_one_or_none()
        name = claim_name(name, code, existing.name if existing is not None else None)

        values = dict(
            code=code,
            name=name,
            contact_name=truncate(first_nonempty(row.get("Person"), row.get("linker")), 100),
            phone=truncate(first_nonempty(row.get("moPhone"), row.get("linkerTel"), row.get("TelAndAddress")), 30),
            address=truncate(row.get("Area"), 255),
            unified_social_credit_code=credit_code or None,
            bank_name=truncate(row.get("BankAndAcount"), 150) or None,
            bank_account=truncate(row.get("BankAccounts"), 60) or None,
            remark="\n".join(notes),
            status="disabled" if is_stopped(row, "isStop") else "enabled",
        )
        if existing is None:
            existing = Supplier(**values)
            if apply:
                db.add(existing)
            report.count("供应商", "新增")
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            report.count("供应商", "更新")
        report.remember(db, existing, apply)
        count += 1
    if apply:
        db.flush()
    print(f"    供应商 {count} 家")
    return count


def import_boms(
    db: Session,
    ndx_rows: list[dict],
    detail_rows: list[dict],
    product_map: dict[str, Product],
    material_map: dict[str, Material],
    report: Report,
    apply: bool,
) -> int:
    print("\n[7/9] BOM")
    details_by_vch: dict[str, list[dict]] = defaultdict(list)
    for row in detail_rows:
        details_by_vch[s(row.get("VchCode"))].append(row)

    count = 0
    for row in ndx_rows:
        vch = s(row.get("VchCode"))
        product = product_map.get(s(row.get("PtypeId")))
        if product is None:
            report.warn(f"BOM {vch} 的成品 {s(row.get('PtypeId'))} 不在产品表里，跳过")
            continue
        output_qty = dec(row.get("Qty"), "1")
        if output_qty <= 0:
            output_qty = Decimal("1")

        version_no = truncate(f"LEGACY-{vch}", 30)
        if report.skip_conflicts:
            # 新系统 BOM.product_id 唯一，不能以旧单号/版本号判断是否重复。
            if (id(product) in report.seen["bom"] or
                    (product.id and report.conflicts(db, BOM, "BOM", product_id=product.id))):
                if id(product) in report.seen["bom"]:
                    report.count("BOM", "冲突跳过")
                continue
        existing = db.execute(
            select(BOM).where(BOM.product_id == product.id, BOM.version_no == version_no)
        ).scalar_one_or_none() if product.id else None

        notes = [s(row.get("BOMFullname")), s(row.get("Comment"))]
        notes = [n for n in notes if n]
        notes.append(f"[旧系统] BOM 单号 {vch}，原始产出数量 {output_qty}")

        items = []
        missing_material = False
        for detail in details_by_vch.get(vch, []):
            material = material_map.get(s(detail.get("Ptypeid")))
            if material is None:
                # 成品也可能作为半成品出现在用料里
                report.warn(f"BOM {vch} 的用料 {s(detail.get('Ptypeid'))} 不在原料表里，该行跳过")
                missing_material = True
                continue
            qty = dec(detail.get("Qty"))
            attrition = dec(detail.get("AttritionRate"))
            if attrition > 0:
                # 旧系统损耗率是独立列，新系统没有，折进标准用量
                factor = (Decimal("1") + attrition / Decimal("100")) if attrition >= 1 else (Decimal("1") + attrition)
                qty = qty * factor
                notes.append(f"用料 {material.name} 原始用量 {dec(detail.get('Qty'))}，损耗率 {attrition}")
            # 归一化到单位成品用量
            items.append((material, qty / output_qty))

        if report.skip_conflicts and missing_material:
            report.warn(f"BOM {vch} 有用料缺失或关联冲突，整份配方跳过，避免导入不完整配方")
            report.count("BOM", "关联缺失跳过")
            continue
        if not items:
            report.warn(f"BOM {vch} 没有可用的用料明细，跳过")
            continue

        if existing is None:
            existing = BOM(
                product_id=product.id,
                version_no=version_no,
                is_active=True,
                notes="\n".join(notes),
            )
            if apply:
                db.add(existing)
                db.flush()
            report.count("BOM", "新增")
        else:
            existing.notes = "\n".join(notes)
            if apply:
                # 重跑时先清掉旧用料行，避免累加
                for old_item in db.execute(select(BOMItem).where(BOMItem.bom_id == existing.id)).scalars().all():
                    db.delete(old_item)
            report.count("BOM", "更新")

        if apply and existing.id:
            for material, qty in items:
                db.add(BOMItem(bom_id=existing.id, material_id=material.id, standard_quantity=qty))
        report.count("BOM", "用料行", len(items))
        report.seen["bom"].add(id(product))
        report.remember(db, existing, apply)
        count += 1

    if apply:
        db.flush()
    print(f"    BOM {count} 张")
    return count


def import_inventory(
    db: Session,
    stock_rows: list[dict],
    batch_rows: list[dict],
    warehouse_map: dict[str, Warehouse],
    product_map: dict[str, Product],
    material_map: dict[str, Material],
    report: Report,
    apply: bool,
) -> int:
    print("\n[8/9] 库存结存")
    count = 0
    blocked_batches = set()
    for row in stock_rows:
        warehouse = warehouse_map.get(s(row.get("KtypeId")))
        if warehouse is None:
            report.warn(f"库存记录的仓库 {s(row.get('KtypeId'))} 不存在，跳过")
            continue
        ptypeid = s(row.get("PtypeId"))
        product = product_map.get(ptypeid)
        material = material_map.get(ptypeid)
        if product is None and material is None:
            report.warn(f"库存记录的货品 {ptypeid} 不在产品/原料表里，跳过")
            continue

        quantity = dec(row.get("qty"))
        avg_cost = dec(row.get("avg_unit_cost"))

        existing = db.execute(
            select(Inventory).where(
                Inventory.warehouse_id == warehouse.id,
                Inventory.product_id == (product.id if product is not None else None),
                Inventory.material_id == (material.id if material is not None else None),
            )
        ).scalar_one_or_none() if warehouse.id else None

        stock_key = (id(warehouse), id(product), id(material))
        if report.skip_conflicts:
            if existing is not None or stock_key in report.seen["stock"]:
                report.count("库存", "冲突跳过")
                blocked_batches.add(stock_key)
                continue
            report.seen["stock"].add(stock_key)

        if existing is None:
            existing = Inventory(
                warehouse_id=warehouse.id,
                product_id=product.id if product is not None else None,
                material_id=material.id if material is not None else None,
                quantity=quantity,
                reserved_quantity=Decimal("0"),
                avg_unit_cost=avg_cost,
            )
            if apply:
                db.add(existing)
            report.count("库存", "新增")
        else:
            existing.quantity = quantity
            existing.avg_unit_cost = avg_cost
            report.count("库存", "更新")
        report.remember(db, existing, apply)
        count += 1

    if apply:
        db.flush()

    print("\n[9/9] 库存批次")
    batch_count = 0
    for row in batch_rows:
        warehouse = warehouse_map.get(s(row.get("KtypeId")))
        ptypeid = s(row.get("PtypeId"))
        product = product_map.get(ptypeid)
        material = material_map.get(ptypeid)
        batch_no = truncate(first_nonempty(row.get("GoodsBatchID"), row.get("JobNumber")), 80)
        if warehouse is None or (product is None and material is None) or not batch_no:
            continue
        quantity = dec(row.get("Qty"))
        if quantity == 0:
            continue
        stock_key = (id(warehouse), id(product), id(material))
        batch_key = (*stock_key, batch_no)
        if report.skip_conflicts:
            # 即使源文件缺少汇总行，也不能向已存在的库存结存追加旧库批次。
            prior_stock = None
            if stock_key not in report.seen["stock"] and warehouse.id:
                prior_stock = db.execute(select(Inventory.id).where(
                    Inventory.warehouse_id == warehouse.id,
                    Inventory.product_id == (product.id if product is not None else None),
                    Inventory.material_id == (material.id if material is not None else None),
                )).first()
            if stock_key in blocked_batches or prior_stock is not None:
                report.count("库存批次", "结存冲突跳过")
                continue
            if batch_key in report.seen["batch"]:
                report.count("库存批次", "冲突跳过")
                continue
        existing = db.execute(
            select(InventoryBatch).where(
                InventoryBatch.warehouse_id == warehouse.id,
                InventoryBatch.batch_no == batch_no,
                InventoryBatch.product_id == (product.id if product is not None else None),
                InventoryBatch.material_id == (material.id if material is not None else None),
            )
        ).scalar_one_or_none() if warehouse.id else None
        if existing is None:
            if apply:
                db.add(
                    InventoryBatch(
                        warehouse_id=warehouse.id,
                        product_id=product.id if product is not None else None,
                        material_id=material.id if material is not None else None,
                        batch_no=batch_no,
                        quantity=quantity,
                        unit_cost=dec(row.get("Price")),
                        source_type="legacy_import",
                    )
                )
            report.count("库存批次", "新增")
            batch_count += 1
            report.seen["batch"].add(batch_key)
            if report.skip_conflicts and apply:
                db.flush()
        elif report.skip_conflicts:
            report.count("库存批次", "冲突跳过")
    if apply:
        db.flush()
    print(f"    库存 {count} 条 / 批次 {batch_count} 条")
    return count


# ======================================================================
# 主流程
# ======================================================================
def main():
    parser = argparse.ArgumentParser(description="旧系统主数据导入")
    parser.add_argument("--source", default="old_data/export", help="导出的 json 所在目录")
    parser.add_argument("--apply", action="store_true", help="真正写库；不加此参数只试运行")
    parser.add_argument("--skip-conflicts", action="store_true", help="只新增，编码/唯一名称/有效税号重复则跳过，保留已有档案、BOM、库存")
    parser.add_argument(
        "--fill-existing-empty",
        action="store_true",
        help="配合 --skip-conflicts：已有客户/供应商仅补齐空的联系人、电话、地址、银行等字段",
    )
    parser.add_argument("--skip", default="", help="跳过的阶段，逗号分隔：dept,user,warehouse,product,customer,supplier,bom,stock")
    parser.add_argument("--existing-user-map", default="", help="旧员工ID到现有 username 的 CSV")
    parser.add_argument("--existing-warehouse-map", default="", help="旧仓库ID到现有仓库编码的 CSV")
    parser.add_argument("--product-map", default="", help="产品/原料拆分 CSV；不传则读取数据源目录")
    args = parser.parse_args()

    source = (BASE_DIR / args.source).resolve() if not Path(args.source).is_absolute() else Path(args.source)
    if not source.exists():
        print(f"找不到导出目录：{source}")
        print("请先执行 old_data/export_from_sqlserver.bat 生成 json")
        return 1

    skip = {item.strip() for item in args.skip.split(",") if item.strip()}
    def resolve_optional(value: str) -> Path | None:
        if not value:
            return None
        path = Path(value)
        return path.resolve() if path.is_absolute() else (BASE_DIR / path).resolve()

    existing_user_map_path = resolve_optional(args.existing_user_map)
    existing_warehouse_map_path = resolve_optional(args.existing_warehouse_map)
    product_map_path = resolve_optional(args.product_map)
    mode = "正式导入" if args.apply else "试运行（不写库）"
    print("=" * 68)
    print(f"旧系统主数据导入 · {mode}")
    print(f"数据源：{source}")
    print(f"目标库：{settings.database_url.split('@')[-1]}")
    if not HAS_PYPINYIN:
        print("警告：未安装 pypinyin，用户名将回退为简拼，建议先 pip install pypinyin")
    print("=" * 68)

    print("\n读取导出文件 ...")
    def read_export(filename):
        return load_json(source, filename, strict=args.skip_conflicts)

    btype_rows = read_export("01_btype.json")
    ptype_rows = read_export("02_ptype.json")
    price_rows = read_export("03_ptype_price.json")
    employee_rows = read_export("04_employee.json")
    department_rows = read_export("05_department.json")
    stock_rows = read_export("06_stock.json")
    owner_rows = read_export("07_customer_owner.json")
    doc_type_rows = read_export("08_btype_doc_types.json")
    bom_ndx_rows = read_export("09_bom_ndx.json")
    bom_detail_rows = read_export("10_bom_detail.json")
    goods_stock_rows = read_export("11_goods_stocks.json")
    goods_batch_rows = read_export("12_goods_stocks_batch.json")
    print(
        f"    往来单位 {len(btype_rows)} / 商品 {len(ptype_rows)} / 员工 {len(employee_rows)} / "
        f"部门 {len(department_rows)} / 仓库 {len(stock_rows)}"
    )
    print(
        f"    BOM {len(bom_ndx_rows)}(明细 {len(bom_detail_rows)}) / 库存 {len(goods_stock_rows)} / "
        f"负责人映射 {len(owner_rows)}"
    )

    report = Report(
        skip_conflicts=args.skip_conflicts,
        fill_existing_empty=args.fill_existing_empty,
    )
    if args.skip_conflicts:
        print("冲突策略：只新增、保留已有数据；身份不一致的档案不复用关联")
        if not args.apply:
            print("试运行统计供复核；正式导入以目标数据库的唯一键比较规则为准")
    db: Session = SessionLocal()
    try:
        dept_map = {} if "dept" in skip else import_departments(db, department_rows, report, args.apply)

        owner_ids = {s(r.get("etypeid")) for r in owner_rows if s(r.get("etypeid"))}
        role_map = resolve_employee_roles(source, employee_rows, owner_ids, report)
        explicit_user_map = load_existing_user_map(db, source, report, existing_user_map_path)
        user_map = {} if "user" in skip else import_employees(db, employee_rows, dept_map, role_map, report, args.apply)
        user_map.update(explicit_user_map)

        warehouse_map = {} if "warehouse" in skip else import_warehouses(
            db,
            stock_rows,
            report,
            args.apply,
            source=source,
            mapping_path=existing_warehouse_map_path,
        )

        kind_map = split_products(source, ptype_rows, report, product_map_path)
        target_code_map: dict[str, str] = {}
        effective_product_map = product_map_path or (source / "product_material_split.csv")
        if effective_product_map.exists():
            with effective_product_map.open(encoding="utf-8-sig", newline="") as fh:
                for item in csv.DictReader(fh):
                    old_id = s(item.get("ptypeid"))
                    target_code = s(item.get("target_code"))
                    if old_id and target_code:
                        target_code_map[old_id] = target_code
        if "product" in skip:
            product_map, material_map = {}, {}
        else:
            product_map, material_map = import_products(
                db,
                ptype_rows,
                price_rows,
                kind_map,
                target_code_map,
                report,
                args.apply,
            )

        print("\n    往来单位归类 ...")
        btype_kind, btype_duplicates = classify_btype(btype_rows, doc_type_rows, report)

        if "customer" not in skip:
            import_customers(db, btype_rows, btype_kind, btype_duplicates, owner_rows, user_map, report, args.apply)
        if "supplier" not in skip:
            import_suppliers(db, btype_rows, btype_kind, report, args.apply)
        if "bom" not in skip:
            import_boms(db, bom_ndx_rows, bom_detail_rows, product_map, material_map, report, args.apply)
        if "stock" not in skip:
            import_inventory(
                db, goods_stock_rows, goods_batch_rows, warehouse_map, product_map, material_map, report, args.apply
            )

        if args.apply:
            db.commit()
            print("\n>>> 已提交，数据已写入新系统")
        else:
            db.rollback()
            print("\n>>> 试运行结束，未写入任何数据。确认无误后加 --apply 正式执行")
    except Exception:
        db.rollback()
        print("\n>>> 出错已回滚，数据库未改动")
        raise
    finally:
        db.close()
        report.dump()
    return 0


if __name__ == "__main__":
    sys.exit(main())
