from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class Message:
    adapter_name: str
    channel_id: str
    thread_id: str
    user_id: str
    text: str
    raw: dict = field(default_factory=dict, repr=False)
    images: list[str] = field(default_factory=list)


MessageHandler = Callable[[Message], Awaitable[str | None]]


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

    @abstractmethod
    async def post_message(self, channel_id: str, thread_id: str, text: str) -> str:
        """Post a message and return a platform-specific message ID (e.g. Slack ts).

        Unlike ``send_message``, callers can use the returned ID with
        ``update_message`` to edit the message in-place.
        """

    @abstractmethod
    async def update_message(self, channel_id: str, thread_id: str, message_id: str, text: str) -> None:
        """Edit an existing message identified by *message_id*."""
