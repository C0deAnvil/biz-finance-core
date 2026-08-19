import os
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.router import api_router
from config import settings
from database import SessionLocal
from exceptions import BusinessError
from responses import api_response
from seed import init_seed_data
from services.core import sync_low_stock_alerts
from services.production_service import repair_material_request_states
from services.realtime import realtime_manager


def run_database_migrations():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    alembic_ini_path = os.path.join(project_dir, 'alembic.ini')
    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option('script_location', os.path.join(project_dir, 'alembic'))
    command.upgrade(alembic_cfg, 'heads')


@asynccontextmanager
async def lifespan(app: FastAPI):
    #realtime_manager.bind_loop(asyncio.get_running_loop())
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.run_migrations_on_startup:
        run_database_migrations()
    db = SessionLocal()
    try:
        init_seed_data(db, settings.default_user_password)
        repair_material_request_states(db)
        sync_low_stock_alerts(db)
        db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(api_router, prefix=settings.api_prefix)
app.mount('/uploads', StaticFiles(directory=settings.upload_dir), name='uploads')


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(status_code=exc.code, content=api_response(code=exc.code, msg=exc.message))


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, list):
        message = "；".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
    else:
        message = str(detail or "请求失败")
    return JSONResponse(status_code=exc.status_code, content=api_response(code=exc.status_code, msg=message))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    messages = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", []) if item != "body")
        msg = error.get("msg", "参数错误")
        messages.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(status_code=422, content=api_response(code=422, msg="；".join(messages) or "请求参数错误"))


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=api_response(code=500, msg=str(exc)))


@app.get('/')
async def root():
    return api_response(
        data={
            'name': settings.app_name,
            'api_prefix': settings.api_prefix,
            'message': '系统已启动。',
            'startup_migrations_enabled': settings.run_migrations_on_startup,
        }
    )


@app.get('/health')
async def health_check():
    return api_response(msg='ok', data={'status': 'healthy'})


if __name__ == '__main__':
    uvicorn.run('main:app', host=settings.host, port=settings.port, reload=False)
