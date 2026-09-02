from fastapi import APIRouter

from api.routers.admin import router as admin_router
from api.routers.analytics import router as analytics_router
from api.routers.approvals import router as approvals_router
from api.routers.auth import router as auth_router
from api.routers.customer_ai import router as customer_ai_router
from api.routers.dashboard import router as dashboard_router
from api.routers.export import router as export_router
from api.routers.files import router as files_router
from api.routers.finance import router as finance_router
from api.routers.inventory import router as inventory_router_list
from api.routers.logistics import router as logistics_router
from api.routers.master_data import router as master_data_router
from api.routers.production import router as production_router
from api.routers.purchases import router as purchases_router
from api.routers.quality import router as quality_router
from api.routers.realtime import router as realtime_router
from api.routers.reimbursements import router as reimbursements_router
from api.routers.sales import router as sales_router
from api.routers.supplier_ai import router as supplier_ai_router


api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(analytics_router)
api_router.include_router(customer_ai_router)
api_router.include_router(supplier_ai_router)
api_router.include_router(dashboard_router)
api_router.include_router(approvals_router)
api_router.include_router(master_data_router)
api_router.include_router(sales_router)
for _r in inventory_router_list:
    api_router.include_router(_r)
api_router.include_router(logistics_router)
api_router.include_router(purchases_router)
api_router.include_router(production_router)
api_router.include_router(quality_router)
api_router.include_router(realtime_router)
api_router.include_router(reimbursements_router)
api_router.include_router(finance_router)
api_router.include_router(admin_router)
api_router.include_router(export_router)
api_router.include_router(files_router)
