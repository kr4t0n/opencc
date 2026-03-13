from __future__ import annotations

import logging
import os

from opencc.adapters.base import Message
from opencc.claude.process import ClaudeProcessManager

logger = logging.getLogger(__name__)


class GatewayRouter:
    """Routes incoming IM messages to Claude Code sessions."""

    def __init__(self, claude_manager: ClaudeProcessManager) -> None:
        self.claude_manager = claude_manager

    async def handle(self, message: Message) -> str:
        session_key = f"{message.adapter_name}:{message.channel_id}:{message.thread_id}"
        logger.info(
            "routing message from user=%s session_key=%s",
            message.user_id,
            session_key,
        )

        prompt = _build_prompt(message.text, message.images)

        try:
            response = await self.claude_manager.send(session_key, prompt)
        except RuntimeError as exc:
            logger.exception("claude session error for %s", session_key)
            response = f"Sorry, something went wrong talking to Claude Code.\n```\n{exc}\n```"
        finally:
            _cleanup_images(message.images)

        return response


def _escape_slash_command(text: str) -> str:
    """Prevent bare slash-commands from being interpreted as CLI skills.

    Claude Code's ``-p`` mode treats a leading ``/`` as a skill invocation
    (e.g. ``/help`` → skill "help").  When the text originates from a chat
    adapter it should always be handled as a plain user message, so we
    strip the leading ``/``.
    """
    if text.startswith("/"):
        return text.lstrip("/")
    return text


def _build_prompt(text: str, images: list[str]) -> str:
    """Prepend image-read instructions to the user's text when images are attached."""
    text = _escape_slash_command(text)
    if not images:
        return text

    parts: list[str] = []
    parts.append(
        "The user attached the following image(s). "
        "Use your Read tool to view each file before responding:\n"
    )
    for path in images:
        parts.append(f"  - {path}")
    parts.append("")  # blank line separator

    if text.strip():
        parts.append(text)

    return "\n".join(parts)


def _cleanup_images(images: list[str]) -> None:
    """Remove temporary image files."""
    for path in images:
        try:
            os.remove(path)
        except OSError:
            logger.debug("Could not remove temp image %s", path)
