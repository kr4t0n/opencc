from __future__ import annotations

import logging
import os
import re
import tempfile

import aiohttp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from opencc.adapters.base import IMAdapter, Message, MessageHandler, ProgressTask

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

    def _prepare_message(self, text: str) -> tuple[str, dict | None]:
        """Extract the first markdown table as a Slack table block.

        Returns (remaining_text, table_block_or_None).
        """
        table_match = re.search(r"(?:^\|.+\|$\n?){2,}", text, flags=re.MULTILINE)

        if table_match:
            table_block = _markdown_table_to_slack_block(table_match.group(0))
            if table_block:
                before = text[: table_match.start()].rstrip("\n")
                after = text[table_match.end() :].lstrip("\n")
                text = before + ("\n\n" if before and after else "") + after
                return text, table_block

        return text, None

    async def send_message(self, channel_id: str, thread_id: str, text: str) -> None:
        text, table_block = self._prepare_message(text)
        chunks = _split_message(text)
        for i, chunk in enumerate(chunks):
            # Attach the table block to the last chunk.
            tb = table_block if i == len(chunks) - 1 else None
            await self._app.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_id,
                text=chunk,
                blocks=_build_blocks(chunk, tb),
            )

    async def post_message(self, channel_id: str, thread_id: str, text: str) -> str:
        text, table_block = self._prepare_message(text)
        truncated = self.truncate(text)
        resp = await self._app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_id,
            text=truncated,
            blocks=_build_blocks(truncated, table_block),
        )
        return resp["ts"]

    async def update_message(self, channel_id: str, thread_id: str, message_id: str, text: str) -> None:
        text, table_block = self._prepare_message(text)
        truncated = self.truncate(text)
        await self._app.client.chat_update(
            channel=channel_id,
            ts=message_id,
            text=truncated,
            blocks=_build_blocks(truncated, table_block),
        )

    async def post_progress(self, channel_id: str, thread_id: str, title: str, tasks: list[ProgressTask]) -> str:
        resp = await self._app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_id,
            text=title,
            blocks=[_build_plan_block(title, tasks)],
        )
        return resp["ts"]

    async def update_progress(
        self,
        channel_id: str,
        thread_id: str,
        message_id: str,
        title: str,
        tasks: list[ProgressTask],
        result_text: str | None = None,
    ) -> None:
        blocks: list[dict] = [_build_plan_block(title, tasks)]
        fallback = title
        if result_text:
            text, table_block = self._prepare_message(result_text)
            blocks.extend(_build_blocks(text, table_block))
            fallback = self.truncate(text)
        await self._app.client.chat_update(
            channel=channel_id,
            ts=message_id,
            text=fallback,
            blocks=blocks,
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


def _build_blocks(text: str, table_block: dict | None = None) -> list[dict]:
    """Wrap text in a Slack markdown block, optionally appending a table block."""
    blocks: list[dict] = [{"type": "markdown", "text": text}]
    if table_block is not None:
        blocks.append(table_block)
    return blocks


def _rich_text_block(text: str) -> dict:
    """Build a minimal rich_text block for use inside plan task fields."""
    return {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [{"type": "text", "text": text}],
            }
        ],
    }


def _build_plan_block(title: str, tasks: list[ProgressTask]) -> dict:
    """Build a Slack plan block from a list of ProgressTask items."""
    plan_tasks: list[dict] = []
    for task in tasks:
        pt: dict = {
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status,
        }
        if task.output:
            pt["output"] = _rich_text_block(task.output)
        plan_tasks.append(pt)
    return {
        "type": "plan",
        "title": title,
        "tasks": plan_tasks,
    }


def _parse_table_rows(table_text: str) -> tuple[list[str], list[list[str]]] | None:
    """Parse a Markdown table into (headers, data_rows).  Returns *None* on failure."""
    lines = [line.strip() for line in table_text.strip().splitlines()]
    if len(lines) < 2:
        return None

    headers = [c.strip() for c in lines[0].strip("|").split("|")]

    data_rows: list[list[str]] = []
    for line in lines[1:]:
        inner = line.strip("| ")
        if inner and all(c in "-: |" for c in inner):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        data_rows.append(cells)

    if not data_rows:
        return None
    return headers, data_rows


def _clean_cell(text: str) -> str:
    """Strip Markdown inline formatting so the text is clean for raw_text cells."""
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def _markdown_table_to_slack_block(table_text: str) -> dict | None:
    """Convert a Markdown table to a Slack ``table`` block for use in ``blocks``."""
    parsed = _parse_table_rows(table_text)
    if parsed is None:
        return None

    headers, data_rows = parsed
    num_cols = len(headers)

    rows: list[list[dict]] = []
    # Header row
    rows.append([{"type": "raw_text", "text": _clean_cell(h)} for h in headers])
    # Data rows
    for row in data_rows:
        slack_row = []
        for i in range(num_cols):
            cell_text = _clean_cell(row[i]) if i < len(row) else ""
            slack_row.append({"type": "raw_text", "text": cell_text})
        rows.append(slack_row)

    return {
        "type": "table",
        "column_settings": [{"is_wrapped": True} for _ in range(num_cols)],
        "rows": rows,
    }


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
