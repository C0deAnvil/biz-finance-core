from api.routers.inventory.demands import router as demands_router
from api.routers.inventory.transfers import router as transfers_router
from api.routers.inventory.queues import router as queues_router
from api.routers.inventory.stocks import router as stocks_router

router = [demands_router, transfers_router, queues_router, stocks_router]
