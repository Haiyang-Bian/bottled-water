"""
应用层事件总线

作为 agent_runtime EventDispatcher 的 app 层桥接层。
运行时事件通过 EventDispatcher 分发给多个 Sink（Redis、数据库、SSE 等），
由 Run Kernel 统一管理生命周期。

设计原则：
- EventDispatcher 是运行时唯一的事件总线
- 各 Sink 由 app 层注入 RuntimeEngine
- 多订阅者模式：同一事件可同时推给 SSE、Redis Stream、数据库等
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from fastapi import WebSocket

from agent_runtime.core.interfaces import EventSink
from agent_runtime.core.types import Event as RuntimeEvent

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------
# App 层 Event 数据结构（业务层事件，不依赖运行时）
# --------------------------------------------------------------------


class AppEvent:
    """业务层事件"""

    def __init__(self, event: str, data: dict[str, Any]):
        self.event = event
        self.data = data
        self.timestamp = _timestamp()

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.event, "data": self.data, "timestamp": self.timestamp}

    def as_sse(self) -> dict[str, Any]:
        return {"event": self.event, "data": json.dumps(self.data, ensure_ascii=False)}


# --------------------------------------------------------------------
# App 层事件总线（订阅者管理 + Redis 集成）
# --------------------------------------------------------------------


class AppEventBus:
    """
    应用层事件总线

    维护多个订阅者（asyncio.Queue），支持 Redis 跨进程。
    运行时事件通过 EventDispatcher 直接分发，
    业务层事件通过 publish() 注入。
    """

    def __init__(self):
        self._queues: dict[str, set[asyncio.Queue[AppEvent]]] = defaultdict(set)
        self._redis: redis.Redis | None = None
        self._redis_checked = False

    async def _get_redis(self) -> redis.Redis | None:
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            client = redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=0.25,
                socket_timeout=0.5,
            )
            await client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    async def publish(self, channel: str, event: str, data: dict[str, Any]) -> None:
        """发布业务层事件"""
        app_event = AppEvent(event=event, data=data)
        for queue in list(self._queues[channel]):
            queue.put_nowait(app_event)

        client = await self._get_redis()
        if client is not None:
            encoded = json.dumps(app_event.as_dict(), ensure_ascii=False)
            await client.publish(channel, encoded)
            await client.xadd(
                f"stream:{channel}",
                {"payload": encoded},
                maxlen=500,
                approximate=True,
            )

    async def subscribe(self, channel: str, replay: bool = True) -> AsyncIterator[AppEvent]:
        """订阅事件流"""
        queue: asyncio.Queue[AppEvent] = asyncio.Queue(maxsize=500)
        self._queues[channel].add(queue)
        try:
            async for event in self._queue_iter(queue):
                yield event
        finally:
            self._queues[channel].discard(queue)

    async def _queue_iter(self, queue: asyncio.Queue[AppEvent]) -> AsyncIterator[AppEvent]:
        while True:
            event = await queue.get()
            yield event


# 全局单例
app_event_bus = AppEventBus()


# --------------------------------------------------------------------
# SSE Sink：将运行时事件转为 SSE 推送给前端
# --------------------------------------------------------------------


class SseSink(EventSink):
    """
    将运行时事件通过 SSE 推送给前端。

    每个会话维护一个 asyncio.Queue，API 层从中读取并推送给客户端。
    """

    _queues: dict[str, asyncio.Queue[RuntimeEvent]] = {}

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self._queues[conversation_id] = asyncio.Queue()

    async def emit(self, event: RuntimeEvent) -> None:
        queue = self._queues.get(self.conversation_id)
        if queue:
            await queue.put(event)
        else:
            logger.warning(f"SSE 队列不存在 conversation_id={self.conversation_id}")

    async def emit_batch(self, events: list[RuntimeEvent]) -> None:
        for event in events:
            await self.emit(event)

    def get_queue(self) -> asyncio.Queue[RuntimeEvent]:
        return self._queues[self.conversation_id]

    @classmethod
    def get_queue_for(cls, conversation_id: str) -> asyncio.Queue[RuntimeEvent] | None:
        return cls._queues.get(conversation_id)


# --------------------------------------------------------------------
# WebSocket Sink：将运行时事件推送到 WebSocket 连接
# --------------------------------------------------------------------

class WebSocketSink(EventSink):
    """将运行时事件推送到 WebSocket 连接。

    支持多客户端同时连接同一 conversation（Web + 移动端同时在线）。
    客户端断开连接后自动清理，不影响 Run 继续执行。
    """

    _connections: dict[str, list[WebSocket]] = {}

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.dedupe_key = f"websocket:{conversation_id}"

    def register(self, websocket: WebSocket) -> None:
        """注册 WebSocket 连接。"""
        self._connections.setdefault(self.conversation_id, []).append(websocket)

    def unregister(self, websocket: WebSocket) -> None:
        """注销 WebSocket 连接。"""
        websockets = self._connections.get(self.conversation_id, [])
        if websocket in websockets:
            websockets.remove(websocket)

    @classmethod
    def register_for(cls, conversation_id: str, websocket: WebSocket) -> None:
        """类方法：为指定 conversation 注册连接（方便外部调用）。"""
        cls._connections.setdefault(conversation_id, []).append(websocket)

    @classmethod
    def unregister_for(cls, conversation_id: str, websocket: WebSocket) -> None:
        """类方法：为指定 conversation 注销连接。"""
        websockets = cls._connections.get(conversation_id, [])
        if websocket in websockets:
            websockets.remove(websocket)

    @classmethod
    def get_connections(cls, conversation_id: str) -> list[WebSocket]:
        """获取指定 conversation 的所有活跃连接。"""
        return cls._connections.get(conversation_id, [])

    async def emit(self, event: RuntimeEvent) -> None:
        """将事件推送到该 conversation 的所有 WebSocket 连接。"""
        websockets = self._connections.get(self.conversation_id, [])
        if not websockets:
            return

        payload = {
            "event": event.type,
            "data": event.payload,
        }
        if event.correlation_id:
            payload["request_id"] = event.correlation_id

        dead: list[WebSocket] = []
        for ws in websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            websockets.remove(ws)

        # 每发一个事件停 50ms，缓解前端渲染压力
        await asyncio.sleep(0)

    async def emit_batch(self, events: list[RuntimeEvent]) -> None:
        for event in events:
            await self.emit(event)


# --------------------------------------------------------------------
# Redis Sink：跨进程事件分发
# --------------------------------------------------------------------


class RedisSink(EventSink):
    """
    将运行时事件发布到 Redis pub/sub，实现跨进程事件分发。
    """

    def __init__(self, channel_prefix: str = "runtime"):
        self.channel_prefix = channel_prefix
        self._redis: redis.Redis | None = None
        self._checked = False

    async def _get_redis(self) -> redis.Redis | None:
        if self._checked:
            return self._redis
        self._checked = True
        try:
            client = redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=2,
            )
            await client.ping()
            self._redis = client
        except Exception as exc:
            logger.warning(f"Redis Sink 初始化失败，降级 error={str(exc)}")
            self._redis = None
        return self._redis

    async def emit(self, event: RuntimeEvent) -> None:
        client = await self._get_redis()
        if client is None:
            return
        channel = f"{self.channel_prefix}:{event.type}"
        payload = json.dumps(
            {
                "session_id": getattr(event, "session_id", ""),
                "type": event.type,
                "data": getattr(event, "data", {}),
            },
            ensure_ascii=False,
        )
        try:
            await client.publish(channel, payload)
        except Exception as exc:
            logger.warning(f"Redis emit 失败 error={str(exc)}")

    async def emit_batch(self, events: list[RuntimeEvent]) -> None:
        for event in events:
            await self.emit(event)
