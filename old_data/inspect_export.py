"""导出数据的脱敏诊断 —— 只产出统计量，不输出任何一条业务记录。

为什么需要它：
    迁移映射对不对，靠的是「字段填充率 / 取值分布 / 引用完整性」，
    不需要看客户名单本身。这个脚本把需要的判断依据抽出来，
    客户名、电话、地址、税号这些敏感内容一个字都不会出现在报告里。

用法
    python old_data/inspect_export.py --source old_data/export
    python old_data/inspect_export.py --source old_data/export --out report.md

产出
    一份 markdown 报告，可以直接贴给我，也可以自己先通读确认没有敏感内容再发。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# ======================================================================
# 敏感字段名单：这些列只统计「填没填、长度多少、格式对不对」，绝不输出取值
# ======================================================================
SENSITIVE_FIELDS = {
    # 名称类
    "bname", "bfullname", "bnamepy", "pname", "pfullname", "pnamepy",
    "ename", "efullname", "enamepy", "dname", "dfullname", "dnamepy",
    "kname", "kfullname", "knamepy", "BOMFullname",
    # 联系方式
    "Person", "linker", "linkerTel", "moPhone", "Tel", "TelAndAddress",
    "Fax", "EMail", "Address", "PostCode",
    # 证件与账户
    "TaxNumber", "BankAndAcount", "BankAccounts",
    # 自由文本
    "bcomment", "pcomment", "ecomment", "dcomment", "kcomment", "Comment",
    "Birthday", "Area", "Standard", "Type",
    "Custom1", "Custom2", "Custom3", "Custom4",
    # 业务量：金额和数量也当敏感处理，只给区间不给明细
    "Price", "Qty", "qty", "Total", "total_amount", "avg_unit_cost",
    "LastBuyPrice", "warnup", "warndown", "R_WARNUP", "P_WARNUP",
    "ChangeRate", "AttritionRate",
}

# 可以安全展示取值的字段：全是分类/枚举/标志位，不含个人或商业秘密
SAFE_ENUM_FIELDS = {
    "leveal", "isStop", "Incumbency", "deleted", "soncount", "psonnum",
    "Ptypetype", "sourcewayzz", "sourcewaywg", "sourcewayww",
    "punitname", "SideUnit", "Unit", "UnitID", "PRTypeId",
    "VchType", "last_vch_type", "vchname", "bom_type_name", "BMTypeid",
    "duty",  # 职务是角色映射的依据，属于岗位信息
}

FILES = [
    ("00_btype_roots.json", "往来单位顶层分类"),
    ("01_btype.json", "往来单位"),
    ("02_ptype.json", "商品"),
    ("03_ptype_price.json", "商品价格"),
    ("04_employee.json", "职员"),
    ("05_department.json", "部门"),
    ("06_stock.json", "仓库"),
    ("07_customer_owner.json", "客户负责人"),
    ("08_btype_doc_types.json", "单据类型分布"),
    ("09_bom_ndx.json", "BOM 表头"),
    ("10_bom_detail.json", "BOM 明细"),
    ("11_goods_stocks.json", "库存结存"),
    ("12_goods_stocks_batch.json", "库存批次"),
]

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
CREDIT_RE = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{18}$")
PROVINCE_KEYWORDS = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西",
    "甘肃", "青海", "宁夏", "新疆", "台湾", "香港", "澳门",
]


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not raw:
        return []
    raw = "".join(line.strip() for line in raw.splitlines() if not line.startswith("-"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def s(v) -> str:
    return "" if v is None else str(v).strip()


def bucket(n: int) -> str:
    """把计数落到区间，避免精确数字泄露业务规模细节。"""
    for edge in (0, 1, 10, 50, 100, 500, 1000, 5000, 10000, 50000):
        if n <= edge:
            return f"≤{edge}"
    return ">50000"


def profile_file(rows: list[dict], label: str, lines: list[str]):
    lines.append(f"\n### {label}（{len(rows)} 行）\n")
    if not rows:
        lines.append("_文件为空或不存在_\n")
        return

    fields = []
    for row in rows[:2000]:
        for key in row:
            if key not in fields:
                fields.append(key)

    lines.append("| 字段 | 填充率 | 去重数 | 长度(最小/中位/最大) | 备注 |")
    lines.append("| --- | ---: | ---: | --- | --- |")
    for field in fields:
        values = [s(r.get(field)) for r in rows]
        filled = [v for v in values if v]
        rate = f"{len(filled) * 100 // max(len(values), 1)}%"
        distinct = len(set(filled))
        if filled:
            lengths = sorted(len(v) for v in filled)
            length_desc = f"{lengths[0]}/{lengths[len(lengths) // 2]}/{lengths[-1]}"
        else:
            length_desc = "-"

        note = ""
        if field in SAFE_ENUM_FIELDS and 0 < distinct <= 30:
            top = Counter(filled).most_common(12)
            note = "取值：" + "、".join(f"`{k}`×{v}" for k, v in top)
        elif field in SENSITIVE_FIELDS:
            note = "敏感字段，仅统计"
        lines.append(f"| `{field}` | {rate} | {distinct} | {length_desc} | {note} |")
    lines.append("")


def check_formats(source: Path, lines: list[str]):
    lines.append("\n## 格式合规性\n")
    btype = load_json(source / "01_btype.json")
    if btype:
        phones = [
            s(r.get("moPhone")) or s(r.get("linkerTel")) or s(r.get("TelAndAddress"))
            for r in btype
        ]
        phones = [p for p in phones if p]
        mobile_ok = sum(1 for p in phones if PHONE_RE.match(p))
        lines.append(f"- 往来单位电话：{len(phones)} 个非空，其中 {mobile_ok} 个是标准手机号"
                     f"（{mobile_ok * 100 // max(len(phones), 1)}%），其余多为座机或带分机")

        tax = [s(r.get("TaxNumber")) for r in btype]
        tax = [t for t in tax if t]
        tax_ok = sum(1 for t in tax if CREDIT_RE.match(t.upper()))
        lines.append(f"- 税号：{len(tax)} 个非空，其中 {tax_ok} 个符合 18 位统一社会信用代码格式。"
                     f"不合规的 {len(tax) - tax_ok} 个会被写进备注而不是信用代码列")

        area_blob = [s(r.get("Area")) + s(r.get("TelAndAddress")) for r in btype]
        hit = sum(1 for b in area_blob if any(k in b for k in PROVINCE_KEYWORDS))
        lines.append(f"- 地区可识别省份：{hit}/{len(btype)}（{hit * 100 // max(len(btype), 1)}%），"
                     f"识别不到的 region_province 留空")


def check_integrity(source: Path, lines: list[str]):
    """引用完整性 —— 这部分最关键，直接决定导入会不会大面积跳过。"""
    lines.append("\n## 引用完整性\n")

    btype = load_json(source / "01_btype.json")
    ptype = load_json(source / "02_ptype.json")
    employee = load_json(source / "04_employee.json")
    department = load_json(source / "05_department.json")
    stock = load_json(source / "06_stock.json")
    owner = load_json(source / "07_customer_owner.json")
    bom_ndx = load_json(source / "09_bom_ndx.json")
    bom_detail = load_json(source / "10_bom_detail.json")
    goods = load_json(source / "11_goods_stocks.json")

    ptype_ids = {s(r.get("ptypeid")) for r in ptype}
    emp_ids = {s(r.get("etypeid")) for r in employee}
    dept_ids = {s(r.get("dtypeid")) for r in department}
    stock_ids = {s(r.get("ktypeid")) for r in stock}
    btype_ids = {s(r.get("btypeid")) for r in btype}

    def report(label: str, refs: list[str], universe: set[str]):
        refs = [r for r in refs if r]
        missing = [r for r in refs if r not in universe]
        pct = len(missing) * 100 // max(len(refs), 1)
        flag = "✅" if not missing else ("⚠️" if pct < 5 else "❌")
        lines.append(f"- {flag} {label}：{len(refs)} 个引用，{len(missing)} 个找不到对应主数据（{pct}%）")

    report("客户负责人 → 职员", [s(r.get("etypeid")) for r in owner], emp_ids)
    report("负责人映射 → 往来单位", [s(r.get("btypeid")) for r in owner], btype_ids)
    report("职员 → 部门", [s(r.get("Department")) for r in employee], dept_ids)
    report("BOM 成品 → 商品", [s(r.get("PtypeId")) for r in bom_ndx], ptype_ids)
    report("BOM 用料 → 商品", [s(r.get("Ptypeid")) for r in bom_detail], ptype_ids)
    report("库存货品 → 商品", [s(r.get("PtypeId")) for r in goods], ptype_ids)
    report("库存仓库 → 仓库", [s(r.get("KtypeId")) for r in goods], stock_ids)

    # 叶子节点统计：有子节点的是分类目录，不是真实档案
    for label, rows, son_key in (
        ("往来单位", btype, "soncount"),
        ("商品", ptype, "soncount"),
        ("职员", employee, "soncount"),
        ("仓库", stock, "soncount"),
    ):
        leaves = sum(1 for r in rows if s(r.get(son_key)) in ("", "0"))
        lines.append(f"- {label}：共 {len(rows)} 行，其中真实档案（叶子节点）{leaves} 行，"
                     f"分类目录 {len(rows) - leaves} 行")


def check_tree_roots(source: Path, lines: list[str]):
    """顶层分类名 —— 这是判断客户/供应商归属的唯一依据，属于业务分类不是个人信息。"""
    lines.append("\n## 往来单位分类树（判断客户/供应商的依据）\n")
    roots = load_json(source / "00_btype_roots.json")
    if not roots:
        lines.append("_未导出 00_btype_roots.json_\n")
        return
    lines.append("只列出**有下级的分类节点**，不含任何具体单位：\n")
    lines.append("| 层级 | 分类名 | 下级数量 |")
    lines.append("| ---: | --- | ---: |")
    for row in sorted(roots, key=lambda r: (s(r.get("leveal")), s(r.get("bname")))):
        son = s(row.get("soncount"))
        if son in ("", "0"):
            continue  # 叶子节点是具体单位，跳过
        lines.append(f"| {s(row.get('leveal'))} | {s(row.get('bname'))} | {son} |")
    lines.append("")

    lines.append("\n### 单据类型分布\n")
    docs = load_json(source / "08_btype_doc_types.json")
    if docs:
        by_type = defaultdict(int)
        for row in docs:
            name = s(row.get("vchname")) or f"类型{s(row.get('VchType'))}"
            by_type[name] += 1
        lines.append("| 单据类型 | 涉及往来单位数 |")
        lines.append("| --- | ---: |")
        for name, count in sorted(by_type.items(), key=lambda kv: -kv[1])[:20]:
            lines.append(f"| {name} | {count} |")
    lines.append("")


def main():
    parser = argparse.ArgumentParser(description="导出数据脱敏诊断")
    parser.add_argument("--source", default="old_data/export")
    parser.add_argument("--out", default="old_data/export/inspect_report.md")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        print(f"找不到目录：{source}")
        return 1

    lines = [
        "# 旧系统导出数据 · 脱敏诊断报告",
        "",
        "> 本报告只包含统计量与分类名，**不含任何客户名称、电话、地址、税号、金额明细**。",
        "> 生成后建议自己通读一遍再对外发送。",
        "",
        "## 各文件字段概况",
    ]

    for filename, label in FILES:
        profile_file(load_json(source / filename), f"{label} `{filename}`", lines)

    check_tree_roots(source, lines)
    check_formats(source, lines)
    check_integrity(source, lines)

    lines += [
        "\n## 结论要点",
        "",
        "重点看三处：",
        "1. **分类树**——能不能清楚区分客户和供应商",
        "2. **引用完整性**——标 ❌ 的说明会大面积跳过，需要先补数据",
        "3. **叶子节点数**——真实档案数量，和你预期的客户/商品数量对不对得上",
        "",
    ]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成：{out_path}")
    print(f"共 {len(lines)} 行，可直接查看或贴给对方。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
