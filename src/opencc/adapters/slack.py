from __future__ import annotations

import logging
import re
from typing import Optional

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from opencc.adapters.base import IMAdapter, Message, MessageHandler

logger = logging.getLogger(__name__)

SLACK_MAX_MESSAGE_LENGTH = 3000


class SlackAdapter(IMAdapter):
    """Slack adapter using Socket Mode (WebSocket, no public URL needed)."""

    name = "slack"

    def __init__(self, bot_token: str, app_token: str) -> None:
        self._app = AsyncApp(token=bot_token)
        self._app_token = app_token
        self._handler: Optional[AsyncSocketModeHandler] = None
        self._message_handler: Optional[MessageHandler] = None

    async def start(self, handler: MessageHandler) -> None:
        self._message_handler = handler
        self._register_listeners()
        self._handler = AsyncSocketModeHandler(self._app, self._app_token)
        await self._handler.connect_async()
        logger.info("Slack adapter started (Socket Mode)")

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
            logger.info("Slack adapter stopped")

    async def send_message(self, channel_id: str, thread_id: str, text: str) -> None:
        for chunk in _split_message(text):
            await self._app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_id,
                text=chunk,
            )

    def _register_listeners(self) -> None:
        @self._app.event("app_mention")
        async def on_mention(event: dict, say) -> None:  # noqa: ANN001
            await self._handle_event(event)

    async def _handle_event(self, event: dict) -> None:
        if self._message_handler is None:
            return

        text = _strip_mention(event.get("text", ""))
        if not text.strip():
            return

        thread_ts = event.get("thread_ts") or event.get("ts", "")
        channel = event.get("channel", "")

        msg = Message(
            adapter_name=self.name,
            channel_id=channel,
            thread_id=thread_ts,
            user_id=event.get("user", ""),
            text=text,
            raw=event,
        )

        response = await self._message_handler(msg)
        await self.send_message(channel, thread_ts, response)


_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


def _strip_mention(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _split_message(text: str, limit: int = SLACK_MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks
