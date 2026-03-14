from __future__ import annotations

import asyncio
import json
import logging
import shlex
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def clone_session(work_dir: str, old_session_id: str, new_session_id: str) -> Path:
    """Clone a Claude Code session by duplicating its JSONL file.

    The working directory is normalised to match Claude Code's on-disk layout:
    ``/home/kyle/projects`` → ``-home-kyle-projects``.

    Returns the path to the newly created session file.
    """
    normalized = work_dir.replace("/", "-")
    projects_dir = Path.home() / ".claude" / "projects" / normalized

    src = projects_dir / f"{old_session_id}.jsonl"
    if not src.exists():
        raise FileNotFoundError(f"Source session file not found: {src}")

    dst = projects_dir / f"{new_session_id}.jsonl"
    shutil.copy2(src, dst)
    logger.info("cloned session %s → %s", src, dst)
    return dst


@dataclass
class ClaudeSession:
    """Tracks a single Claude Code conversation backed by the CLI."""

    session_key: str
    session_id: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _proc: asyncio.subprocess.Process | None = field(default=None, repr=False)

    async def send(
        self,
        prompt: str,
        *,
        cli_path: str = "claude",
        work_dir: str = ".",
        cli_args: list[str] | None = None,
        extra_args: list[str] | None = None,
    ) -> str:
        async with self._lock:
            return await self._run(
                prompt,
                cli_path=cli_path,
                work_dir=work_dir,
                cli_args=cli_args or [],
                extra_args=extra_args or [],
            )

    async def send_streaming(
        self,
        prompt: str,
        *,
        cli_path: str = "claude",
        work_dir: str = ".",
        cli_args: list[str] | None = None,
        extra_args: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        """Send a prompt and yield parsed stream-json events as they arrive."""
        await self._lock.acquire()
        try:
            async for event in self._run_streaming(
                prompt,
                cli_path=cli_path,
                work_dir=work_dir,
                cli_args=cli_args or [],
                extra_args=extra_args or [],
            ):
                yield event
        finally:
            self._lock.release()

    def cancel(self) -> bool:
        """Terminate the running Claude process, if any. Returns True if killed."""
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            return True
        return False

    async def _run(
        self, prompt: str, *, cli_path: str, work_dir: str, cli_args: list[str], extra_args: list[str]
    ) -> str:
        cmd = [cli_path, *cli_args]
        if extra_args:
            cmd.extend(extra_args)
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
            raise RuntimeError("Could not parse Claude Code JSON output") from None

        if payload.get("is_error"):
            raise RuntimeError(f"Claude Code error: {payload.get('result', 'unknown')}")

        if sid := payload.get("session_id"):
            self.session_id = sid

        return payload.get("result", "")

    async def _run_streaming(
        self, prompt: str, *, cli_path: str, work_dir: str, cli_args: list[str], extra_args: list[str]
    ) -> AsyncIterator[dict]:
        cmd = [cli_path, *cli_args]
        if extra_args:
            cmd.extend(extra_args)
        if self.session_id is not None:
            cmd.extend(["--resume", self.session_id])
        cmd.append(prompt)

        logger.info("claude stream cmd: %s (cwd=%s)", " ".join(cmd), work_dir)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc

        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("skipping non-JSON line: %s", text[:200])
                    continue

                if sid := event.get("session_id"):
                    self.session_id = sid

                yield event

            await proc.wait()

            if proc.returncode != 0:
                assert proc.stderr is not None
                stderr_data = await proc.stderr.read()
                err = stderr_data.decode(errors="replace").strip()
                raise RuntimeError(f"Claude Code exited with code {proc.returncode}: {err}")
        finally:
            self._proc = None


class ClaudeProcessManager:
    """Manages per-key Claude Code sessions."""

    def __init__(
        self,
        cli_path: str = "claude",
        work_dir: str = ".",
        cli_args: str = "-p",
        extra_args: str = "",
    ) -> None:
        self.cli_path = cli_path
        self.work_dir = work_dir
        self._cli_args: list[str] = shlex.split(cli_args)
        self._extra_args: list[str] = shlex.split(extra_args)
        self._sessions: dict[str, ClaudeSession] = {}

        # Detect output format from CLI args.
        # json → batch mode, stream-json → streaming mode.
        # If no --output-format is provided, default to stream-json.
        self.streaming: bool = True
        try:
            idx = self._cli_args.index("--output-format")
            fmt = self._cli_args[idx + 1]
            self.streaming = fmt != "json"
        except (ValueError, IndexError):
            self._cli_args.extend(["--output-format", "stream-json"])

    async def send(self, session_key: str, prompt: str) -> str:
        session = self._sessions.get(session_key)
        if session is None:
            session = ClaudeSession(session_key=session_key)
            self._sessions[session_key] = session
        return await session.send(
            prompt,
            cli_path=self.cli_path,
            work_dir=self.work_dir,
            cli_args=self._cli_args,
            extra_args=self._extra_args,
        )

    async def send_streaming(self, session_key: str, prompt: str) -> AsyncIterator[dict]:
        session = self._sessions.get(session_key)
        if session is None:
            session = ClaudeSession(session_key=session_key)
            self._sessions[session_key] = session
        async for event in session.send_streaming(
            prompt,
            cli_path=self.cli_path,
            work_dir=self.work_dir,
            cli_args=self._cli_args,
            extra_args=self._extra_args,
        ):
            yield event

    def cancel(self, session_key: str) -> bool:
        """Cancel the running process for *session_key*. Returns True if killed."""
        session = self._sessions.get(session_key)
        if session is None:
            return False
        return session.cancel()

    async def send_btw(self, session_key: str, prompt: str) -> str:
        """Send a message in a cloned copy of an existing session.

        Looks up the current session for *session_key*, clones its JSONL file
        to a new session id, and resumes the prompt against the clone so the
        original conversation history is preserved untouched.
        """
        session = self._sessions.get(session_key)
        if session is None or session.session_id is None:
            raise RuntimeError("No active session to branch from. Send a normal message first.")

        new_session_id = str(uuid.uuid4())
        clone_session(self.work_dir, session.session_id, new_session_id)

        btw_session = ClaudeSession(
            session_key=f"btw:{new_session_id[:8]}",
            session_id=new_session_id,
        )
        return await btw_session.send(
            prompt,
            cli_path=self.cli_path,
            work_dir=self.work_dir,
            cli_args=self._cli_args,
            extra_args=self._extra_args,
        )

    async def send_btw_streaming(self, session_key: str, prompt: str) -> AsyncIterator[dict]:
        """Like ``send_btw`` but yields stream-json events."""
        session = self._sessions.get(session_key)
        if session is None or session.session_id is None:
            raise RuntimeError("No active session to branch from. Send a normal message first.")

        new_session_id = str(uuid.uuid4())
        clone_session(self.work_dir, session.session_id, new_session_id)

        btw_session = ClaudeSession(
            session_key=f"btw:{new_session_id[:8]}",
            session_id=new_session_id,
        )
        async for event in btw_session.send_streaming(
            prompt,
            cli_path=self.cli_path,
            work_dir=self.work_dir,
            cli_args=self._cli_args,
            extra_args=self._extra_args,
        ):
            yield event

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
