from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class Message:
    adapter_name: str
    channel_id: str
    thread_id: str
    user_id: str
    text: str
    raw: dict = field(default_factory=dict, repr=False)
    images: list[str] = field(default_factory=list)


MessageHandler = Callable[[Message], Awaitable[str]]


class IMAdapter(ABC):
    """Base class for IM platform adapters."""

    name: str

    @abstractmethod
    async def start(self, handler: MessageHandler) -> None:
        """Start receiving messages and route them through *handler*.

        *handler* receives a ``Message`` and must return a reply string.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the adapter."""

    @abstractmethod
    async def send_message(self, channel_id: str, thread_id: str, text: str) -> None:
        """Send a message back to the IM platform."""
