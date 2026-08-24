import os
import asyncio
import logging
import time
import uuid
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
from services.request_trace import emit, request_id_var, operation_id_var


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


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


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"
    operation_id = request.headers.get("X-Operation-ID") or request_id
    request_token = request_id_var.set(request_id)
    operation_token = operation_id_var.set(operation_id)
    started_at = time.perf_counter()
    emit(
        "request_started",
        method=request.method,
        path=request.url.path,
        query=str(request.url.query)[:500],
    )
    try:
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        emit(
            "request_finished",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query)[:500],
            status_code=response.status_code,
            duration_ms=elapsed_ms,
        )
        if elapsed_ms >= settings.slow_request_ms:
            emit(
                "slow_request",
                method=request.method,
                path=request.url.path,
                query=str(request.url.query)[:500],
                status_code=response.status_code,
                duration_ms=elapsed_ms,
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Operation-ID"] = operation_id
        return response
    except Exception:
        emit(
            "request_error",
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        raise
    finally:
        request_id_var.reset(request_token)
        operation_id_var.reset(operation_token)
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
    response_data = None
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("msg") or detail.get("detail") or "请求失败")
        response_data = {key: value for key, value in detail.items() if key not in {"message", "msg", "detail"}}
    elif isinstance(detail, list):
        message = "；".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
    else:
        message = str(detail or "请求失败")
    return JSONResponse(status_code=exc.status_code, content=api_response(code=exc.status_code, msg=message, data=response_data))


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
