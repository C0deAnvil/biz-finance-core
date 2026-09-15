"""客户 / 供应商标签字典。

客户标签来源：《销帮帮标签表》编号 KP-44-0818（2025-08-18，戴慧娟）。
标签在数据库中以 code 存储（customer_tags.tag），维度存 customer_tags.dimension，
展示用的中文名与注释由本字典解析，方便后续调整文案而不动历史数据。

字典之外的自定义标签同样允许写入，维度记为 custom，label 回落为标签原文。
"""

from __future__ import annotations


# 维度定义：code -> 中文名。顺序即前端展示顺序。
CUSTOMER_TAG_DIMENSIONS: dict[str, str] = {
    "value": "客户价值",
    "industry": "客户行业",
    "lifecycle": "客户生命周期",
    "demand": "客户需求",
    "status": "客户状态",
    "source": "客户来源",
}

CUSTOM_DIMENSION = "custom"
CUSTOM_DIMENSION_LABEL = "自定义"

# (dimension, code, label, note, readonly)
# readonly=True 表示由公司统一打标，销售端只读展示（PDF 备注：无需自己打标签，公司标）。
_CUSTOMER_TAG_ROWS: list[tuple[str, str, str, str, bool]] = [
    # 客户价值
    ("value", "S1", "五星客户", "销售额 10 万以上/年", True),
    ("value", "S2", "四星客户", "销售额 5 万-10 万/年", True),
    ("value", "S3", "三星客户", "销售额 5 万以下/年", False),
    # 客户行业
    ("industry", "H1", "日化行业", "", False),
    ("industry", "H2", "生物医药", "发酵、培养等", False),
    ("industry", "H3", "新能源", "", False),
    ("industry", "H4", "印染行业", "", False),
    ("industry", "H5", "畜牧饲料", "", False),
    ("industry", "H6", "高端肥料", "", False),
    ("industry", "H7", "化工助剂/表面处理", "", False),
    ("industry", "H8", "试剂分装", "", False),
    ("industry", "H9", "新材料", "", False),
    ("industry", "H10", "发泡行业", "", False),
    ("industry", "H11", "石油助剂", "", False),
    ("industry", "H12", "鱼油提取", "", False),
    ("industry", "H13", "车船用/脱销", "", False),
    ("industry", "H14", "水基涂料", "", False),
    ("industry", "H15", "陶瓷行业", "", False),
    ("industry", "H16", "经销/出口", "", False),
    ("industry", "H17", "其他行业", "", False),
    # 客户生命周期
    ("lifecycle", "L0", "相关客户", "展会名片客户 / L1 以下客户", False),
    ("lifecycle", "L1", "潜在客户", "非工业级咨询 / 样品 / ＜100 元", False),
    ("lifecycle", "L2", "首单客户", "≥100 元", False),
    ("lifecycle", "L3", "成长客户", "复购 2-5 次", False),
    ("lifecycle", "L4", "成熟客户", "稳定复购", False),
    # 客户需求
    ("demand", "N1", "价格敏感型", "客户提过价格 / 账期等", False),
    ("demand", "N2", "技术定制型", "客户提过指标 / 配方等", False),
    ("demand", "N3", "时效敏感型", "客户抱怨 / 反馈过时效问题", False),
    ("demand", "N4", "服务或其他", "客户抱怨 / 反馈过我们其他服务", False),
    # 客户状态
    ("status", "X", "联系不上", "", False),
    ("status", "Y", "已流失客户", "", False),
    ("status", "T", "出过问题", "有过退换货或流失复购的客户", False),
    ("status", "K", "卡点客户", "高潜力难推进（看板上的客户）", False),
    ("status", "G", "天使客户", "有过情绪困扰自己无法跟进的", False),
    ("status", "M1", "见过面", "", False),
    ("status", "M2", "待见面", "已邀约同意见面但还未见的", False),
    ("status", "W", "转交客户", "", False),
    ("status", "C", "竞品客户", "提到过竞品或正在用竞品的", False),
    # 客户来源（获客渠道，与档案上的“个人开发/公司转交”是两个维度）
    ("source", "O1", "1688", "", False),
    ("source", "O2", "百度", "", False),
    ("source", "O3", "爱采购", "", False),
    ("source", "O4", "布克", "", False),
    ("source", "O5", "淘宝", "", False),
    ("source", "O6", "抖音", "", False),
    ("source", "O7", "国际站", "", False),
    ("source", "O8", "主动开发", "电销 / 免费 B2B 等", False),
    ("source", "O9", "展会/走访", "", False),
    ("source", "O10", "转介绍", "", False),
    ("source", "O11", "未知来源", "", False),
]

CUSTOMER_TAG_MAP: dict[str, dict] = {
    code: {"code": code, "label": label, "note": note, "dimension": dimension, "readonly": readonly}
    for dimension, code, label, note, readonly in _CUSTOMER_TAG_ROWS
}

# 公司统一打标、销售端不可自行增删的标签
COMPANY_MANAGED_CUSTOMER_TAGS: set[str] = {code for code, item in CUSTOMER_TAG_MAP.items() if item["readonly"]}

# 客户价值标签同时用于回填 customers.star_level，保持旧字段可用
CUSTOMER_STAR_LEVEL_BY_TAG: dict[str, str] = {"S1": "五星", "S2": "四星", "S3": "三星"}


def customer_tag_catalog() -> list[dict]:
    """按维度分组返回客户标签字典，供前端下拉渲染。"""
    groups: list[dict] = []
    for dimension, dimension_label in CUSTOMER_TAG_DIMENSIONS.items():
        groups.append(
            {
                "dimension": dimension,
                "dimension_label": dimension_label,
                "options": [
                    {"code": item["code"], "label": item["label"], "note": item["note"], "readonly": item["readonly"]}
                    for item in CUSTOMER_TAG_MAP.values()
                    if item["dimension"] == dimension
                ],
            }
        )
    return groups


def resolve_customer_tag(code: str) -> dict:
    """把存储的标签 code 解析为展示结构；字典外的按自定义标签处理。"""
    normalized = (code or "").strip()
    known = CUSTOMER_TAG_MAP.get(normalized)
    if known:
        return {
            "code": known["code"],
            "label": known["label"],
            "note": known["note"],
            "dimension": known["dimension"],
            "dimension_label": CUSTOMER_TAG_DIMENSIONS[known["dimension"]],
            "readonly": known["readonly"],
        }
    return {
        "code": normalized,
        "label": normalized,
        "note": "",
        "dimension": CUSTOM_DIMENSION,
        "dimension_label": CUSTOM_DIMENSION_LABEL,
        "readonly": False,
    }


def customer_tag_dimension(code: str) -> str:
    known = CUSTOMER_TAG_MAP.get((code or "").strip())
    return known["dimension"] if known else CUSTOM_DIMENSION


def normalize_tags(tags: list[str] | None, *, limit: int = 30, max_length: int = 50) -> list[str]:
    """去空、去重、限长，保持录入顺序。"""
    result: list[str] = []
    for raw in tags or []:
        value = str(raw or "").strip()
        if not value:
            continue
        value = value[:max_length]
        if value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def star_level_from_tags(tags: list[str]) -> str | None:
    """从客户价值标签推导星级，用于兼容既有 star_level 字段与筛选。"""
    for tag in tags:
        if tag in CUSTOMER_STAR_LEVEL_BY_TAG:
            return CUSTOMER_STAR_LEVEL_BY_TAG[tag]
    return None


# ---------- 档案枚举 ----------

CUSTOMER_TYPE_OPTIONS: dict[str, str] = {
    "importer": "进口商",
    "dealer": "经销商",
    "factory": "工厂",
    "trading": "贸易公司",
    "agent": "代理商",
}

CUSTOMER_REGION_OPTIONS: dict[str, str] = {
    "domestic": "国内客户",
    "overseas": "海外客户",
}

SUPPLIER_TYPE_OPTIONS: dict[str, str] = {
    "trader": "贸易商",
    "manufacturer": "生产商",
    "manufacturer_trader": "生产贸易一体",
    "service": "服务商",
    "logistics": "物流商",
    "other": "其他",
}


def _label_of(mapping: dict[str, str], code: str | None) -> str | None:
    if not code:
        return None
    return mapping.get(code, code)


def customer_type_label(code: str | None) -> str | None:
    return _label_of(CUSTOMER_TYPE_OPTIONS, code)


def customer_region_label(code: str | None) -> str | None:
    return _label_of(CUSTOMER_REGION_OPTIONS, code)


def supplier_type_label(code: str | None) -> str | None:
    return _label_of(SUPPLIER_TYPE_OPTIONS, code)


def options_payload(mapping: dict[str, str]) -> list[dict]:
    return [{"value": value, "label": label} for value, label in mapping.items()]
