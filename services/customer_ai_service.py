from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from models import (
    Customer,
    CustomerAIResult,
    CustomerInquiry,
    Product,
    ReceivableRecord,
    SalesOrder,
    SalesOrderItem,
    User,
)
from services.organization import get_scoped_user_ids, user_in_scope


MODEL_VERSION = "customer-ai-v1"
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "thesis_ai_lab" / "final_customer_study" / "artifacts"
MODEL_DIR = ARTIFACT_DIR / "models"
TABLE_DIR = ARTIFACT_DIR / "tables"

SEGMENT_LABELS = {
    "高价值客户": {"type": "success", "priority": 3},
    "潜力客户": {"type": "primary", "priority": 2},
    "流失风险客户": {"type": "warning", "priority": 1},
}


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _to_iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@lru_cache(maxsize=1)
def _load_segmentation_artifact() -> dict | None:
    path = MODEL_DIR / "customer_segmentation_model.joblib"
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _load_recommendation_rules() -> list[dict]:
    model_path = MODEL_DIR / "product_recommendation_rules.joblib"
    table_path = TABLE_DIR / "recommendation_top_rules.csv"
    try:
        if model_path.exists():
            rules = joblib.load(model_path)
            if isinstance(rules, pd.DataFrame):
                return rules.fillna("").to_dict("records")
        if table_path.exists():
            return pd.read_csv(table_path, encoding="utf-8-sig").fillna("").to_dict("records")
    except Exception:
        return []
    return []


@lru_cache(maxsize=1)
def _load_lead_artifact() -> dict | None:
    path = MODEL_DIR / "lead_scoring_model.joblib"
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


def _new_no(prefix: str) -> str:
    return f"{prefix}{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')[:17]}"


def _latest_order_date(db: Session) -> datetime:
    latest = db.execute(select(func.max(SalesOrder.created_at))).scalar_one_or_none()
    return latest or datetime.utcnow()


def _customer_scope_filter(db: Session, current_user: User, query):
    scoped_user_ids = get_scoped_user_ids(db, current_user, "sales")
    if scoped_user_ids is not None:
        query = query.where(SalesOrder.sales_user_id.in_(scoped_user_ids))
    return query


def ensure_customer_access(db: Session, current_user: User, customer_id: int) -> None:
    order = db.execute(select(SalesOrder).where(SalesOrder.customer_id == customer_id).limit(1)).scalar_one_or_none()
    if order is not None and not user_in_scope(db, current_user, business_role_code="sales", owner_user_id=order.sales_user_id):
        raise PermissionError("仅可查看数据范围内的客户智能分析结果")


def _build_customer_feature_rows(db: Session, current_user: User | None = None) -> tuple[list[dict], dict[int, Customer]]:
    customer_query = select(Customer).order_by(Customer.created_at.desc(), Customer.id.desc())
    customers = db.execute(customer_query).scalars().all()
    customer_map = {row.id: row for row in customers}

    order_query = (
        select(SalesOrder)
        .options(joinedload(SalesOrder.items).joinedload(SalesOrderItem.product))
        .where(SalesOrder.status != "cancelled")
    )
    if current_user is not None:
        order_query = _customer_scope_filter(db, current_user, order_query)
    orders = db.execute(order_query).unique().scalars().all()

    order_ids = [row.id for row in orders]
    receivables = (
        db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids))).scalars().all()
        if order_ids
        else []
    )
    receivable_amount = {}
    for row in receivables:
        receivable_amount[row.sales_order_id] = receivable_amount.get(row.sales_order_id, Decimal("0")) + Decimal(row.amount or 0)

    latest_date = _latest_order_date(db)
    grouped_orders: dict[int, list[SalesOrder]] = {}
    for order in orders:
        grouped_orders.setdefault(order.customer_id, []).append(order)

    inquiry_rows = db.execute(select(CustomerInquiry)).scalars().all()
    inquiries_by_customer: dict[int, list[CustomerInquiry]] = {}
    for row in inquiry_rows:
        inquiries_by_customer.setdefault(row.customer_id, []).append(row)

    rows: list[dict] = []
    for customer_id, customer in customer_map.items():
        customer_orders = grouped_orders.get(customer_id, [])
        if current_user is not None and get_scoped_user_ids(db, current_user, "sales") is not None and not customer_orders:
            continue
        order_count = len(customer_orders)
        amount = sum(Decimal(order.total_amount or 0) for order in customer_orders)
        profit = sum(Decimal(order.profit_amount or 0) for order in customer_orders)
        product_names: set[str] = set()
        product_categories: set[str] = set()
        for order in customer_orders:
            for item in order.items:
                if item.product:
                    product_names.add(item.product.name)
                    product_categories.add(item.product.fulfillment_mode or item.product.name)
        latest_order_at = max((order.created_at for order in customer_orders if order.created_at), default=None)
        recency = (latest_date - latest_order_at).days if latest_order_at else 999
        avg_order_amount = float(amount / order_count) if order_count else 0.0
        inquiry_count = len(inquiries_by_customer.get(customer_id, []))
        high_intent_count = sum(1 for row in inquiries_by_customer.get(customer_id, []) if _to_float(row.intent_score) >= 0.65)
        intent_rate = high_intent_count / inquiry_count if inquiry_count else 0.0
        rows.append(
            {
                "customer_id": customer_id,
                "customer_code": customer.code,
                "customer_name": customer.name,
                "contact_name": customer.contact_name,
                "phone": customer.phone,
                "star_level": customer.star_level,
                "recency": recency,
                "frequency": order_count,
                "monetary": float(amount),
                "avg_order_amount": avg_order_amount,
                "category_diversity": len(product_categories),
                "product_diversity": len(product_names),
                "gross_profit": float(profit),
                "profit_rate": float(profit / amount) if amount else 0.0,
                "inquiry_count": inquiry_count,
                "high_intent_count": high_intent_count,
                "intent_rate": intent_rate,
                "latest_order_at": _to_iso(latest_order_at),
                "purchased_products": sorted(product_names),
                "received_amount": float(sum(receivable_amount.get(order.id, Decimal("0")) for order in customer_orders)),
            }
        )
    return rows, customer_map


def _fallback_segment(row: dict) -> str:
    frequency = _to_float(row.get("frequency"))
    monetary = _to_float(row.get("monetary"))
    recency = _to_float(row.get("recency"), 999)
    intent_rate = _to_float(row.get("intent_rate"))
    if frequency >= 8 and monetary >= 80000 and recency <= 180:
        return "高价值客户"
    if recency >= 360 or (frequency <= 1 and intent_rate < 0.4):
        return "流失风险客户"
    return "潜力客户"


def _predict_segment(row: dict) -> str:
    artifact = _load_segmentation_artifact()
    if not artifact:
        return _fallback_segment(row)
    features = artifact.get("features") or []
    model = artifact.get("model")
    label_map = artifact.get("label_map") or {}
    if not features or model is None:
        return _fallback_segment(row)
    vector = np.array([[_to_float(row.get(name)) for name in features]], dtype=float)
    try:
        cluster_id = int(model.predict(vector)[0])
    except Exception:
        return _fallback_segment(row)
    return label_map.get(cluster_id, _fallback_segment(row))


def _predict_segments_for_rows(rows: list[dict]) -> dict[int, str]:
    artifact = _load_segmentation_artifact()
    if not artifact or not rows:
        return {row["customer_id"]: _fallback_segment(row) for row in rows}
    features = artifact.get("features") or []
    model = artifact.get("model")
    label_map = artifact.get("label_map") or {}
    if not features or model is None or len(rows) < 3:
        return {row["customer_id"]: _fallback_segment(row) for row in rows}
    frame = pd.DataFrame([{name: _to_float(row.get(name)) for name in features} for row in rows])
    try:
        clipped = frame.copy()
        for name in features:
            q1 = clipped[name].quantile(0.25)
            q3 = clipped[name].quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                clipped[name] = clipped[name].clip(q1 - 1.5 * iqr, q3 + 1.5 * iqr)
        scaled = RobustScaler().fit_transform(clipped[features])
        cluster_ids = model.predict(scaled)
        return {
            row["customer_id"]: label_map.get(int(cluster_id), _fallback_segment(row))
            for row, cluster_id in zip(rows, cluster_ids)
        }
    except Exception:
        return {row["customer_id"]: _fallback_segment(row) for row in rows}


def _segment_reason(row: dict, segment: str) -> str:
    if segment == "高价值客户":
        return "近期交易活跃，累计采购金额和购买频次较高，适合重点维护并优先推荐配套产品。"
    if segment == "潜力客户":
        return "存在一定购买记录或咨询意向，但交易规模尚未充分释放，适合持续跟进。"
    return "最近购买间隔较长或交易频次偏低，需要确认客户需求变化并评估挽留策略。"


def _recommend_products(row: dict, limit: int = 5) -> list[dict]:
    purchased = set(row.get("purchased_products") or [])
    results: list[dict] = []
    for rule in _load_recommendation_rules():
        antecedents = {item.strip() for item in str(rule.get("antecedents_text") or "").split(",") if item.strip()}
        consequents = [item.strip() for item in str(rule.get("consequents_text") or "").split(",") if item.strip()]
        if not antecedents or not consequents:
            continue
        if not antecedents.issubset(purchased):
            continue
        for product_name in consequents:
            if product_name in purchased:
                continue
            candidate = {
                "product_name": product_name,
                "reason": f"客户已购买{','.join(sorted(antecedents))}，历史规则显示可配套推荐{product_name}",
                "confidence": round(_to_float(rule.get("confidence")), 3),
                "lift": round(_to_float(rule.get("lift")), 3),
                "support": round(_to_float(rule.get("support")), 4),
            }
            if all(item["product_name"] != product_name for item in results):
                results.append(candidate)
    results.sort(key=lambda item: (item["lift"], item["confidence"]), reverse=True)
    return results[:limit]


def _lead_label(score: float) -> str:
    if score >= 0.75:
        return "高意向"
    if score >= 0.5:
        return "中意向"
    return "待观察"


def _lead_score_from_features(row: dict) -> float:
    score = 0.25
    score += min(_to_float(row.get("intent_rate")), 1.0) * 0.35
    score += min(_to_float(row.get("frequency")) / 10, 1.0) * 0.15
    score += min(_to_float(row.get("monetary")) / 100000, 1.0) * 0.15
    if _to_float(row.get("recency"), 999) <= 90:
        score += 0.1
    return max(0.0, min(score, 1.0))


def _make_text_features(text: str) -> dict:
    text = text or ""
    lower_text = text.lower()
    high_words = ["现货", "急需", "合同", "下单", "质检报告", "长期", "批量", "报价", "发货", "付款"]
    return {
        "文本长度": len(text),
        "数字数量": sum(ch.isdigit() for ch in text),
        "是否包含吨位": int("吨" in text or "kg" in lower_text),
        "是否询价": int(any(word in text for word in ["价", "报价", "多少钱"])),
        "是否要报告": int(any(word in text for word in ["报告", "检测", "质检", "资料"])),
        "是否现货": int(any(word in text for word in ["现货", "库存", "急需"])),
        "是否长期合作": int(any(word in text for word in ["长期", "年度", "稳定", "合作"])),
        "高意向关键词数": sum(1 for word in high_words if word in text),
    }


def score_inquiry_text(text: str) -> dict:
    artifact = _load_lead_artifact()
    fallback_features = _make_text_features(text)
    fallback_score = min(0.95, 0.2 + fallback_features["高意向关键词数"] * 0.11 + fallback_features["是否包含吨位"] * 0.12)
    if artifact:
        try:
            vectorizer = artifact["vectorizer"]
            scaler = artifact["scaler"]
            model = artifact["model"]
            struct_features = artifact.get("struct_features") or list(fallback_features)
            X_text = vectorizer.transform([text or ""])
            struct_vector = pd.DataFrame([[fallback_features.get(name, 0) for name in struct_features]], columns=struct_features)
            X_struct = scaler.transform(struct_vector)
            from scipy.sparse import hstack

            X = hstack([X_text, X_struct])
            if hasattr(model, "decision_function"):
                decision = float(model.decision_function(X)[0])
                score = 1 / (1 + math.exp(-decision))
            elif hasattr(model, "predict_proba"):
                score = float(model.predict_proba(X)[0][1])
            else:
                score = fallback_score
        except Exception:
            score = fallback_score
    else:
        score = fallback_score
    score = max(0.0, min(float(score), 1.0))
    return {
        "score": round(score, 4),
        "label": _lead_label(score),
        "features": fallback_features,
        "model_version": MODEL_VERSION,
    }


def refresh_customer_ai_results(db: Session, current_user: User) -> dict:
    rows, customer_map = _build_customer_feature_rows(db, current_user)
    generated_at = datetime.utcnow()
    segment_map = _predict_segments_for_rows(rows)
    refreshed = 0
    for row in rows:
        segment = segment_map.get(row["customer_id"], _fallback_segment(row))
        recommendations = _recommend_products(row)
        lead_score = _lead_score_from_features(row)
        existing = db.execute(select(CustomerAIResult).where(CustomerAIResult.customer_id == row["customer_id"])).scalar_one_or_none()
        payload = {
            "segment_label": segment,
            "segment_reason": _segment_reason(row, segment),
            "lead_score": Decimal(str(round(lead_score, 4))),
            "lead_label": _lead_label(lead_score),
            "rfm_json": {
                "recency": row["recency"],
                "frequency": row["frequency"],
                "monetary": round(row["monetary"], 4),
                "avg_order_amount": round(row["avg_order_amount"], 4),
                "category_diversity": row["category_diversity"],
                "profit_rate": round(row["profit_rate"], 4),
                "intent_rate": round(row["intent_rate"], 4),
                "latest_order_at": row["latest_order_at"],
            },
            "recommendations_json": recommendations,
            "model_version": MODEL_VERSION,
            "generated_at": generated_at,
        }
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(CustomerAIResult(customer_id=row["customer_id"], **payload))
        refreshed += 1
    db.commit()
    return {"refreshed": refreshed, "generated_at": generated_at.isoformat(), "customer_total": len(customer_map)}


def _serialize_result(row: CustomerAIResult | None, feature_row: dict) -> dict:
    if row is None:
        segment = _fallback_segment(feature_row)
        lead_score = _lead_score_from_features(feature_row)
        return {
            "customer_id": feature_row["customer_id"],
            "segment_label": segment,
            "segment_type": SEGMENT_LABELS.get(segment, {}).get("type", "info"),
            "segment_reason": _segment_reason(feature_row, segment),
            "lead_score": round(lead_score, 4),
            "lead_label": _lead_label(lead_score),
            "rfm": {
                "recency": feature_row["recency"],
                "frequency": feature_row["frequency"],
                "monetary": round(feature_row["monetary"], 4),
                "avg_order_amount": round(feature_row["avg_order_amount"], 4),
                "category_diversity": feature_row["category_diversity"],
                "profit_rate": round(feature_row["profit_rate"], 4),
                "intent_rate": round(feature_row["intent_rate"], 4),
                "latest_order_at": feature_row["latest_order_at"],
            },
            "recommendations": _recommend_products(feature_row),
            "model_version": MODEL_VERSION,
            "generated_at": None,
        }
    return {
        "customer_id": row.customer_id,
        "segment_label": row.segment_label,
        "segment_type": SEGMENT_LABELS.get(row.segment_label, {}).get("type", "info"),
        "segment_reason": row.segment_reason,
        "lead_score": _to_float(row.lead_score),
        "lead_label": row.lead_label,
        "rfm": row.rfm_json or {},
        "recommendations": row.recommendations_json or [],
        "model_version": row.model_version,
        "generated_at": _to_iso(row.generated_at),
    }


def list_customer_ai_results(db: Session, current_user: User, keyword: str | None = None, segment: str | None = None) -> dict:
    feature_rows, _ = _build_customer_feature_rows(db, current_user)
    result_rows = {
        row.customer_id: row
        for row in db.execute(select(CustomerAIResult).where(CustomerAIResult.customer_id.in_([item["customer_id"] for item in feature_rows] or [-1]))).scalars().all()
    }
    items = []
    lowered = (keyword or "").strip().lower()
    for feature_row in feature_rows:
        result = _serialize_result(result_rows.get(feature_row["customer_id"]), feature_row)
        if segment and result["segment_label"] != segment:
            continue
        if lowered and not any(
            lowered in str(value or "").lower()
            for value in [feature_row["customer_code"], feature_row["customer_name"], feature_row["contact_name"], feature_row["phone"]]
        ):
            continue
        items.append(
            {
                **feature_row,
                **result,
                "monetary": round(feature_row["monetary"], 4),
                "gross_profit": round(feature_row["gross_profit"], 4),
                "received_amount": round(feature_row["received_amount"], 4),
            }
        )
    items.sort(key=lambda item: (SEGMENT_LABELS.get(item["segment_label"], {}).get("priority", 0), item["lead_score"], item["monetary"]), reverse=True)
    summary = {
        "customer_count": len(items),
        "high_value_count": sum(1 for item in items if item["segment_label"] == "高价值客户"),
        "potential_count": sum(1 for item in items if item["segment_label"] == "潜力客户"),
        "risk_count": sum(1 for item in items if item["segment_label"] == "流失风险客户"),
        "high_lead_count": sum(1 for item in items if item["lead_label"] == "高意向"),
    }
    return {"summary": summary, "items": items}


def get_customer_ai_detail(db: Session, current_user: User, customer_id: int) -> dict:
    ensure_customer_access(db, current_user, customer_id)
    rows, customer_map = _build_customer_feature_rows(db, current_user)
    feature_row = next((item for item in rows if item["customer_id"] == customer_id), None)
    customer = customer_map.get(customer_id)
    if customer is None or feature_row is None:
        raise KeyError("客户不存在或不在当前数据范围内")
    result = db.execute(select(CustomerAIResult).where(CustomerAIResult.customer_id == customer_id)).scalar_one_or_none()
    orders = db.execute(
        select(SalesOrder)
        .options(joinedload(SalesOrder.items).joinedload(SalesOrderItem.product))
        .where(SalesOrder.customer_id == customer_id, SalesOrder.status != "cancelled")
        .order_by(SalesOrder.created_at.desc())
        .limit(10)
    ).unique().scalars().all()
    inquiries = db.execute(
        select(CustomerInquiry).where(CustomerInquiry.customer_id == customer_id).order_by(CustomerInquiry.created_at.desc()).limit(10)
    ).scalars().all()
    return {
        "customer": {
            "id": customer.id,
            "code": customer.code,
            "name": customer.name,
            "contact_name": customer.contact_name,
            "phone": customer.phone,
            "address": customer.address,
            "star_level": customer.star_level,
        },
        "analysis": _serialize_result(result, feature_row),
        "orders": [
            {
                "id": order.id,
                "order_no": order.order_no,
                "created_at": _to_iso(order.created_at),
                "status": order.status,
                "total_amount": _to_float(order.total_amount),
                "profit_amount": _to_float(order.profit_amount),
                "products": [item.product.name for item in order.items if item.product],
            }
            for order in orders
        ],
        "inquiries": [
            {
                "id": row.id,
                "inquiry_no": row.inquiry_no,
                "inquiry_text": row.inquiry_text,
                "source_channel": row.source_channel,
                "product_hint": row.product_hint,
                "intent_score": _to_float(row.intent_score),
                "intent_label": row.intent_label,
                "created_at": _to_iso(row.created_at),
            }
            for row in inquiries
        ],
    }


def create_customer_inquiry(db: Session, current_user: User, payload) -> dict:
    customer = db.execute(select(Customer).where(Customer.id == payload.customer_id)).scalar_one_or_none()
    if customer is None:
        raise KeyError("客户不存在")
    ensure_customer_access(db, current_user, customer.id)
    scoring = score_inquiry_text(payload.inquiry_text)
    inquiry = CustomerInquiry(
        inquiry_no=_new_no("INQ"),
        customer_id=payload.customer_id,
        inquiry_text=payload.inquiry_text.strip(),
        source_channel=(payload.source_channel or "").strip() or None,
        product_hint=(payload.product_hint or "").strip() or None,
        intent_score=Decimal(str(scoring["score"])),
        intent_label=scoring["label"],
        model_version=scoring["model_version"],
        created_by_id=current_user.id,
    )
    db.add(inquiry)
    db.commit()
    db.refresh(inquiry)
    refresh_customer_ai_results(db, current_user)
    return {
        "id": inquiry.id,
        "inquiry_no": inquiry.inquiry_no,
        "intent_score": _to_float(inquiry.intent_score),
        "intent_label": inquiry.intent_label,
        "model_version": inquiry.model_version,
    }


def recommend_for_order(db: Session, current_user: User, customer_id: int, product_ids: list[int]) -> dict:
    ensure_customer_access(db, current_user, customer_id)
    products = db.execute(select(Product).where(Product.id.in_(product_ids or [-1]))).scalars().all()
    purchased = sorted({row.name for row in products})
    fake_row = {"purchased_products": purchased}
    return {
        "customer_id": customer_id,
        "selected_products": purchased,
        "recommendations": _recommend_products(fake_row, limit=6),
    }
