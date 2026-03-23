from __future__ import annotations

import asyncio
import logging
import os
import uuid

from opencc.adapters.base import IMAdapter, Message, ProgressTask
from opencc.claude.process import ClaudeProcessManager

logger = logging.getLogger(__name__)

# Maximum length for a single Slack message before we split into a follow-up.
_MAX_MESSAGE_LEN = 3000

# Minimum interval (seconds) between Slack message updates during streaming.
_UPDATE_INTERVAL = 1.5


class GatewayRouter:
    """Routes incoming IM messages to Claude Code sessions."""

    def __init__(self, claude_manager: ClaudeProcessManager, adapter: IMAdapter) -> None:
        self.claude_manager = claude_manager
        self.adapter = adapter

    async def handle(self, message: Message) -> str | None:
        session_key = f"{message.adapter_name}:{message.channel_id}:{message.thread_id}"
        logger.info(
            "routing message from user=%s session_key=%s",
            message.user_id,
            session_key,
        )

        if message.text.startswith("/"):
            cmd = message.text.split()[0].lstrip("/").lower()
            if cmd == "ask":
                body = message.text[len(message.text.split()[0]) :].strip()
                if not body:
                    return "Usage: `/ask <message>` — send a quick message without session context."
                ephemeral_key = f"ask:{uuid.uuid4().hex[:8]}"
                prompt = _build_prompt(body, message.images)
                return await self._dispatch(
                    message,
                    ephemeral_key,
                    prompt,
                    stream=self.claude_manager.send_streaming,
                    batch=self.claude_manager.send,
                )
            elif cmd == "btw":
                body = message.text[len(message.text.split()[0]) :].strip()
                if not body:
                    return "Usage: `/btw <message>` — send a side message using a cloned session."
                prompt = _build_prompt(body, message.images)
                return await self._dispatch(
                    message,
                    session_key,
                    prompt,
                    stream=self.claude_manager.send_btw_streaming,
                    batch=self.claude_manager.send_btw,
                )
            else:
                return self._handle_command(message.text, session_key)

        prompt = _build_prompt(message.text, message.images)
        return await self._dispatch(
            message, session_key, prompt, stream=self.claude_manager.send_streaming, batch=self.claude_manager.send
        )

    # -- response dispatch ----------------------------------------------------

    async def _dispatch(
        self,
        message: Message,
        session_key: str,
        prompt: str,
        *,
        stream: ...,
        batch: ...,
    ) -> None:
        if self.claude_manager.streaming:
            return await self._stream_response(message, session_key, prompt, stream)
        return await self._batch_response(message, session_key, prompt, batch)

    # -- streaming response ---------------------------------------------------

    async def _stream_response(
        self,
        message: Message,
        session_key: str,
        prompt: str,
        stream_fn: ...,
    ) -> None:
        """Post a plan block, stream events, and update tasks in real-time.

        Returns ``None`` to signal the caller that the response was already
        sent directly to the adapter (no additional ``send_message`` needed).
        """
        channel = message.channel_id
        thread = message.thread_id

        msg_ts = await self.adapter.post_progress(channel, thread, _TITLE_WORKING, [])

        tasks: list[ProgressTask] = []
        result_text = ""
        last_update = 0.0

        try:
            async for event in stream_fn(session_key, prompt):
                now = asyncio.get_event_loop().time()
                etype = event.get("type")

                if etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use":
                            # Mark the previous in-progress task as complete.
                            for t in tasks:
                                if t.status == "in_progress":
                                    t.status = "complete"

                            title, detail = _summarize_tool(block.get("name", "unknown"), block.get("input", {}))
                            tasks.append(
                                ProgressTask(
                                    task_id=f"tool_{len(tasks)}",
                                    title=title,
                                    status="in_progress",
                                    details=detail,
                                )
                            )

                            if now - last_update >= _UPDATE_INTERVAL:
                                await self.adapter.update_progress(channel, thread, msg_ts, _TITLE_WORKING, tasks)
                                last_update = now

                elif etype == "result":
                    if event.get("is_error"):
                        result_text = f"⚠️ Error: {event.get('result', 'unknown error')}"
                    else:
                        result_text = event.get("result", "")

            # Mark all remaining tasks as complete.
            for t in tasks:
                if t.status != "complete":
                    t.status = "complete"

            await self._send_final(channel, thread, msg_ts, tasks, result_text)

        except Exception as exc:
            logger.exception("streaming error for %s", session_key)
            for t in tasks:
                if t.status != "complete":
                    t.status = "complete"
            error_text = f"Sorry, something went wrong.\n```\n{exc}\n```"
            await self.adapter.update_progress(channel, thread, msg_ts, "Error", tasks, error_text)

        finally:
            _cleanup_images(message.images)

        return None

    async def _send_final(
        self,
        channel: str,
        thread: str,
        msg_ts: str,
        tasks: list[ProgressTask],
        result_text: str,
    ) -> None:
        """Update the plan block with completed tasks and the final result.

        If the result fits alongside the plan block, include it in the same
        message.  Otherwise post the result as a follow-up.
        """
        n = len(tasks)
        title = f"Done ({n} tool{'s' if n != 1 else ''} used)" if tasks else "Done"

        if result_text and len(result_text) > _MAX_MESSAGE_LEN:
            await self.adapter.update_progress(channel, thread, msg_ts, title, tasks)
            await self.adapter.send_message(channel, thread, result_text)
        else:
            await self.adapter.update_progress(channel, thread, msg_ts, title, tasks, result_text or None)

    # -- batch response -------------------------------------------------------

    async def _batch_response(
        self,
        message: Message,
        session_key: str,
        prompt: str,
        send_fn: ...,
    ) -> None:
        """Send prompt in batch (json) mode and post the result when done."""
        channel = message.channel_id
        thread = message.thread_id

        msg_ts = await self.adapter.post_message(channel, thread, _STATUS_WORKING)

        try:
            result = await send_fn(session_key, prompt)

            if not result:
                await self.adapter.update_message(channel, thread, msg_ts, "✅ _Done (no response)_")
            elif len(result) <= _MAX_MESSAGE_LEN:
                await self.adapter.update_message(channel, thread, msg_ts, result)
            else:
                await self.adapter.update_message(channel, thread, msg_ts, "✅ _Done_")
                await self.adapter.send_message(channel, thread, result)

        except Exception as exc:
            logger.exception("batch error for %s", session_key)
            error_text = f"Sorry, something went wrong.\n```\n{exc}\n```"
            await self.adapter.update_message(channel, thread, msg_ts, error_text)

        finally:
            _cleanup_images(message.images)

        return None

    # -- slash commands --------------------------------------------------------

    def _handle_command(self, text: str, session_key: str) -> str:
        """Dispatch gateway slash commands."""
        cmd = text.split()[0].lstrip("/").lower()

        handler = self._commands.get(cmd)
        if handler is None:
            return f"Unknown command: `/{cmd}`. Type `/help` for available commands."
        return handler(self, session_key)

    def _cmd_help(self, session_key: str) -> str:
        return (
            "*Available commands:*\n"
            "• `/help` — Show this message\n"
            "• `/stop` — Cancel the currently running Claude response\n"
            "• `/sessions` — List all active Claude Code sessions\n"
            "• `/ask <message>` — Quick context-free reply (no session history)\n"
            "• `/btw <message>` — Side message in a cloned session (preserves original)"
        )

    def _cmd_stop(self, session_key: str) -> str:
        if self.claude_manager.cancel(session_key):
            return "Cancelled the running Claude process."
        return "No active Claude process to stop."

    def _cmd_sessions(self, session_key: str) -> str:
        sessions = self.claude_manager.list_sessions()
        if not sessions:
            return "No active sessions."
        lines = ["*Active sessions:*"]
        for s in sessions:
            sid = s["session_id"] or "(pending)"
            lines.append(f"• `{s['session_key']}` — session_id: `{sid}`")
        return "\n".join(lines)

    _commands: dict[str, callable] = {
        "help": _cmd_help,
        "stop": _cmd_stop,
        "sessions": _cmd_sessions,
    }


# -- helpers ------------------------------------------------------------------

_STATUS_WORKING = "⏳ _Working..._"
_TITLE_WORKING = "Working…"


def _summarize_tool(name: str, tool_input: dict) -> tuple[str, str]:
    """Return (title, detail) for a tool_use event.

    *title* is the tool name; *detail* holds the key parameter(s) and is
    rendered inside the plan task's ``details`` rich-text field.
    """
    detail = ""
    if name in ("Read", "read", "Edit", "edit", "Write", "write"):
        detail = tool_input.get("file_path", "")
    elif name in ("Bash", "bash"):
        cmd = tool_input.get("command", "")
        detail = cmd[:80] + ("…" if len(cmd) > 80 else "")
    elif name in ("Grep", "grep", "Glob", "glob"):
        detail = tool_input.get("pattern", "")
    elif name in ("Agent", "agent"):
        detail = tool_input.get("description", "") or tool_input.get("prompt", "")[:60]
    elif name in ("WebFetch", "web_fetch"):
        detail = tool_input.get("url", "")
    elif name in ("WebSearch", "web_search", "ToolSearch", "tool_search"):
        detail = tool_input.get("query", "")
    elif name in ("TodoWrite", "todo_write"):
        todos = tool_input.get("todos", [])
        detail = f"{len(todos)} item{'s' if len(todos) != 1 else ''}"

    return name, detail


def _build_prompt(text: str, images: list[str]) -> str:
    """Prepend image-read instructions to the user's text when images are attached."""
    if not images:
        return text

    parts: list[str] = []
    parts.append("The user attached the following image(s). Use your Read tool to view each file before responding:\n")
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
