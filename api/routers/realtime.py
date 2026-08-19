from sqlalchemy import select

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import settings
from database import SessionLocal
from models import UserSession
from responses import api_response
from security import decode_token
from services.realtime import realtime_manager


router = APIRouter(prefix="/realtime", tags=["实时"])


def _is_active_session(token: str) -> bool:
    db = SessionLocal()
    try:
        session = db.execute(
            select(UserSession).where(
                UserSession.session_token == token,
                UserSession.is_active.is_(True),
            )
        ).scalar_one_or_none()
        return session is not None
    finally:
        db.close()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token") or websocket.cookies.get(settings.access_cookie_name)
    if not token:
        await websocket.close(code=4401)
        return

    try:
        payload = decode_token(token)
    except ValueError:
        await websocket.close(code=4401)
        return

    if not _is_active_session(token):
        await websocket.close(code=4401)
        return

    user_id = int(payload["sub"])
    await realtime_manager.connect(user_id, websocket)
    await websocket.send_json(api_response(data={"type": "connected", "user_id": user_id}))

    try:
        while True:
            message = await websocket.receive_text()
            if not _is_active_session(token):
                realtime_manager.disconnect(user_id, websocket)
                await websocket.close(code=4401)
                break
            if message == "ping":
                await websocket.send_json(api_response(data={"type": "pong", "user_id": user_id}))
    except WebSocketDisconnect:
        realtime_manager.disconnect(user_id, websocket)
    except Exception:
        realtime_manager.disconnect(user_id, websocket)
