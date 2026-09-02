from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models import User
from responses import api_response
from services.analytics_service import get_metric_detail, list_accessible_metrics


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/metrics")
def list_metrics(
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return api_response(data=list_accessible_metrics(current_user, keyword=keyword))


@router.get("/metrics/{metric_code}")
def metric_detail(
    metric_code: str,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    top_n: int = Query(default=8, ge=3, le=20),
    chart_type: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 需求:统计默认展示本月数据;未传时间段时按本月1号~今天
    today = date.today()
    if date_from is None:
        date_from = today.replace(day=1)
    if date_to is None:
        date_to = today
    try:
        data = get_metric_detail(
            db,
            current_user,
            metric_code=metric_code,
            date_from=date_from,
            date_to=date_to,
            top_n=top_n,
            chart_type=chart_type,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return api_response(data=data)
