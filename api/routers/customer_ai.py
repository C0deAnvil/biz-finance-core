from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_roles
from models import User
from responses import api_response
from schemas.customer_ai import CustomerInquiryCreate, OrderRecommendationRequest
from services.customer_ai_service import (
    create_customer_inquiry,
    get_customer_ai_detail,
    list_customer_ai_results,
    recommend_for_order,
    refresh_customer_ai_results,
    score_inquiry_text,
)


router = APIRouter(prefix="/customer-ai", tags=["客户智能分析"])


AI_ROLES = ("sales", "manager", "chairman", "system_admin", "master_data_admin")


@router.get("/customers")
def list_customer_analysis(
    keyword: str | None = Query(default=None),
    segment: str | None = Query(default=None),
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    return api_response(data=list_customer_ai_results(db, current_user, keyword=keyword, segment=segment))


@router.post("/refresh")
def refresh_customer_analysis(
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    return api_response(msg="客户智能分析结果已刷新", data=refresh_customer_ai_results(db, current_user))


@router.get("/customers/{customer_id}")
def customer_analysis_detail(
    customer_id: int,
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    try:
        return api_response(data=get_customer_ai_detail(db, current_user, customer_id))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/inquiries")
def create_inquiry(
    payload: CustomerInquiryCreate,
    current_user: User = Depends(require_roles("sales", "manager", "chairman", "system_admin")),
    db: Session = Depends(get_db),
):
    try:
        return api_response(msg="客户咨询已记录并完成意向评分", data=create_customer_inquiry(db, current_user, payload))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/lead-score")
def lead_score(payload: CustomerInquiryCreate, current_user: User = Depends(require_roles(*AI_ROLES))):
    return api_response(data=score_inquiry_text(payload.inquiry_text))


@router.post("/recommendations/order")
def order_recommendations(
    payload: OrderRecommendationRequest,
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    try:
        return api_response(data=recommend_for_order(db, current_user, payload.customer_id, payload.product_ids))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
