"""Agent inbox/outbox primitives for the async runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from ..core.types import Event
from .event_dispatcher import EventDispatcher


class Mailbox:
    """Per-agent inbox backed by ``asyncio.Queue``."""

    def __init__(self, owner_id: str, *, maxsize: int = 100) -> None:
        self.owner_id = owner_id
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._subscriptions: list[tuple[EventDispatcher, str]] = []

    def bind(
        self,
        event_bus: EventDispatcher,
        *,
        event_filter: str | None = "control.*",
        target: str | None = None,
    ) -> None:
        """Subscribe this mailbox to targeted events on an EventDispatcher."""

        subscription_id = event_bus.subscribe(
            event_filter,
            self.send,
            target=target if target is not None else self.owner_id,
        )
        self._subscriptions.append((event_bus, subscription_id))

    async def send(self, event: Event) -> None:
        await self._queue.put(event)

    async def recv(self, timeout: float | None = None) -> Event:
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def get_nowait(self) -> Event | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def drain(self) -> list[Event]:
        events: list[Event] = []
        while True:
            event = self.get_nowait()
            if event is None:
                break
            events.append(event)
        return events

    def close(self) -> None:
        for event_bus, subscription_id in self._subscriptions:
            event_bus.unsubscribe(subscription_id)
        self._subscriptions.clear()


async def send_many(mailboxes: Iterable[Mailbox], event: Event) -> None:
    await asyncio.gather(*(mailbox.send(event) for mailbox in mailboxes))
