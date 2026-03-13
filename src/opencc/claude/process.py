from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClaudeSession:
    """Tracks a single Claude Code conversation backed by the CLI."""

    session_key: str
    session_id: Optional[str] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)

    async def send(
        self, prompt: str, *, cli_path: str = "claude", work_dir: str = "."
    ) -> str:
        async with self._lock:
            return await self._run(prompt, cli_path=cli_path, work_dir=work_dir)

    def cancel(self) -> bool:
        """Terminate the running Claude process, if any. Returns True if killed."""
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            return True
        return False

    async def _run(self, prompt: str, *, cli_path: str, work_dir: str) -> str:
        cmd = [cli_path, "-p", "--output-format", "json", "--dangerously-skip-permissions"]
        if self.session_id is not None:
            cmd.extend(["--resume", self.session_id])
        cmd.append(prompt)

        logger.info("claude cmd: %s (cwd=%s)", " ".join(cmd), work_dir)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc
        try:
            stdout, stderr = await proc.communicate()
        finally:
            self._proc = None

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.error("claude exited %d: %s", proc.returncode, err)
            raise RuntimeError(f"Claude Code exited with code {proc.returncode}: {err}")

        raw = stdout.decode(errors="replace").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("failed to parse claude output: %s", raw[:500])
            raise RuntimeError("Could not parse Claude Code JSON output")

        if payload.get("is_error"):
            raise RuntimeError(f"Claude Code error: {payload.get('result', 'unknown')}")

        if sid := payload.get("session_id"):
            self.session_id = sid

        return payload.get("result", "")


class ClaudeProcessManager:
    """Manages per-key Claude Code sessions."""

    def __init__(self, cli_path: str = "claude", work_dir: str = ".") -> None:
        self.cli_path = cli_path
        self.work_dir = work_dir
        self._sessions: dict[str, ClaudeSession] = {}

    async def send(self, session_key: str, prompt: str) -> str:
        session = self._sessions.get(session_key)
        if session is None:
            session = ClaudeSession(session_key=session_key)
            self._sessions[session_key] = session
        return await session.send(
            prompt, cli_path=self.cli_path, work_dir=self.work_dir
        )

    def cancel(self, session_key: str) -> bool:
        """Cancel the running process for *session_key*. Returns True if killed."""
        session = self._sessions.get(session_key)
        if session is None:
            return False
        return session.cancel()

    def list_sessions(self) -> list[dict]:
        return [
            {
                "session_key": s.session_key,
                "session_id": s.session_id,
            }
            for s in self._sessions.values()
        ]

    async def cleanup(self) -> None:
        self._sessions.clear()
