from __future__ import annotations

import logging

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

        try:
            response = await self.claude_manager.send(session_key, message.text)
        except RuntimeError:
            logger.exception("claude session error for %s", session_key)
            response = "Sorry, something went wrong talking to Claude Code."

        return response
