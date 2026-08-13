"""
事件分发器

负责将事件并发投递到多个 Sink：
- SSE → 前端实时流
- Logger → 日志系统
- Persistence → 持久化存储
- 支持自定义 Sink 扩展

职责上提：由 Run Kernel 统一管理，执行器只负责产生事件。

事件分类（按 type 前缀）：
- control.*  → 控制事件（调度决策、看门狗等，系统内部消费）
- agent.*    → Agent 观测事件（thinking、token、tool_call 等，前端可展示）
- user.*     → 用户相关事件（用户输入、等待输入等）
- system.*   → 系统级事件（run 生命周期、round 生命周期等）
"""

import asyncio
import fnmatch
import uuid
from dataclasses import dataclass
from typing import Awaitable, List, Callable, Union

from common.logger import get_logger
from ..core.types import Event
from ..core.interfaces import EventSink

logger = get_logger(__name__)


EventFilter = Union[str, Callable[[Event], bool], None]
EventCallback = Callable[[Event], Awaitable[None]]


@dataclass
class Subscription:
    id: str
    event_filter: EventFilter
    callback: EventCallback
    target: str | None = None


class _SinkEntry:
    """带过滤条件的 Sink 注册项"""

    def __init__(self, sink: EventSink, event_filter: EventFilter = None):
        self.sink = sink
        self.event_filter = event_filter

    def matches(self, event: Event) -> bool:
        """判断事件是否匹配本 Sink 的过滤条件"""
        if self.event_filter is None:
            return True
        if callable(self.event_filter):
            return self.event_filter(event)
        if isinstance(self.event_filter, str):
            return fnmatch.fnmatch(event.type, self.event_filter)
        return True


class EventDispatcher:
    """事件分发器 - 多 Sink 并发消费，支持按事件类型过滤"""

    def __init__(self):
        self._entries: List[_SinkEntry] = []
        self._subscriptions: dict[str, Subscription] = {}

    def register_sink(self, sink: EventSink, event_filter: EventFilter = None) -> "EventDispatcher":
        """注册事件 Sink（链式调用）

        Args:
            sink: 事件接收器
            event_filter: 事件过滤条件
                - None: 接收所有事件
                - str: 通配符模式，如 "agent.*"、"system.*"
                - Callable[[Event], bool]: 自定义过滤函数
        """
        dedupe_key = getattr(sink, "dedupe_key", None)
        if dedupe_key:
            self._entries = [
                entry
                for entry in self._entries
                if getattr(entry.sink, "dedupe_key", None) != dedupe_key
            ]
        self._entries.append(_SinkEntry(sink, event_filter))
        return self

    def unregister_sink(self, sink: EventSink) -> None:
        """注销事件 Sink"""
        self._entries = [e for e in self._entries if e.sink is not sink]

    def subscribe(
        self,
        event_filter: EventFilter,
        callback: EventCallback,
        *,
        target: str | None = None,
    ) -> str:
        subscription_id = uuid.uuid4().hex
        self._subscriptions[subscription_id] = Subscription(
            id=subscription_id,
            event_filter=event_filter,
            callback=callback,
            target=target,
        )
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        self._subscriptions.pop(subscription_id, None)

    async def publish(self, event: Event) -> None:
        callbacks = [
            subscription.callback(event)
            for subscription in list(self._subscriptions.values())
            if self._subscription_matches(subscription, event)
        ]
        if callbacks:
            results = await asyncio.gather(*callbacks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("EventBus subscriber failed", error=str(result))
        await self.dispatch(event)

    async def dispatch(self, event: Event) -> None:
        """将事件分发给所有匹配的 Sink"""
        if not self._entries:
            return

        for entry in self._entries:
            if not entry.matches(event):
                continue
            try:
                await entry.sink.emit(event)
            except Exception as e:
                logger.warning(
                    "事件 Sink 投递失败", sink_type=type(entry.sink).__name__, error=str(e)
                )

    async def dispatch_batch(self, events: List[Event]) -> None:
        """批量分发事件"""
        for event in events:
            await self.dispatch(event)

    @staticmethod
    def _filter_matches(event_filter: EventFilter, event: Event) -> bool:
        if event_filter is None:
            return True
        if callable(event_filter):
            return event_filter(event)
        if isinstance(event_filter, str):
            return fnmatch.fnmatch(event.type, event_filter)
        return True

    @classmethod
    def _subscription_matches(cls, subscription: Subscription, event: Event) -> bool:
        if not cls._filter_matches(subscription.event_filter, event):
            return False
        if subscription.target == "*":
            return True
        if event.target:
            return subscription.target == event.target
        return subscription.target is None
