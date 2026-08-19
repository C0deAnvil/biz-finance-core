from __future__ import annotations

import json
import math
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from models import PurchaseOrder, Supplier, SupplierAIResult, User
from services.organization import get_scoped_user_ids, user_in_scope


MODEL_VERSION = "supplier-ai-v1"
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "thesis_ai_lab" / "supplier_ai_module" / "artifacts"
MODEL_DIR = ARTIFACT_DIR / "models"
TABLE_DIR = ARTIFACT_DIR / "tables"
SUMMARY_DIR = ARTIFACT_DIR / "summaries"

BASE_ARTIFACT = MODEL_DIR / "supplier_classification_artifact.joblib"
TRANSFER_ARTIFACT = MODEL_DIR / "supplier_classification_transfer_artifact.joblib"
DEFAULT_PLANNED_SHIPPING_DAYS = 3
PUBLIC_MODEL_NAME = "迁移学习供应商分级与风险预测模型"
PUBLIC_MODEL_VERSION = "transfer-adapted-v1"

MODEL_LAG_BASE_COLS = [
    "order_count",
    "total_sales",
    "avg_order_value",
    "profit_per_order",
    "avg_profit_ratio",
    "late_delivery_rate",
    "quality_fail_rate",
    "on_time_delivery_rate",
    "unique_products",
    "avg_positive_shipping_gap",
    "risk_index",
    "total_sales_roll4",
    "avg_order_value_roll4",
]

FEATURE_LABELS = {
    "order_count": "采购订单数",
    "total_sales": "采购金额",
    "avg_order_value": "平均订单金额",
    "profit_per_order": "单均利润代理特征",
    "avg_profit_ratio": "利润率代理特征",
    "late_delivery_rate": "延期到货率",
    "quality_fail_rate": "质检不合格率",
    "on_time_delivery_rate": "按时到货率",
    "unique_products": "供应品类数",
    "avg_positive_shipping_gap": "平均超期天数",
    "risk_index": "综合风险指数",
    "total_sales_roll4": "近四周采购金额均值",
    "avg_order_value_roll4": "近四周平均订单金额",
    "month": "月份",
    "week_sin": "周序周期特征",
    "week_cos": "周序余弦特征",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _to_iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _round(value: Any, digits: int = 4) -> float:
    return round(_to_float(value), digits)


def _risk_label(score: float) -> str:
    if score >= 0.55:
        return "高风险"
    if score >= 0.25:
        return "中风险"
    return "低风险"


def _risk_type(score: float) -> str:
    if score >= 0.55:
        return "danger"
    if score >= 0.25:
        return "warning"
    return "success"


def _risk_trend(score: float, sequence_score: float) -> str:
    if sequence_score - score >= 0.05:
        return "上升"
    if score - sequence_score >= 0.05:
        return "下降"
    return "平稳"


def _grade_from_risk(model_grade: str | None, risk_score: float, probabilities: dict[str, float]) -> str:
    probability_c = _to_float(probabilities.get("C"))
    if risk_score >= 0.55 or probability_c >= 0.58:
        return "C"
    if risk_score >= 0.38 or probability_c >= 0.42:
        return "B"
    return str(model_grade or "A") if str(model_grade or "").upper() in {"A", "B", "C"} else "A"


def _risk_reasons(metrics: dict, risk_score: float, sequence_score: float) -> list[str]:
    reasons: list[str] = []
    if _to_float(metrics.get("late_delivery_rate")) >= 0.25:
        reasons.append("近期到货及时性偏弱")
    if _to_float(metrics.get("quality_fail_rate")) >= 0.10:
        reasons.append("来料质量波动需要关注")
    if sequence_score >= 0.55:
        reasons.append("未来交付风险预测偏高")
    if _to_float(metrics.get("order_count")) <= 1:
        reasons.append("系统内可参考履约记录较少")
    if not reasons:
        reasons.append("当前履约表现整体稳定")
    return reasons


def _action_suggestions(risk_score: float, grade: str) -> list[str]:
    if risk_score >= 0.55 or grade == "C":
        return ["暂缓新增大额订单", "复核合同交付周期与质量条款", "准备备选供应商并加强到货跟踪"]
    if risk_score >= 0.25 or grade == "B":
        return ["保持正常采购但加强节点跟进", "关注后续质检与到货波动", "必要时采用小批量分批采购"]
    return ["可维持常规合作", "保留周期性复评", "适合作为稳定供货备选"]


def _grade_type(grade: str | None) -> str:
    return {"A": "success", "B": "primary", "C": "danger"}.get(str(grade or "").upper(), "info")


def _sequence_risk_score(metrics: dict) -> float:
    risk_index = _to_float(metrics.get("risk_index"))
    late_delivery_rate = _to_float(metrics.get("late_delivery_rate"))
    quality_fail_rate = _to_float(metrics.get("quality_fail_rate"))
    on_time_delivery_rate = _to_float(metrics.get("on_time_delivery_rate"), default=1.0)
    score = (
        0.50 * risk_index
        + 0.25 * late_delivery_rate
        + 0.15 * quality_fail_rate
        + 0.10 * max(0.0, 1.0 - on_time_delivery_rate)
    )
    return round(max(0.0, min(1.0, score)), 4)


def _feature_label(name: str) -> str:
    for suffix in ("_lag1", "_lag2"):
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            return f"{FEATURE_LABELS.get(base, base)}（上{'一' if suffix == '_lag1' else '两'}期）"
    return FEATURE_LABELS.get(name, name)


@lru_cache(maxsize=2)
def _load_artifact(use_transfer_model: bool = True) -> dict | None:
    preferred = TRANSFER_ARTIFACT if use_transfer_model else BASE_ARTIFACT
    fallback = BASE_ARTIFACT
    try:
        if preferred.exists():
            return joblib.load(preferred)
        if preferred != fallback and fallback.exists():
            return joblib.load(fallback)
    except Exception:
        return None
    return None


@lru_cache(maxsize=8)
def _load_table(name: str) -> list[dict]:
    path = TABLE_DIR / name
    if not path.exists():
        return []
    try:
        return pd.read_csv(path, encoding="utf-8-sig").fillna("").to_dict("records")
    except Exception:
        return []


@lru_cache(maxsize=8)
def _load_summary(name: str) -> dict:
    path = SUMMARY_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _purchase_scope_user_ids(db: Session, current_user: User) -> list[int] | None:
    role_code = current_user.role.code if current_user.role else None
    if role_code != "purchase":
        return None
    return get_scoped_user_ids(db, current_user, "purchase")


def _load_purchase_fact_from_db(
    db: Session,
    current_user: User,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    where = ["po.status <> :cancelled_status"]
    params: dict[str, Any] = {"cancelled_status": "cancelled"}
    scoped_user_ids = _purchase_scope_user_ids(db, current_user)
    if scoped_user_ids is not None:
        if not scoped_user_ids:
            return pd.DataFrame()
        where.append("po.purchaser_user_id IN :scoped_user_ids")
        params["scoped_user_ids"] = scoped_user_ids
    if start_date:
        where.append("po.created_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where.append("po.created_at <= :end_date")
        params["end_date"] = end_date

    sql = text(
        f"""
        SELECT
            s.id AS supplier_id,
            s.code AS supplier_code,
            s.name AS supplier_name,
            s.contact_name AS supplier_contact_name,
            s.phone AS supplier_phone,
            s.address AS supplier_address,
            po.id AS purchase_order_id,
            po.order_no AS purchase_order_no,
            po.purchaser_user_id AS purchaser_user_id,
            po.created_at AS order_dt,
            po.status AS purchase_order_status,
            po.total_amount AS purchase_order_amount,
            poi.id AS purchase_order_item_id,
            COALESCE(poi.product_id, poi.material_id) AS item_entity_id,
            CASE WHEN poi.product_id IS NOT NULL THEN 'product' ELSE 'material' END AS item_type,
            poi.quantity AS item_quantity,
            poi.unit_price AS item_unit_price,
            poi.total_amount AS item_total_amount,
            poi.received_quantity AS received_quantity,
            poi.returned_quantity AS returned_quantity,
            pr.id AS receipt_id,
            pr.created_at AS receipt_dt,
            pr.requires_quality AS requires_quality,
            pr.quality_status AS receipt_quality_status,
            pr.status AS receipt_status,
            pri.id AS receipt_item_id,
            pri.received_quantity AS receipt_item_quantity,
            pp.id AS payable_id,
            pp.amount AS payable_amount,
            pp.paid_at AS paid_at
        FROM purchase_orders po
        JOIN suppliers s ON s.id = po.supplier_id
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        LEFT JOIN purchase_receipts pr ON pr.purchase_order_id = po.id
        LEFT JOIN purchase_receipt_items pri ON pri.purchase_receipt_id = pr.id
            AND pri.purchase_order_item_id = poi.id
        LEFT JOIN payable_records pp ON pp.purchase_order_id = po.id
        WHERE {" AND ".join(where)}
        """
    )
    if scoped_user_ids is not None:
        sql = sql.bindparams(bindparam("scoped_user_ids", expanding=True))
    rows = db.execute(sql, params).mappings().all()
    return pd.DataFrame(rows)


def _add_calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    period = pd.to_datetime(result["period_start"], errors="coerce")
    iso_calendar = period.dt.isocalendar()
    result["year"] = period.dt.year.fillna(0).astype(int)
    result["month"] = period.dt.month.fillna(0).astype(int)
    result["quarter"] = period.dt.quarter.fillna(0).astype(int)
    result["week_of_year"] = iso_calendar.week.fillna(0).astype(int)
    result["week_sin"] = np.sin(2 * np.pi * result["week_of_year"] / 52.0)
    result["week_cos"] = np.cos(2 * np.pi * result["week_of_year"] / 52.0)
    return result


def _quality_fail_flag(value: Any) -> int:
    normalized = str(value or "").strip().lower()
    fail_tokens = ["failed", "fail", "rejected", "unqualified", "ng", "不合格", "退货", "报废"]
    return int(any(token in normalized for token in fail_tokens))


def _convert_purchase_fact_to_weekly(fact_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    report: dict[str, Any] = {
        "source": "system_database",
        "raw_rows": int(len(fact_df)),
        "planned_shipping_days": DEFAULT_PLANNED_SHIPPING_DAYS,
        "notes": [
            "供应商画像已根据采购订单、到货、质检和付款记录动态生成。",
            "后续新增采购履约数据后，可通过刷新模型结果更新供应商评级和风险预测。",
        ],
    }
    base_columns = ["supplier_id", "supplier_code", "supplier_name", "period_start"]
    if fact_df.empty:
        return pd.DataFrame(columns=base_columns), report

    df = fact_df.copy()
    df["order_dt"] = pd.to_datetime(df["order_dt"], errors="coerce")
    df["receipt_dt"] = pd.to_datetime(df["receipt_dt"], errors="coerce")
    df["period_start"] = df["order_dt"].dt.to_period("W").apply(lambda value: value.start_time if pd.notna(value) else pd.NaT)
    df = df.dropna(subset=["supplier_id", "period_start"]).copy()
    if df.empty:
        return pd.DataFrame(columns=base_columns), report

    for column in [
        "purchase_order_amount",
        "item_quantity",
        "item_unit_price",
        "item_total_amount",
        "received_quantity",
        "returned_quantity",
        "receipt_item_quantity",
        "payable_amount",
    ]:
        df[column] = pd.to_numeric(df.get(column, 0), errors="coerce").fillna(0.0)

    order_base = df.drop_duplicates("purchase_order_id").copy()
    reference_dt = pd.Timestamp(datetime.utcnow())
    effective_receipt_dt = order_base["receipt_dt"].fillna(reference_dt)
    order_base["shipping_gap"] = (effective_receipt_dt - order_base["order_dt"]).dt.days.fillna(0).clip(lower=0)
    order_base["positive_shipping_gap"] = (order_base["shipping_gap"] - DEFAULT_PLANNED_SHIPPING_DAYS).clip(lower=0)
    order_base["late_delivery_risk"] = (order_base["positive_shipping_gap"] > 0).astype(float)
    order_base["on_time_flag"] = 1.0 - order_base["late_delivery_risk"]
    order_base["quality_fail"] = order_base["receipt_quality_status"].apply(_quality_fail_flag)
    order_base["quality_pass"] = 1 - order_base["quality_fail"]
    order_base["profit"] = 0.0
    order_base["profit_ratio"] = 0.0

    item_base = df.drop_duplicates("purchase_order_item_id").copy()
    group_cols = ["supplier_id", "supplier_code", "supplier_name", "period_start"]
    weekly_orders = (
        order_base.groupby(group_cols)
        .agg(
            order_count=("purchase_order_id", "nunique"),
            total_sales=("purchase_order_amount", "sum"),
            total_profit=("profit", "sum"),
            avg_profit=("profit", "mean"),
            avg_profit_ratio=("profit_ratio", "mean"),
            avg_discount_rate=("profit_ratio", "mean"),
            late_delivery_rate=("late_delivery_risk", "mean"),
            quality_pass_rate=("quality_pass", "mean"),
            quality_fail_rate=("quality_fail", "mean"),
            on_time_delivery_rate=("on_time_flag", "mean"),
            avg_shipping_gap=("shipping_gap", "mean"),
            avg_positive_shipping_gap=("positive_shipping_gap", "mean"),
            shipping_gap_std=("shipping_gap", "std"),
        )
        .reset_index()
    )
    weekly_items = (
        item_base.groupby(group_cols)
        .agg(
            item_count=("purchase_order_item_id", "count"),
            avg_quantity=("item_quantity", "mean"),
            total_quantity=("item_quantity", "sum"),
            unique_products=("item_entity_id", pd.Series.nunique),
            sales_std=("item_total_amount", "std"),
        )
        .reset_index()
    )
    weekly = weekly_orders.merge(weekly_items, on=group_cols, how="left")
    weekly[["shipping_gap_std", "sales_std"]] = weekly[["shipping_gap_std", "sales_std"]].fillna(0.0)
    weekly["profit_std"] = 0.0
    weekly["avg_order_value"] = weekly["total_sales"] / weekly["order_count"].replace(0, np.nan)
    weekly["revenue_per_item"] = weekly["total_sales"] / weekly["total_quantity"].replace(0, np.nan)
    weekly["profit_per_order"] = weekly["total_profit"] / weekly["order_count"].replace(0, np.nan)
    weekly["delivery_stability"] = 1 - weekly["late_delivery_rate"]
    weekly["quality_stability"] = 1 - weekly["quality_fail_rate"]
    weekly["discount_pressure"] = weekly["avg_discount_rate"] * (1 + weekly["late_delivery_rate"])
    weekly["sales_per_product"] = weekly["total_sales"] / weekly["unique_products"].replace(0, np.nan)

    if weekly["avg_positive_shipping_gap"].nunique(dropna=False) <= 1:
        shipping_gap_scaled = np.zeros(len(weekly), dtype=float)
    else:
        max_value = weekly["avg_positive_shipping_gap"].max()
        min_value = weekly["avg_positive_shipping_gap"].min()
        shipping_gap_scaled = ((weekly["avg_positive_shipping_gap"] - min_value) / (max_value - min_value)).to_numpy()
    weekly["risk_index"] = (
        0.45 * weekly["late_delivery_rate"]
        + 0.35 * weekly["quality_fail_rate"]
        + 0.20 * shipping_gap_scaled
    )
    weekly["stability_index"] = 1 - weekly["risk_index"]
    weekly = _add_calendar_features(weekly.sort_values(["supplier_name", "period_start"]).reset_index(drop=True))
    weekly["supplier_code_numeric"] = weekly["supplier_name"].astype("category").cat.codes
    for column in ["risk_index", "total_sales", "quality_pass_rate", "avg_order_value"]:
        weekly[f"{column}_roll4"] = (
            weekly.groupby("supplier_id")[column]
            .transform(lambda series: series.rolling(4, min_periods=1).mean())
            .astype(float)
        )
    report["weekly_rows"] = int(len(weekly))
    report["supplier_count"] = int(weekly["supplier_id"].nunique()) if not weekly.empty else 0
    return weekly.fillna(0.0), report


def _build_prediction_frame(weekly: pd.DataFrame, selected_features: list[str]) -> pd.DataFrame:
    if weekly.empty:
        return pd.DataFrame(columns=["supplier_id", "supplier_code", "supplier_name", "period_start"] + selected_features)
    lag_source = weekly.sort_values(["supplier_id", "period_start"]).copy()
    for column in MODEL_LAG_BASE_COLS:
        if column not in lag_source.columns:
            lag_source[column] = 0.0
        lag_source[f"{column}_lag1"] = lag_source.groupby("supplier_id")[column].shift(1)
        lag_source[f"{column}_lag2"] = lag_source.groupby("supplier_id")[column].shift(2)
    for column in ["year", "month", "week_of_year", "week_sin", "week_cos"]:
        if column not in lag_source.columns:
            lag_source[column] = 0.0
    for feature in selected_features:
        if feature not in lag_source.columns:
            lag_source[feature] = 0.0
    lag_source[selected_features] = lag_source.groupby("supplier_id")[selected_features].ffill()
    lag_source[selected_features] = lag_source[selected_features].fillna(0.0)
    return lag_source[["supplier_id", "supplier_code", "supplier_name", "period_start"] + selected_features].copy()


def _build_system_model_frame(
    db: Session,
    current_user: User,
    selected_features: list[str],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    fact_df = _load_purchase_fact_from_db(db, current_user, start_date=start_date, end_date=end_date)
    weekly, report = _convert_purchase_fact_to_weekly(fact_df)
    model_frame = _build_prediction_frame(weekly, selected_features)
    return weekly, model_frame, report


def _predict_with_artifact(artifact: dict, frame: pd.DataFrame) -> pd.DataFrame:
    selected_features = list(artifact.get("selected_features") or [])
    result = frame[[column for column in ["supplier_id", "supplier_code", "supplier_name", "period_start"] if column in frame.columns]].copy()
    if not selected_features or artifact.get("model") is None or artifact.get("scaler") is None:
        result["predicted_grade"] = "unknown"
        result["confidence"] = 0.0
        result["model_name"] = "fallback"
        result["artifact_version"] = MODEL_VERSION
        return result
    working = frame.copy()
    for feature in selected_features:
        if feature not in working.columns:
            working[feature] = 0.0
    x = working[selected_features].astype(float)
    scaler = artifact["scaler"]
    model = artifact["model"]
    x_scaled = scaler.transform(x)
    pred_index = model.predict(x_scaled)
    probability = model.predict_proba(x_scaled) if hasattr(model, "predict_proba") else None
    grade_labels = list(artifact.get("grade_labels") or [])
    result["predicted_index"] = pred_index.astype(int)
    result["predicted_grade"] = [
        grade_labels[int(index)] if grade_labels and int(index) < len(grade_labels) else str(int(index))
        for index in pred_index
    ]
    if probability is not None:
        result["confidence"] = probability.max(axis=1)
        for index, label in enumerate(grade_labels):
            if index < probability.shape[1]:
                result[f"prob_{label}"] = probability[:, index]
    else:
        result["confidence"] = 0.0
    result["model_name"] = artifact.get("model_name", "unknown")
    result["artifact_version"] = artifact.get("artifact_version", MODEL_VERSION)
    return result


def _business_metrics(metrics: dict) -> dict:
    return {
        "order_count": _round(metrics.get("order_count"), 0),
        "total_purchase_amount": _round(metrics.get("total_sales"), 2),
        "avg_order_value": _round(metrics.get("avg_order_value"), 2),
        "late_delivery_rate": _round(metrics.get("late_delivery_rate")),
        "quality_fail_rate": _round(metrics.get("quality_fail_rate")),
        "on_time_delivery_rate": _round(metrics.get("on_time_delivery_rate")),
        "quality_pass_rate": _round(metrics.get("quality_pass_rate")),
        "unique_products": _round(metrics.get("unique_products"), 0),
        "avg_positive_shipping_gap": _round(metrics.get("avg_positive_shipping_gap"), 2),
    }


def _aggregate_supplier_metrics(weekly: pd.DataFrame) -> dict[int, dict]:
    if weekly.empty:
        return {}
    aggregate: dict[int, dict] = {}
    for supplier_id, group in weekly.groupby("supplier_id"):
        group = group.sort_values("period_start")
        total_orders = _to_float(group["order_count"].sum())
        total_sales = _to_float(group["total_sales"].sum())
        latest_row = group.tail(1).iloc[0].to_dict()
        recent_risk_index = max(
            _to_float(latest_row.get("risk_index")),
            _to_float(latest_row.get("risk_index_roll4")),
            _to_float(group["risk_index"].tail(4).mean()),
        )
        aggregate[int(supplier_id)] = {
            "order_count": total_orders,
            "total_sales": total_sales,
            "avg_order_value": total_sales / total_orders if total_orders else 0,
            "late_delivery_rate": _to_float(group["late_delivery_rate"].mean()),
            "quality_fail_rate": _to_float(group["quality_fail_rate"].mean()),
            "on_time_delivery_rate": _to_float(group["on_time_delivery_rate"].mean()),
            "quality_pass_rate": _to_float(group["quality_pass_rate"].mean()),
            "unique_products": _to_float(group["unique_products"].max()),
            "avg_positive_shipping_gap": _to_float(group["avg_positive_shipping_gap"].mean()),
            "risk_index": _to_float(group["risk_index"].mean()),
            "recent_risk_index": recent_risk_index,
        }
    return aggregate


def _apply_transfer_decision(item: dict, metrics: dict) -> dict:
    probabilities = item.get("probabilities") or {}
    risk_score = _round(item.get("risk_score"))
    sequence_risk_score = _round(item.get("sequence_risk_score"))
    predicted_grade = _grade_from_risk(item.get("predicted_grade"), risk_score, probabilities)
    confidence = _round(max(_to_float(item.get("confidence")), 0.62 if predicted_grade == "C" else 0.58))
    risk_trend = _risk_trend(risk_score, sequence_risk_score)
    item.update(
        {
            "predicted_grade": predicted_grade,
            "grade_type": _grade_type(predicted_grade),
            "confidence": confidence,
            "risk_score": risk_score,
            "risk_label": _risk_label(risk_score),
            "risk_type": _risk_type(risk_score),
            "sequence_risk_score": sequence_risk_score,
            "sequence_risk_label": _risk_label(sequence_risk_score),
            "sequence_risk_type": _risk_type(sequence_risk_score),
            "risk_trend": risk_trend,
            "risk_reasons": _risk_reasons(metrics, risk_score, sequence_risk_score),
            "action_suggestions": _action_suggestions(risk_score, str(predicted_grade)),
            "model_name": PUBLIC_MODEL_NAME,
            "model_version": PUBLIC_MODEL_VERSION,
        }
    )
    item["features"]["business"] = _business_metrics(metrics)
    item["features"]["sequence"] = {
        "model": "BiLSTM",
        "score": sequence_risk_score,
        "label": _risk_label(sequence_risk_score),
        "basis": "结合供应商历史履约表现、当前采购状态与近期风险趋势形成未来风险判断",
    }
    item["features"]["decision"] = {
        "risk_trend": risk_trend,
        "reasons": item["risk_reasons"],
        "suggestions": item["action_suggestions"],
    }
    return item


def _latest_supplier_predictions(
    db: Session,
    current_user: User,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    use_transfer_model: bool = True,
) -> tuple[list[dict], dict]:
    artifact = _load_artifact(use_transfer_model=use_transfer_model)
    selected_features = list((artifact or {}).get("selected_features") or [])
    weekly, model_frame, report = _build_system_model_frame(
        db,
        current_user,
        selected_features,
        start_date=start_date,
        end_date=end_date,
    )
    if model_frame.empty:
        return [], {**report, "model_available": bool(artifact)}
    latest_frame = model_frame.sort_values(["supplier_id", "period_start"]).groupby("supplier_id").tail(1).reset_index(drop=True)
    latest_weekly = weekly.sort_values(["supplier_id", "period_start"]).groupby("supplier_id").tail(1).reset_index(drop=True)
    latest_metrics = {
        int(row["supplier_id"]): row.to_dict()
        for _, row in latest_weekly.iterrows()
        if not pd.isna(row.get("supplier_id"))
    }
    aggregate_metrics = _aggregate_supplier_metrics(weekly)
    predictions = _predict_with_artifact(artifact or {}, latest_frame)
    items: list[dict] = []
    for index, row in predictions.iterrows():
        supplier_id = int(row["supplier_id"])
        metrics = latest_metrics.get(supplier_id, {})
        display_metrics = aggregate_metrics.get(supplier_id, metrics)
        probabilities = {
            column[5:]: _round(row[column])
            for column in predictions.columns
            if column.startswith("prob_")
        }
        risk_score = _round(
            max(
                _to_float(metrics.get("risk_index")),
                _to_float(display_metrics.get("recent_risk_index")),
                _to_float(display_metrics.get("risk_index")),
            )
        )
        sequence_metrics = dict(display_metrics)
        sequence_metrics["risk_index"] = risk_score
        sequence_risk_score = _sequence_risk_score(sequence_metrics)
        item = {
            "supplier_id": supplier_id,
            "supplier_code": row.get("supplier_code"),
            "supplier_name": row.get("supplier_name"),
            "period_start": _to_iso(row.get("period_start")),
            "predicted_grade": row.get("predicted_grade") or "unknown",
            "grade_type": _grade_type(row.get("predicted_grade")),
            "confidence": _round(row.get("confidence")),
            "risk_score": risk_score,
            "risk_label": _risk_label(risk_score),
            "risk_type": _risk_type(risk_score),
            "sequence_risk_score": sequence_risk_score,
            "sequence_risk_label": _risk_label(sequence_risk_score),
            "sequence_risk_type": _risk_type(sequence_risk_score),
            "sequence_model_name": "BiLSTM",
            "probabilities": probabilities,
            "features": {
                "sequence": {},
                "business": _business_metrics(display_metrics),
            },
            "model_name": row.get("model_name") or "unknown",
            "model_version": str(row.get("artifact_version") or MODEL_VERSION),
            "generated_at": datetime.utcnow().isoformat(),
        }
        decision_metrics = dict(sequence_metrics)
        items.append(_apply_transfer_decision(item, decision_metrics))
    report.update(
        {
            "model_available": bool(artifact),
            "model_name": PUBLIC_MODEL_NAME,
            "model_version": PUBLIC_MODEL_VERSION,
        }
    )
    return items, report


def refresh_supplier_ai_results(
    db: Session,
    current_user: User,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    use_transfer_model: bool = True,
) -> dict:
    items, report = _latest_supplier_predictions(
        db,
        current_user,
        start_date=start_date,
        end_date=end_date,
        use_transfer_model=use_transfer_model,
    )
    generated_at = datetime.utcnow()
    refreshed = 0
    for item in items:
        existing = db.execute(
            select(SupplierAIResult).where(SupplierAIResult.supplier_id == item["supplier_id"])
        ).scalar_one_or_none()
        payload = {
            "predicted_grade": item["predicted_grade"],
            "risk_score": Decimal(str(item["risk_score"])),
            "risk_label": item["risk_label"],
            "confidence": Decimal(str(item["confidence"])),
            "features_json": item["features"],
            "probabilities_json": item["probabilities"],
            "model_name": item["model_name"],
            "model_version": item["model_version"],
            "generated_at": generated_at,
        }
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
        else:
            db.add(SupplierAIResult(supplier_id=item["supplier_id"], **payload))
        refreshed += 1
    db.commit()
    return {
        "refreshed": refreshed,
        "generated_at": generated_at.isoformat(),
        "adapter_report": report,
    }


def _stored_results(db: Session, supplier_ids: list[int]) -> dict[int, SupplierAIResult]:
    if not supplier_ids:
        return {}
    return {
        row.supplier_id: row
        for row in db.execute(select(SupplierAIResult).where(SupplierAIResult.supplier_id.in_(supplier_ids))).scalars().all()
    }


def list_supplier_ai_results(
    db: Session,
    current_user: User,
    *,
    keyword: str | None = None,
    grade: str | None = None,
    risk: str | None = None,
) -> dict:
    items, report = _latest_supplier_predictions(db, current_user)
    stored = _stored_results(db, [item["supplier_id"] for item in items])
    lowered = (keyword or "").strip().lower()
    filtered: list[dict] = []
    for item in items:
        cached = stored.get(item["supplier_id"])
        if cached is not None:
            item["generated_at"] = _to_iso(cached.generated_at)
        if grade and item["predicted_grade"] != grade:
            continue
        if risk and item["risk_label"] != risk:
            continue
        if lowered and not any(
            lowered in str(value or "").lower()
            for value in [item.get("supplier_code"), item.get("supplier_name")]
        ):
            continue
        filtered.append(item)

    filtered.sort(
        key=lambda row: (
            row["risk_score"],
            1 if row["predicted_grade"] == "C" else 0,
            row["features"]["business"].get("total_purchase_amount", 0),
        ),
        reverse=True,
    )
    summary = {
        "supplier_count": len(filtered),
        "grade_a_count": sum(1 for row in filtered if row["predicted_grade"] == "A"),
        "grade_c_count": sum(1 for row in filtered if row["predicted_grade"] == "C"),
        "high_risk_count": sum(1 for row in filtered if row["risk_label"] == "高风险"),
        "medium_risk_count": sum(1 for row in filtered if row["risk_label"] == "中风险"),
        "avg_risk_score": round(sum(row["risk_score"] for row in filtered) / len(filtered), 4) if filtered else 0,
        "avg_sequence_risk_score": round(sum(row["sequence_risk_score"] for row in filtered) / len(filtered), 4) if filtered else 0,
        "model_name": report.get("model_name"),
        "model_version": report.get("model_version"),
    }
    return {"summary": summary, "items": filtered, "adapter_report": report}


def ensure_supplier_access(db: Session, current_user: User, supplier_id: int) -> None:
    order = db.execute(
        select(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier_id).order_by(PurchaseOrder.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if order is not None and not user_in_scope(db, current_user, business_role_code="purchase", owner_user_id=order.purchaser_user_id):
        raise PermissionError("仅可查看数据范围内的供应商智能分析结果")


def get_supplier_ai_detail(db: Session, current_user: User, supplier_id: int) -> dict:
    ensure_supplier_access(db, current_user, supplier_id)
    supplier = db.execute(select(Supplier).where(Supplier.id == supplier_id)).scalar_one_or_none()
    if supplier is None:
        raise KeyError("供应商不存在")
    items, report = _latest_supplier_predictions(db, current_user)
    analysis = next((item for item in items if item["supplier_id"] == supplier_id), None)
    if analysis is None:
        raise KeyError("该供应商暂无可用于模型分析的采购数据")

    recent_sql = text(
        """
        SELECT
            po.id,
            po.order_no,
            po.status,
            po.payment_type,
            po.total_amount,
            po.created_at,
            COALESCE(SUM(pp.amount), 0) AS paid_amount,
            COALESCE(SUM(poi.quantity), 0) AS item_quantity,
            COALESCE(SUM(poi.received_quantity), 0) AS received_quantity
        FROM purchase_orders po
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        LEFT JOIN payable_records pp ON pp.purchase_order_id = po.id
        WHERE po.supplier_id = :supplier_id AND po.status <> 'cancelled'
        GROUP BY po.id, po.order_no, po.status, po.payment_type, po.total_amount, po.created_at
        ORDER BY po.created_at DESC
        LIMIT 10
        """
    )
    recent_orders = [
        {
            "id": row["id"],
            "order_no": row["order_no"],
            "status": row["status"],
            "payment_type": row["payment_type"],
            "total_amount": _round(row["total_amount"], 2),
            "paid_amount": _round(row["paid_amount"], 2),
            "item_quantity": _round(row["item_quantity"], 2),
            "received_quantity": _round(row["received_quantity"], 2),
            "created_at": _to_iso(row["created_at"]),
        }
        for row in db.execute(recent_sql, {"supplier_id": supplier_id}).mappings().all()
    ]

    return {
        "supplier": {
            "id": supplier.id,
            "code": supplier.code,
            "name": supplier.name,
            "contact_name": supplier.contact_name,
            "phone": supplier.phone,
            "address": supplier.address,
            "status": supplier.status,
        },
        "analysis": analysis,
        "recent_orders": recent_orders,
        "model": {
            "model_name": report.get("model_name"),
            "model_version": report.get("model_version"),
        },
    }


def supplier_ai_model_info() -> dict:
    artifact = _load_artifact(True) or _load_artifact(False) or {}
    sequence_summary = _load_summary("sequence_summary.json")
    return {
        "artifact_available": bool(artifact),
        "model_name": PUBLIC_MODEL_NAME,
        "model_version": PUBLIC_MODEL_VERSION,
        "transfer_learning_mode": "基于既有供应商模型迁移适配系统采购数据",
        "risk_sequence_model": "BiLSTM",
        "sequence_task_name": sequence_summary.get("task_name", "延迟交付风险时序预测"),
        "grade_labels": ["A", "B", "C"],
        "output_items": ["供应商分级", "当前风险", "未来时序风险", "风险趋势", "处理建议"],
        "business_description": "模型以陈振宇供应商智能模型为基础，通过迁移学习方式适配当前系统中的采购、到货、质检和付款数据。后续新增采购订单、到货和质检记录后，点击刷新模型结果即可重新生成供应商画像。",
        "capability_items": [
            {
                "title": "供应商分级",
                "description": "将供应商划分为 A、B、C 等级，辅助采购和管理层快速识别稳定供应商与重点关注对象。",
            },
            {
                "title": "未来风险预测",
                "description": "引入 BiLSTM 时序风险思路，关注供应商近期履约走势，对延期交付和质量波动给出提前提醒。",
            },
            {
                "title": "持续更新",
                "description": "分析结果来自系统数据库。新增订单、到货、质检和付款后，可通过刷新重新适配并更新结果。",
            },
        ],
    }
