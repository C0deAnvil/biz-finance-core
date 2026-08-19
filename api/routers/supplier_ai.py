from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_roles
from models import User
from responses import api_response
from services.supplier_ai_service import (
    get_supplier_ai_detail,
    list_supplier_ai_results,
    refresh_supplier_ai_results,
    supplier_ai_model_info,
)


router = APIRouter(prefix="/supplier-ai", tags=["供应商智能分析"])

AI_ROLES = ("purchase", "manager", "chairman", "system_admin", "master_data_admin")


@router.get("/suppliers")
def list_supplier_analysis(
    keyword: str | None = Query(default=None),
    grade: str | None = Query(default=None),
    risk: str | None = Query(default=None),
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    return api_response(data=list_supplier_ai_results(db, current_user, keyword=keyword, grade=grade, risk=risk))


@router.post("/refresh")
def refresh_supplier_analysis(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    use_transfer_model: bool = Query(default=True),
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    return api_response(
        msg="供应商智能分析结果已刷新",
        data=refresh_supplier_ai_results(
            db,
            current_user,
            start_date=start_date,
            end_date=end_date,
            use_transfer_model=use_transfer_model,
        ),
    )


@router.get("/suppliers/{supplier_id}")
def supplier_analysis_detail(
    supplier_id: int,
    current_user: User = Depends(require_roles(*AI_ROLES)),
    db: Session = Depends(get_db),
):
    try:
        return api_response(data=get_supplier_ai_detail(db, current_user, supplier_id))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/model-info")
def model_info(current_user: User = Depends(require_roles(*AI_ROLES))):
    return api_response(data=supplier_ai_model_info())
