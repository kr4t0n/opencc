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


@dataclass
class ProgressTask:
    """A single task within a progress indicator (e.g. a tool invocation)."""

    task_id: str
    title: str
    status: str  # "pending", "in_progress", "complete"
    output: str = ""


MessageHandler = Callable[[Message], Awaitable[str | None]]

_TRUNCATION_PREFIX = "_(earlier messages truncated)_\n…\n"


def truncate_message(text: str, limit: int) -> str:
    """Truncate text from the beginning so the most recent content stays visible."""
    if len(text) <= limit:
        return text
    cut = len(text) - (limit - len(_TRUNCATION_PREFIX))
    # Try to cut at the first newline after the cut point for a clean break.
    split_at = text.find("\n", cut)
    if split_at == -1:
        split_at = cut
    return _TRUNCATION_PREFIX + text[split_at:].lstrip("\n")


class IMAdapter(ABC):
    """Base class for IM platform adapters.

    Subclasses should set ``max_message_length`` to the platform's
    character limit.  The ``truncate`` helper uses this value to trim
    oversized text from the beginning, keeping the most recent content
    visible.
    """

    name: str
    max_message_length: int = 4000

    def truncate(self, text: str) -> str:
        """Truncate *text* to ``max_message_length``, cutting from the beginning."""
        return truncate_message(text, self.max_message_length)

    def format_message(self, text: str) -> str:
        """Convert *text* to the platform's native markup format.

        The default implementation returns *text* unchanged.  Subclasses
        should override this to translate standard Markdown into the IM
        platform's own formatting (e.g. Slack mrkdwn).
        """
        return text

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

    @abstractmethod
    async def post_progress(self, channel_id: str, thread_id: str, title: str, tasks: list[ProgressTask]) -> str:
        """Post a progress indicator and return a platform-specific message ID."""

    @abstractmethod
    async def update_progress(
        self,
        channel_id: str,
        thread_id: str,
        message_id: str,
        title: str,
        tasks: list[ProgressTask],
        result_text: str | None = None,
    ) -> None:
        """Update a progress indicator, optionally appending a final result."""
