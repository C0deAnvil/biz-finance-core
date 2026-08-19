from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket

from responses import api_response


class RealtimeManager:
    def __init__(self):
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        self._connections[user_id].discard(websocket)
        if not self._connections[user_id]:
            self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict):
        stale_connections = []
        for websocket in list(self._connections.get(user_id, set())):
            try:
                await websocket.send_json(api_response(data=payload))
            except Exception:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self.disconnect(user_id, websocket)

    def push_to_user(self, user_id: int, payload: dict):
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.send_to_user(user_id, payload), self._loop)


realtime_manager = RealtimeManager()
