from __future__ import annotations

import logging
import os
import re
import tempfile

import aiohttp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from opencc.adapters.base import IMAdapter, Message, MessageHandler

logger = logging.getLogger(__name__)

_IMAGE_MIMETYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)

SLACK_MAX_MESSAGE_LENGTH = 3000


class SlackAdapter(IMAdapter):
    """Slack adapter using Socket Mode (WebSocket, no public URL needed)."""

    name = "slack"
    max_message_length = SLACK_MAX_MESSAGE_LENGTH

    def __init__(self, bot_token: str, app_token: str) -> None:
        self._app = AsyncApp(token=bot_token)
        self._bot_token = bot_token
        self._app_token = app_token
        self._handler: AsyncSocketModeHandler | None = None
        self._message_handler: MessageHandler | None = None

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

    async def post_message(self, channel_id: str, thread_id: str, text: str) -> str:
        resp = await self._app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_id,
            text=self.truncate(text),
        )
        return resp["ts"]

    async def update_message(self, channel_id: str, thread_id: str, message_id: str, text: str) -> None:
        await self._app.client.chat_update(
            channel=channel_id,
            ts=message_id,
            text=self.truncate(text),
        )

    def _register_listeners(self) -> None:
        @self._app.event("app_mention")
        async def on_mention(event: dict, say) -> None:  # noqa: ANN001
            await self._handle_event(event)

    async def _handle_event(self, event: dict) -> None:
        if self._message_handler is None:
            return

        text = _strip_mention(event.get("text", ""))

        # Download any attached images.
        image_paths = await self._download_images(event.get("files", []))

        if not text.strip() and not image_paths:
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
            images=image_paths,
        )

        response = await self._message_handler(msg)
        if response is not None:
            await self.send_message(channel, thread_ts, response)

    async def _download_images(self, files: list[dict]) -> list[str]:
        """Download image attachments from Slack and return local file paths."""
        if not files:
            return []

        image_files = [f for f in files if f.get("mimetype", "") in _IMAGE_MIMETYPES]
        if not image_files:
            return []

        paths: list[str] = []
        headers = {"Authorization": f"Bearer {self._bot_token}"}

        async with aiohttp.ClientSession(headers=headers) as session:
            for file_info in image_files:
                url = file_info.get("url_private_download") or file_info.get("url_private")
                if not url:
                    logger.warning("Slack file missing download URL: %s", file_info.get("id"))
                    continue

                try:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(
                                "Failed to download Slack file %s: HTTP %d",
                                file_info.get("id"),
                                resp.status,
                            )
                            continue
                        data = await resp.read()
                except Exception:
                    logger.exception("Error downloading Slack file %s", file_info.get("id"))
                    continue

                # Determine file extension from the original filename.
                name = file_info.get("name", "image")
                _, ext = os.path.splitext(name)
                if not ext:
                    ext = ".png"

                fd, path = tempfile.mkstemp(suffix=ext, prefix="opencc_img_")
                os.write(fd, data)
                os.close(fd)
                paths.append(path)
                logger.info("Downloaded Slack image %s → %s", file_info.get("id"), path)

        return paths


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
